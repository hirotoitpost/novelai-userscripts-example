"""
取り込んだ漫画に似た漫画を作る: 取り込んだページの絵から舞台(場所・時間帯・小物)と人物の見た目を読み取り
(リバースプロンプトの判定モデル)、それを元に新しい話を作って、取り込んだ作品のコマ割りで漫画にする。

似せるのは構成(コマ割り・コマ運び)と、舞台・見た目の傾向まで。舞台のタグには判定モデルのキャラ名のタグを
使わない。ただし人物は、判定モデルがキャラ名(既存の作品のキャラ)を見分けたら、そのキャラとして作る
(名前とキャラ名のタグ。Person.character)。全年齢向けにするため、性的なタグ・体つきのタグも使わない。
R18 の取り込み(成人誌の漫画。adult)では、性的なタグ・体つきのタグも使う。未成年を思わせるタグはどちらでも使わず、
作る人物は全員成人(18 歳以上)にする。
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
# R18 の取り込みでは、体つきも人物の見た目に入れる(全年齢では similar.excluded_words で使わない)
_ADULT_LOOK_WORDS = {"breasts", "hips", "thighs", "ass", "abs", "muscular", "curvy", "plump", "toned", "cleavage", "navel"}
_COLOURS = "black|brown|light brown|dark brown|blonde|red|blue|green|pink|purple|white|grey|silver|orange|aqua|multicolored"
_HAIR_COLOUR = re.compile(rf"^({_COLOURS}) hair$")
# 色のタグ(髪・目)。一人の見た目には、それぞれ一番多いものだけを入れる(白黒の作品では色を読み違える)
_COLOUR_TAG = re.compile(rf"^({_COLOURS}) (hair|eyes)$")
# 人物は多い順に、この人数まで(台本の人数と、参照画像の生成を増やしすぎないため)
MAX_PEOPLE = 2
# 人物の見た目に入れるタグ: その人物と見なした絵のうち、この割合以上に出てきたもの
_LOOK_SHARE = 0.4
# 白黒の作品では、見た目のタグが出にくい(体の寄りのコマが多く、色も読めない。実機: 25コマの女性で最多の
# ponytail が7コマ)。割合を下げ、2コマ以上に出たものにする
_LOOK_SHARE_MONOCHROME = 0.2
# 判定モデルがキャラ名を見分けたコマが、これ以上あればそのキャラとして扱う(1コマだけの見分け違いを除く)
_MIN_CHARACTER_PANELS = 2
# cluster_people のまとまりのキーで、キャラ名でまとめたもの(髪の色のタグと混ざらない印)
_CHARACTER_KEY = "@"
# 舞台のタグ: 確率がこれ以上のものを、ページをまたいで数える
_SETTING_PROB = 0.45
_MAX_SETTING_TAGS = 14


def _words(tag: str) -> set[str]:
    return set(tag.replace("(", " ").replace(")", " ").split())


def _is_minor(tag: str) -> bool:
    """
    未成年を思わせるタグか(性的な場面から除くタグと、成人キャラに付けられないタグ。中身は content_guard)。
    R18 の取り込みで作る人物は全員成人なので、成人の衣装(制服のコスプレ など)は除かない。
    """
    minor = content_guard.search("scene.minor_tags", tag) or content_guard.search("dataset.minor_tags", tag)
    return minor and not content_guard.is_costume(tag)


def _is_safe(tag: str, adult: bool = False) -> bool:
    """
    人物の見た目・舞台に使ってよいタグか。全年齢では体つき・露出の語と性的なタグを使わない。R18(adult)では
    それらも使い、未成年を思わせるタグだけを使わない(中身は content_guard)。
    """
    if adult:
        return not _is_minor(tag)
    return not (_words(tag) & content_guard.words("similar.excluded_words")) and not content_guard.is_sexual(tag)


@dataclass
class Person:
    gender: str
    tags: list[str]
    # この人物と見なした絵の数(多い順に選ぶ)
    count: int = 0
    # 判定モデルが見分けた既存の作品のキャラ(キャラ名のタグ。hatsune miku など)。見分けられなければ空
    character: str = ""


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


def _panel_people(panel: Image.Image, adult: bool = False) -> list[tuple[str, list[str], list[str]]]:
    """
    コマ1つに写っている人物の(性別, 見た目のタグ, キャラ名のタグ)。一人ならコマ全体のタグがその人の見た目。
    二人以上なら頭の位置で人物ごとに分ける(split_characters)。漫画のページは同じ人物が何コマにも出るので、
    ページ全体で人物を分けると、どの切り抜きにも同じタグが出て誰のものか決まらない(実機で見た目が空になった)。
    """
    from .image_tagger import CHARACTER_THRESHOLD, split_characters, tag_image

    people = split_characters(panel)
    if people:
        return [(p.gender, [t for t in p.tags if _is_look(t, adult)], p.characters) for p in people]
    scores = tag_image(panel)
    probs = {s.tag: s.probability for s in scores if s.category == "general"}
    genders = [g for g in ("1girl", "1boy") if probs.get(g, 0.0) >= _LOOK_PROB]
    if len(genders) != 1 or any(
        probs.get(t, 0.0) >= _LOOK_PROB for t in ("multiple girls", "multiple boys", "2girls", "2boys")
    ):
        return []
    names = [s.tag for s in scores if s.category == "character" and s.probability >= CHARACTER_THRESHOLD]
    return [(genders[0], [t for t, p in probs.items() if p >= _LOOK_PROB and _is_look(t, adult)], names)]


def read_pages(
    images: list[Image.Image],
    panels: list[list[tuple[int, int, int, int]]] | None = None,
    *,
    adult: bool = False,
    max_people: int = MAX_PEOPLE,
) -> ImportFeatures:
    """
    取り込んだページの絵から、舞台のタグ・人物の見た目・白黒かどうかを読み取る。panels はページごとの
    コマの位置 (x, y, 幅, 高さ)。あればコマごとに人物を読む(無ければページ全体で)。adult は R18 の取り込み(_is_safe)。
    人物は多い順に max_people 人まで(cluster_people)。
    """
    from .image_tagger import colour_profile, split_characters, tag_image

    setting = Counter()
    guesses: list[tuple[str, list[str], list[str]]] = []
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
                and _is_safe(s.tag, adult)
            ):
                setting[s.tag] += 1
        grey, _ = colour_profile(image)
        grey_pages += grey >= 0.8
        boxes = panels[len(guesses_pages)] if panels and len(guesses_pages) < len(panels) else []
        guesses_pages.append(1)
        if boxes:
            for x, y, w, h in boxes:
                guesses.extend(_panel_people(image.crop((x, y, x + w, y + h)), adult))
        else:
            for person in split_characters(image):
                guesses.append((person.gender, [t for t in person.tags if _is_look(t, adult)], person.characters))
    return ImportFeatures(
        setting=[tag for tag, _ in setting.most_common(_MAX_SETTING_TAGS)],
        people=cluster_people(guesses, monochrome=grey_pages * 2 >= max(len(images), 1), max_people=max_people),
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


def _is_look(tag: str, adult: bool = False) -> bool:
    words = _LOOK_WORDS | _ADULT_LOOK_WORDS if adult else _LOOK_WORDS
    return bool(_words(tag) & words) and _is_safe(tag, adult)


def cluster_people(
    guesses: list[tuple[str, list[str]] | tuple[str, list[str], list[str]]],
    *,
    monochrome: bool = False,
    max_people: int = MAX_PEOPLE,
) -> list[Person]:
    """
    ページごとに見つけた人物を、性別と髪の色で同じ人物にまとめ、多い順に max_people 人まで返す。
    両方の性別が出てくるなら、まず性別ごとに一番多い人物を選ぶ(女性が髪の色で2人に分かれ、男性が抜けた)。
    白黒の作品(monochrome)では髪の色で分けない(同じ人物が、コマによって金髪にも黒髪にも読まれた)。
    髪・目の色のタグは、それぞれ一番多いものだけを見た目に入れる。白黒の作品では、見た目に入れる割合を下げる。
    見た目のタグは、その人物の絵の _LOOK_SHARE 以上に出てきたもの。
    判定モデルがキャラ名(既存の作品のキャラ)を見分けた絵は、髪の色より先に、そのキャラごとにまとめる。
    見分け違いの1コマで人物が増えないよう、_MIN_CHARACTER_PANELS コマ以上で見分けたキャラだけ。
    """
    names = Counter(name for guess in guesses for name in dict.fromkeys(guess[2] if len(guess) > 2 else []))
    known = {name for name, n in names.items() if n >= _MIN_CHARACTER_PANELS}
    groups: dict[tuple[str, str], list[list[str]]] = {}
    for guess in guesses:
        gender, tags = guess[0], guess[1]
        character = next((n for n in (guess[2] if len(guess) > 2 else []) if n in known), "")
        hair = "" if monochrome else next((t for t in tags if _HAIR_COLOUR.match(t)), "")
        groups.setdefault((gender, _CHARACTER_KEY + character if character else hair), []).append(tags)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    chosen = [next(g for g in ranked if g[0][0] == gender) for gender in dict.fromkeys(k[0] for k, _ in ranked)]
    chosen = chosen[:max_people]
    chosen += [g for g in ranked if g not in chosen][: max_people - len(chosen)]
    chosen.sort(key=lambda kv: -len(kv[1]))
    people = []
    for (gender, key), members in chosen:
        counts = Counter(t for tags in members for t in set(tags))
        least = (
            max(2, math.ceil(len(members) * _LOOK_SHARE_MONOCHROME))
            if monochrome
            else max(1, math.ceil(len(members) * _LOOK_SHARE))
        )
        tags = [t for t, n in counts.most_common() if n >= least]
        # 髪・目の色は、それぞれ一番多いもの(most_common の順)だけ
        seen: set[str] = set()
        kept = []
        for tag in tags:
            colour = _COLOUR_TAG.match(tag)
            if colour and colour.group(2) in seen:
                continue
            if colour:
                seen.add(colour.group(2))
            kept.append(tag)
        character = key[len(_CHARACTER_KEY):] if key.startswith(_CHARACTER_KEY) else ""
        people.append(Person(gender, kept, len(members), character))
    return people


_NAMING_SYSTEM = """あなたは漫画のキャラクター設定を作る編集者です。渡された見た目(英語のdanbooruタグ)の人物それぞれに、
日本語の名前(姓と名)と、話を作るための短い人物像を考えてください。JSONだけを返してください(前置きや```は不要):
{"characters":[{"name":"姓 名","age":年齢(数字),"profile":"人物像(年齢・職業や立場・性格を40文字以内)","look":"足す見た目の英語タグ"}]}
- characters は渡された人物と同じ数・同じ順
{name_source}{naming_rule}- look: 髪の色・髪型・目の色のタグが無い人物には、ほかの人物と見分けやすい髪の色と髪型(と目の色)を英語のdanbooruタグで
  足す(例: "brown hair, short hair, green eyes")。渡されたタグとかぶるもの・矛盾するものは書かない。足りていれば空文字"""


# R18 の取り込みで作る人物の年齢の下限
ADULT_AGE = 18


# 名前の付け方。新しく作る(似た漫画)か、作品のセリフから名前を拾う(自分の作品の作り直し)か
_NEW_NAMES = "- キャラ名の無い人物には、実在の人物や、既存の作品のキャラクターの名前は使わない\n"
# 判定モデルがキャラ名(既存の作品のキャラ)を見分けた人物
_KNOWN_CHARACTERS = """- 「キャラ名」がある人物は、その既存の作品のキャラクター。name にはそのキャラの名前を日本語の表記で入れ
  (例: hatsune miku → 初音ミク)、age・profile もそのキャラに合わせる。分からなければ英語のキャラ名を読みやすく書く
"""
_NAMES_FROM_LINES = """- 渡すセリフは、この作品(利用者自身の作品)のもの。セリフの呼びかけ・名乗り・呼び名(「藤堂さん」「楓」など)から
  その人物の名前が分かれば、その名前を使う。性別・立場・話し方の手がかりで、どの人物の名前か決める
- 姓だけ・名だけが分かったときは、残りを作品に合うように考える。名前が分からない人物は、作品に合う名前を考える
- 同じ名前を二人に付けない。実在の人物の名前は使わない
"""
# 名前の手がかりとして渡すセリフの長さ(全部で)
NAMING_LINES_CHARS = 3000


def _naming_system(adult: bool = False, from_lines: bool = False, known: bool = False) -> str:
    """
    人物づくりの指示文。内容についての決まりは content_guard(DB)にある(空なら足さない)。R18 は成人の決まり。
    from_lines: 作品のセリフから名前を拾う(自分の作品)。known: キャラ名を見分けた人物がいる(その名前を使う)。
    """
    rule = content_guard.text("similar.adult_naming_rule" if adult else "similar.naming_rule")
    source = (_NAMES_FROM_LINES if from_lines else _NEW_NAMES) + (_KNOWN_CHARACTERS if known else "")
    return _NAMING_SYSTEM.replace("{name_source}", source).replace(
        "{naming_rule}", f"- {rule}\n" if rule else ""
    )


def _quoted_lines(lines: list[str]) -> str:
    """名前の手がかりに渡すセリフ。名前の入っていそうな行(敬称・呼びかけ)を先に、全部で NAMING_LINES_CHARS まで。"""
    texts = list(dict.fromkeys(t.strip() for t in lines if t.strip()))
    named = re.compile(r"さん|くん|君|ちゃん|様|先生|先輩|部長|課長|社長|\b(?:mr|mrs|ms|miss)\b", re.IGNORECASE)
    texts.sort(key=lambda t: not named.search(t))
    out, total = [], 0
    for text in texts:
        if total + len(text) > NAMING_LINES_CHARS:
            break
        out.append(f"「{text}」")
        total += len(text)
    return "\n".join(out)


def _age(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def name_people(
    api_key: str,
    people: list[Person],
    genre: str,
    setting: list[str],
    *,
    adult: bool = False,
    lines: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    見た目から、名前・人物像・年齢と、足りない見た目(髪の色・髪型)を作る(NovelAI の文章モデル)。
    白黒の漫画からは髪の色が読めないので、そのままだと人物どうしが似た見た目になる(実機で確認)。
    R18 の取り込み(adult)では、全員が 18 歳以上でなければ作り直させる(3回で作れなければ止める)。
    lines(自分の作品のセリフ)を渡すと、セリフの呼びかけ・名乗りから名前を拾い、分からなければ考える。
    判定モデルがキャラ名を見分けた人物(Person.character)は、そのキャラの名前(日本語の表記)にする。
    """
    from .manga_draft import _ask_json

    rows = [
        f"{i + 1}. {p.gender}, {', '.join(p.tags) or '(見た目のタグなし)'}" + (f"(キャラ名: {p.character})" if p.character else "")
        for i, p in enumerate(people)
    ]
    user = f"ジャンル: {genre}\n舞台のタグ: {', '.join(setting) or '(なし)'}\n人物:\n" + "\n".join(rows)
    quoted = _quoted_lines(lines or [])
    if quoted:
        user += f"\n作品のセリフ(名前の手がかり):\n{quoted}"
    system = _naming_system(adult, from_lines=bool(quoted), known=any(p.character for p in people))
    for _ in range(3):
        data = await _ask_json(api_key, system, user, max_tokens=800, temperature=0.8)
        items = (data or {}).get("characters") if isinstance(data, dict) else None
        if isinstance(items, list) and len(items) >= len(people):
            named = []
            for item in items[: len(people)]:
                name = str((item or {}).get("name") or "").strip()
                if not name:
                    break
                age = _age(item.get("age"))
                if adult and (age is None or age < ADULT_AGE):
                    break
                look = [t.strip() for t in str(item.get("look") or "").split(",") if t.strip()]
                named.append(
                    {
                        "name": name[:20],
                        "age": age,
                        "profile": str(item.get("profile") or "").strip()[:80],
                        # 白黒の漫画からは髪の色が読めず、人物が似てしまうので、見分けやすい見た目を足す
                        "look": ", ".join(t for t in look[:4] if _is_look(t, adult)),
                    }
                )
            if len(named) == len(people):
                return named
    raise RuntimeError("登場人物の名前を作れませんでした。もう一度試してください。")


def apply_import_panels(
    script: list[dict[str, Any]], imported: list[dict[str, Any]], *, adult: bool = False
) -> list[dict[str, Any]]:
    """
    台本の1話分に、取り込んだページのコマの場面・所作を写す(文章モデルが入れ忘れても再現されるように)。
    - 場面のタグ(舞台・小物・構図)を prompt_tags の先頭に足す
    - 所作のタグが台本のどの人物にも付いていなければ、描く人物が1人のコマではその人に付ける
    - セリフの無かったコマは、セリフを空にする
    場面・所作を読んでいない取り込み(detailed でないコマ)には何もしない。adult は R18 の取り込み(_is_safe)。
    """
    from .manga_draft import _clean_tags

    for panel, source in zip(script, imported):
        if not source.get("detailed"):
            continue
        scene_tags, action_tags = source.get("scene_tags") or [], source.get("action_tags") or []
        # R18 の取り込みで未成年を思わせるタグを外すのは、性的なコマだけ(日常のコマの赤ちゃん・子どもは残す)
        sexual = adult and content_guard.is_sexual(", ".join([*scene_tags, *action_tags]))

        def safe(tag: str) -> bool:
            if not adult:
                return _is_safe(tag)
            return not (sexual and _is_minor(tag))

        scene = [t for t in scene_tags if safe(t)]
        if scene:
            panel["prompt_tags"] = _clean_tags(", ".join([*scene, panel.get("prompt_tags") or ""]))
        action = [t for t in action_tags if safe(t)]
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
