"""キャラシート: 自作キャラを同じ見た目で安定して出すための設定と、その組み立て。

キャラ(characters テーブル)に、変えない容姿タグ・普段の服装・画風・ネガティブ・基準シード・
参照画像を持たせる。物語の挿絵/漫画v2のコマ生成と、キャラ別データセット生成の両方が
ここを通してプロンプトを作るので、どちらでも同じキャラとして出る。

データセットのバリエーション(ポーズ/服装/表情/場所)は既定で全年齢向けに限る。
水着・下着・裸などは選択肢に入れず、手入力されても弾く。
R18 は成人フラグ(is_adult)の付いたキャラだけで使え、その場合は性的なタグの代わりに
未成年を示すタグを弾く。弾くタグ・足すネガティブの中身は DB にある(content_guard.py)。
"""

from __future__ import annotations

import itertools
import random
import re
from dataclasses import dataclass
from typing import Any

from . import content_guard

# --- バリエーションの既定リスト(UIのチェックボックスの初期値) ---

# 構図。参照画像を強く効かせると元画像の構図(上半身・正面)に寄るため、明示して散らす。
FRAMINGS = [
    "full body", "upper body", "cowboy shot", "portrait", "close-up",
    # 横顔は from side だけだと無視されやすく、profile と組み合わせると効きやすい
    "from side, profile", "from above", "from below", "dutch angle",
]

POSES = [
    "standing", "sitting on chair", "walking", "arms behind back", "hand on own chest",
    "waving", "peace sign", "leaning forward", "looking back", "arms crossed",
    "holding bag", "reading book", "stretching", "hand on own cheek", "running",
]
OUTFITS = [
    "school uniform, cardigan",
    "school uniform, summer uniform, short sleeves",
    "school uniform, blazer",
    "school uniform, sweater vest",
    "gym uniform, track jacket",
    "hoodie, jeans",
    "sundress",
    "casual, blouse, long skirt",
    "yukata",
    "pajamas",
    "winter coat, scarf",
]
EXPRESSIONS = [
    "smile", "smirk", "expressionless", "half-closed eyes", "blush", "surprised",
    "pout", "laughing", "sad", "angry", "sleepy", "embarrassed",
]
LOCATIONS = [
    "classroom", "school hallway", "school rooftop", "library", "street", "park",
    "cafe", "bedroom", "train interior", "shrine", "simple background, white background",
]

DEFAULT_NEGATIVE = (
    "lowres, worst quality, low quality, blurry, bad anatomy, bad hands, "
    "extra fingers, missing fingers, text, watermark"
)


def split_tags(tags: str | None) -> list[str]:
    return [t.strip() for t in (tags or "").split(",") if t.strip()]


def join_tags(*parts: str | None) -> str:
    """カンマ区切りタグを順に連結し、重複を除く(先に出た方を残す)。"""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for tag in split_tags(part):
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                out.append(tag)
    return ", ".join(out)


# --- 全年齢 / R18(成人キャラ限定) ---
# 全年齢では性的なタグを、R18 では代わりに未成年を示すタグを弾く。弾くタグと、足すタグ・ネガティブの中身は
# content_guard(DB)にある。成人フラグの保存時にもキャラシートを検査する。


def safe_negative() -> str:
    """全年齢で常に付けるネガティブ。"""
    return content_guard.text("general.safe_negative")


def r18_positive() -> str:
    """R18 生成で常に付けるタグ(成人として描かせるため)。"""
    return content_guard.text("dataset.r18_positive")


def r18_negative() -> str:
    return content_guard.text("dataset.r18_negative")


def minor_tags(tags: str) -> list[str]:
    return content_guard.find("dataset.minor_tags", tags)


def character_minor_tags(character: dict[str, Any]) -> list[str]:
    """キャラシートの中で未成年を示すタグ(成人フラグを付けられるかの判定に使う)。"""
    text = ", ".join(
        str(character.get(k) or "")
        for k in ("name", "trigger_word", "appearance_tags", "body_tags", "outfit_tags", "style_tags")
    )
    return minor_tags(text)


def core_blocked_tags() -> list[str]:
    """基本のガード(全年齢で止めるタグ)の表示用の一覧。ガードプロファイルはこれへの上乗せ。"""
    return [p.replace("?", "") for p in content_guard.get("dataset.blocked_tags")]


