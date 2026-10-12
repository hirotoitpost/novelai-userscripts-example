"""
ギャラリーで選んだ画像から漫画を作る(routes/gallery_manga.py)。選んだ画像1枚が1コマになる。

- コマの絵: 選んだ画像そのものは使わず、その画像のプロンプトから似た絵を生成し直す(漫画のコマの形と絵柄に
  そろえるため)。プロンプトは、画像に埋め込まれた NovelAI の設定(無ければギャラリーの記録)から取り、品質タグ
  などを除く。指定のジャンルに合うように、文章モデル(NovelAI の GLM)が表情・動作のタグを足したり外したりする。
- セリフ・描き文字・ナレーション: 文章モデルが、ジャンルと各コマのプロンプトから、1つの話になるように考える。

登録済みのキャラは使わない(だれの絵かは画像のプロンプトにしか無い)ので、セリフに話し手は付かず、吹き出しの
しっぽは近い顔へ向く。人物の見た目は、各コマのプロンプトに入っているタグがそのまま生かされる。
"""

from __future__ import annotations

import re
from typing import Any

from . import content_guard
from .manga_draft import _ask_json, _clean_tags, _content_rule

# 1回に漫画にできる画像の数(1コマ=1枚。4コマ×6ページ)
MAX_IMAGES = 24
# セリフ・タグの上限
MAX_LINES = 2
MAX_LINE_CHARS = 40
MAX_NARRATION_CHARS = 15
MAX_ADDED_TAGS = 6
_MAX_ATTEMPTS = 3

# コマのプロンプトから除くタグ: 品質・絵柄・レーティングなど、コマの生成の側で付け直すもの
# (manga_v2/prompt.py の build_panel_prompt)と、漫画のコマに要らないもの
_DROP = re.compile(
    r"^(masterpiece|best quality|amazing quality|very aesthetic|aesthetic|absurdres|highres|lowres|"
    r"no text|text|location|rating:.*|nsfw|sfw|manga style|monochrome|greyscale|screentone|"
    r"(very )?(high|low|normal) complexity|year \d{4}|newest|recent|official art|"
    r"-?\d+(\.\d+)?::.*|.*::)$",
    re.IGNORECASE,
)
# プロンプトの強調の書き方(1.3::tag:: や {tag} や [tag])を外す。マイナスの重み(-1::tag::)は「出さない」指定なので、
# タグごと除く
_NEGATIVE_WEIGHT = re.compile(r"-\d+(?:\.\d+)?::.*?::")
_WEIGHT = re.compile(r"\d+(?:\.\d+)?::(.*?)::")
# 角かっこで囲んだ、英語でない見出し
_LABEL = re.compile(r"\[[^\]]*[^\x00-\x7f][^\]]*\]")
# 文章モデルに外させないタグ: 人数(1girl など)
_COUNT = re.compile(r"^(\d\+?|multiple )(girls?|boys?|others?)$|^solo$", re.IGNORECASE)

_SYSTEM = """あなたは漫画の脚本家です。画像のプロンプト(英語のタグ)を、コマの順に渡します。1枚が1コマです。
指定のジャンルに合う、1つの話になるように、各コマのセリフ・効果音と、絵の手直しを考えてください。
JSONだけを返してください(前置きや```は不要):
{"title":"作品タイトル","panels":[{"lines":[{"kind":"speech","text":"セリフ"}],"sfx":["効果音"],"narration":"","add_tags":"英語タグ","remove_tags":"英語タグ"}]}
- panels はちょうど{count}つ。渡されたコマと同じ順。コマの順は入れ替えない
- 全体で1つの流れになるようにする(導入 → 展開 → 最後のコマにオチや余韻)。ジャンルの雰囲気を出す
- lines は各コマ0〜2個、1つ{line_chars}文字以内。kind は声に出すなら speech、心の声なら thought。絵に描かれていることと合うセリフにする。人物のいないコマは空
- sfx はカタカナかひらがなの擬音・擬態語を0〜1個。絵の動きや空気に合うときだけ
- narration は場所や時間の説明が必要なときだけ({narration_chars}文字以内、不要なら空文字)
- add_tags は、ジャンルと話に合うように絵に足す表情・動作・小物の英語のdanbooruタグ(0〜{added}個)。人物の髪・目・服などの見た目は変えない
- remove_tags は、ジャンルと話に合わないので外すタグ。渡されたプロンプトにあるタグだけを書く。人数・髪・目・服のタグは外さない
- 元の絵と似た絵にする。足す・外すのは必要なものだけにする{content_rule}"""


def clean_source_tags(prompt: str) -> str:
    """画像のプロンプトを、コマのプロンプトにする: 強調の書き方を外し、品質・絵柄のタグと、使えないタグを除く。"""
    # ギャラリーの記録には、タグではない見出し([表情: scared] や [2倍に拡大])が入っていることがある
    text = _LABEL.sub(", ", str(prompt or ""))
    text = _WEIGHT.sub(r"\1", _NEGATIVE_WEIGHT.sub("", text))
    text = re.sub(r"[{}\[\]]", "", text).replace("\n", ", ")
    # タグは英語なので、それ以外の文字を含むものは使わない
    kept = [t for t in _clean_tags(text).split(", ") if t and t.isascii() and not _DROP.match(t)]
    return content_guard.sanitize_scene_tags(", ".join(kept), adult=False)


