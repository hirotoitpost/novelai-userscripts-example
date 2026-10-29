"""
取り込んだ漫画を、セリフごと台本に起こす(取り込みの使い方 rebuild)。自分の作品(権利のある作品)を、
このアプリで描き直すためのもの。

セリフの文面は読み取ったもの(画面で直したもの)を使う。整える(clean_page)ときは、作品の用語(work_terms)の
読み違いを直す。英語のページ(英訳の漫画)だけ、文章モデルに文字の読み違いを直させ、日本語に訳させ、セリフでない
文字(広告・クレジット・意味のないかけら)を外させる。日本語のページは文章モデルに書き換えさせない(読み取りは
十分正確で、書き換えさせると意味が変わったり言葉が和らげられたりした)。描き文字(効果音)は、画面で読みを入れたものを台本の効果音にする
(読みの無い候補は入れない)。話し手を決める(assign_page)ときに文章モデル(NovelAI の GLM)に決めさせるのは、
各セリフの話し手・声か心の声か・コマに描く人物・所作のタグを誰に付けるか、だけ。セリフの文面は返させない
(書き換えさせない)。
"""

from __future__ import annotations

import re
from typing import Any

from .manga_draft import DraftCharacter, _ask_json, _clean_tags

_MAX_ATTEMPTS = 3
_KANJI = re.compile(r"[\u3400-\u9fff]")
_JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
# 全角の数字(「100０」「１0００」のように半角と混ざって読める)
_WIDE_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
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


# 作品の用語(固有名詞・造語)の数と長さの上限、用語を探すときに渡すセリフの長さの上限
MAX_TERMS = 12
_TERM_CHARS = 16
_TERMS_INPUT_CHARS = 6000

_TERMS_SYSTEM = f"""漫画1作品分のセリフを渡します。絵から文字認識で読み取ったもので、読み違い(形の似た漢字への置き換え・字の抜け)が
混ざっています。作品の中で繰り返し出てくる固有名詞(人名・組織名・部署名など)と造語・用語を、前後の文脈から推し量った
正しい表記で挙げてください。JSONだけを返してください(前置きや```は不要):
{{"terms":["正しい表記"]}}
- 読み違いで表記がばらばらになっているものは、正しい1つにまとめる(例: 「魔法少女」「魔法小女」「麻法少女」→ 魔法少女)
- 1回しか出てこない、ありふれた言葉は入れない
- {MAX_TERMS}個まで。無ければ空の配列"""


def terms_prompt(pages: list[list[dict[str, Any]]]) -> str:
    texts = [text for panels in pages for panel in panels for text in _texts(panel)]
    return "\n".join(texts)[:_TERMS_INPUT_CHARS]


async def work_terms(api_key: str, pages: list[list[dict[str, Any]]]) -> list[str]:
    """作品の中で繰り返し出てくる固有名詞・用語の正しい表記(セリフを整えるときの手がかり)。分からなければ空。"""
    user = terms_prompt(pages)
    if not user:
        return []
    try:
        data = await _ask_json(api_key, _TERMS_SYSTEM, user, max_tokens=600, temperature=0.2)
    except (ValueError, RuntimeError):
        return []
    raw = data.get("terms") if isinstance(data, dict) else None
    terms = [str(t).strip()[:_TERM_CHARS] for t in raw if str(t).strip()] if isinstance(raw, list) else []
    return checked_terms(list(dict.fromkeys(terms)), user)[:MAX_TERMS]


def _near(window: str, term: str) -> bool:
    """同じ長さで、漢字1字だけ違うか(形の似た漢字への読み違い)。"""
    diffs = [i for i, (a, b) in enumerate(zip(window, term)) if a != b]
    return len(diffs) == 1 and bool(_KANJI.match(window[diffs[0]])) and bool(_KANJI.match(term[diffs[0]]))


def _appears(term: str, text: str) -> bool:
    """セリフに出てくるか(3字以上の用語は、漢字1字違いの読み違いも数える)。"""
    if term in text:
        return True
    return len(term) >= 3 and any(_near(text[i : i + len(term)], term) for i in range(len(text) - len(term) + 1))


