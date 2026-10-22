"""
漫画v2: コマの枠より絵がはみ出すときの切り抜き位置を、頭(顔)が入るように決める(compose._face_crop / _cover)のテスト。

実行方法:
  uv run pytest tests/test_face_crop.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.manga_v2.compose import _cover, _face_crop  # noqa: E402


def test_default_crop_is_kept_when_heads_fit() -> None:
    # 頭が既定の切り抜き(上寄り)に収まるなら、そのまま
    assert _face_crop([(400, 100, 500, 200)], 1.0, (1000, 1000), 1000, 400, 0, 150) == (0, 0)
    assert _face_crop([(400, 200, 500, 300)], 1.0, (1000, 1000), 1000, 400, 0, 150) == (0, 150)


def test_crop_moves_to_a_low_head() -> None:
    # 頭が下の方にあれば、頭の範囲の真ん中に寄せる
    left, top = _face_crop([(400, 700, 500, 800)], 1.0, (1000, 1000), 1000, 400, 0, 150)
    assert left == 0 and top <= 700 and top + 400 >= 800


def test_close_up_keeps_the_eyes_not_just_the_mouth() -> None:
    # 顔のアップ(頭が絵いっぱい)を横長の枠に入れるとき、目の高さ(頭の上から 45%)が枠に入る
    _, top = _face_crop([(0, 0, 1000, 1000)], 1.0, (1000, 1000), 1000, 400, 0, 150)
    eye = 450
    assert top < eye < top + 400


def test_cover_uses_heads_for_the_normal_panel() -> None:
    image = Image.new("RGB", (1000, 1000), (255, 255, 255))
    _, scale, left, top = _cover(image, 1000, 300, 0, [(400, 800, 500, 900)])
    assert scale == 1.0 and top + 300 >= 900 and top <= 800
