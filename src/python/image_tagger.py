"""
画像から NovelAI のプロンプトを逆引きする。

1. NovelAI で生成した PNG は、生成時のプロンプト・キャラごとのプロンプト・設定(シードなど)が
   画像に埋め込まれているので、それをそのまま返す(完全に再現できる)。
2. 埋め込みが無い画像は、danbooru タグの画像分類モデル WD Tagger v3(SmilingWolf、Apache-2.0、
   https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3)でタグを推定する。NovelAI は
   danbooru タグで学習しているので、同じ語彙で返せる。初回に data/models へダウンロードする。

以前は画像を読めるローカルLLM(qwen3-vl:2b)に自由にタグを書かせていたが、2026-10 に生成履歴の
コマ40枚で測ったところ、思考で出力を使い切って空を返すことが大半で(16枚中14枚)、返っても
"romance, cute" のような曖昧な語だった。qwen2.5vl:7b は VRAM 8GB では1枚6分かかった。
WD Tagger は CPU で1枚3秒、danbooru の語彙にある正解タグの約56%を当てた(しきい値0.5)。
"""

from __future__ import annotations

import csv
import io
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_NAME = "wd-eva02-large-tagger-v3"
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / MODEL_NAME
_MODEL_URL = f"https://huggingface.co/SmilingWolf/{MODEL_NAME}/resolve/main/{{name}}"

# selected_tags.csv の category
_GENERAL = 0
_CHARACTER = 4
_RATING = 9

# 既定のしきい値。一般タグは測定で精度と再現率の釣り合いが良かった値、キャラ名は誤りが目立つので高め
GENERAL_THRESHOLD = 0.5
CHARACTER_THRESHOLD = 0.85
# 画面でしきい値を下げられるよう、これ以上の確率のタグは候補として返す
_CANDIDATE_FLOOR = 0.2

# 絵柄・色のタグ。生成し直したときの見た目を大きく左右するのに確率が低めに出る(白黒の漫画のコマで
# monochrome が 0.3〜0.5)ので、しきい値を下げて拾い、人数の次に置く
STYLE_TAGS = (
    "monochrome",
    "greyscale",
    "spot color",
    "partially colored",
    "limited palette",
    "sepia",
    "sketch",
    "lineart",
    "screentone",
    "halftone",
    "traditional media",
    "watercolor (medium)",
    "realistic",
    "photorealistic",
    "3d",
    "pixel art",
    "chibi",
    "flat color",
)
STYLE_THRESHOLD = 0.25

# 元の画像がコマ割りの漫画でなければ、ネガティブに足す(横長の画像はコマが並んだ絵になりやすい)
_PANEL_TAGS = ("comic", "multiple views", "4koma", "2koma", "3koma", "panels")

# プロンプトの先頭に置く人数のタグ(NovelAI の書き方)
_COUNT_TAGS = (
    "1girl",
    "2girls",
    "3girls",
    "4girls",
    "5girls",
    "6+girls",
    "multiple girls",
    "1boy",
    "2boys",
    "3boys",
    "4boys",
    "5boys",
    "6+boys",
    "multiple boys",
    "1other",
    "2others",
    "3others",
    "multiple others",
    "no humans",
)

_lock = threading.Lock()
_session: Any = None
_tags: list[tuple[str, int]] = []


@dataclass
class TagScore:
    tag: str
    probability: float
    category: str  # "general" | "character" | "rating"


def _ensure_model() -> Path:
    model = _MODEL_DIR / "model.onnx"
    if model.is_file():
        return model
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=600) as client:
        for name in ("selected_tags.csv", "model.onnx"):
            tmp = _MODEL_DIR / f"{name}.part"
            with client.stream("GET", _MODEL_URL.format(name=name)) as response:
                response.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in response.iter_bytes(1 << 20):
                        f.write(chunk)
            tmp.replace(_MODEL_DIR / name)
    return model


def _get_session() -> tuple[Any, list[tuple[str, int]]]:
    global _session, _tags
    with _lock:
        if _session is None:
            import onnxruntime as ort

            model = _ensure_model()
            with open(_MODEL_DIR / "selected_tags.csv", encoding="utf-8") as f:
                _tags = [(row["name"], int(row["category"])) for row in csv.DictReader(f)]
            _session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    return _session, _tags