def settings_tags(settings: dict[str, Any] | None, fallback: str = "") -> str:
    """
    画像に埋め込まれた設定(nai_metadata.generation_settings)から、コマのプロンプトを作る。本体のプロンプトに、
    キャラごとのプロンプトをつなげる。設定が無ければ fallback(ギャラリーの記録のプロンプト)を使う。
    """
    if not settings:
        return clean_source_tags(fallback)
    parts = [str(settings.get("prompt") or "")]
    parts.extend(str(c.get("prompt") or "") for c in settings.get("characters") or [])
    return clean_source_tags(", ".join(p for p in parts if p.strip())) or clean_source_tags(fallback)


def script_prompts(genre: str, theme: str, sources: list[str]) -> tuple[str, str]:
    system = (
        _SYSTEM.replace("{count}", str(len(sources)))
        .replace("{line_chars}", str(MAX_LINE_CHARS))
        .replace("{narration_chars}", str(MAX_NARRATION_CHARS))
        .replace("{added}", str(MAX_ADDED_TAGS))
        .replace("{content_rule}", _content_rule())
    )
    user = [f"ジャンル: {genre}"]
    if theme.strip():
        user.append(f"話の方向・補足: {theme.strip()}")
    user.extend(f"{i + 1}コマ目: {tags or '(プロンプトなし)'}" for i, tags in enumerate(sources))
    return system, "\n".join(user)


def _tag_text(value: Any) -> str:
    """文章モデルが返したタグ(文字列のことも、配列のこともある)を、カンマ区切りの文字列にする。"""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if str(v).strip())
    return str(value or "")


def apply_tag_changes(source: str, add: Any, remove: Any) -> str:
    """
    元のプロンプトから remove のタグを外し、add のタグを足す。外せるのは元にあるタグだけで、人数と、人物の見た目
    (髪・目・服など)のタグは外さない(だれの絵かが変わってしまうため)。
    """
    from .manga_similar import _is_look

    original = [t for t in source.split(", ") if t]
    drop = {t.lower() for t in _clean_tags(_tag_text(remove)).split(", ") if t}
    kept = [t for t in original if t.lower() not in drop or _COUNT.match(t) or _is_look(t)]
    # 全部外すような答えは、絵が別物になるので聞かない
    if original and len(kept) < max(1, len(original) // 2):
        kept = original
    added = [t for t in clean_source_tags(_tag_text(add)).split(", ") if t][:MAX_ADDED_TAGS]
    return content_guard.sanitize_scene_tags(_clean_tags(", ".join([*kept, *added])), adult=False)


def parse_script(data: Any, sources: list[str]) -> dict[str, Any]:
    """文章モデルの答えを検める。コマ数が足りなければ ValueError。"""
    raw_panels = data.get("panels") if isinstance(data, dict) else None
    if not isinstance(raw_panels, list) or len(raw_panels) < len(sources):
        raise ValueError("コマ数が合いません")
    panels = []
    for source, raw in zip(sources, raw_panels):
        raw = raw if isinstance(raw, dict) else {}
        lines = []
        for line in raw.get("lines") if isinstance(raw.get("lines"), list) else []:
            text = str((line or {}).get("text") or "").strip() if isinstance(line, dict) else ""
            if text:
                kind = "thought" if line.get("kind") == "thought" else "speech"
                lines.append({"kind": kind, "text": text[:MAX_LINE_CHARS]})
        sfx = [str(s).strip() for s in (raw.get("sfx") if isinstance(raw.get("sfx"), list) else []) if str(s).strip()]
        panels.append(
            {
                "lines": lines[:MAX_LINES],
                "sfx": sfx[:1],
                "narration": str(raw.get("narration") or "").strip()[:MAX_NARRATION_CHARS],
                "prompt_tags": apply_tag_changes(source, raw.get("add_tags"), raw.get("remove_tags")),
            }
        )
    title = str(data.get("title") or "").strip()[:100]
    return {"title": title, "panels": panels}


def fallback_script(sources: list[str]) -> dict[str, Any]:
    """文章モデルに考えさせられなかったとき: 絵だけ(セリフなし)。"""
    return {
        "title": "",
        "panels": [{"lines": [], "sfx": [], "narration": "", "prompt_tags": source} for source in sources],
    }


async def write_script(api_key: str, genre: str, theme: str, sources: list[str]) -> tuple[dict[str, Any], bool]:
    """(台本, 文章モデルが考えたか)。うまく答えが得られなければ、セリフなしの台本を返す。"""
    system, user = script_prompts(genre, theme, sources)
    for _ in range(_MAX_ATTEMPTS):
        try:
            data = await _ask_json(api_key, system, user, max_tokens=400 + 260 * len(sources), temperature=0.8)
            return parse_script(data, sources), True
        except (ValueError, RuntimeError):
            continue
    return fallback_script(sources), False


def panel_scene(panel: dict[str, Any], title: str) -> dict[str, Any]:
    """台本の1コマを、/api/story/scripted のシーンにする。話し手は付けない(登録済みのキャラを使わないため)。"""
    rows = [f"（{line['text']}）" if line["kind"] == "thought" else f"「{line['text']}」" for line in panel["lines"]]
    if not rows:
        # 吹き出しの無いコマも本文は要る(地の文は吹き出しにならない)
        rows.append(panel["narration"] or "(セリフなし)")
    return {
        "title": title,
        "text": "\n".join(rows),
        "prompt_tags": panel["prompt_tags"],
        "character_ids": [],
        "character_actions": {},
        "narration": panel["narration"] or None,
        "sfx": panel["sfx"],
    }
