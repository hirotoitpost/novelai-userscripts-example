"""
漫画v2: 吹き出しのしっぽの長さのテスト。先端は吹き出しの中心と口元を結ぶ線の上で、顔の輪郭(無ければ口元)の手前で止まり、
長くなりすぎない(上限)・短くなりすぎない(下限)。

実行方法:
  uv run pytest tests/test_bubble_tail.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.manga_v2 import lettering  # noqa: E402
from python.manga_v2.compose import _face_reach  # noqa: E402
from python.manga_v2.lettering import fit_text, resolve_font, tail_length  # noqa: E402

BOX = (200, 200, 320, 440)  # 横 120・縦 240 → 短い方の半径 60、中心 (260, 320)


def test_tail_length_reaches_toward_the_stop_with_limits() -> None:
    # 止める所が遠い → 上限(半径の 0.9 = 54)で止まる
    assert tail_length(60, 120, 60, 400) == 54
    # ほどほど → 止める所まで
    assert tail_length(60, 120, 60, 90) == 30
    # すぐそば → 下限(半径の 0.15 = 9)は残す
    assert tail_length(60, 120, 60, 62) == 9
    # 大きな吹き出しでも、ピクセルの上限で止まる
    assert tail_length(200, 300, 200, 2000) == lettering._TAIL_MAX_PX


def test_face_reach_stops_outside_the_face_outline() -> None:
    head = (400, 220, 560, 420)  # 頭の箱。口元は (480, 360)
    box = (200, 200, 320, 440)  # 中心 (260, 320)
    reach = _face_reach(box, (480, 360), [head])
    assert reach is not None
    # 線は頭の箱の左端(x=400)より手前で止まり、余白(短い辺 160 の 0.12 ≒ 19px)を空ける
    stop_x = 260 + reach * (220 / (220**2 + 40**2) ** 0.5)
    assert 370 < stop_x < 400
    # 口元を含む頭が無ければ None、中心が顔の中なら 0
    assert _face_reach(box, (480, 360), []) is None
    assert _face_reach((440, 280, 520, 330), (480, 360), [head]) == 0.0


def _tip_x(target: tuple[float, float]) -> int:
    """右へ向けたしっぽの、いちばん右の黒い画素の x。"""
    image = Image.new("RGB", (900, 640), "white")
    font = resolve_font(None)
    block = fit_text("やあ", 80, 200, 24, 16)
    lettering.draw_bubble(image, BOX, block, font, tail_toward=target)
    dark = np.asarray(image.convert("L")) < 128
    rows = dark[310:331]  # 中心の高さ(320)のまわりだけ見る
    return int(np.nonzero(rows.any(axis=0))[0].max())


def test_tail_tip_stops_before_a_near_mouth() -> None:
    # 口元が縁(x=320)の 40px 先 → 先端は口元の手前(30px)、口元は越えない
    tip = _tip_x((360, 320))
    assert 340 <= tip < 360


def test_tail_tip_is_capped_for_a_far_mouth() -> None:
    tip = _tip_x((880, 320))
    assert tip <= 320 + 54 + lettering._OUTLINE
    assert tip >= 320 + 54 - lettering._OUTLINE * 2
