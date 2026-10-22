"""
キャラシートの「顔・表情シート」: キャラの見た目を固定して、顔の向き(正面・側面・背面・見返り・逆立ち など)と
表情(喜怒哀楽・羞恥・テヘペロ・ウインク など)を1枚ずつ描き、一覧で見られるようにする。

キャラの描き分けの確認(漫画で使う前に、その表情・角度がちゃんと描けるか)と、LoRA の学習データ(キャプション付き)の
両方に使う。キャラシートの基準シードで描くので、項目ごとの違いは向き・表情だけになりやすい。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaceItem:
    key: str
    label: str
    # 足すタグ(構図を含む)
    tags: str
    # angle: 顔の向き・姿勢 / expression: 表情
    group: str
    # 生成サイズ(portrait 832×1216 / square 1024×1024)
    size: str = "square"


_PORTRAIT = "portrait, close-up, looking at viewer"

ANGLES: tuple[FaceItem, ...] = (
    FaceItem("front", "正面", "front view, facing viewer, looking at viewer, upper body", "angle"),
    FaceItem("three_quarter", "斜め", "three-quarter view, looking at viewer, upper body", "angle"),
    FaceItem("side", "側面", "from side, profile, upper body", "angle"),
    FaceItem("back", "背面", "from behind, back view, facing away, upper body", "angle"),
    FaceItem("look_back", "見返り", "from behind, looking back, looking over shoulder, upper body", "angle"),
    FaceItem("from_above", "見下ろし(上から)", "from above, looking up at viewer, upper body", "angle"),
    FaceItem("from_below", "見上げ(下から)", "from below, looking down at viewer, upper body", "angle"),
    FaceItem("upside_down", "逆立ち", "handstand, upside-down, full body, arms on ground", "angle", "portrait"),
)

EXPRESSIONS: tuple[FaceItem, ...] = (
    FaceItem("smile", "喜(笑顔)", f"{_PORTRAIT}, smile, happy", "expression"),
    FaceItem("laugh", "大笑い", f"{_PORTRAIT}, laughing, open mouth, closed eyes", "expression"),
    FaceItem("angry", "怒", f"{_PORTRAIT}, angry, frown, clenched teeth", "expression"),
    FaceItem("furious", "激怒", f"{_PORTRAIT}, furious, angry, shouting, anger vein", "expression"),
    FaceItem("sad", "哀(悲しみ)", f"{_PORTRAIT}, sad, downcast eyes, frown", "expression"),
    FaceItem("crying", "号泣", f"{_PORTRAIT}, crying, streaming tears, open mouth", "expression"),
    FaceItem("relaxed", "楽(くつろぎ)", f"{_PORTRAIT}, relaxed, gentle smile, half-closed eyes", "expression"),
    FaceItem("embarrassed", "羞恥(恥ずかしい)", f"{_PORTRAIT}, embarrassed, full-face blush, wavy mouth", "expression"),
    FaceItem("shy", "照れ", "blush, shy, looking away, small smile, portrait, close-up", "expression"),
    FaceItem("surprised", "驚き", f"{_PORTRAIT}, surprised, wide-eyed, open mouth", "expression"),
    FaceItem("scared", "怯え", f"{_PORTRAIT}, scared, trembling, tears in eyes", "expression"),
    FaceItem("troubled", "困り顔", f"{_PORTRAIT}, troubled, worried, raised eyebrows, sweatdrop", "expression"),
    FaceItem("jitome", "ジト目", f"{_PORTRAIT}, half-closed eyes, annoyed, deadpan", "expression"),
    FaceItem("smug", "ドヤ顔", f"{_PORTRAIT}, smug, confident smile, closed mouth", "expression"),
    FaceItem("pout", "拗ね(ふくれっ面)", f"{_PORTRAIT}, pout, puffed cheeks, blush", "expression"),
    FaceItem("sleepy", "眠そう", f"{_PORTRAIT}, sleepy, half-closed eyes, yawning", "expression"),
    FaceItem("expressionless", "無表情", f"{_PORTRAIT}, expressionless, closed mouth", "expression"),
    FaceItem("determined", "決意", f"{_PORTRAIT}, determined, serious, v-shaped eyebrows", "expression"),
    FaceItem("smile_tears", "泣き笑い", f"{_PORTRAIT}, crying, tears, smile, happy tears", "expression"),
    FaceItem("tehepero", "テヘペロ", f"{_PORTRAIT}, tongue out, one eye closed, hand on own head, :p", "expression"),
    FaceItem("wink", "ウインク", f"{_PORTRAIT}, one eye closed, wink, smile, v", "expression"),
    FaceItem("dreamy", "うっとり", f"{_PORTRAIT}, dreamy, half-closed eyes, blush, gentle smile", "expression"),
    FaceItem("nervous", "苦笑い", f"{_PORTRAIT}, nervous smile, sweatdrop, awkward", "expression"),
    FaceItem("disgust", "嫌悪", f"{_PORTRAIT}, disgust, frown, looking down on viewer", "expression"),
)

ITEMS: dict[str, FaceItem] = {item.key: item for item in (*ANGLES, *EXPRESSIONS)}

# 顔・表情シートの背景(絵ごとの違いを向き・表情だけにするため、無地にする)
BACKGROUND = "simple background, white background"
SIZES = {"square": (1024, 1024), "portrait": (832, 1216)}


def presets() -> list[dict[str, str]]:
    return [{"key": i.key, "label": i.label, "group": i.group, "tags": i.tags} for i in ITEMS.values()]
