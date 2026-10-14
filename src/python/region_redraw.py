"""
ギャラリーの拡大表示で見ている範囲を、高解像度で描き直す(切り出して img2img)。

元の画像から範囲を切り出し、縦横比を保って生成の大きさ(1MP 以内・64 の倍数)まで AI 拡大(upscaler.py)してから、
弱めの強さで img2img する。構図や色は元のまま、線と細部が描き直される。Opus なら 1MP・28 ステップ以内は Anlas がかからない。

プロンプトは、元の画像の設定(メタデータ)のプロンプトから、絵全体のための構図・人数のタグを外し、切り出した範囲を
判定モデル(リバースプロンプトと同じもの)で読んだタグを足す。キャラごとのプロンプトは、位置を指定して描いた画像なら
範囲の中にいるキャラだけを、範囲の中の位置に直して残す。位置が AI まかせの画像は、キャラが1人のときだけ残す。
"""

from __future__ import annotations

import math
import re
from typing import Any

from .character_sheet import _plain, split_tags

# 生成の大きさの上限(1MP。Opus で Anlas がかからない範囲)と、切り出しを大きくする倍率の上限(大きくしすぎると、
# 描き直しが強く出て元の絵から離れる)
MAX_PIXELS = 1024 * 1024
MAX_ZOOM = 4.0
# これより大きくならない範囲(ほとんど拡大していない)は、描き直しても解像度が上がらないので断る
MIN_GAIN = 1.25
# 位置の指定(NovelAI のキャラの位置は 0.1〜0.9 の 5 段)
_GRID = (0.1, 0.3, 0.5, 0.7, 0.9)
# 絵全体のための構図・人数のタグ(切り出した範囲には合わない。人数と構図は判定モデルの結果を使う)
_WHOLE_PICTURE = re.compile(
    r"^(\d\+?(girl|boy|other)s?|multiple (girls|boys|others)|solo|no humans|"
    r"full body|cowboy shot|upper body|portrait|close-up|wide shot|very wide shot|lower body|"
    r"from (above|below|side|behind)|dutch angle|panorama|landscape)$"
)


class RegionError(ValueError):
    """範囲が小さすぎる・大きすぎるなど、描き直せない範囲。"""


def clamp_box(box: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """範囲(x, y, 幅, 高さ)を画像の中に収める。"""
    x, y, w, h = box
    x = min(max(int(x), 0), size[0] - 1)
    y = min(max(int(y), 0), size[1] - 1)
    w = min(max(int(w), 1), size[0] - x)
    h = min(max(int(h), 1), size[1] - y)
    return x, y, w, h


def target_size(width: int, height: int) -> tuple[int, int]:
    """切り出した範囲を描き直す大きさ(縦横比を保ち、1MP 以内・64 の倍数・倍率は MAX_ZOOM まで)。"""
    zoom = min(math.sqrt(MAX_PIXELS / (width * height)), MAX_ZOOM)
    w = max(64, int(width * zoom) // 64 * 64)
    h = max(64, int(height * zoom) // 64 * 64)
    if w * h / (width * height) < MIN_GAIN**2:
        raise RegionError("もっと拡大してから使ってください(いまの範囲は、描き直しても解像度がほとんど上がりません)")
    return w, h


def upscale_size(width: int, height: int) -> tuple[int, int]:
    """AI 拡大だけのときの大きさ(4倍まで、4MP まで。NovelAI を使わないので 1MP の上限は無い)。"""
    zoom = min(math.sqrt(4 * MAX_PIXELS / (width * height)), MAX_ZOOM)
    if zoom < MIN_GAIN:
        raise RegionError("もっと拡大してから使ってください(いまの範囲は、拡大しても解像度がほとんど上がりません)")
    return max(1, round(width * zoom)), max(1, round(height * zoom))


def _snap(value: float) -> float:
    return min(_GRID, key=lambda g: abs(g - value))


def region_characters(
    settings: dict[str, Any] | None, box: tuple[int, int, int, int], size: tuple[int, int]
) -> list[dict[str, Any]]:
    """範囲の中にいるキャラのプロンプト(位置は範囲の中の位置に直す)。"""
    characters = [c for c in (settings or {}).get("characters") or [] if str(c.get("prompt") or "").strip()]
    if not characters:
        return []
    if not (settings or {}).get("use_coords"):
        # 位置が AI まかせなら、どのキャラが範囲にいるか分からない。1人なら、写っているのはその人
        return [{"prompt": characters[0]["prompt"], "negative_prompt": characters[0].get("negative") or ""}] if len(characters) == 1 else []
    x, y, w, h = box
    kept = []
    for c in characters:
        if c.get("x") is None or c.get("y") is None:
            continue
        px, py = float(c["x"]) * size[0], float(c["y"]) * size[1]
        if x <= px <= x + w and y <= py <= y + h:
            kept.append({
                "prompt": c["prompt"],
                "negative_prompt": c.get("negative") or "",
                "position": (_snap((px - x) / w), _snap((py - y) / h)),
            })
    return kept


def region_prompt(base_prompt: str, crop_tags: str) -> str:
    """元のプロンプトから絵全体のための構図・人数のタグを外し、切り出した範囲で読んだタグを前に足す。"""
    kept = [t for t in split_tags(base_prompt) if not _WHOLE_PICTURE.match(_plain(t))]
    seen: set[str] = set()
    tags = []
    for tag in [*split_tags(crop_tags), *kept]:
        key = _plain(tag)
        if key and key not in seen:
            seen.add(key)
            tags.append(tag)
    return ", ".join(tags)