def _prepare(image: Image.Image, size: int) -> np.ndarray:
    """透明部分を白にし、白で正方形に余白を足して縮める。モデルの入力は BGR・0〜255。"""
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    canvas.alpha_composite(rgba)
    rgb = canvas.convert("RGB")
    side = max(rgb.size)
    square = Image.new("RGB", (side, side), (255, 255, 255))
    square.paste(rgb, ((side - rgb.width) // 2, (side - rgb.height) // 2))
    square = square.resize((size, size), Image.Resampling.BICUBIC)
    return np.asarray(square, dtype=np.float32)[:, :, ::-1][None].copy()


def tag_image(image: Image.Image) -> list[TagScore]:
    """画像のタグの候補(確率の高い順)。一般タグは _CANDIDATE_FLOOR 以上、評価(rating)は全部。"""
    session, tags = _get_session()
    size = session.get_inputs()[0].shape[1]
    probs = session.run(None, {session.get_inputs()[0].name: _prepare(image, size)})[0][0]
    result: list[TagScore] = []
    for index in np.argsort(-probs):
        name, category = tags[index]
        p = float(probs[index])
        if category == _RATING:
            result.append(TagScore(name, p, "rating"))
        elif p >= _CANDIDATE_FLOOR and category in (_GENERAL, _CHARACTER):
            result.append(TagScore(name.replace("_", " "), p, "character" if category == _CHARACTER else "general"))
    return _add_monochrome(image, result)


# 彩度の平均がこれ未満なら白黒とみなす(評価用データの白黒の絵は 0.02〜0.05、カラーは 0.1 以上)
_MONOCHROME_SATURATION = 0.06


def _add_monochrome(image: Image.Image, result: list[TagScore]) -> list[TagScore]:
    """
    白黒の絵に monochrome・greyscale を確実に付ける。モデルは白黒の絵でも monochrome を落とすことがある
    (評価用データの白黒の絵の半分ほど)が、色の有無は画素から確実に分かる。
    """
    small = image.convert("RGB")
    small.thumbnail((256, 256))
    saturation = float(np.asarray(small.convert("HSV"))[:, :, 1].mean()) / 255
    if saturation >= _MONOCHROME_SATURATION:
        return result
    added = [TagScore(tag, 1.0, "general") for tag in ("monochrome", "greyscale")]
    rest = [s for s in result if s.tag not in ("monochrome", "greyscale")]
    return added + rest


def _threshold(score: TagScore, general_threshold: float, character_threshold: float) -> float:
    if score.category == "character":
        return character_threshold
    if score.tag in STYLE_TAGS:
        return min(STYLE_THRESHOLD, general_threshold)
    return general_threshold


def _implied(tag: str, others: list[str]) -> bool:
    """
    大きさの語を付けたタグがあるときの、元のタグか(breasts と large breasts)。両方入れると大きさが
    強く出すぎる(生成し直すと胸が大きくなりすぎた)。

    ほかの詳しいタグ(white apron・black necktie・single hair bun)に含まれるタグは外さない。
    評価用データ(scripts/make_reverse_eval.py)で、広く外すと apron・sweater・necktie などを
    取りこぼし、服・小物の再現率が 0.87 → 0.50 に落ちた。
    """
    return any(other.endswith(" " + tag) and other[: -len(tag) - 1] in _SIZE_WORDS for other in others)


_SIZE_WORDS = {"large", "huge", "gigantic", "small", "flat", "medium", "big", "long", "short", "thick"}


def select_tags(
    scores: list[TagScore],
    general_threshold: float = GENERAL_THRESHOLD,
    character_threshold: float = CHARACTER_THRESHOLD,
) -> list[TagScore]:
    """
    しきい値を超え、大きさの語を付けたタグに含まれないタグ(確率の高い順)。構図のタグは、しきい値に
    関係なく一番確かな1つだけにする(_framing)。
    """
    chosen = [
        s
        for s in scores
        if s.category in ("general", "character")
        and s.tag not in FRAMING_TAGS
        and s.probability >= _threshold(s, general_threshold, character_threshold)
    ]
    tags = [s.tag for s in chosen]
    chosen = [s for s in chosen if s.tag in _COUNT_TAGS or not _implied(s.tag, tags)]
    framing = _framing(scores)
    if framing is not None:
        # 確率の高い順を保って入れる
        chosen = sorted([*chosen, framing], key=lambda s: -s.probability)
    return chosen


# 構図のタグ。モデルはどれか1つに確率を寄せるが、値は低め(正しい cowboy shot でも 0.2〜0.45)に出る。
# 評価用データ(目視で直した正解)では、しきい値 0.5 だと構図の当たりは 37 枚中 19 枚、一番確かな
# 1つを選ぶと 26 枚になった。顔の大きさからも決めてみたが、upper body と cowboy shot は顔の高さ
# (画像の 0.22〜0.43)が重なって分けられなかった。
FRAMING_TAGS = ("portrait", "upper body", "cowboy shot", "full body")
FRAMING_THRESHOLD = 0.2


def _framing(scores: list[TagScore]) -> TagScore | None:
    candidates = [s for s in scores if s.tag in FRAMING_TAGS and s.probability >= FRAMING_THRESHOLD]
    return max(candidates, key=lambda s: s.probability, default=None)


def build_prompt(
    scores: list[TagScore],
    general_threshold: float = GENERAL_THRESHOLD,
    character_threshold: float = CHARACTER_THRESHOLD,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> str:
    """
    選んだタグを NovelAI の並び(人数 → キャラ名 → 絵柄 → そのほかを確率の高い順)にする。
    exclude はキャラごとのプロンプトに移したタグ(全体には入れない)。
    """
    chosen = [s for s in select_tags(scores, general_threshold, character_threshold) if s.tag not in exclude]
    counts = [s.tag for s in chosen if s.tag in _COUNT_TAGS]
    counts.sort(key=_COUNT_TAGS.index)
    characters = [s.tag for s in chosen if s.category == "character"]
    styles = [s.tag for s in chosen if s.tag in STYLE_TAGS]
    others = [s.tag for s in chosen if s.category == "general" and s.tag not in _COUNT_TAGS and s.tag not in STYLE_TAGS]
    return ", ".join(counts + characters + styles + others)


# ---- 人物ごとのタグ(誰がどの髪色・服か) ----

# 人物ごとに分けるのは、この人数まで(NovelAI のキャラごとのプロンプトの上限に合わせる)
_MAX_CHARACTERS = 4
# 人物ごとのタグにする条件: その人の切り抜きでの確率がこれ以上で、ほかの人より _CHARACTER_MARGIN 以上高い
_CHARACTER_TAG_THRESHOLD = 0.5
_CHARACTER_MARGIN = 0.3
# 人物ごとには分けないタグ(絵全体のこと)
_SCENE_ONLY = {"solo", "multiple girls", "multiple boys", "male focus", "female focus", "solo focus"}

# 人物のタグにする語(容姿・服・小物・表情・しぐさ)。切り抜きには近くの物や背景も写るので(cup・brick wall など)、
# これらの語を含むタグだけを人物に付け、ほかは全体に残す
_PERSON_WORDS = {
    # 髪・顔・体
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
    "eyebrows",
    "eyelashes",
    "skin",
    "mole",
    "freckles",
    "ears",
    "fang",
    "teeth",
    "lips",
    "beard",
    "mustache",
    "facial",
    "breasts",
    "muscular",
    # 服・小物
    "shirt",
    "jacket",
    "coat",
    "dress",
    "skirt",
    "pants",
    "shorts",
    "jeans",
    "kimono",
    "hakama",
    "yukata",
    "obi",
    "apron",
    "sweater",
    "hoodie",
    "cardigan",
    "vest",
    "suit",
    "necktie",
    "tie",
    "bowtie",
    "collar",
    "sleeves",
    "gloves",
    "socks",
    "thighhighs",
    "pantyhose",
    "boots",
    "shoes",
    "sneakers",
    "scarf",
    "earrings",
    "jewelry",
    "necklace",
    "choker",
    "uniform",
    "clothes",
    "hat",
    "headwear",
    "beret",
    "cap",
    "hood",
    "ribbon",
    "bow",
    "hairclip",
    "hairband",
    "headband",
    "headphones",
    "glasses",
    "eyewear",
    "belt",
    "buttons",
    "lapels",
    "frills",
    "maid",
    "wa",
    "turtleneck",
    "camisole",
    "blazer",
    "sailor",
    "overalls",
    "bag",
    "backpack",
    "watch",
    "bracelet",
    "ring",
    "cape",
    "cloak",
    "armor",
    # 表情・しぐさ
    "smile",
    "grin",
    "blush",
    "mouth",
    "tongue",
    "tears",
    "crying",
    "frown",
    "pout",
    "expressionless",
    "serious",
    "surprised",
    "closed",
    "looking",
    "wink",
    "hand",
    "hands",
    "arm",
    "arms",
    "finger",
    "legs",
    "sitting",
    "standing",
    "walking",
    "running",
    "holding",
    "reading",
    "waving",
    "v",
    "crossed",
    "head",
    "tilt",
    "leaning",
    "pocket",
}


def _is_person_tag(tag: str) -> bool:
    return any(word in _PERSON_WORDS for word in tag.replace("(", " ").replace(")", " ").split())


@dataclass
class CharacterGuess:
    """推定した人物。tags はその人だけのタグ(確率の高い順)、center はキャラの位置(画像に対する割合)。"""

    gender: str
    tags: list[str]
    center: tuple[float, float]

    @property
    def prompt(self) -> str:
        return ", ".join([self.gender, *self.tags])


def _person_crops(image: Image.Image, heads: list[tuple[float, float, float, float]]) -> list[Image.Image]:
    """頭ごとに、その人が写っている範囲(左右は隣の人との中間まで、上は頭の少し上から画像の下まで)。"""
    width, height = image.size
    centers = [(b[0] + b[2]) / 2 for b in heads]
    crops = []
    for i, (x0, y0, x1, y1) in enumerate(heads):
        head_w = x1 - x0
        left = max(0.0, centers[i] - 2 * head_w, (centers[i - 1] + centers[i]) / 2 if i else 0.0)
        right = min(
            float(width),
            centers[i] + 2 * head_w,
            (centers[i] + centers[i + 1]) / 2 if i + 1 < len(heads) else float(width),
        )
        top = max(0.0, y0 - 0.3 * (y1 - y0))
        crops.append(image.crop((int(left), int(top), int(right), height)))
    return crops


def _grid(value: float) -> float:
    """NovelAI のキャラの位置は 0.1〜0.9 の 0.2 刻み(5×5 のマス)。"""
    return round(min(max(round((value - 0.1) / 0.2) * 0.2 + 0.1, 0.1), 0.9), 1)


def split_characters(image: Image.Image) -> list[CharacterGuess]:
    """
    二人以上写っている絵で、人物ごとのタグを推定する(左から順)。一人以下なら空。

    頭の位置で人物を切り抜き、切り抜きごとにタグを判定する。ある人の切り抜きだけで確率が高いタグ
    (髪の色・服など)をその人のタグにし、どの人でも同じくらいのタグ(場所・笑顔など)は全体に残す。
    評価用データの二人の絵 10 枚では、人物ごとの髪の色が全部正しい人に付いた(絵を目視で確かめた正解で)。
    """
    from .manga_v2.detect import detect_heads_in_image

    heads = sorted(detect_heads_in_image(image), key=lambda b: (b[0] + b[2]) / 2)[:_MAX_CHARACTERS]
    if len(heads) < 2:
        return []
    per_person = [
        {s.tag: s.probability for s in tag_image(crop) if s.category == "general"}
        for crop in _person_crops(image, heads)
    ]
    skip = set(_COUNT_TAGS) | set(STYLE_TAGS) | set(FRAMING_TAGS) | _SCENE_ONLY
    assigned: list[list[tuple[float, str]]] = [[] for _ in heads]
    for tag in {t for t in set().union(*per_person) - skip if _is_person_tag(t)}:
        values = [person.get(tag, 0.0) for person in per_person]
        best = max(range(len(values)), key=values.__getitem__)
        runner_up = max(v for i, v in enumerate(values) if i != best)
        if values[best] >= _CHARACTER_TAG_THRESHOLD and values[best] - runner_up >= _CHARACTER_MARGIN:
            assigned[best].append((values[best], tag))
    width = image.width
    guesses = []
    for (x0, y0, x1, y1), person, tags in zip(heads, per_person, assigned):
        gender = max(("1girl", "1boy", "1other"), key=lambda g: person.get(g, 0.0))
        names = [tag for _, tag in sorted(tags, reverse=True)]
        names = [t for t in names if not _implied(t, names)]
        # 位置は頭の中心の左右。上下は真ん中にする(座っている・寝ているなどで体の中心が読みにくいため)
        center = (_grid((x0 + x1) / 2 / width), 0.5)
        guesses.append(CharacterGuess(gender, names, center))
    return guesses


def build_negative(base: str, prompt: str) -> str:
    """
    元の画像が漫画のコマ割りでなければ、コマが並んだ絵にならないよう打ち消す。画像にあるタグ
    (ぼかしの効いた背景の blurry など)は、ネガティブから外す(両方にあると打ち消し合う)。
    """
    tags = {t.strip() for t in prompt.split(",")}
    negative = [t.strip() for t in base.split(",") if t.strip()]
    if not tags & set(_PANEL_TAGS):
        negative += ["multiple views", "comic", "panels", "border"]
    return ", ".join(t for t in dict.fromkeys(negative) if t not in tags)


def rating_of(scores: list[TagScore]) -> str | None:
    ratings = [s for s in scores if s.category == "rating"]
    return max(ratings, key=lambda s: s.probability).tag if ratings else None


def embedded_prompt(data: bytes) -> dict[str, Any] | None:
    """
    NovelAI で生成した PNG に埋め込まれたプロンプトと設定。無ければ None。
    文字の埋め込み(PNG のテキスト)と、画素に隠した埋め込み(Stealth)の両方を読む。
    """
    from novelai._utils.nai_meta import extract_image_metadata

    try:
        image = Image.open(io.BytesIO(data))
    except Exception:  # noqa: BLE001 読めない画像は呼び出し側で扱う
        return None
    # PNG のテキスト(保存し直すと消えることがある)を先に見て、無ければ画素に隠した埋め込みを読む
    metadata: Any = dict(image.info) if image.info.get("Comment") else None
    if metadata is None:
        try:
            metadata = extract_image_metadata(image)
        except Exception:  # noqa: BLE001 埋め込みが無い・壊れている画像は推定に回す
            return None
        if not isinstance(metadata, dict):
            metadata = metadata[0]
    comment = metadata.get("Comment")
    if isinstance(comment, str):
        try:
            comment = json.loads(comment)
        except json.JSONDecodeError:
            return None
    if not isinstance(comment, dict) or not comment.get("prompt"):
        return None

    def number(key: str) -> float | None:
        try:
            return float(comment[key])
        except (KeyError, TypeError, ValueError):
            return None

    characters: list[dict[str, str]] = []
    v4 = _as_dict(comment.get("v4_prompt"))
    v4_negative = _as_dict(comment.get("v4_negative_prompt"))
    captions = (v4.get("caption") or {}).get("char_captions") or []
    negatives = (v4_negative.get("caption") or {}).get("char_captions") or []
    for index, caption in enumerate(captions):
        negative = negatives[index].get("char_caption", "") if index < len(negatives) else ""
        centers = caption.get("centers") or []
        center = centers[0] if centers and isinstance(centers[0], dict) else {}
        characters.append(
            {
                "prompt": caption.get("char_caption", ""),
                "negative": negative,
                "x": center.get("x"),
                "y": center.get("y"),
            }
        )
    seed, steps, scale = number("seed"), number("steps"), number("scale")
    width, height = number("width"), number("height")
    return {
        "positive": str(comment["prompt"]),
        "negative": str(comment.get("uc") or ""),
        "characters": characters,
        "settings": {
            "seed": int(seed) if seed is not None else None,
            "steps": int(steps) if steps is not None else None,
            "scale": scale,
            "sampler": comment.get("sampler"),
            "width": int(width) if width is not None else None,
            "height": int(height) if height is not None else None,
        },
        "software": metadata.get("Source") or metadata.get("Software"),
    }


def _as_dict(value: Any) -> dict[str, Any]:
    """v4_prompt は JSON 文字列のことも、Python の repr の文字列のこともある(SDK の版による)。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            import ast

            try:
                loaded = ast.literal_eval(value)
                return loaded if isinstance(loaded, dict) else {}
            except (ValueError, SyntaxError):
                return {}
    return {}
