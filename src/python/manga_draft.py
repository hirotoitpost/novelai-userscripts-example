"""
漫画ドラフト: テーマ・ジャンル・登場キャラから大枠シナリオ案を作り、選んだ案を1話(4コマ)ずつ
1シーン=1コマの台本にする。文章は NovelAI の GLM-4.6(全プランで使える汎用モデル)に書かせる。

台本の本文は、話し手が地の文で分かる形(「栄子が言った。「…」」)にこちらで組み立てる。
LLM にはセリフごとの話し手を構造で答えさせるので、合成時の話し手の判定(manga_v2/speakers.py)が
必ず合う。地の文は吹き出しにならない。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .manga_v2.speakers import cast_members
from .novelai_text_oa import INSTRUCT_MODEL, stream_chat

# 1話=4コマ(起承転結)
PANELS_PER_EPISODE = 4
# 形式が崩れたときの作り直しの回数
_MAX_ATTEMPTS = 3
_MAX_LINE_CHARS = 40
_MAX_NARRATION_CHARS = 30


@dataclass(frozen=True)
class DraftCharacter:
    """台本に出すキャラ。name は台本での呼び名(LLM が話し手として答える名前)。"""

    id: int
    name: str
    profile: str


def draft_characters(characters: list[dict[str, Any]], profiles: dict[int, str] | None = None) -> list[DraftCharacter]:
    """
    キャラシートから台本用のキャラを作る。呼び名は、共通の姓を除いた名前(矢野栄子 → 栄子)。
    人物像は画面で書いたもの > キャラシートのメモの順。
    """
    members = cast_members(characters)
    result: list[DraftCharacter] = []
    for character, member in zip(characters, members):
        short = min(member.names, key=len)
        profile = ((profiles or {}).get(character["id"]) or character.get("notes") or "").strip()
        result.append(DraftCharacter(int(character["id"]), short, profile))
    return result


def _cast_block(characters: list[DraftCharacter]) -> str:
    return "\n".join(f"- {c.name}: {c.profile or '(人物像の指定なし)'}" for c in characters)


# ---- JSON の取り出し ----


def extract_json(text: str) -> Any:
    """応答から最初の JSON オブジェクトを取り出す(前置き・```json・思考タグを許す)。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON が見つかりません")
    return json.loads(text[start : end + 1])


async def _ask_json(api_key: str, system: str, user: str, *, max_tokens: int, temperature: float) -> Any:
    """GLM に聞いて JSON を返す。形式が崩れたら作り直す。"""
    last_error = ""
    for _ in range(_MAX_ATTEMPTS):
        parts: list[str] = []
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        async for delta in stream_chat(
            api_key, messages, model=INSTRUCT_MODEL, max_tokens=max_tokens, temperature=temperature
        ):
            parts.append(delta)
        try:
            return extract_json("".join(parts))
        except ValueError as exc:  # json.JSONDecodeError も ValueError
            last_error = f"{exc}: {''.join(parts)[:200]!r}"
    raise RuntimeError(f"台本AIの応答を読み取れませんでした({_MAX_ATTEMPTS}回試行)。{last_error}")


# ---- 大枠シナリオ ----

_OUTLINE_SYSTEM = """あなたは漫画の構成作家です。指定のテーマ・ジャンル・登場人物で、全年齢向けの短編漫画の大枠シナリオ案を{options}つ考えます。
JSONだけを返してください(前置きや```は不要):
{{"outlines":[{{"title":"作品タイトル","logline":"一文のあらすじ","episodes":["第1話の内容","第2話の内容"]}}]}}
- episodes はちょうど{episodes}個。1話は4コマ(起承転結のある小話)で、全体で1つの流れになるようにする
- 各話の内容は、誰が何をしてどうオチるかが分かるように60文字程度で書く
- {options}つの案は雰囲気や切り口をはっきり変える
- 登場人物の性格・口調・呼び方を守る。登場人物は指定された人だけを中心にする
- 露出や性的な描写はしない"""


# 取り込んだ作品の構成を渡すときの但し書き。市販の作品も入るので、内容は使わせない
_STRUCTURE_NOTE = (
    "参考にするコマ運び(別の作品から読み取った、各コマの構図・人数・セリフの量・役割だけ。"
    "このテンポや山場・オチの位置に合わせるが、話の内容・設定・セリフは新しく考える):"
)


def outline_prompts(
    theme: str,
    genre: str,
    characters: list[DraftCharacter],
    episodes: int,
    *,
    notes: str = "",
    previous: list[str] | None = None,
    options: int = 3,
    structure: list[list[str]] | None = None,
) -> tuple[str, str]:
    system = _OUTLINE_SYSTEM.format(options=options, episodes=episodes)
    user = [f"テーマ: {theme}", f"ジャンル: {genre}", "登場人物:", _cast_block(characters)]
    if notes.strip():
        user.append(f"補足: {notes.strip()}")
    if previous:
        user.append("これまでの巻のあらすじ(同じネタは避け、続編として自然にする):")
        user.extend(f"- {p}" for p in previous)
    if structure:
        user.append(_STRUCTURE_NOTE)
        user.extend(f"第{i + 1}話: " + " / ".join(lines) for i, lines in enumerate(structure))
    return system, "\n".join(user)


def parse_outlines(data: Any, episodes: int) -> list[dict[str, Any]]:
    """大枠案を検める。話数が足りない案は捨て、多すぎる分は切る。"""
    outlines: list[dict[str, Any]] = []
    for item in (data or {}).get("outlines") or []:
        if not isinstance(item, dict):
            continue
        # 実機で "log線" のようにキー名が崩れることがあったので、title/episodes 以外は緩く拾う
        logline = item.get("logline") or next(
            (v for k, v in item.items() if k.startswith("log") and isinstance(v, str)), ""
        )
        eps = [str(e).strip() for e in item.get("episodes") or item.get("beats") or [] if str(e).strip()]
        title = str(item.get("title") or "").strip()
        if title and len(eps) >= episodes:
            outlines.append({"title": title, "logline": str(logline).strip(), "episodes": eps[:episodes]})
    return outlines


async def generate_outlines(
    api_key: str,
    theme: str,
    genre: str,
    characters: list[DraftCharacter],
    episodes: int,
    *,
    notes: str = "",
    previous: list[str] | None = None,
    structure: list[list[str]] | None = None,
) -> list[dict[str, Any]]:
    system, user = outline_prompts(
        theme, genre, characters, episodes, notes=notes, previous=previous, structure=structure
    )
    outlines = parse_outlines(await _ask_json(api_key, system, user, max_tokens=4000, temperature=0.9), episodes)
    if not outlines:
        raise RuntimeError("使える大枠シナリオ案がありませんでした。もう一度試してください。")
    return outlines


# ---- 1話分の台本 ----

_EPISODE_SYSTEM = """あなたは4コマ漫画の脚本家です。渡された1話分の内容を、ちょうど4コマの台本にします。JSONだけを返してください(前置きや```は不要):
{"panels":[{"characters":["登場する人物名"],"actions":{"人物名":"その人の表情・動作の英語タグ"},"lines":[{"speaker":"人物名","kind":"speech","text":"セリフ"}],"narration":"","sfx":["効果音"],"prompt_tags":"英語のdanbooruタグ"}]}
- panels はちょうど4つ。起承転結で、4コマ目にオチ
- 1コマのセリフは0〜2個、1つ20文字以内。説明ではなく会話で見せる
- speaker は登場人物名のどれか。kind は声に出すなら speech、心の声なら thought
- narration は場所や時間の説明が必要なときだけ(15文字以内、不要なら空文字)
- sfx はカタカナの擬音・擬態語を0〜1個
- prompt_tags はそのコマ全体の絵: 人数(1girl, 1boy など)、場所、時間帯、構図、二人の位置関係(facing each other など)を英語のdanbooruタグで。人物の髪型・服装は書かない(別に指定する)
- actions は characters の人物ごとの表情・動作・視線を英語のdanbooruタグで(例: "blush, looking away, hand on own cheek")。表情や仕草は prompt_tags ではなく必ずここに書く(全体に書くと全員に付いてしまう)
- characters はそのコマに描く人物(声だけの人は入れない)。人物がいないコマは空で、prompt_tags に no humans
- 全年齢向け。露出・性的な描写はしない"""


def episode_prompts(
    outline: dict[str, Any],
    episode_index: int,
    characters: list[DraftCharacter],
    *,
    notes: str = "",
    previous_panels: list[dict[str, Any]] | None = None,
    structure: list[str] | None = None,
) -> tuple[str, str]:
    episodes = outline["episodes"]
    user = ["登場人物:", _cast_block(characters)]
    if notes.strip():
        user.append(f"補足: {notes.strip()}")
    user.append(f"作品: {outline['title']}({outline.get('logline', '')})")
    user.append("全体の流れ:")
    user.extend(f"{i + 1}. {e}" for i, e in enumerate(episodes))
    if previous_panels:
        # 直前の話のセリフを渡して、流れと口調をつなげる
        recent = [f"{line['speaker']}「{line['text']}」" for p in previous_panels[-4:] for line in p.get("lines", [])]
        if recent:
            user.append("直前の話のセリフ: " + " / ".join(recent))
    user.append(f"この話(第{episode_index + 1}話): {episodes[episode_index]}")
    if structure:
        user.append(
            "各コマはこの構成に合わせる(構図は prompt_tags にも close-up / cowboy shot / full body などで入れる。"
            "セリフの量も合わせる):"
        )
        user.extend(f"{i + 1}コマ目: {line}" for i, line in enumerate(structure))
    return _EPISODE_SYSTEM, "\n".join(user)


def _clean_tags(tags: str) -> str:
    """danbooru タグの _ を空白にし、空や重複を除く。"""
    seen: list[str] = []
    for tag in str(tags or "").split(","):
        tag = tag.strip().replace("_", " ")
        if tag and tag.lower() not in [s.lower() for s in seen]:
            seen.append(tag)
    return ", ".join(seen)


def parse_episode(data: Any, characters: list[DraftCharacter]) -> list[dict[str, Any]]:
    """
    1話分の台本を検めて、画面で編集する形(話し手は名前)にそろえる。知らない人物の名前は、
    描く人物からは外し、セリフの話し手は空にする(合成では一番近い顔へしっぽが向く)。
    """
    names = {c.name for c in characters}
    panels: list[dict[str, Any]] = []
    for raw in ((data or {}).get("panels") or [])[:PANELS_PER_EPISODE]:
        if not isinstance(raw, dict):
            continue
        lines = []
        for line in raw.get("lines") or []:
            if not isinstance(line, dict) or not str(line.get("text") or "").strip():
                continue
            text = str(line["text"]).strip().strip("「」『』（）()")[:_MAX_LINE_CHARS]
            speaker = str(line.get("speaker") or "").strip()
            lines.append(
                {
                    "speaker": speaker if speaker in names else "",
                    "kind": "thought" if line.get("kind") == "thought" else "speech",
                    "text": text,
                }
            )
        sfx = [str(s).strip() for s in raw.get("sfx") or [] if str(s).strip()]
        # 「ニヤ」が ["ニ", "ヤ"] のように1文字ずつに分かれて返ることがある(実機で確認)
        if len(sfx) > 1 and all(len(s) == 1 for s in sfx):
            sfx = ["".join(sfx)]
        sfx = sfx[:2]
        drawn = [n for n in dict.fromkeys(str(n).strip() for n in raw.get("characters") or []) if n in names]
        raw_actions = raw.get("actions")
        if not isinstance(raw_actions, dict):
            raw_actions = {}
        # 描く人物の分だけ残す(描かない人の動作は使い道がない)
        actions = {
            str(name).strip(): _clean_tags(tags)
            for name, tags in raw_actions.items()
            if str(name).strip() in drawn and _clean_tags(tags)
        }
        panels.append(
            {
                "characters": drawn,
                "actions": actions,
                "lines": lines,
                "narration": str(raw.get("narration") or "").strip()[:_MAX_NARRATION_CHARS],
                "sfx": sfx,
                "prompt_tags": _clean_tags(raw.get("prompt_tags") or ""),
            }
        )
    if len(panels) != PANELS_PER_EPISODE:
        raise ValueError(f"コマ数が{len(panels)}でした(4コマ必要)")
    return panels


async def generate_episode(
    api_key: str,
    outline: dict[str, Any],
    episode_index: int,
    characters: list[DraftCharacter],
    *,
    notes: str = "",
    previous_panels: list[dict[str, Any]] | None = None,
    structure: list[str] | None = None,
) -> list[dict[str, Any]]:
    system, user = episode_prompts(
        outline, episode_index, characters, notes=notes, previous_panels=previous_panels, structure=structure
    )
    last_error = ""
    for _ in range(_MAX_ATTEMPTS):
        data = await _ask_json(api_key, system, user, max_tokens=3000, temperature=0.7)
        try:
            return parse_episode(data, characters)
        except ValueError as exc:
            last_error = str(exc)
    raise RuntimeError(f"第{episode_index + 1}話の台本を作れませんでした: {last_error}")


# ---- 台本 → 物語のシーン ----


def panel_to_scene(panel: dict[str, Any], characters: list[DraftCharacter], title: str | None = None) -> dict[str, Any]:
    """
    画面で編集した1コマを、/api/story/scripted のシーンにする。本文は話し手が地の文で分かる形にする:
    声は「{名前}が言った。「…」」(同じ行の主語)、心の声は「（…）」の次の行に「{名前}は心の中で思った。」。
    話し手が空のセリフは地の文を付けない(合成では一番近い顔へしっぽが向く)。
    """
    by_name = {c.name: c for c in characters}
    rows: list[str] = []
    for line in panel.get("lines") or []:
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        speaker = by_name.get(str(line.get("speaker") or ""))
        if line.get("kind") == "thought":
            rows.append(f"（{text}）")
            if speaker:
                rows.append(f"{speaker.name}は心の中で思った。")
        else:
            rows.append(f"{speaker.name}が言った。「{text}」" if speaker else f"「{text}」")
    if not rows:
        # 吹き出しの無いコマも本文は要る(地の文は吹き出しにならない)
        rows.append(panel.get("narration") or "(セリフなし)")
    return {
        "title": title,
        "text": "\n".join(rows),
        "prompt_tags": _clean_tags(panel.get("prompt_tags") or ""),
        "character_ids": [by_name[n].id for n in panel.get("characters") or [] if n in by_name],
        "character_actions": {
            by_name[n].id: _clean_tags(tags)
            for n, tags in (panel.get("actions") or {}).items()
            if n in by_name and n in (panel.get("characters") or []) and _clean_tags(tags)
        },
        "narration": (panel.get("narration") or "").strip() or None,
        "sfx": [s for s in panel.get("sfx") or [] if str(s).strip()],
    }
