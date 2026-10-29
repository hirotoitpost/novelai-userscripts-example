"""
取り込んだ漫画を、セリフごと台本に起こす(取り込みの使い方 rebuild)。自分の作品(権利のある作品)を、
このアプリで描き直すためのもの。

セリフの文面は読み取ったもの(画面で直したもの)を使う。整える(clean_page)ときだけ、文章モデルに文字の
読み違いを直させ、英語のセリフを日本語に訳させ、セリフでない文字(広告・クレジット・意味のないかけら)を
外させる(言い回しは変えさせない)。描き文字(効果音)は、画面で読みを入れたものを台本の効果音にする
(読みの無い候補は入れない)。話し手を決める(assign_page)ときに文章モデル(NovelAI の GLM)に決めさせるのは、
各セリフの話し手・声か心の声か・コマに描く人物・所作のタグを誰に付けるか、だけ。セリフの文面は返させない
(書き換えさせない)。
"""

from __future__ import annotations

from typing import Any

from .manga_draft import DraftCharacter, _ask_json, _clean_tags

_MAX_ATTEMPTS = 3
# 台本の1コマに入れられるセリフの数(models.MangaDraftPanel.lines の上限)
MAX_LINES = 8
# 台本の1コマに入れる効果音の数(manga_draft.parse_episode と同じ)
MAX_SFX = 2
# 構図のタグが読めなかったコマに、寄り/引きから入れるタグ
_SHOT_TAGS = {"close-up": "close-up", "medium": "upper body", "long": "full body"}

_SYSTEM = """あなたは漫画の編集者です。漫画の1ページ分について、コマごとのセリフ(絵から読み取ったもの)と、絵から読み取ったタグを渡します。
各セリフの話し手と、各コマに描かれている人物と、その表情・動作を決めてください。JSONだけを返してください(前置きや```は不要):
{"panels":[{"characters":["人物名"],"actions":{"人物名":"英語タグ"},"speakers":["1つ目のセリフの話し手"],"kinds":["speech"]}]}
- panels は渡されたコマと同じ数・同じ順
- speakers と kinds は、そのコマのセリフと同じ数・同じ順。セリフが無いコマは空の配列
- speakers の話し手は登場人物名のどれか。口調・呼び方・前後の流れから決める。どうしても分からなければ空文字
- kinds は、声に出すセリフなら speech、心の声・独白なら thought
- characters はそのコマに描かれている人物(「写っている人」の人数と性別に合わせる。声だけの人は入れない)。人物がいないコマは空
- actions は、そのコマの「所作」のタグを、当てはまる人物に割り振る(渡されたタグだけを使う。当てはまらなければ入れない)
- セリフの文面は返さない(書き換えない)"""


# 整えたセリフ1つの長さの上限(台本のセリフの上限と同じ。40字で切ると、長いセリフが文の途中で切れた)
CLEAN_LINE_CHARS = 80

_CLEAN_SYSTEM = f"""あなたは漫画の翻訳・校正の担当です。漫画の1ページ分の、コマごとのセリフを渡します。
セリフは絵から文字認識で読み取ったもので、読み違い(綴りの崩れ・語のつながり・先頭や末尾の欠け)や、セリフではない文字
(広告・クレジット・ページ番号・描き文字や絵の一部の読み違い)が混ざっています。各セリフを、日本語の自然なセリフに整えてください。
JSONだけを返してください(前置きや```は不要):
{{"panels":[{{"lines":["整えたセリフ",""]}}]}}
- panels は渡されたコマと同じ数・同じ順。lines は、そのコマのセリフと同じ数・同じ順
- 英語のセリフは日本語に訳す(漫画のセリフらしい話し言葉に。人名はカタカナ)
- 読み違いは、前後のセリフの流れから元の文を推し量って直してから訳す(例: 「SANACHAN OS SI CUTE」は SANA-CHAN IS SO CUTE)
- 日本語のセリフは、読み違いを直すだけにして、言い回しは変えない
- セリフではないもの・意味を推し量れないかけらは、空文字にする。英語のページに混ざる日本語の1〜2字(「山」「十」)や、
  文にならない語の並び(「Golban P」「han Brain」)は、絵や描き文字の読み違いなので空文字
- 1つのセリフは{CLEAN_LINE_CHARS}字まで。新しいセリフは足さない"""


