"""
漫画v2: 吹き出しを人物の体に掛からない所へ置く(manga_v2/bodies.py と compose._place の body)のテスト。
人物の切り分け(anime-seg)は使わず、体の範囲を直接与える。

実行方法:
  uv run pytest tests/test_bubble_bodies.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.manga_v2 import bodies, compose  # noqa: E402
from python.manga_v2.bodies import cover_function  # noqa: E402
from python.manga_v2.lettering import DEFAULT_SFX_FONT_ID, resolve_font  # noqa: E402

PANEL = (0, 0, 600, 800)


def _body_right_half() -> np.ndarray:
    """コマの右半分(上から下まで)に人物がいる。"""
    mask = np.zeros((800, 600), bool)
    mask[:, 300:] = True
    return mask


def test_cover_function_measures_the_overlap() -> None:
    cover = cover_function(_body_right_half(), (100, 50))
    assert cover((100, 50, 200, 150)) == 0.0
    assert cover((400, 50, 500, 150)) == 1.0
    assert cover((350, 50, 450, 150)) == pytest.approx(0.5)
    # コマの外にはみ出す分は体ではない
    assert cover((650, 50, 750, 150)) == pytest.approx(0.5)


def test_place_moves_the_bubble_off_the_body() -> None:
    body = cover_function(_body_right_half(), (0, 0))
    # 体を避けないと、右上(読み始め)に置く
    plain, _ = compose._place((120, 200), PANEL, [])
    assert plain[0] >= 300
    box, free = compose._place((120, 200), PANEL, [], body=body)
    assert free and body(box) <= compose._BODY_LIMIT
    assert box[2] <= 300


def test_place_keeps_the_reading_order() -> None:
    body = cover_function(_body_right_half(), (0, 0))
    first = (40, 400, 160, 600)  # 先の吹き出しは左下
    box, _ = compose._place((120, 150), PANEL, [first], body=body, earlier=[first])
    # 先の吹き出しより上・右には置かない(体に掛からない空きは左上にあるが、読む順が逆になる)
    assert compose._reads_after(box, [first])


def test_place_with_speaker_prefers_off_body() -> None:
    body = cover_function(_body_right_half(), (0, 0))
    # 話し手は右にいても、体の上ではなく左の空きに置く
    box, free = compose._place((120, 200), PANEL, [], prefer_x=450, body=body, earlier=[], rivals=[])
    assert free and body(box) <= compose._BODY_LIMIT


def test_lettering_avoids_bodies_only_when_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = tmp_path / "panel.png"
    Image.new("RGB", (600, 800), (200, 220, 255)).save(image_path)
    monkeypatch.setattr(compose, "detect_heads", lambda path: [])
    monkeypatch.setattr(compose, "body_cover", lambda picture, origin, path, crop: cover_function(_body_right_half(), origin))
    content = compose.PanelContent(image_path, ["ここは右の人の上に乗りやすいセリフ"], [], key="1")
    fonts = resolve_font(None), resolve_font(None, DEFAULT_SFX_FONT_ID)

    page = Image.new("RGB", (600, 800), "white")
    plain = compose._draw_panel(page, PANEL, content, compose.LetteringStyle(*fonts))
    avoided = compose._draw_panel(page, PANEL, content, compose.LetteringStyle(*fonts, avoid_bodies=True))
    body = cover_function(_body_right_half(), (0, 0))
    assert body(plain[0].box) > compose._BODY_LIMIT
    assert body(avoided[0].box) <= compose._BODY_LIMIT


def test_no_model_means_no_avoidance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bodies.anime_seg, "_MODEL_DIR", tmp_path / "missing")
    image_path = tmp_path / "p.png"
    Image.new("RGB", (10, 10)).save(image_path)
    assert bodies.body_cover(Image.open(image_path), (0, 0), image_path, (1.0, 0, 0, 0)) is None