def blocked_tags(tags: str, extra: list[str] | None = None, *, include_core: bool = True) -> list[str]:
    """
    基本のブロックリストと、ガードプロファイルで追加したタグ(extra)に当たるものを返す。
    extra は正規表現ではなくタグそのものとして扱う(記号入りのタグでも誤動作しないように)。
    include_core=False は R18(成人キャラ)用で、代わりに minor_tags() で未成年を弾く。
    """
    found = set(content_guard.find("dataset.blocked_tags", tags)) if include_core else set()
    words = [re.escape(t.strip()) for t in extra or [] if t.strip()]
    if words:
        extra_re = re.compile(r"(?<![\w-])(" + "|".join(words) + r")(?![\w-])", re.IGNORECASE)
        found |= {m.group(0).lower() for m in extra_re.finditer(tags)}
    return sorted(found)


# 画風・品質のタグ(キャラシートの「画風」に入れる)
_STYLE_PHRASES = {
    "masterpiece", "best quality", "very aesthetic", "amazing quality", "high quality", "absurdres", "highres",
    "no text", "official art", "anime coloring", "anime screencap", "game cg", "illustration", "realistic",
    "photorealistic", "3d", "pixel art", "chibi", "flat color", "sketch", "lineart", "monochrome", "greyscale",
    "watercolor (medium)", "traditional media", "screentone", "halftone", "sepia", "limited palette", "spot color",
}  # fmt: skip
_STYLE_RE = re.compile(r"(^artist:|\bstyle$|^year \d{4}$|\bcomplexity$|\bquality$)")
# 人物を表す語(キャラごとのプロンプトの先頭に書く girl / boy など)と、人数のタグ
_PERSON_NOUNS = {"girl", "boy", "woman", "man", "female", "male", "other"}
_COUNT_RE = re.compile(r"^\d\+?(girls?|boys?|others?)$")
# 強調の記号と重み(1.2::tag::、{tag}、[tag]、-tag)を、見分けるときだけ外す
_WEIGHT_RE = re.compile(r"^-?\d+(\.\d+)?::|::$")


def _plain(tag: str) -> str:
    """見分けるための形(強調の記号・重みを外し、小文字に、_ を空白に)。"""
    text = tag.strip().lower().replace("_", " ")
    previous = None
    while previous != text:
        previous = text
        text = _WEIGHT_RE.sub("", text).strip().strip("{}[]").strip()
    return text


def split_prompt(prompt: str) -> dict[str, str]:
    """
    プロンプトを、キャラシートの項目に分ける: appearance(容姿: 人数・髪・目・体)、outfit(服装: 服・小物)、
    style(画風: 品質・画風のタグ)、other(それ以外: 表情・ポーズ・場所など。場面ごとに変わるので、キャラシート
    には入れない)。タグの文字はそのまま残す(強調の記号や重みも)。語の一覧に無いタグは other になるので、
    画面で確かめて直してもらう前提。
    """
    from .image_tagger import BODY_WORDS, CLOTHES_WORDS, STYLE_TAGS

    parts: dict[str, list[str]] = {"appearance": [], "outfit": [], "style": [], "other": []}
    for tag in split_tags(prompt.replace("\n", ",")):
        plain = _plain(tag)
        words = set(plain.replace("(", " ").replace(")", " ").split())
        if not plain:
            continue
        if plain in _STYLE_PHRASES or plain in STYLE_TAGS or _STYLE_RE.search(plain):
            key = "style"
        elif _COUNT_RE.match(plain) or plain in _PERSON_NOUNS:
            key = "appearance"
        elif words & CLOTHES_WORDS:
            # 「hair ribbon」「hair bow」のように髪の語が入っていても、身に着けるものは服装
            key = "outfit"
        elif words & BODY_WORDS:
            key = "appearance"
        else:
            key = "other"
        parts[key].append(tag)
    return {key: join_tags(", ".join(tags)) for key, tags in parts.items()}


def character_prompt_tags(character: dict[str, Any]) -> str:
    """挿絵/コマ生成で characterPrompts に渡すタグ。容姿+普段の服装(画風は混ぜない)。"""
    return join_tags(character.get("appearance_tags"), character.get("body_tags"), character.get("outfit_tags"))


def characters_negative(characters: list[dict[str, Any]]) -> str:
    return join_tags(*(c.get("negative_tags") for c in characters))


def sheet_prompt(character: dict[str, Any], *, r18: bool = False) -> str:
    """
    キャラシートだけで組み立てた生成用プロンプト。キャラ別データセットの1枚から、構図・ポーズ・
    表情・場所の差し替え分とトリガーワードを除いたもの(並びはデータセットと同じ)。画像生成ページへの
    読み込みに使う。トリガーワードは LoRA 学習用のキャプションの語で、NovelAI の生成には意味がない。
    """
    return join_tags(
        character.get("appearance_tags"),
        character.get("body_tags"),
        r18_positive() if r18 else "",
        character.get("outfit_tags"),
        character.get("style_tags"),
    )


