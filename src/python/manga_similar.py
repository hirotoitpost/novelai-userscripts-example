"""
取り込んだ漫画に似た漫画を作る: 取り込んだページの絵から舞台(場所・時間帯・小物)と人物の見た目を読み取り
(リバースプロンプトの判定モデル)、それを元に新しい話を作って、取り込んだ作品のコマ割りで漫画にする。

似せるのは構成(コマ割り・コマ運び)と、舞台・見た目の傾向まで。作品のキャラそのものは写さない
(判定モデルのキャラ名のタグは使わない)。全年齢向けにするため、性的なタグ・体つきのタグも使わない。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from . import content_guard

# 舞台として使わないタグ(絵柄・構図・人数・画面の作り)
_NOT_SETTING = {
    "comic",
    "monochrome",
    "greyscale",
    "spot color",
    "speech bubble",
    "translated",
    "border",
    "multiple views",
    "4koma",
    "manga",
    "white background",
    "simple background",
    "blurry",
    "blurry background",
    "depth of field",
    "solo",
    "solo focus",
    "male focus",
    "female focus",
    "no humans",
    "commentary request",
    "highres",
    "text focus",
    "english text",
    "japanese text",
    "sound effects",
    "onomatopoeia",
    "emphasis lines",
    "motion lines",
    "close-up",
    "upper body",
    "cowboy shot",
    "full body",
    "portrait",
    "from side",
    "from above",
    "from below",
    "from behind",
    "profile",
}
# 人物の見た目として使う語(髪・目・服・小物)。表情やしぐさは場面ごとに変わるので使わない
_LOOK_WORDS = {
    "hair",
    "bangs",
    "sidelocks",
    "ahoge",
    "ponytail",
    "twintails",
    "braid",
    "bun",
    "bob",
    "eyes",
    "glasses",
    "eyewear",
    "shirt",
    "jacket",
    "coat",
    "dress",
    "skirt",
    "pants",
    "shorts",
    "kimono",
    "apron",
    "sweater",
    "hoodie",
    "cardigan",
    "vest",
    "suit",
    "necktie",
    "bowtie",
    "uniform",
    "hat",
    "beret",
    "cap",
    "ribbon",
    "hairclip",
    "hairband",
    "headband",
    "headphones",
    "scarf",
    "earrings",
    "choker",
    "blazer",
    "sailor",
    "serafuku",
    "gakuran",
    "collar",
    "sleeves",
    "hood",
    "mole",
    "freckles",
    "beard",
    "mustache",
}
_HAIR_COLOUR = re.compile(r"^(\w+) hair$")
# 人物は多い順に、この人数まで(台本の人数と、参照画像の生成を増やしすぎないため)
MAX_PEOPLE = 2
# 人物の見た目に入れるタグ: その人物と見なした絵のうち、この割合以上に出てきたもの
_LOOK_SHARE = 0.4
# 舞台のタグ: 確率がこれ以上のものを、ページをまたいで数える
_SETTING_PROB = 0.45
_MAX_SETTING_TAGS = 14


def _words(tag: str) -> set[str]:
    return set(tag.replace("(", " ").replace(")", " ").split())


def _is_safe(tag: str) -> bool:
    """人物の見た目・舞台に使ってよいタグか(体つき・露出の語と性的なタグは使わない。中身は content_guard)。"""
    return not (_words(tag) & content_guard.words("similar.excluded_words")) and not content_guard.is_sexual(tag)


@dataclass
class Person:
    gender: str
    tags: list[str]
    # この人物と見なした絵の数(多い順に選ぶ)
    count: int = 0


@dataclass
class ImportFeatures:
    setting: list[str]
    people: list[Person]
    # 取り込んだ作品が白黒か(白黒なら白黒で、カラーならカラーで作る)
    monochrome: bool
    pages: int = 0
    rating_flags: list[str] = field(default_factory=list)


# 一人の人物の見た目とみなすタグの確率
_LOOK_PROB = 0.5


def _panel_people(panel: Image.Image) -> list[tuple[str, list[str]]]:
    """
    コマ1つに写っている人物の(性別, 見た目のタグ)。一人ならコマ全体のタグがその人の見た目。
    二人以上なら頭の位置で人物ごとに分ける(split_characters)。漫画のページは同じ人物が何コマにも出るので、
    ページ全体で人物を分けると、どの切り抜きにも同じタグが出て誰のものか決まらない(実機で見た目が空になった)。
    """
    from .image_tagger import split_characters, tag_image

    people = split_characters(panel)
    if people:
        return [(p.gender, [t for t in p.tags if _is_look(t)]) for p in people]
    probs = {s.tag: s.probability for s in tag_image(panel) if s.category == "general"}
    genders = [g for g in ("1girl", "1boy") if probs.get(g, 0.0) >= _LOOK_PROB]
    if len(genders) != 1 or any(
        probs.get(t, 0.0) >= _LOOK_PROB for t in ("multiple girls", "multiple boys", "2girls", "2boys")
    ):
        return []
    return [(genders[0], [t for t, p in probs.items() if p >= _LOOK_PROB and _is_look(t)])]


def read_pages(
    images: list[Image.Image], panels: list[list[tuple[int, int, int, int]]] | None = None
) -> ImportFeatures:
    """
    取り込んだページの絵から、舞台のタグ・人物の見た目・白黒かどうかを読み取る。panels はページごとの
    コマの位置 (x, y, 幅, 高さ)。あればコマごとに人物を読む(無ければページ全体で)。
    """
    from .image_tagger import colour_profile, split_characters, tag_image

    setting = Counter()
    guesses: list[tuple[str, list[str]]] = []
    guesses_pages: list[int] = []
    grey_pages = 0
    for image in images:
        scores = tag_image(image)
        for s in scores:
            if (
                s.category == "general"
                and s.probability >= _SETTING_PROB
                and s.tag not in _NOT_SETTING
                and not _is_person_or_look(s.tag)
                and _is_safe(s.tag)
            ):
                setting[s.tag] += 1
        grey, _ = colour_profile(image)
        grey_pages += grey >= 0.8
        boxes = panels[len(guesses_pages)] if panels and len(guesses_pages) < len(panels) else []
        guesses_pages.append(1)
        if boxes:
            for x, y, w, h in boxes:
                guesses.extend(_panel_people(image.crop((x, y, x + w, y + h))))
        else:
            for person in split_characters(image):
                guesses.append((person.gender, [t for t in person.tags if _is_look(t)]))
    return ImportFeatures(
        setting=[tag for tag, _ in setting.most_common(_MAX_SETTING_TAGS)],
        people=cluster_people(guesses),
        monochrome=grey_pages * 2 >= max(len(images), 1),
        pages=len(images),
    )


# 人物の様子を表すタグ(舞台ではない)
_PERSON_STATE = {"drooling", "saliva", "blush", "tears", "crying", "sweat", "sweatdrop", "food on face", "heart"}


def _is_person_or_look(tag: str) -> bool:
    """人数・人物の容姿や表情のタグか(舞台のタグには入れない)。"""
    from .image_tagger import _COUNT_TAGS, _is_person_tag

    return (
        tag in _COUNT_TAGS
        or tag in _PERSON_STATE
        or tag in ("multiple girls", "multiple boys", "multiple others")
        or bool(re.match(r"^\d\+?(girls?|boys?|others?)$", tag))
        or _is_person_tag(tag)
    )


def _is_look(tag: str) -> bool:
    return bool(_words(tag) & _LOOK_WORDS) and _is_safe(tag)


def cluster_people(guesses: list[tuple[str, list[str]]]) -> list[Person]:
    """
    ページごとに見つけた人物を、性別と髪の色で同じ人物にまとめ、多い順に MAX_PEOPLE 人まで返す。
    見た目のタグは、その人物の絵の _LOOK_SHARE 以上に出てきたもの。
    """
    groups: dict[tuple[str, str], list[list[str]]] = {}
    for gender, tags in guesses:
        hair = next((t for t in tags if _HAIR_COLOUR.match(t)), "")
        groups.setdefault((gender, hair), []).append(tags)
    people = []
    for (gender, _), members in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:MAX_PEOPLE]:
        counts = Counter(t for tags in members for t in set(tags))
        tags = [t for t, n in counts.most_common() if n >= max(1, math.ceil(len(members) * _LOOK_SHARE))]
        people.append(Person(gender, tags, len(members)))
    return people


_NAMING_SYSTEM = """あなたは漫画のキャラクター設定を作る編集者です。渡された見た目(英語のdanbooruタグ)の人物それぞれに、
日本語の名前(姓と名)と、話を作るための短い人物像を考えてください。JSONだけを返してください(前置きや```は不要):
{"characters":[{"name":"姓 名","profile":"人物像(年齢・職業や立場・性格を40文字以内)","look":"足す見た目の英語タグ"}]}
- characters は渡された人物と同じ数・同じ順
- 実在の人物や、既存の作品のキャラクターの名前は使わない
{naming_rule}- look: 髪の色・髪型・目の色のタグが無い人物には、ほかの人物と見分けやすい髪の色と髪型(と目の色)を英語のdanbooruタグで
  足す(例: "brown hair, short hair, green eyes")。渡されたタグとかぶるもの・矛盾するものは書かない。足りていれば空文字"""


def _naming_system() -> str:
    """人物づくりの指示文。内容についての決まりは content_guard(DB)にある(空なら足さない)。"""
    rule = content_guard.text("similar.naming_rule")
    return _NAMING_SYSTEM.replace("{naming_rule}", f"- {rule}\n" if rule else "")


async def name_people(api_key: str, people: list[Person], genre: str, setting: list[str]) -> list[dict[str, str]]:
    """
    見た目から、名前・人物像と、足りない見た目(髪の色・髪型)を作る(NovelAI の文章モデル)。
    白黒の漫画からは髪の色が読めないので、そのままだと人物どうしが似た見た目になる(実機で確認)。
    """
    from .manga_draft import _ask_json

    lines = [f"{i + 1}. {p.gender}, {', '.join(p.tags) or '(見た目のタグなし)'}" for i, p in enumerate(people)]
    user = f"ジャンル: {genre}\n舞台のタグ: {', '.join(setting) or '(なし)'}\n人物:\n" + "\n".join(lines)
    for _ in range(3):
        data = await _ask_json(api_key, _naming_system(), user, max_tokens=800, temperature=0.8)
        items = (data or {}).get("characters") if isinstance(data, dict) else None
        if isinstance(items, list) and len(items) >= len(people):
            named = []
            for item in items[: len(people)]:
                name = str((item or {}).get("name") or "").strip()
                if not name:
                    break
                look = [t.strip() for t in str(item.get("look") or "").split(",") if t.strip()]
                named.append(
                    {
                        "name": name[:20],
                        "profile": str(item.get("profile") or "").strip()[:80],
                        # 白黒の漫画からは髪の色が読めず、人物が似てしまうので、見分けやすい見た目を足す
                        "look": ", ".join(t for t in look[:4] if _is_look(t)),
                    }
                )
            if len(named) == len(people):
                return named
    raise RuntimeError("登場人物の名前を作れませんでした。もう一度試してください。")


def apply_import_panels(script: list[dict[str, Any]], imported: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    台本の1話分に、取り込んだページのコマの場面・所作を写す(文章モデルが入れ忘れても再現されるように)。
    - 場面のタグ(舞台・小物・構図)を prompt_tags の先頭に足す
    - 所作のタグが台本のどの人物にも付いていなければ、描く人物が1人のコマではその人に付ける
    - セリフの無かったコマは、セリフを空にする
    場面・所作を読んでいない取り込み(detailed でないコマ)には何もしない。
    """
    from .manga_draft import _clean_tags

    for panel, source in zip(script, imported):
        if not source.get("detailed"):
            continue
        scene = [t for t in source.get("scene_tags") or [] if _is_safe(t)]
        if scene:
            panel["prompt_tags"] = _clean_tags(", ".join([*scene, panel.get("prompt_tags") or ""]))
        action = [t for t in source.get("action_tags") or [] if _is_safe(t)]
        drawn = panel.get("characters") or []
        used = {t.lower() for tags in (panel.get("actions") or {}).values() for t in tags.split(", ")}
        missing = [t for t in action if t.lower() not in used]
        if missing and len(drawn) == 1:
            actions = dict(panel.get("actions") or {})
            actions[drawn[0]] = _clean_tags(", ".join([actions.get(drawn[0], ""), *missing]))
            panel["actions"] = actions
        if not source.get("lines"):
            panel["lines"] = []
    return script


def wordless(pages: list[list[dict[str, Any]]]) -> bool:
    """セリフを読んだうえで、どのコマにもセリフが無かったか(セリフのない漫画)。"""
    panels = [p for page in pages for p in page]
    return bool(panels) and all(p.get("detailed") for p in panels) and not any(p.get("lines") for p in panels)


def theme_of(features: ImportFeatures) -> str:
    """大枠シナリオのテーマ。舞台のタグを渡して、同じ舞台・雰囲気の新しい話にする。"""
    setting = ", ".join(features.setting) or "(読み取れませんでした)"
    return f"取り込んだ漫画と同じ舞台・雰囲気の、新しい話。舞台のタグ(英語): {setting}"[:480]
