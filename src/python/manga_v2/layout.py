"""ページのコマ割りテンプレートと、コマの形に合わせた生成サイズの決定。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# 出力ページの大きさ(B判に近い縦横比)。セリフのフォントサイズ等もこの幅を基準にしている。
PAGE_WIDTH = 1200
PAGE_HEIGHT = 1700
PAGE_MARGIN = 50
# コマ間の余白。日本の漫画に合わせて、横の間隔より縦の間隔を広く取る。
GUTTER_X = 16
GUTTER_Y = 28

Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    # 本文領域を単位正方形とした各コマの (x0, y0, x1, y1)。読む順(右上から左へ、上から下へ)。
    panels: tuple[tuple[float, float, float, float], ...]


TEMPLATES: dict[str, Template] = {
    t.id: t
    for t in (
        Template(
            "vertical4",
            "縦4コマ",
            tuple((0.0, i / 4, 1.0, (i + 1) / 4) for i in range(4)),
        ),
        Template(
            "grid4",
            "2×2の4コマ",
            ((0.5, 0.0, 1.0, 0.5), (0.0, 0.0, 0.5, 0.5), (0.5, 0.5, 1.0, 1.0), (0.0, 0.5, 0.5, 1.0)),
        ),
        Template(
            "tri3",
            "上に大ゴマ+下に2コマ",
            ((0.0, 0.0, 1.0, 0.55), (0.5, 0.55, 1.0, 1.0), (0.0, 0.55, 0.5, 1.0)),
        ),
        Template("stack2", "上下2コマ", ((0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 1.0, 1.0))),
        Template("single1", "1ページ1コマ", ((0.0, 0.0, 1.0, 1.0),)),
    )
}

# 最後のページでコマが余るときに使うテンプレート(コマ数 → テンプレート)。空きゴマを残さず、
# 最後のコマを大きく見せる(締めのコマは大ゴマの方が収まりが良い)。
_FILL_TEMPLATES = {1: "single1", 2: "stack2", 3: "tri3"}


def fill_template(template_id: str, n_panels: int) -> str:
    if n_panels >= len(TEMPLATES[template_id].panels):
        return template_id
    return _FILL_TEMPLATES.get(n_panels, template_id)


def panel_rects(template_id: str) -> list[Rect]:
    """テンプレートのコマをページ上のピクセル座標にする。内側の辺だけ余白の半分ずつ削る。"""
    template = TEMPLATES[template_id]
    content_w = PAGE_WIDTH - PAGE_MARGIN * 2
    content_h = PAGE_HEIGHT - PAGE_MARGIN * 2
    rects: list[Rect] = []
    for x0, y0, x1, y1 in template.panels:
        rects.append(
            (
                PAGE_MARGIN + round(x0 * content_w) + (GUTTER_X // 2 if x0 > 0 else 0),
                PAGE_MARGIN + round(y0 * content_h) + (GUTTER_Y // 2 if y0 > 0 else 0),
                PAGE_MARGIN + round(x1 * content_w) - (GUTTER_X // 2 if x1 < 1 else 0),
                PAGE_MARGIN + round(y1 * content_h) - (GUTTER_Y // 2 if y1 < 1 else 0),
            )
        )
    return rects


# ---- コマの形とページ ----

Point = tuple[int, int]


@dataclass(frozen=True)
class PanelShape:
    """
    ページ上のコマ1つ。rect は外接矩形(絵を嵌め込む範囲)、points は輪郭(左上から時計回りの4点)。
    斜めの枠は points が rect の角からずれる。border が False なら枠線を描かない(枠なしのコマ)。
    ページの端に接する辺(裁ち落とし)にも枠線は描かない。
    """

    rect: Rect
    points: tuple[Point, ...]
    border: bool = True

    @property
    def is_rect(self) -> bool:
        x0, y0, x1, y1 = self.rect
        return self.points == ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def rect_shape(rect: Rect, border: bool = True) -> PanelShape:
    x0, y0, x1, y1 = rect
    return PanelShape(rect, ((x0, y0), (x1, y0), (x1, y1), (x0, y1)), border)


@dataclass(frozen=True)
class PageSpec:
    """合成する1ページ: 大きさ(見開きは横2ページ分)と、読む順のコマ。"""

    width: int
    height: int
    panels: tuple[PanelShape, ...]


def template_page(template_id: str) -> PageSpec:
    return PageSpec(PAGE_WIDTH, PAGE_HEIGHT, tuple(rect_shape(r) for r in panel_rects(template_id)))


# ---- 取り込んだ作品から写したコマ割り ----

# ページごとのコマ割り。古い形はコマの (x0, y0, x1, y1)(ページの幅・高さに対する割合)を読む順に並べたもの。
# 今の形は {"spread": 見開きか, "panels": [{"points": [[x, y], ×4](割合), "border": 枠線の有無}]}。
PageLayout = Sequence[Sequence[float]] | Mapping[str, Any]


def layout_page(layout: PageLayout) -> PageSpec:
    """写したコマ割りを出力ページのピクセル座標にする(余白も元のページの割合のまま)。"""
    if not isinstance(layout, Mapping):
        rects = [
            (round(x0 * PAGE_WIDTH), round(y0 * PAGE_HEIGHT), round(x1 * PAGE_WIDTH), round(y1 * PAGE_HEIGHT))
            for x0, y0, x1, y1 in layout
        ]
        return PageSpec(PAGE_WIDTH, PAGE_HEIGHT, tuple(rect_shape(r) for r in rects))
    width = PAGE_WIDTH * 2 if layout.get("spread") else PAGE_WIDTH
    shapes = []
    for panel in layout.get("panels") or []:
        points = tuple((round(x * width), round(y * PAGE_HEIGHT)) for x, y in panel["points"])
        xs, ys = [x for x, _ in points], [y for _, y in points]
        shapes.append(PanelShape((min(xs), min(ys), max(xs), max(ys)), points, bool(panel.get("border", True))))
    return PageSpec(width, PAGE_HEIGHT, tuple(shapes))


def layout_rects(layout: PageLayout) -> list[Rect]:
    """写したコマ割りの各コマの外接矩形(ピクセル座標)。"""
    return [shape.rect for shape in layout_page(layout).panels]


def layout_size(layout: PageLayout) -> int:
    """そのページのコマ数。"""
    return len(layout.get("panels") or []) if isinstance(layout, Mapping) else len(layout)


def normalize_boxes(
    boxes: Sequence[Sequence[int]], page_width: int, page_height: int
) -> list[tuple[float, float, float, float]]:
    """取り込んだページのコマ (x, y, 幅, 高さ)(ピクセル)を、ページに対する割合のコマ割り(古い形)にする。"""
    layout: list[tuple[float, float, float, float]] = []
    for x, y, w, h in boxes:
        x0, y0 = max(x / page_width, 0.0), max(y / page_height, 0.0)
        x1, y1 = min((x + w) / page_width, 1.0), min((y + h) / page_height, 1.0)
        if x1 > x0 and y1 > y0:
            layout.append((round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)))
    return layout


def scene_rects(layouts: Sequence[PageLayout], template_id: str, count: int) -> list[Rect]:
    """
    シーン順のコマの形(ピクセル座標)。写したコマ割りを先頭のページから順に使い、足りない分は
    テンプレートのコマで続ける。コマの生成サイズを決めるのに使う。
    """
    rects = [rect for layout in layouts for rect in layout_rects(layout)]
    template = panel_rects(template_id)
    while len(rects) < count:
        rects.append(template[(len(rects) - sum(layout_size(layout) for layout in layouts)) % len(template)])
    return rects[:count]


def panels_per_page(template_id: str) -> int:
    return len(TEMPLATES[template_id].panels)


# 生成サイズの上限。Opusプランでは 1024×1024 相当の画素数・28ステップ以下・1枚なら
# 通常モデルの生成がAnlasを消費しない。コマ単位なら大きな画像は要らないので、この範囲に収める。
_MAX_PIXELS = 1024 * 1024
# 極端に細長い画像は構図が破綻しやすいので、生成時の縦横比はこの範囲に丸め、あとで切り抜く。
_MIN_ASPECT = 0.4
_MAX_ASPECT = 2.5


def generation_size(rect: Rect) -> tuple[int, int]:
    """コマの形に近い、64の倍数の生成サイズ(幅, 高さ)。"""
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    aspect = min(max(w / h, _MIN_ASPECT), _MAX_ASPECT)
    gen_h = math.sqrt(_MAX_PIXELS / aspect)
    gen_w = gen_h * aspect
    return int(gen_w // 64) * 64, int(gen_h // 64) * 64