def sheet_negative(character: dict[str, Any], *, r18: bool = False, extra: str | None = None) -> str:
    """キャラ別データセットと同じネガティブ(基本+キャラシート+年齢区分ごとの安全タグ+上乗せ分)。"""
    from . import app_settings

    # 基本のネガティブは、開発管理者のページで変えられる(sheet.default_negative)
    base = str(app_settings.get("sheet.default_negative"))
    return join_tags(base, character.get("negative_tags"), r18_negative() if r18 else safe_negative(), extra)


def characters_seed(characters: list[dict[str, Any]]) -> int | None:
    """登場キャラのうち基準シードを持つ最初の1人のシード(名前順)。"""
    for character in characters:
        if character.get("seed") is not None:
            return int(character["seed"])
    return None


@dataclass
class VariationShot:
    stem: str
    prompt: str
    # 基準シードからのずらし幅。同じ組み合わせを2周目以降に使うときに、同じ絵にならないようにする
    seed_offset: int = 0
    # この1枚のシチュエーション・状態の名前(使っていなければ空)
    situation: str = ""
    state: str = ""


# 周回ごとのシードのずらし幅。引き直し(シード+試行回数、最大5回)と重ならないように間を空ける
SEED_STEP_PER_ROUND = 10


def balanced_combos(axes: list[list[str]], count: int, rng: random.Random) -> list[tuple[str, ...]]:
    """
    各軸の値ができるだけ均等に出るように組み合わせを count 個選ぶ(重複なし)。

    全組み合わせからランダムに選ぶと、少ない枚数では同じポーズが何度も出る
    (実測: 4枚中3枚が waving)。各軸をシャッフルした列を順に回して使い、
    1周したら並べ直す。重複した組み合わせになったら、その軸を並べ直して引き直す。
    """
    total = 1
    for axis in axes:
        total *= len(axis)
    count = min(count, total)

    decks = [rng.sample(axis, len(axis)) for axis in axes]
    positions = [0] * len(axes)

    def draw(k: int) -> str:
        if positions[k] >= len(decks[k]):
            decks[k] = rng.sample(axes[k], len(axes[k]))
            positions[k] = 0
        value = decks[k][positions[k]]
        positions[k] += 1
        return value

    seen: set[tuple[str, ...]] = set()
    combos: list[tuple[str, ...]] = []
    while len(combos) < count:
        combo = tuple(draw(k) for k in range(len(axes)))
        for _ in range(20):
            if combo not in seen:
                break
            # 重複したら、値の種類が多い軸から順に別の値を引き直す
            k = max(range(len(axes)), key=lambda j: len(axes[j]))
            combo = tuple(draw(j) if j == k else v for j, v in enumerate(combo))
        else:
            remaining = [c for c in itertools.product(*axes) if c not in seen]
            combo = rng.choice(remaining)
        seen.add(combo)
        combos.append(combo)
    return combos


def build_variation_shots(
    character: dict[str, Any],
    *,
    poses: list[str],
    outfits: list[str],
    expressions: list[str],
    locations: list[str],
    count: int,
    framings: list[str] | None = None,
    rng: random.Random | None = None,
    r18: bool = False,
    situations: list[dict[str, Any]] | None = None,
    states: list[dict[str, Any] | None] | None = None,
) -> list[VariationShot]:
    """
    キャラシートの容姿を固定し、構図/ポーズ/服装/表情/場所の組み合わせを count 枚分作る。
    組み合わせは各軸が均等に出るように選ぶ(balanced_combos)。
    服装を1つも選ばなければキャラシートの普段の服装を使う。

    situations(シチュエーション)を渡すと、1枚ごとにどれか1つを使い、そのシチュエーションが決めている軸
    (例: ジムなら場所・服装・ポーズ)は、その中からだけ選ぶ(軸どうしがばらばらに組み合わさらない)。決めていない軸は、
    ふつうのバリエーションから選ぶ。extra_tags は、そのシチュエーションの1枚にいつも足す。
    states(状態)を渡すと、1枚ごとにどれか1つを使い、キャラシートの容姿から remove_tags を外して add_tags を足す
    (例: 食べ過ぎでお腹が出る)。並びに None を入れると、状態なし(ふだんの体)の枚も作る。
    シチュエーションと状態は、すべての組み合わせ(単位)が均等に出るように枚数を割り振る。

    count が組み合わせの数より多いときは、組み合わせを一巡するごとにシードをずらして繰り返す
    (同じプロンプト・同じシードだと同じ絵になるため)。
    """
    rng = rng or random.Random()
    base_axes = {
        "framings": framings or [""],
        "poses": poses or [""],
        "outfits": outfits or [character.get("outfit_tags") or ""],
        "expressions": expressions or [""],
        "locations": locations or [""],
    }
    units = [(situation, state) for situation in (situations or [None]) for state in (states or [None])]
    rng.shuffle(units)
    shots: list[VariationShot] = []
    for index, (situation, state) in enumerate(units):
        # 単位ごとの枚数(端数は先頭の単位から1枚ずつ)
        share = count // len(units) + (1 if index < count % len(units) else 0)
        if share == 0:
            continue
        axes = [
            [v for v in (situation or {}).get(axis) or [] if v] or base_axes[axis]
            for axis in ("framings", "poses", "outfits", "expressions", "locations")
        ]
        head = join_tags(
            character.get("trigger_word"),
            look_tags(character, state),
            r18_positive() if r18 else "",
        )
        extra = (situation or {}).get("extra_tags") or ""
        made = 0
        round_no = 0
        while made < share:
            for framing, pose, outfit, expression, location in balanced_combos(axes, share - made, rng):
                prompt = join_tags(head, framing, outfit, pose, expression, location, extra, character.get("style_tags"))
                shots.append(VariationShot(
                    stem=f"{len(shots) + 1:04d}",
                    prompt=prompt,
                    seed_offset=round_no * SEED_STEP_PER_ROUND,
                    situation=(situation or {}).get("name") or "",
                    state=(state or {}).get("name") or "",
                ))
                made += 1
            round_no += 1
    return shots


