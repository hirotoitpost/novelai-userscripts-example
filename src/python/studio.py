"""
対話生成(/studio): 文章モデルと日本語で相談しながら、画像のタグを組み立てて直していく。

- 文章モデル(NovelAI の GLM-4.6)は、ツール呼び出し(OpenAI 形式の tools)を黙って無視する(2026-11 の実機)。
  代わりに「決まった形の JSON だけを返す」ように頼み、アプリが読み取って生成する。
- モデルが書くのは場面のタグ(scene)だけ。キャラの見た目・普段の服・絵柄はキャラシートからアプリが入れる
  (モデルに任せると、キャラの名前をタグにしたり、髪の色を書き換えたりする)。
- タグの効き方のメモ(studio_knowledge.md)を毎回渡す。GLM はそのままだと構図・ポーズのタグを知らない。
- 安全: 場面のタグは content_guard.sanitize_scene_tags を通す。成人として登録していないキャラには、
  性的なタグの生成を断る(build_prompt で ValueError)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KNOWLEDGE_PATH = Path(__file__).resolve().parent / "studio_knowledge.md"
ACTIONS = ("generate", "ask", "none")
SIZES = ("portrait", "landscape", "square")
# 会話を長く渡すと文脈(約 2.8 万トークン)に入りきらない。古いものから落とす
MAX_HISTORY_TURNS = 16
MAX_HISTORY_CHARS = 12000


@dataclass
class Proposal:
    say: str
    action: str
    scene: str
    negative: str
    size: str
    use_outfit: bool
    changes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "say": self.say, "action": self.action, "scene": self.scene, "negative": self.negative,
            "size": self.size, "use_outfit": self.use_outfit, "changes": self.changes,
        }


def knowledge() -> str:
    try:
        return KNOWLEDGE_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _tag_key(tag: str) -> str:
    """重み(1.3::tag::, {tag}, (tag))を外して、比べるための形にする。"""
    t = tag.strip().lower()
    t = re.sub(r"^-?\d+(\.\d+)?::", "", t)
    t = t.replace("::", "")
    t = t.strip("{}()[] ")
    return re.sub(r"\s+", " ", t)


def split_tags(text: str | None) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def join_tags(*parts: str | None) -> str:
    """カンマ区切りをつなぐ。同じタグ(重みを除いて同じ)は最初の1つだけ残す。"""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for tag in split_tags(part):
            key = _tag_key(tag)
            if key and key not in seen:
                seen.add(key)
                out.append(tag)
    return ", ".join(out)


Characters = dict[str, Any] | list[dict[str, Any]] | None


def _as_list(characters: Characters) -> list[dict[str, Any]]:
    if not characters:
        return []
    return [characters] if isinstance(characters, dict) else [c for c in characters if c]


def character_tags(characters: Characters) -> set[str]:
    """キャラシートが入れるタグ(scene から取り除く)。コマなら場面のキャラ全員の分。"""
    keys: set[str] = set()
    for character in _as_list(characters):
        for field in ("appearance_tags", "outfit_tags", "style_tags", "body_tags"):
            keys.update(_tag_key(t) for t in split_tags(character.get(field)))
    return keys


def scene_from_prompt(character: Characters, prompt: str) -> str:
    """生成済みの画像のプロンプトから、キャラシートが入れる分を除いた場面のタグ(画像から始めるとき)。"""
    own = character_tags(character)
    return ", ".join(t for t in split_tags(prompt) if _tag_key(t) not in own)


def _name_keys(characters: Characters) -> set[str]:
    return {str(c.get("name") or "").strip().lower() for c in _as_list(characters)} - {""}


def clean_scene(character: Characters, scene: str) -> str:
    """モデルが書いた場面のタグから、キャラシートの分とキャラの名前を除く。"""
    own = character_tags(character) | _name_keys(character)
    return join_tags(", ".join(t for t in split_tags(scene) if _tag_key(t) not in own))


def is_adult_character(character: dict[str, Any] | None) -> bool:
    from .content_guard import is_defined_adult

    return bool(character) and is_defined_adult(character or {})


def build_prompt(character: dict[str, Any] | None, scene: str, negative: str, use_outfit: bool) -> tuple[str, str]:
    """
    生成に使うプロンプトとネガティブ。キャラの見た目 + (普段の服) + 場面 + 絵柄。
    成人として登録していないキャラ(キャラ無しも含む)で性的なタグがあれば ValueError。
    """
    from . import content_guard

    adult = is_adult_character(character)
    scene_tags = clean_scene(character, scene)
    scene_tags = content_guard.sanitize_scene_tags(scene_tags, adult=False, costume_ok=adult)
    if not adult and content_guard.is_sexual(scene_tags):
        raise ValueError("成人として登録したキャラ(キャラシートで年齢を入れたもの)でないと、性的な場面は描けません。")
    if character:
        prompt = join_tags(
            character.get("appearance_tags"),
            character.get("body_tags"),
            character.get("outfit_tags") if use_outfit else "",
            scene_tags,
            character.get("style_tags"),
        )
        neg = join_tags(negative, character.get("negative_tags"))
    else:
        prompt, neg = scene_tags, join_tags(negative)
    if content_guard.is_sexual(prompt):
        safety = content_guard.text("scene.adult_safety_negative")
        neg = join_tags(neg, safety)
    return prompt, neg


def _who(character: dict[str, Any]) -> str:
    return (
        f"キャラ: {character.get('name')}(年齢 {character.get('age') or '未設定'})。\n"
        f"見た目のタグ(アプリが入れる。scene に書かない): {character.get('appearance_tags') or ''}\n"
        f"普段の服(use_outfit が true のときアプリが入れる): {character.get('outfit_tags') or 'なし'}\n"
        f"メモ: {(character.get('notes') or '')[:300]}"
    )


def _panel_note(panel: dict[str, Any]) -> str:
    """漫画のコマを描き直すときの前置き。"""
    return (
        "これは漫画のコマの描き直し。コマの形(縦長・横長)とモノクロ/カラーはアプリが決めるので、size は前のままでよい。\n"
        "登場人物の見た目・服はキャラシートとシーンの設定からアプリが入れる(use_outfit は使わない)。"
        "scene には、人数(1girl, 2girls など)・構図・ポーズ・表情・背景・光を書く。セリフ・吹き出し・文字は描かない。\n"
        "negative は「このコマに出てはいけない物」(英語タグ)。\n"
        f"シーンの内容: {panel.get('title') or ''} / {str(panel.get('text') or '')[:400]}"
    )


def system_prompt(character: Characters, panel: dict[str, Any] | None = None) -> str:
    members = _as_list(character)
    who = "\n\n".join(_who(c) for c in members) if members else "キャラは選ばれていない。人物の見た目も scene に書く。"
    if panel:
        who = f"{_panel_note(panel)}\n\n{who}"
    return f"""あなたは NovelAI(NAI Diffusion 4.5)の画像生成アシスタント。ユーザーと日本語で相談しながら1枚の絵を仕上げる。
