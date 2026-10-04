"""
描き文字スタンプ(効果音の手描き文字素材)の取り込み。

素材集は「透過PNGの1枚に数十個の擬音を並べたシート」の形で配られていることが多いので、
シートを1語ずつのスタンプに切り分けて data/stamps/ に保存する。手描きの崩し文字は
ローカルの画像モデルでは読めない(実機で8個中1個しか読めなかった)ので、読みは付けず、
使う側で「この効果音にはこのスタンプ」と割り当てる。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
from PIL import Image
from scipy import ndimage

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STAMP_DIR = _PROJECT_ROOT / "data" / "stamps"

# 透明な部分がこの割合以上ある画像だけを素材シートとみなす(表紙や使い方の説明ページを除く)
_MIN_TRANSPARENT_RATIO = 0.5
_ALPHA_THRESHOLD = 40
# 同じ語の線をまとめる距離(シート幅1000px換算)。縦書きなので縦方向を広く取る。
# 実機の素材シートで、縦28/横10だと上下の別の語まで繋がり、縦14/横6でほぼ1語ずつに分かれた。
_GROUP_GAP_Y = 14
_GROUP_GAP_X = 6
# これより小さい塊(シート幅1000px換算の面積)は点やゴミとして捨てる
_MIN_INK = 40 * 40 * 0.3
_PADDING = 6

_PIXIV_ARTWORK_RE = re.compile(r"pixiv\.net/(?:[a-z]{2}/)?artworks/(\d+)")
_PIXIV_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.pixiv.net/"}


@dataclass
class SheetSource:
    """取り込み元。クレジット表示に使う。"""

    key: str
    title: str
    author: str
    url: str


def is_stamp_sheet(image: Image.Image) -> bool:
    alpha = np.asarray(image.convert("RGBA"))[..., 3]
    return float((alpha == 0).mean()) >= _MIN_TRANSPARENT_RATIO


def split_sheet(image: Image.Image) -> list[Image.Image]:
    """
    透過シートを、近い線どうしをまとめた塊ごとに切り出す(上の行から、行の中は右から)。

    塊の判定は幅1000pxに縮めた画像で行い(原寸の 3514x4999 で大きな構造要素の膨張をすると
    遅すぎる)、切り出しは原寸で行う。
    """
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    factor = rgba.width / 1000
    small_size = (1000, max(1, round(rgba.height / factor)))
    small = np.asarray(alpha.resize(small_size, Image.Resampling.BOX)) > _ALPHA_THRESHOLD // 4
    merged = ndimage.binary_dilation(
        small, structure=np.ones((_GROUP_GAP_Y * 2 + 1, _GROUP_GAP_X * 2 + 1), dtype=bool)
    )
    labels, _ = ndimage.label(merged)
    full_ink = np.asarray(alpha) > _ALPHA_THRESHOLD
    boxes: list[tuple[int, int, int, int]] = []
    for region in ndimage.find_objects(labels):
        if region is None:
            continue
        ys, xs = region
        if small[region].sum() < _MIN_INK:
            continue
        # 縮小画像での範囲を原寸に戻し、その中のインクの外接矩形を取り直す
        fx0, fy0 = int(xs.start * factor), int(ys.start * factor)
        fx1, fy1 = min(int(xs.stop * factor) + 1, rgba.width), min(int(ys.stop * factor) + 1, rgba.height)
        local = full_ink[fy0:fy1, fx0:fx1]
        if not local.any():
            continue
        yy, xx = np.nonzero(local)
        boxes.append((fx0 + int(xx.min()), fy0 + int(yy.min()), fx0 + int(xx.max()) + 1, fy0 + int(yy.max()) + 1))
    row = max(1, int(150 * factor))
    boxes.sort(key=lambda b: (b[1] // row, -b[0]))
    pad = max(1, int(_PADDING * factor))
    return [
        rgba.crop((max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, rgba.width), min(y1 + pad, rgba.height)))
        for x0, y0, x1, y1 in boxes
    ]


def pixiv_artwork_id(url: str) -> str | None:
    match = _PIXIV_ARTWORK_RE.search(url)
    return match.group(1) if match else None


async def fetch_pixiv_sheets(artwork_id: str) -> tuple[SheetSource, list[Image.Image]]:
    """pixiv の作品の全ページを取得し、素材シート(透過画像)だけを返す。"""
    async with httpx.AsyncClient(headers=_PIXIV_HEADERS, timeout=120, follow_redirects=True) as client:
        info = (await client.get(f"https://www.pixiv.net/ajax/illust/{artwork_id}")).json()
        if info.get("error"):
            raise ValueError(info.get("message") or "作品情報を取得できませんでした。")
        body = info["body"]
        pages = (await client.get(f"https://www.pixiv.net/ajax/illust/{artwork_id}/pages")).json()["body"]
        sheets: list[Image.Image] = []
        for page in pages:
            response = await client.get(page["urls"]["original"])
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content))
            image.load()
            if is_stamp_sheet(image):
                sheets.append(image)
    source = SheetSource(
        key=f"pixiv-{artwork_id}",
        title=body.get("title", ""),
        author=body.get("userName", ""),
        url=f"https://www.pixiv.net/artworks/{artwork_id}",
    )
    return source, sheets