def body_tags(appearance: str | None, state: dict[str, Any] | None) -> str:
    """
    状態に合わせた容姿: キャラシートの容姿から remove_tags に当たるタグを外し(強調の記号は見ずに比べる)、
    add_tags を足す。状態が無ければ容姿のまま。
    """
    if not state:
        return appearance or ""
    removed = {_plain(t) for t in split_tags(state.get("remove_tags"))}
    kept = [t for t in split_tags(appearance) if _plain(t) not in removed]
    return join_tags(", ".join(kept), state.get("add_tags"))


def look_tags(character: dict[str, Any], state: dict[str, Any] | None = None) -> str:
    """
    その1枚の見た目(容姿+体型)。キャラシートに標準の体型(body_tags)があれば、状態はそれを丸ごと置き換える
    (外すタグを書き漏らしても、元の体型とぶつからない)。体型が無いキャラは、これまでどおり容姿から外して足す。
    """
    body = character.get("body_tags") or ""
    if not state:
        return join_tags(character.get("appearance_tags"), body)
    if body.strip():
        removed = {_plain(t) for t in split_tags(state.get("remove_tags"))}
        face = ", ".join(t for t in split_tags(character.get("appearance_tags")) if _plain(t) not in removed)
        return join_tags(face, state.get("add_tags"))
    return body_tags(character.get("appearance_tags"), state)


# 体型のタグに入っている語(体つき・胸・腰など)。顔・髪・目・肌の色は容姿の側に残す
BODY_SHAPE_WORDS = {
    "breasts", "breast", "chest", "flat", "slim", "slender", "thin", "skinny", "petite", "curvy", "voluptuous",
    "plump", "chubby", "fat", "muscular", "toned", "abs", "stomach", "belly", "waist", "hips", "hip", "thighs",
    "thigh", "tall", "height", "figure", "build", "body", "bodied", "stature", "legs", "shoulders", "ass", "butt",
    "pregnant", "muscle", "muscles", "athletic", "busty", "huge", "gigantic", "small", "large", "medium",
}
# 語は体型のものでも、顔・髪・目のタグ(long hair の long、large eyes の large など)は容姿に残す
_FACE_WORDS = {"hair", "eyes", "eye", "face", "ears", "ear", "nose", "mouth", "lips", "eyebrows", "eyelashes", "skin", "teeth"}


def split_body(appearance: str) -> tuple[str, str]:
    """容姿のタグを(顔・髪・目など, 体型)に分ける。人数のタグ(1girl など)は顔の側に残す。"""
    face: list[str] = []
    body: list[str] = []
    for tag in split_tags(appearance):
        words = set(_plain(tag).replace("(", " ").replace(")", " ").split())
        (body if words & BODY_SHAPE_WORDS and not words & _FACE_WORDS else face).append(tag)
    return ", ".join(face), ", ".join(body)


def adult_only_tags(tags: str) -> list[str]:
    """成人キャラだけに使うタグ(コンテンツガードの dataset.adult_only_tags。妊娠など)。"""
    return content_guard.find("dataset.adult_only_tags", tags)


def caption_for(character: dict[str, Any], extra: str | None = None) -> str:
    """手動で取り込む画像のキャプション既定値(トリガーワード+容姿+任意の追加タグ)。"""
    return join_tags(character.get("trigger_word"), character.get("appearance_tags"), character.get("body_tags"), extra)