def checked_terms(terms: list[str], text: str) -> list[str]:
    """
    文章モデルが挙げた用語のうち、セリフに出てくるもの。出てくる所がどれも、先に挙げた用語の読み違いの中に
    あるもの(「搾乳科」のあとの「捧乳」。「捧乳科」の中にしか出てこない)は外す。実機では、出てこない人名や、
    作った言葉も挙げた。
    """
    kept: list[str] = []
    for term in terms:
        if not _appears(term, text):
            continue
        spots = [i for i in range(len(text) - len(term) + 1) if text.startswith(term, i)]
        if spots and all(_inside_misreading(text, i, len(term), kept) for i in spots):
            continue
        kept.append(term)
    return kept


def _inside_misreading(text: str, start: int, length: int, terms: list[str]) -> bool:
    """text の start から length 字が、terms のどれかの読み違い(漢字1字違い)の中にあるか。"""
    for term in terms:
        for begin in range(max(0, start + length - len(term)), start + 1):
            window = text[begin : begin + len(term)]
            if len(window) == len(term) and _near(window, term):
                return True
    return False


def _misread(window: str, term: str) -> bool:
    """
    用語の読み違いとして直してよいか: 漢字1字違いで、違うのが最後の字ではない。最後の字だけ違うものは、別の
    言葉のことが多い(実機: 「搾乳科」に合わせて「搾乳機」「搾乳活動」「搾乳練習」まで「搾乳科」にした)。
    """
    return _near(window, term) and window[-1] == term[-1]


def fix_terms(text: str, terms: list[str]) -> str:
    """セリフの中の、作品の用語の読み違い(_misread。3字以上の用語)を直し、全角の数字を半角にする。"""
    text = text.translate(_WIDE_DIGITS)
    for term in terms:
        if len(term) < 3 or term in text:
            continue
        for i in range(len(text) - len(term) + 1):
            if _misread(text[i : i + len(term)], term):
                text = text[:i] + term + text[i + len(term) :]
    return text


def japanese_page(panels: list[dict[str, Any]]) -> bool:
    """日本語の漫画のページか(セリフの半分より多くに、かな・漢字がある)。"""
    texts = [text for panel in panels for text in _texts(panel)]
    return sum(1 for text in texts if _JAPANESE.search(text)) * 2 > len(texts)


def clean_prompts(panels: list[dict[str, Any]], terms: list[str] | None = None) -> tuple[str, str]:
    blocks = []
    for index, panel in enumerate(panels):
        texts = _texts(panel)
        rows = [f"{index + 1}コマ目:"]
        rows.extend(f"  セリフ{i + 1}: 「{text}」" for i, text in enumerate(texts))
        if not texts:
            rows.append("  セリフなし")
        blocks.append("\n".join(rows))
    system = _CLEAN_SYSTEM
    if terms:
        system += (
            "\n- この作品の用語(正しい表記): " + "、".join(terms)
            + "。セリフの中の、これらに似た読み違いは、この表記に直す"
        )
    return system, "\n".join(blocks)


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


async def clean_page(
    api_key: str, panels: list[dict[str, Any]], terms: list[str] | None = None
) -> list[dict[str, Any]]:
    """
    1ページ分のセリフを整える。terms は作品の用語の正しい表記(work_terms)。日本語のページは、用語の読み違いと
    全角の数字だけを直す(fix_terms)。英語のページは文章モデルで、読み違いを直し、日本語にし、セリフでない文字を
    外す(整えられなければ読み取ったまま)。
    """
    if not any(_texts(p) for p in panels):
        return panels
    if japanese_page(panels):
        return [
            {**panel, "lines": [{**line, "text": fix_terms(str(line.get("text") or ""), terms or [])} for line in panel.get("lines") or []]}
            for panel in panels
        ]
    system, user = clean_prompts(panels, terms)
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
