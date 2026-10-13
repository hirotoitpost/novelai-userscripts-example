"""
キャラ別データセットのバリエーション(構図・ポーズ・服装・表情・場所)の候補に、プロンプトからキーワードを
取り出して足すための仕分け。

プロンプト(画像生成で使ったもの、リバースプロンプトの結果、貼り付けたもの)のタグを、5つの軸に分ける。
- 容姿(髪・目・体)と人数は取り出さない。キャラシートが持つもので、バリエーションにすると別人になる。
- 品質・画風のタグも取り出さない(生成のときに別に付く)。
- 性的なタグは、軸には入れずに adult として分けて返す(バリエーションの候補は全年齢向けが既定。R18 の
  データセットで使うときは、画面で軸を選んで足す。生成のときの検査は routes/lora_dataset.py がする)。
- どの軸か分からないタグは other として返す(画面で軸を選んで足せる)。

語の一覧で見分けているだけなので、外れることがある。足す前に画面で確かめてもらう前提。
"""

from __future__ import annotations

import re
from typing import Any

from . import content_guard
from .character_sheet import _COUNT_RE, _PERSON_NOUNS, _STYLE_PHRASES, _STYLE_RE, _plain, split_tags

AXES = ("framings", "poses", "outfits", "expressions", "locations")
MAX_VALUE_CHARS = 100

# 強調の書き方: マイナスの重み(-1::tag::)は「出さない」指定なので、タグごと除く。ほかは記号だけ外す
_NEGATIVE_WEIGHT = re.compile(r"-\d+(?:\.\d+)?::.*?::")
_WEIGHT = re.compile(r"\d+(?:\.\d+)?::(.*?)::")
# 角かっこで囲んだ、英語でない見出し(ギャラリーの記録の [表情: scared] など)
_LABEL = re.compile(r"\[[^\]]*[^\x00-\x7f][^\]]*\]")

# 構図
_FRAMING_TAGS = {
    "close-up", "upper body", "lower body", "full body", "cowboy shot", "portrait", "profile", "pov", "dutch angle",
    "straight-on", "wide shot", "fisheye", "foreshortening", "head out of frame", "feet out of frame", "bust",
}
_FRAMING_RE = re.compile(r"(^from (above|below|side|behind|outside)$|\bshot$|\bview$|\bangle$|^pov\b)")

# 表情(語が1つでも入っていれば表情)と、語の組で決まる表情
_EXPRESSION_WORDS = {
    "smile", "smiling", "grin", "smirk", "blush", "tears", "crying", "frown", "pout", "expressionless", "serious",
    "surprised", "angry", "sad", "laughing", "sleepy", "embarrassed", "wink", "happy", "scared", "nervous", "shy",
    "annoyed", "confused", "worried", "tired", "bored", "excited", "determined", "smug", "drunk", "teary",
    "sweatdrop", "teeth", "fang",
}
_EXPRESSION_PHRASES = {
    "closed eyes", "half-closed eyes", "one eye closed", "wide-eyed", "open mouth", "closed mouth", "parted lips",
    "tongue out", "light smile", "evil smile", "sweat", "nose blush", "raised eyebrow", "furrowed brow",
    "looking away", "looking down", "looking up", "averting eyes", "empty eyes", "heart-shaped pupils", "v-shaped eyebrows",
}
# 顔文字のタグ(:d, ;), >_< など)
_EMOTICON = re.compile(r"^[:;>=xX^][-_^o3dpDP<>()|/\\\s]{0,3}[:;<=xX^]?$")

# 場所・時間帯・天気・背景
_LOCATION_WORDS = {
    "indoors", "outdoors", "room", "classroom", "bedroom", "kitchen", "bathroom", "school", "hallway", "rooftop",
    "library", "street", "road", "alley", "park", "cafe", "restaurant", "shop", "store", "office", "station",
    "shrine", "temple", "church", "castle", "beach", "ocean", "sea", "pool", "poolside", "river", "lake", "forest",
    "mountain", "field", "garden", "city", "cityscape", "town", "village", "sky", "night", "day", "sunset",
    "sunrise", "evening", "morning", "rain", "snow", "background", "stage", "gym", "hospital", "onsen", "desert",
    "cave", "ruins", "space", "underwater", "festival", "interior", "balcony", "bridge", "stairs", "playground",
    "courtyard", "scenery", "landscape", "meadow", "countryside", "apartment", "house", "home", "hotel", "bar",
    "market", "train", "bus", "cloudy", "sunlight", "moonlight", "starry", "autumn", "winter", "spring", "summer",
}

# 服の語で終わる語(sundress, miniskirt, undershirt, swimwear など。語の一覧に無い合成語を拾う)
_CLOTHES_ENDINGS = ("dress", "skirt", "shirt", "coat", "suit", "wear", "pants", "socks", "shoes", "boots", "jacket")
# 場所の語を含むが、場所ではないもの
_NOT_LOCATION = {"depth of field", "day off"}