毎回、次の JSON オブジェクトだけを返す(前後に文章・コードブロックを付けない):
{{"say": "ユーザーへの返事(日本語。短く。説明を頼まれたらここに詳しく)",
 "action": "generate"(描く・描き直す) | "ask"(描く前に確かめたいことがある) | "none"(説明だけ・描かない),
 "scene": "場面のタグ(英語の Danbooru タグ、カンマ区切り、重みは 1.3::tag::)。構図・ポーズ・表情・服・背景・光",
 "negative": "ネガティブのタグ(英語、カンマ区切り)",
 "size": "portrait" | "landscape" | "square",
 "use_outfit": true(キャラの普段の服を着せる) | false(scene で服を決める),
 "changes": "前回からタグをどう変えたか(日本語。初回は空)"}}
決まり:
- 前回の scene と negative を土台にして、指摘された所だけ直す。重みを黙って消さない。頼まれていない背景・小物を足さない。
- 「説明して」「どういう意味?」には action を "none" にして、say で答える(scene・negative は前回のまま)。
- 絵は縦長(portrait)が既定。横長を頼まれたときだけ landscape。
- 子どもの体・未成年に見える描写はしない。性器・肛門は描かない。

{who}

タグの効き方(実際に確かめたこと):
{knowledge()}"""


def state_note(current: dict[str, Any] | None) -> str:
    """今のタグ(手で直した分も含む)をモデルに伝える。"""
    if not current:
        return "(まだ何も描いていない)"
    return (
        "今のタグ(これを土台にする):\n"
        f"scene: {current.get('scene') or ''}\n"
        f"negative: {current.get('negative') or ''}\n"
        f"size: {current.get('size') or 'portrait'} / use_outfit: {bool(current.get('use_outfit'))}"
    )


def build_messages(
    character: Characters,
    history: list[dict[str, str]],
    message: str,
    current: dict[str, Any] | None,
    panel: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """system + 会話(新しい方から入るだけ)+ 今のタグ + 今回の発言。"""
    turns: list[dict[str, str]] = []
    total = 0
    for turn in reversed(history[-MAX_HISTORY_TURNS:]):
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = str(turn.get("content") or "")
        total += len(content)
        if total > MAX_HISTORY_CHARS:
            break
        turns.append({"role": role, "content": content})
    turns.reverse()
    return [
        {"role": "system", "content": system_prompt(character, panel)},
        *turns,
        {"role": "user", "content": f"{state_note(current)}\n\nユーザー: {message}"},
    ]


def _json_object(text: str) -> dict[str, Any] | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_reply(text: str, current: dict[str, Any] | None) -> Proposal | None:
    """モデルの答え(JSON)を読む。読めなければ None。足りない所は今のタグで埋める。"""
    data = _json_object(text)
    if data is None:
        return None
    current = current or {}
    action = str(data.get("action") or "").strip().lower()
    if action not in ACTIONS:
        action = "generate" if data.get("scene") else "none"
    size = str(data.get("size") or current.get("size") or "portrait").strip().lower()
    if size not in SIZES:
        size = "portrait"
    use_outfit = data.get("use_outfit")
    if not isinstance(use_outfit, bool):
        use_outfit = bool(current.get("use_outfit", False))
    scene = str(data.get("scene") or data.get("prompt") or "").strip()
    negative = str(data.get("negative") or "").strip()
    if action != "generate":
        scene = scene or str(current.get("scene") or "")
        negative = negative or str(current.get("negative") or "")
    return Proposal(
        say=str(data.get("say") or "").strip(),
        action=action,
        scene=scene,
        negative=negative,
        size=size,
        use_outfit=use_outfit,
        changes=str(data.get("changes") or "").strip(),
    )


def tag_diff(before: str | None, after: str | None) -> dict[str, list[str]]:
    """足したタグ・外したタグ(重みだけ変えたものは changed)。"""
    old = {_tag_key(t): t for t in split_tags(before)}
    new = {_tag_key(t): t for t in split_tags(after)}
    return {
        "added": [t for k, t in new.items() if k not in old],
        "removed": [t for k, t in old.items() if k not in new],
        "changed": [t for k, t in new.items() if k in old and old[k] != t],
    }
