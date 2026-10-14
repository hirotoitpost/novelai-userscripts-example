"""
部位の範囲(インペイントで描き直す範囲のマスク)を、絵の中の頭の位置と大きさから見積もる。

キャラ別データセットの状態(食べ過ぎでお腹が出る など)を、ふだんの体の絵から、その部位だけを描き直して作るのに使う
(顔・服・背景が同じで、体だけが違う組ができる)。頭の検出は manga_v2/detect.py(色付きのイラストでも見つかる。
漫画用の体の検出 manga_elements は、色付きのイラストでは体を見つけられなかった)。

範囲は、頭の下端からの距離と左右の広がりを、頭の高さ・幅を単位にして決める。立っている・座っている絵を想定していて、
寝ている絵や、頭が画面の外にある絵では外れる(見つからなければ None を返し、呼び出し側はプロンプトだけで作る)。
docs/trials/2026-10-15_dataset-body-parts.md を参照。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# 部位: (頭の下端から上端まで, 下端まで[頭の高さの倍数], 左右の広がり[頭の幅の倍数、中心から片側], 画面での名前)
PARTS: dict[str, tuple[float, float, float, str]] = {
    "belly": (0.9, 2.5, 1.3, "お腹"),
    "chest": (0.2, 1.5, 1.3, "胸"),
    "torso": (0.1, 2.8, 1.5, "上半身(首から腰)"),
    "legs": (2.4, 6.0, 1.4, "脚"),
}
# マスクの境目をなじませるぼかし(ピクセル)
_FEATHER = 12


def part_box(head: tuple[float, float, float, float], part: str, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """頭の枠(x0, y0, x1, y1)から、部位の範囲。画面の外に出る分は切る。範囲が無くなれば None。"""
    if part not in PARTS:
        return None
    top, bottom, half, _ = PARTS[part]
    x0, y0, x1, y1 = head
    w, h = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2
    box = (
        int(max(cx - half * w, 0)),
        int(max(y1 + top * h, 0)),
        int(min(cx + half * w, size[0])),
        int(min(y1 + bottom * h, size[1])),
    )
    return box if box[2] > box[0] and box[3] > box[1] else None


def find_head(image: Image.Image) -> tuple[float, float, float, float] | None:
    """いちばん大きく写っている頭の枠。見つからなければ None。"""
    from .manga_v2.detect import detect_heads

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "image.png"
        image.save(path)
        heads = detect_heads(path)
    if not heads:
        return None
    return max(heads, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))


def part_mask(image: Image.Image, part: str) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """部位のマスク(白=描き直す)と範囲。頭が見つからない・範囲が無ければ None。"""
    head = find_head(image)
    if head is None:
        return None
    box = part_box(head, part, image.size)
    if box is None:
        return None
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(_FEATHER)).point(lambda v: 255 if v > 40 else 0)
    return mask.convert("RGB"), box


def face_crop(image: Image.Image, *, margin: float = 0.12, min_side: int = 256) -> Image.Image | None:
    """
    顔の切り抜き(データセットに足す顔のアップ)。頭の枠を、まわりに margin の割合だけ広げた正方形で切る。
    頭が見つからない・切り抜きが min_side より小さければ None(小さい顔を引き伸ばしても学習の役に立たない)。
    """
    head = find_head(image)
    if head is None:
        return None
    x0, y0, x1, y1 = head
    side = max(x1 - x0, y1 - y0) * (1 + 2 * margin)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    side = min(side, image.width, image.height)
    left = int(min(max(cx - side / 2, 0), image.width - side))
    top = int(min(max(cy - side / 2, 0), image.height - side))
    if side < min_side:
        return None
    return image.crop((left, top, left + int(side), top + int(side)))
