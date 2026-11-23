"""インペイントの範囲(inpaint_regions.py)。NovelAI は呼ばない。"""

from __future__ import annotations

import pytest
from PIL import Image

from python import inpaint_regions as ir


def test_snap_box_expands_outward_to_grid_and_clamps() -> None:
    assert ir.snap_box([3, 10, 20, 21], (832, 1216)) == (0, 8, 24, 32)
    # 画像の外へはみ出した分は切る
    assert ir.snap_box([800, 1200, 100, 100], (832, 1216)) == (800, 1200, 832, 1216)
    with pytest.raises(ir.RegionError):
        ir.snap_box([820, 1210, 100, 100], (832, 1216))  # 収めると 12x6 で小さすぎる
    with pytest.raises(ir.RegionError):
        ir.snap_box([0, 0, 0, 10], (832, 1216))
    with pytest.raises(ir.RegionError):
        ir.snap_box([0, 0, 10], (832, 1216))


def test_build_mask_marks_only_regions() -> None:
    mask, boxes = ir.build_mask((64, 64), [[8, 8, 16, 16], [40, 40, 16, 16]])
    assert boxes == [(8, 8, 24, 24), (40, 40, 56, 56)]
    assert mask.getpixel((8, 8)) == 255 and mask.getpixel((23, 23)) == 255
    assert mask.getpixel((24, 24)) == 0 and mask.getpixel((7, 7)) == 0
    assert sum(mask.histogram()[1:]) == 2 * 16 * 16


def test_build_mask_rejects_empty_and_too_large() -> None:
    with pytest.raises(ir.RegionError):
        ir.build_mask((64, 64), [])
    with pytest.raises(ir.RegionError):
        ir.build_mask((64, 64), [[0, 0, 64, 64]])


def test_composite_keeps_outside_pixels_exactly() -> None:
    original = Image.new("RGB", (64, 64), (10, 20, 30))
    generated = Image.new("RGB", (64, 64), (200, 100, 50))
    mask, _ = ir.build_mask((64, 64), [[16, 16, 32, 32]])
    out = ir.composite(original, generated, mask, feather=4)
    for x in range(64):
        for y in range(64):
            inside = 16 <= x < 48 and 16 <= y < 48
            if not inside:
                assert out.getpixel((x, y)) == (10, 20, 30)
    # 真ん中は描き直した絵、縁はなじませる途中の色
    assert out.getpixel((32, 32)) == (200, 100, 50)
    assert out.getpixel((16, 32)) != (200, 100, 50)


def test_composite_without_feather_is_hard_edge() -> None:
    original = Image.new("RGB", (32, 32), (0, 0, 0))
    generated = Image.new("RGB", (32, 32), (255, 255, 255))
    mask, _ = ir.build_mask((32, 32), [[8, 8, 16, 16]])
    out = ir.composite(original, generated, mask, feather=0)
    assert out.getpixel((8, 8)) == (255, 255, 255)
    assert out.getpixel((7, 8)) == (0, 0, 0)
