"""
漫画v2: コマに嵌めた絵の中の人物の範囲(体)。吹き出しを、頭だけでなく体にも掛からない所へ置くために使う。

人物の切り分けは anime_seg(skytnt/anime-seg)。モデルを取ってきていない環境では何もしない
(合成のたびに 176MB を取りに行かないように。うごイラの人物の切り出しを一度使えば入る)。
同じ絵・同じ切り抜きは覚えておき、合成し直しても切り分け直さない。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from .. import anime_seg

Rect = tuple[int, int, int, int]
BodyCover = Callable[[Rect], float]

# 覚えておく切り分けの数(1話のコマ数より多め)
_CACHE_SIZE = 256
_cache: OrderedDict[tuple, np.ndarray] = OrderedDict()


def available() -> bool:
    """人物の切り分けのモデルが手元にあるか。"""
    return (anime_seg._MODEL_DIR / anime_seg.MODEL_FILE).is_file()


def _mask(picture: Image.Image, key: tuple) -> np.ndarray:
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    mask = anime_seg.person_mask(picture) > 0.5
    _cache[key] = mask
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return mask


def body_cover(picture: Image.Image, origin: tuple[int, int], image_path: Path, crop: tuple) -> BodyCover | None:
    """
    コマに嵌めた絵(picture、左上がページの origin)の人物の範囲から、ページ上の四角のうち体に掛かる割合
    (0〜1)を返す関数を作る。crop は切り抜き方(拡大率・左端・上端など。覚えておく鍵に使う)。
    モデルが無い・切り分けに失敗したときは None(体は避けない)。
    """
    if not available():
        return None
    try:
        key = (str(image_path), image_path.stat().st_mtime_ns, picture.size, crop)
        mask = _mask(picture.convert("RGB"), key)
    except Exception:  # noqa: BLE001 - 体を避けられなくても合成は止めない
        logging.getLogger(__name__).warning("人物の範囲を出せませんでした: %s", image_path, exc_info=True)
        return None
    return cover_function(mask, origin)


def cover_function(mask: np.ndarray, origin: tuple[int, int]) -> BodyCover:
    """人物の範囲(True が体)から、ページ上の四角が体に掛かる割合を返す関数。"""
    ox, oy = origin
    height, width = mask.shape
    # 積分画像で、どの四角の面積も一定の速さで数える
    integral = np.zeros((height + 1, width + 1), np.int64)
    integral[1:, 1:] = mask.astype(np.int64).cumsum(0).cumsum(1)

    def cover(box: Rect) -> float:
        x0, y0 = min(max(box[0] - ox, 0), width), min(max(box[1] - oy, 0), height)
        x1, y1 = min(max(box[2] - ox, 0), width), min(max(box[3] - oy, 0), height)
        area = (box[2] - box[0]) * (box[3] - box[1])
        if area <= 0 or x1 <= x0 or y1 <= y0:
            return 0.0
        inside = integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
        return float(inside) / area

    return cover