def _texts(panel: dict[str, Any]) -> list[str]:
    texts = (str(line.get("text") or "").strip() for line in panel.get("lines") or [])
    return [text for text in texts if text][:MAX_LINES]


def clean_prompts(panels: list[dict[str, Any]]) -> tuple[str, str]:
    blocks = []
    for index, panel in enumerate(panels):
        texts = _texts(panel)
        rows = [f"{index + 1}コマ目:"]
        rows.extend(f"  セリフ{i + 1}: 「{text}」" for i, text in enumerate(texts))
        if not texts:
            rows.append("  セリフなし")
        blocks.append("\n".join(rows))
    return _CLEAN_SYSTEM, "\n".join(blocks)


def parse_cleaned(data: Any, panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    整えたセリフを、コマのセリフに入れ替える(空文字にしたセリフは外す)。コマ数が合わなければ ValueError。
    セリフの数が合わないコマは、読み取ったままにする。
    """
    raw_panels = (data or {}).get("panels") if isinstance(data, dict) else None
    if not isinstance(raw_panels, list) or len(raw_panels) < len(panels):
        raise ValueError("コマ数が合いません")
    result = []
    for panel, raw in zip(panels, raw_panels):
        lines = [line for line in panel.get("lines") or [] if str(line.get("text") or "").strip()][:MAX_LINES]
        cleaned = (raw or {}).get("lines") if isinstance(raw, dict) else None
        if isinstance(cleaned, list) and len(cleaned) == len(lines):
            lines = [
                {**line, "text": text[:CLEAN_LINE_CHARS]}
                for line, text in zip(lines, (str(t or "").strip().strip("「」") for t in cleaned))
                if text
            ]
        result.append({**panel, "lines": lines})
    return result


async def clean_page(api_key: str, panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1ページ分のセリフを整える(読み違いを直し、日本語にし、セリフでない文字を外す)。整えられなければ読み取ったまま。"""
    if not any(_texts(p) for p in panels):
        return panels
    system, user = clean_prompts(panels)
    for _ in range(_MAX_ATTEMPTS):
        try:
            data = await _ask_json(api_key, system, user, max_tokens=2500, temperature=0.2)
            return parse_cleaned(data, panels)
        except (ValueError, RuntimeError):
            continue
    return panels


def _panel_block(index: int, panel: dict[str, Any]) -> str:
    rows = [f"{index + 1}コマ目:"]
    rows.append("  写っている人: " + (", ".join(panel.get("cast_tags") or []) or f"{panel.get('people', 0)}人"))
    if panel.get("scene_tags"):
        rows.append("  場面: " + ", ".join(panel["scene_tags"]))
    if panel.get("action_tags"):
        rows.append("  所作: " + ", ".join(panel["action_tags"]))
    lines = [line for line in panel.get("lines") or [] if str(line.get("text") or "").strip()][:MAX_LINES]
    if lines:
        rows.extend(f"  セリフ{i + 1}: 「{line['text']}」" for i, line in enumerate(lines))
    else:
        rows.append("  セリフなし")
    return "\n".join(rows)


def assign_prompts(
    panels: list[dict[str, Any]], characters: list[DraftCharacter], looks: dict[str, str]
) -> tuple[str, str]:
    cast = "\n".join(
        f"- {c.name}: {looks.get(c.name) or '(見た目の指定なし)'} / {c.profile or '(人物像の指定なし)'}"
        for c in characters
    )
    user = (
        "登場人物(名前: 見た目 / 人物像):\n"
        + cast
        + "\n\n"
        + "\n".join(_panel_block(i, p) for i, p in enumerate(panels))
    )
    return _SYSTEM, user


def parse_assignment(data: Any, panels: list[dict[str, Any]], characters: list[DraftCharacter]) -> list[dict[str, Any]]:
    """文章モデルの答えを検める。コマ数が合わなければ ValueError。セリフの数が合わないコマは、話し手を空にする。"""
    names = [c.name for c in characters]
    raw_panels = (data or {}).get("panels") if isinstance(data, dict) else None
    if not isinstance(raw_panels, list) or len(raw_panels) < len(panels):
        raise ValueError("コマ数が合いません")
    result = []
    for panel, raw in zip(panels, raw_panels):
        raw = raw if isinstance(raw, dict) else {}
        count = len([line for line in panel.get("lines") or [] if str(line.get("text") or "").strip()][:MAX_LINES])
        speakers = [str(s or "").strip() for s in raw.get("speakers") or []]
        kinds = [str(k or "") for k in raw.get("kinds") or []]
        if len(speakers) != count:
            speakers = [""] * count
        if len(kinds) != count:
            kinds = ["speech"] * count
        drawn = [n for n in dict.fromkeys(str(n).strip() for n in raw.get("characters") or []) if n in names]
        allowed = {t.lower() for t in panel.get("action_tags") or []}
        actions = {}
        for name, tags in (raw.get("actions") if isinstance(raw.get("actions"), dict) else {}).items():
            kept = [t for t in _clean_tags(tags).split(", ") if t.lower() in allowed]
            if str(name).strip() in drawn and kept:
                actions[str(name).strip()] = ", ".join(kept)
        result.append(
            {
                "characters": drawn,
                "actions": actions,
                "speakers": [s if s in names else "" for s in speakers],
                "kinds": ["thought" if k == "thought" else "speech" for k in kinds],
            }
        )
    return result


def _fallback(panels: list[dict[str, Any]], characters: list[DraftCharacter]) -> list[dict[str, Any]]:
    """文章モデルに決めさせられなかったとき: 話し手は空、描く人物は人数分を先頭から。"""
    result = []
    for panel in panels:
        count = len([line for line in panel.get("lines") or [] if str(line.get("text") or "").strip()][:MAX_LINES])
        drawn = [c.name for c in characters[: min(int(panel.get("people") or 0), len(characters))]]
        result.append({"characters": drawn, "actions": {}, "speakers": [""] * count, "kinds": ["speech"] * count})
    return result


async def assign_page(
    api_key: str, panels: list[dict[str, Any]], characters: list[DraftCharacter], looks: dict[str, str]
) -> list[dict[str, Any]]:
    """1ページ分のコマについて、話し手・描く人物・所作の割り振りを決める。"""
    system, user = assign_prompts(panels, characters, looks)
    for _ in range(_MAX_ATTEMPTS):
        try:
            data = await _ask_json(api_key, system, user, max_tokens=2500, temperature=0.2)
            return parse_assignment(data, panels, characters)
        except (ValueError, RuntimeError):
            continue
    return _fallback(panels, characters)


def panel_sfx(panel: dict[str, Any]) -> list[str]:
    """コマの効果音: 読みの入っている描き文字を、大きいものから MAX_SFX 個まで。"""
    order = {"大": 0, "中": 1, "小": 2}
    named = [s for s in panel.get("sfx") or [] if str(s.get("reading") or "").strip()]
    named.sort(key=lambda s: order.get(str(s.get("size")), 1))
    return list(dict.fromkeys(str(s["reading"]).strip() for s in named))[:MAX_SFX]


def script_panels(panels: list[dict[str, Any]], assignment: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """読み取ったコマと割り振りから、台本の1ページ分(models.MangaDraftPanel の形)を作る。セリフは読み取った文面のまま。"""
    result = []
    for panel, assigned in zip(panels, assignment):
        texts = [str(line["text"]).strip() for line in panel.get("lines") or [] if str(line.get("text") or "").strip()]
        lines = [
            {"speaker": speaker, "kind": kind, "text": text[:80]}
            for text, speaker, kind in zip(texts[:MAX_LINES], assigned["speakers"], assigned["kinds"])
        ]
        tags = list(panel.get("scene_tags") or [])
        shot = _SHOT_TAGS.get(str(panel.get("shot")))
        if shot and not any(t in _SHOT_TAGS.values() or t in ("cowboy shot", "portrait") for t in tags):
            tags.insert(0, shot)
        if not assigned["characters"] and "no humans" not in tags:
            tags.append("no humans")
        result.append(
            {
                "characters": assigned["characters"],
                "actions": assigned["actions"],
                "lines": lines,
                "narration": "",
                "sfx": panel_sfx(panel),
                "prompt_tags": ", ".join(dict.fromkeys(tags)),
                "fixes": [],
            }
        )
    return result