# ポーズ・動作(語の一覧に、動作を表す語を足す)
_POSE_EXTRA = {
    "lying", "kneeling", "squatting", "jumping", "stretching", "sign", "pose", "salute", "hug", "hugging",
    "carrying", "pointing", "reaching", "crouching", "dancing", "sleeping", "eating", "drinking", "cooking",
    "writing", "playing", "back", "stomach", "side", "fours", "seiza", "wariza", "contrapposto", "arched",
    "bent", "spread", "raised", "outstretched", "selfie", "fighting",
}


def prompt_tags(prompt: str) -> list[str]:
    """プロンプトを、強調の書き方を外したタグの並びにする(_ は空白に。英語でないものは除く)。"""
    text = _LABEL.sub(", ", str(prompt or ""))
    text = _WEIGHT.sub(r"\1", _NEGATIVE_WEIGHT.sub("", text))
    text = re.sub(r"[{}\[\]]", "", text).replace("\n", ",")
    tags: list[str] = []
    for tag in split_tags(text):
        tag = re.sub(r"\s+", " ", tag.replace("_", " ")).strip().lstrip("-").strip()
        if tag and tag.isascii() and len(tag) <= MAX_VALUE_CHARS and tag.lower() not in [t.lower() for t in tags]:
            tags.append(tag)
    return tags


def axis_of(tag: str) -> str:
    """
    タグの軸: framings / poses / outfits / expressions / locations。取り出さないものは skip
    (品質・画風・人数・容姿)、性的なものは adult、どれか分からないものは other。
    """
    from .image_tagger import BODY_WORDS, CLOTHES_WORDS, POSE_WORDS, STYLE_TAGS

    plain = _plain(tag)
    words = set(plain.replace("(", " ").replace(")", " ").split())
    if not plain or plain == "solo":
        return "skip"
    if plain in _STYLE_PHRASES or plain in STYLE_TAGS or _STYLE_RE.search(plain):
        return "skip"
    if _COUNT_RE.match(plain) or plain in _PERSON_NOUNS or re.match(r"^(multiple|\d\+?) ?(girls?|boys?|others?)$", plain):
        return "skip"
    if content_guard.is_sexual(tag):
        return "adult"
    if plain in _FRAMING_TAGS or _FRAMING_RE.search(plain):
        return "framings"
    if plain in _EXPRESSION_PHRASES or _EMOTICON.match(tag.strip()) or words & _EXPRESSION_WORDS:
        return "expressions"
    if words & CLOTHES_WORDS or any(w.endswith(_CLOTHES_ENDINGS) for w in words):
        return "outfits"
    if words & _LOCATION_WORDS and plain not in _NOT_LOCATION:
        return "locations"
    if words & (POSE_WORDS | _POSE_EXTRA) or re.match(r"^[a-z]+ing\b", plain):
        return "poses"
    if words & BODY_WORDS:
        return "skip"
    return "other"


# チャンクの分類の名前から、軸の見当を付ける語
_HINT_WORDS = (
    ("expressions", ("表情", "顔", "expression", "face")),
    ("outfits", ("服", "衣装", "衣服", "アクセ", "コスチューム", "outfit", "cloth", "costume")),
    ("framings", ("カメラ", "構図", "視点", "アングル", "camera", "framing", "angle")),
    ("locations", ("風景", "場所", "背景", "ロケ", "location", "scene", "background")),
    ("poses", ("ポーズ", "姿勢", "動作", "仕草", "pose", "action")),
)


def hint_axis(label: str) -> str | None:
    """分類の名前(チャンクの分類など)から見当を付けた軸。分からなければ None。"""
    text = str(label or "").lower()
    return next((axis for axis, words in _HINT_WORDS if any(w in text for w in words)), None)


def extract(prompts: list[str], hints: list[str | None] | None = None) -> dict[str, Any]:
    """
    プロンプトからバリエーションの候補を取り出す。axes は軸ごとのタグ、other はどの軸か分からないタグ、
    adult は性的なタグ、skipped は取り出さなかったタグ(品質・画風・人数・容姿)。どれも出てきた順で、重なりは除く。

    hints は、プロンプトごとの軸の見当(チャンクの分類から付けたもの。無ければ None)。語の一覧で軸が分からなかった
    タグ(other / adult)に、そのプロンプトの見当を suggested として返す(画面で、その軸に置いて見せる)。
    """
    result: dict[str, Any] = {
        "axes": {axis: [] for axis in AXES}, "other": [], "adult": [], "skipped": [], "suggested": {},
    }
    seen: set[str] = set()
    for index, prompt in enumerate(prompts):
        hint = hints[index] if hints and index < len(hints) else None
        for tag in prompt_tags(prompt):
            if tag.lower() in seen:
                continue
            seen.add(tag.lower())
            axis = axis_of(tag)
            if axis in AXES:
                result["axes"][axis].append(tag)
                continue
            result[{"skip": "skipped", "adult": "adult"}.get(axis, "other")].append(tag)
            if axis != "skip" and hint in AXES:
                result["suggested"][tag] = hint
    return result


def clean_value(value: str) -> str:
    """候補として保存する形(前後と重なった空白を除く。タグの並びは「, 」でそろえる)。"""
    return ", ".join(re.sub(r"\s+", " ", t) for t in split_tags(str(value or "")))[:MAX_VALUE_CHARS]
