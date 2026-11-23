"""
生成済みの絵の一部(長方形の範囲)だけを、NovelAI のインペイントで描き直すための下ごしらえと仕上げ。

- build_mask: 範囲(x, y, 幅, 高さ)を白く塗ったマスクを作る。NovelAI はマスクを 8 ピクセル単位で読むので、
  範囲を外側へ 8 の倍数にそろえる(内側に丸めると、描き直したい物の端が残る)。
- composite: NovelAI のインペイントは、マスクの外もわずかに描き直す(1986→1992 で平均 2.6、約 4.6 万ピクセルが変わった)。
  描き直した絵からマスクの中だけを元の絵に貼り戻し、範囲の外は元のピクセルのまま残す。
  境目は feather ピクセルだけぼかしてなじませる(ぼかしはマスクの内側だけに入れ、外側は変えない)。
"""

from __future__ import annotations

from collections.abc import Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

GRID = 8
# 範囲が小さすぎると描き直しても形にならない。大きすぎるならインペイントではなく描き直しを使う
MIN_SIDE = 16
MAX_AREA_RATIO = 0.8
MAX_REGIONS = 8


class RegionError(ValueError):
    """範囲の指定がおかしい(画像の外・小さすぎる・大きすぎる)。"""


Box = tuple[int, int, int, int]


def snap_box(box: Sequence[float], size: tuple[int, int]) -> Box:
    """範囲(x, y, 幅, 高さ)を画像の中に収め、外側へ GRID の倍数にそろえる。返すのは (左, 上, 右, 下)。"""
    if len(box) != 4:
        raise RegionError("範囲は [x, y, 幅, 高さ] の 4 つの数で指定してください")
    x, y, w, h = (float(v) for v in box)
    if w <= 0 or h <= 0:
        raise RegionError("範囲の幅と高さは 1 以上にしてください")
    width, height = size
    left = max(0, int(x) // GRID * GRID)
    top = max(0, int(y) // GRID * GRID)
    right = min(width, -(-int(x + w) // GRID) * GRID)
    bottom = min(height, -(-int(y + h) // GRID) * GRID)
    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        raise RegionError(f"範囲が小さすぎるか、画像の外です({MIN_SIDE} ピクセル以上にしてください)")
    return left, top, right, bottom


def build_mask(size: tuple[int, int], regions: Sequence[Sequence[float]]) -> tuple[Image.Image, list[Box]]:
    """範囲を白(255)、それ以外を黒(0)にしたマスク(L)と、そろえた範囲を返す。"""
    if not regions:
        raise RegionError("描き直す範囲を 1 つ以上指定してください")
    if len(regions) > MAX_REGIONS:
        raise RegionError(f"範囲は {MAX_REGIONS} 個までです")
    boxes = [snap_box(region, size) for region in regions]
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for left, top, right, bottom in boxes:
        draw.rectangle((left, top, right - 1, bottom - 1), fill=255)
    covered = sum(mask.histogram()[1:])
    if covered > size[0] * size[1] * MAX_AREA_RATIO:
        raise RegionError("範囲が広すぎます(絵の 8 割以上)。インペイントではなく描き直しを使ってください")
    return mask, boxes


def composite(original: Image.Image, generated: Image.Image, mask: Image.Image, feather: int = 4) -> Image.Image:
    """
    generated のマスクの中だけを original に貼る。マスクの外は original のピクセルのまま。
    feather > 0 なら、マスクの内側の縁 feather ピクセルを元の絵となめらかにつなぐ。
    """
    base = original.convert("RGB")
    over = generated.convert("RGB")
    if over.size != base.size:
        over = over.resize(base.size, Image.Resampling.LANCZOS)
    alpha = mask.convert("L")
    if feather > 0:
        # 内側へ縮めてからぼかすと、ぼかしがマスクの外へはみ出さない
        shrunk = alpha.filter(ImageFilter.MinFilter(feather * 2 + 1))
        alpha = ImageChops.multiply(shrunk.filter(ImageFilter.GaussianBlur(feather / 2)), alpha)
    return Image.composite(over, base, alpha)
