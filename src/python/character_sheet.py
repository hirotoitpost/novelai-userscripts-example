"""キャラシート: 自作キャラを同じ見た目で安定して出すための設定と、その組み立て。

キャラ(characters テーブル)に、変えない容姿タグ・普段の服装・画風・ネガティブ・基準シード・
参照画像を持たせる。物語の挿絵/漫画v2のコマ生成と、キャラ別データセット生成の両方が
ここを通してプロンプトを作るので、どちらでも同じキャラとして出る。

データセットのバリエーション(ポーズ/服装/表情/場所)は既定で全年齢向けに限る。
水着・下着・裸などは選択肢に入れず、手入力されても弾く。
R18 は成人フラグ(is_adult)の付いたキャラだけで使え、その場合は性的なタグの代わりに
未成年を示すタグを弾く。
"""

from __future__ import annotations

import itertools
import random
import re
from dataclasses import dataclass
from typing import Any

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

# 全年齢向けに限るため、データセット生成では次のタグを受け付けない。
_BLOCKED_PATTERNS = [
    r"nsfw", r"nude", r"naked", r"nipples?", r"topless", r"bottomless", r"sex",
    r"underwear", r"lingerie", r"panties", r"bra", r"brassiere", r"swimsuit", r"bikini",
    r"see-through", r"cleavage", r"pussy", r"penis", r"cum", r"bondage",
]
_BLOCKED_RE = re.compile(r"\b(" + "|".join(_BLOCKED_PATTERNS) + r")\b", re.IGNORECASE)

# データセット生成で常に付けるネガティブ(全年齢向けを保つため)
SAFE_NEGATIVE = "nsfw, nude, underwear, swimsuit, cleavage"

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


# --- R18(成人キャラ限定) ---
# 未成年を示すタグ。R18 では性的なタグの代わりにこちらで弾く。基本のガードと同じく固定で、
# ガードプロファイルからは外せない。成人フラグの保存時にもキャラシートを検査する。
_MINOR_PATTERNS = [
    r"jk", r"joshi ?kousei", r"school ?uniform", r"serafuku", r"gym uniform", r"school ?swimsuit",
    r"randoseru", r"kindergarten", r"(high|middle|elementary) school", r"school ?(girl|boy)s?",
    r"students?", r"loli", r"shota", r"child(ren)?", r"kids?", r"teen(age|ager)?", r"underage",
    r"minor", r"young", r"aged down", r"toddler", r"little girl", r"little boy",
]
_MINOR_RE = re.compile(r"\b(" + "|".join(_MINOR_PATTERNS) + r")\b", re.IGNORECASE)

# R18 生成で常に付けるタグ/ネガティブ(成人として描かせるため)
R18_POSITIVE = "adult, mature female"
R18_NEGATIVE = "child, loli, shota, young, teenage, school uniform, student, petite, flat chest"


def minor_tags(tags: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _MINOR_RE.finditer(tags)})


def character_minor_tags(character: dict[str, Any]) -> list[str]:
    """キャラシートの中で未成年を示すタグ(成人フラグを付けられるかの判定に使う)。"""
    text = ", ".join(
        str(character.get(k) or "") for k in ("name", "trigger_word", "appearance_tags", "outfit_tags", "style_tags")
    )
    return minor_tags(text)


# 基本のガード。ガードプロファイル(DBの guard_profiles)からは上乗せはできるが外せない。
CORE_BLOCKED_TAGS = [p.replace("?", "") for p in _BLOCKED_PATTERNS]
CORE_NEGATIVE = SAFE_NEGATIVE


def blocked_tags(tags: str, extra: list[str] | None = None, *, include_core: bool = True) -> list[str]:
    """
    基本のブロックリストと、ガードプロファイルで追加したタグ(extra)に当たるものを返す。
    extra は正規表現ではなくタグそのものとして扱う(記号入りのタグでも誤動作しないように)。
    include_core=False は R18(成人キャラ)用で、代わりに minor_tags() で未成年を弾く。
    """
    found = {m.group(0).lower() for m in _BLOCKED_RE.finditer(tags)} if include_core else set()
    words = [re.escape(t.strip()) for t in extra or [] if t.strip()]
    if words:
        extra_re = re.compile(r"(?<![\w-])(" + "|".join(words) + r")(?![\w-])", re.IGNORECASE)
        found |= {m.group(0).lower() for m in extra_re.finditer(tags)}
    return sorted(found)


def character_prompt_tags(character: dict[str, Any]) -> str:
    """挿絵/コマ生成で characterPrompts に渡すタグ。容姿+普段の服装(画風は混ぜない)。"""
    return join_tags(character.get("appearance_tags"), character.get("outfit_tags"))


def characters_negative(characters: list[dict[str, Any]]) -> str:
    return join_tags(*(c.get("negative_tags") for c in characters))


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
) -> list[VariationShot]:
    """
    キャラシートの容姿を固定し、構図/ポーズ/服装/表情/場所の組み合わせを count 枚分作る。
    組み合わせは各軸が均等に出るように選ぶ(balanced_combos)。
    服装を1つも選ばなければキャラシートの普段の服装を使う。
    """
    rng = rng or random.Random()
    outfit_choices = outfits or [character.get("outfit_tags") or ""]
    axes = [framings or [""], poses or [""], outfit_choices, expressions or [""], locations or [""]]

    head = join_tags(character.get("trigger_word"), character.get("appearance_tags"), R18_POSITIVE if r18 else "")
    shots = []
    for i, (framing, pose, outfit, expression, location) in enumerate(balanced_combos(axes, count, rng), start=1):
        prompt = join_tags(head, framing, outfit, pose, expression, location, character.get("style_tags"))
        shots.append(VariationShot(stem=f"{i:04d}", prompt=prompt))
    return shots


def caption_for(character: dict[str, Any], extra: str | None = None) -> str:
    """手動で取り込む画像のキャプション既定値(トリガーワード+容姿+任意の追加タグ)。"""
    return join_tags(character.get("trigger_word"), character.get("appearance_tags"), extra)
