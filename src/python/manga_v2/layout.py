"""ページのコマ割りテンプレートと、コマの形に合わせた生成サイズの決定。"""

from __future__ import annotations

import json
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
    return _content_rects(TEMPLATES[template_id].panels)


def _content_rects(panels: Sequence[tuple[float, float, float, float]]) -> list[Rect]:
    """本文領域を単位正方形としたコマを、ページ上のピクセル座標にする。内側の辺だけ余白の半分ずつ削る。"""
    content_w = PAGE_WIDTH - PAGE_MARGIN * 2
    content_h = PAGE_HEIGHT - PAGE_MARGIN * 2
    rects: list[Rect] = []
    for x0, y0, x1, y1 in panels:
        rects.append(
            (
                PAGE_MARGIN + round(x0 * content_w) + (GUTTER_X // 2 if x0 > 0 else 0),
                PAGE_MARGIN + round(y0 * content_h) + (GUTTER_Y // 2 if y0 > 0 else 0),
                PAGE_MARGIN + round(x1 * content_w) - (GUTTER_X // 2 if x1 < 1 else 0),
                PAGE_MARGIN + round(y1 * content_h) - (GUTTER_Y // 2 if y1 < 1 else 0),
            )
        )
    return rects


# ---- コマ数に合わせたコマ割り(ページごとにコマ数を変える漫画) ----

# コマ数 → コマ割りの候補(本文領域を単位正方形とした (x0, y0, x1, y1)。読む順: 右上から左へ、上から下へ)。
# 同じコマ数が続いても単調にならないよう、ページごとに候補を順に使う(dynamic_layout の variant)。
# 1コマ目(導入)か最後のコマ(オチ)を大きくする型を混ぜる。
_COUNT_LAYOUTS: dict[int, tuple[tuple[tuple[float, float, float, float], ...], ...]] = {
    1: (((0.0, 0.0, 1.0, 1.0),),),
    2: (
        ((0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 1.0, 1.0)),
        ((0.0, 0.0, 1.0, 0.38), (0.0, 0.38, 1.0, 1.0)),
    ),
    3: (
        ((0.0, 0.0, 1.0, 0.55), (0.5, 0.55, 1.0, 1.0), (0.0, 0.55, 0.5, 1.0)),
        ((0.5, 0.0, 1.0, 0.45), (0.0, 0.0, 0.5, 0.45), (0.0, 0.45, 1.0, 1.0)),
        ((0.0, 0.0, 1.0, 0.3), (0.0, 0.3, 1.0, 0.6), (0.0, 0.6, 1.0, 1.0)),
    ),
    4: (
        ((0.5, 0.0, 1.0, 0.5), (0.0, 0.0, 0.5, 0.5), (0.5, 0.5, 1.0, 1.0), (0.0, 0.5, 0.5, 1.0)),
        ((0.0, 0.0, 1.0, 0.4), (0.5, 0.4, 1.0, 0.7), (0.0, 0.4, 0.5, 0.7), (0.0, 0.7, 1.0, 1.0)),
        ((0.4, 0.0, 1.0, 0.3), (0.0, 0.0, 0.4, 0.3), (0.0, 0.3, 1.0, 0.6), (0.0, 0.6, 1.0, 1.0)),
    ),
    5: (
        ((0.0, 0.0, 1.0, 0.3), (0.5, 0.3, 1.0, 0.6), (0.0, 0.3, 0.5, 0.6), (0.5, 0.6, 1.0, 1.0), (0.0, 0.6, 0.5, 1.0)),
        ((0.45, 0.0, 1.0, 0.3), (0.0, 0.0, 0.45, 0.3), (0.55, 0.3, 1.0, 0.6), (0.0, 0.3, 0.55, 0.6), (0.0, 0.6, 1.0, 1.0)),
    ),
    6: (
        tuple((x, y / 3, x + 0.5, (y + 1) / 3) for y in range(3) for x in (0.5, 0.0)),
        ((0.0, 0.0, 1.0, 0.25), (0.5, 0.25, 1.0, 0.5), (0.0, 0.25, 0.5, 0.5), (0.5, 0.5, 1.0, 0.75), (0.0, 0.5, 0.5, 0.75),
         (0.0, 0.75, 1.0, 1.0)),
    ),
}
# ページごとに変えられるコマ数の範囲
MIN_DYNAMIC_PANELS = 1
MAX_DYNAMIC_PANELS = max(_COUNT_LAYOUTS)


def dynamic_layout(count: int, variant: int = 0) -> list[list[float]]:
    """
    コマ数 count のページのコマ割り(取り込みと同じ古い形: ページの幅・高さに対する割合の (x0, y0, x1, y1) を読む順に)。
    variant で候補を変える(ページの番号を渡すと、同じコマ数が続いても型が変わる)。
    """
    if not MIN_DYNAMIC_PANELS <= count <= MAX_DYNAMIC_PANELS:
        raise ValueError(f"コマ数は {MIN_DYNAMIC_PANELS}〜{MAX_DYNAMIC_PANELS} にしてください({count})。")
    options = _COUNT_LAYOUTS[count]
    rects = _content_rects(options[variant % len(options)])
    return [
        [round(x0 / PAGE_WIDTH, 4), round(y0 / PAGE_HEIGHT, 4), round(x1 / PAGE_WIDTH, 4), round(y1 / PAGE_HEIGHT, 4)]
        for x0, y0, x1, y1 in rects
    ]


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
    # 枠線の色(RGB)と太さ(ピクセル。0 なら描かない)
    border_color: tuple[int, int, int] = (0, 0, 0)
    border_width: int = 4
    # 重なり順(大きいほど前面)。同じなら読む順の後ろが前
    z: int = 0
    # 背景を消して人物だけを見せる(normalize_cutout の形)。None なら絵をそのまま
    cutout: Mapping[str, Any] | None = None

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
    # ページの地の色(余白・コマの間隔に見える色)
    background: tuple[int, int, int] = (255, 255, 255)


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
    style = normalize_style(layout.get("style"))
    shapes = []
    for panel in layout.get("panels") or []:
        points = tuple((round(x * width), round(y * PAGE_HEIGHT)) for x, y in panel["points"])
        xs, ys = [x for x, _ in points], [y for _, y in points]
        shapes.append(PanelShape(
            (min(xs), min(ys), max(xs), max(ys)),
            points,
            bool(panel.get("border", True)),
            hex_rgb(panel.get("border_color") or style["border_color"]),
            int(panel.get("border_width", style["border_width"])),
            int(panel.get("z", 0)),
            panel.get("cutout"),
        ))
    return PageSpec(width, PAGE_HEIGHT, tuple(shapes), hex_rgb(style["background"]))


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


def panels_per_page(template_id: str) -> int:
    return len(TEMPLATES[template_id].panels)


# コマの生成サイズ(幅, 高さ)。NovelAI の通常サイズの3種類で、Opusプランなら(28ステップ以下・1枚)
# Anlas を使わずに生成できる。コマの形に一番近いものを使い、合成でコマの形に切り抜く。
PANEL_SIZES: dict[str, tuple[int, int]] = {
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "square": (1024, 1024),
}
# 縦長/正方形、正方形/横長の境目(コマの幅÷高さ)。隣り合うサイズの縦横比の中間(幾何平均)
_PORTRAIT_BELOW = math.sqrt(832 / 1216)
_LANDSCAPE_ABOVE = math.sqrt(1216 / 832)


def panel_size_kind(rect: Rect) -> str:
    """コマの形(外接矩形)に一番近い生成サイズの種類(portrait / landscape / square)。"""
    aspect = (rect[2] - rect[0]) / max(rect[3] - rect[1], 1)
    if aspect < _PORTRAIT_BELOW:
        return "portrait"
    if aspect > _LANDSCAPE_ABOVE:
        return "landscape"
    return "square"


# ---- 分割の木で表すコマ割り(パターンの生成・ドラッグでの大きさの調整) ----
#
# コマ割りを「領域を縦(段)か横(列)に分ける」の入れ子で表す。葉 "panel" が1コマ。
#   {"dir": "rows", "sizes": [0.4, 0.6], "items": ["panel", {"dir": "cols", "sizes": [0.5, 0.5], "items": [...]}]}
# rows は上から下へ、cols は右から左へ(日本の漫画の読む順)。items の順がそのまま読む順になる。
# コマの境目をドラッグすると、その親の sizes の隣り合う2つが変わる。tilts は境目ごとの傾き(斜めの境目で、
# 両側のコマが同じ角度の台形になる)。葉は形(楕円・三角形・自由な多角形)と枠線の有無も持てる。

LayoutTree = Any  # 葉("panel" か {"shape", ...})か {"dir", "sizes", "items", "tilts"?}

PANEL = "panel"
# 分けた1つの最小の割合(細すぎるコマにならないように)・木の深さ・1ページのコマ数の上限
MIN_SHARE = 0.08
_MAX_DEPTH = 4
MAX_TREE_PANELS = 12


def is_leaf(tree: LayoutTree) -> bool:
    """葉(1コマ)か。"panel" か、形・枠線を決めた {"shape": ...}。"""
    return tree == PANEL or (isinstance(tree, Mapping) and "dir" not in tree)


def tree_count(tree: LayoutTree) -> int:
    return 1 if is_leaf(tree) else sum(tree_count(item) for item in tree["items"])


def tree_leaves(tree: LayoutTree) -> list[LayoutTree]:
    """葉を読む順に。"""
    return [tree] if is_leaf(tree) else [leaf for item in tree["items"] for leaf in tree_leaves(item)]


def tree_rects(tree: LayoutTree, box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)) -> list[tuple[float, float, float, float]]:
    """木のコマを、本文領域を単位正方形とした (x0, y0, x1, y1) にして読む順に並べる(境目の傾き・コマの形は見ない)。"""
    if is_leaf(tree):
        return [box]
    x0, y0, x1, y1 = box
    rects: list[tuple[float, float, float, float]] = []
    start = 0.0
    for index, (share, item) in enumerate(zip(tree["sizes"], tree["items"])):
        # 最後の1つは端まで(丸めた割合の合計が1からずれても、はみ出したり隙間ができたりしないように)
        end = 1.0 if index == len(tree["items"]) - 1 else start + share
        if tree["dir"] == "rows":
            child = (x0, y0 + (y1 - y0) * start, x1, y0 + (y1 - y0) * end)
        else:  # cols: 右から左へ
            child = (x1 - (x1 - x0) * end, y0, x1 - (x1 - x0) * start, y1)
        rects.extend(tree_rects(item, tuple(round(v, 4) for v in child)))  # type: ignore[arg-type]
        start = end
    return rects


# コマの形(葉の "shape")。rect は枠いっぱい(境目が斜めならその台形)、ellipse は枠に内接する楕円、
# triangle は枠に内接する三角形(point の向きに尖る)、polygon は枠に対する割合の頂点(自由な多角形・手書きの輪郭)
PANEL_SHAPES = ("rect", "ellipse", "triangle", "polygon")
_TRIANGLE_DIRS = ("up", "down", "left", "right")
# 境目の傾きの上限(分ける向きの長さに対する、端での上下/左右のずれ)と、自由な多角形の頂点数の上限
MAX_TILT = 0.25
MAX_POLYGON_POINTS = 400


def _normalize_leaf(leaf: Any) -> LayoutTree:
    if leaf == PANEL:
        return PANEL
    shape = leaf.get("shape", "rect")
    if shape not in PANEL_SHAPES:
        raise ValueError(f"コマの形が正しくありません: {shape}")
    node: dict[str, Any] = {"shape": shape}
    if shape == "triangle":
        direction = leaf.get("point", "up")
        if direction not in _TRIANGLE_DIRS:
            raise ValueError("三角形の向きが正しくありません。")
        node["point"] = direction
    if shape == "polygon":
        points = leaf.get("points") or []
        if not 3 <= len(points) <= MAX_POLYGON_POINTS:
            raise ValueError(f"多角形の頂点は 3〜{MAX_POLYGON_POINTS} にしてください。")
        node["points"] = [[round(min(max(float(x), 0.0), 1.0), 4), round(min(max(float(y), 0.0), 1.0), 4)] for x, y in points]
    if leaf.get("border", True) is False:
        node["border"] = False
    # このコマだけの枠線の色・太さ(無ければページの設定)
    if leaf.get("border_color") is not None:
        node["border_color"] = _color(leaf["border_color"])
    if leaf.get("border_width") is not None:
        node["border_width"] = _border_width(leaf["border_width"])
    # 重なり順(0 が既定。前面へ・背面へで変える)
    if leaf.get("z"):
        node["z"] = _z(leaf["z"])
    # 背景を消す(前面に重ねたコマで、人物だけを見せる)
    if leaf.get("cutout"):
        node["cutout"] = normalize_cutout(leaf["cutout"])
    # 何も決めていなければ、ふつうのコマ
    return PANEL if node == {"shape": "rect"} else node


def normalize_tree(tree: Any, depth: int = 0) -> LayoutTree:
    """
    送られた木を検査して整える(割合は合計1・最小 MIN_SHARE、傾きは ±MAX_TILT まで)。おかしな形は ValueError。
    子が1つだけの分割は、その子にする。
    """
    if tree == PANEL or (isinstance(tree, Mapping) and "dir" not in tree and "items" not in tree):
        return _normalize_leaf(tree)
    if not isinstance(tree, Mapping) or tree.get("dir") not in ("rows", "cols"):
        raise ValueError("コマ割りの形が正しくありません。")
    if depth >= _MAX_DEPTH:
        raise ValueError("コマ割りの入れ子が深すぎます。")
    items = list(tree.get("items") or [])
    sizes = [float(s) for s in tree.get("sizes") or []]
    if not items or len(items) != len(sizes) or any(s <= 0 for s in sizes):
        raise ValueError("コマ割りの割合が正しくありません。")
    if len(items) == 1:
        return normalize_tree(items[0], depth)
    total = sum(sizes)
    shares = [max(s / total, MIN_SHARE) for s in sizes]
    total = sum(shares)
    shares = [round(s / total, 4) for s in shares]
    node: dict[str, Any] = {
        "dir": tree["dir"],
        "sizes": shares,
        "items": [normalize_tree(item, depth + 1) for item in items],
    }
    tilts = [float(t) for t in tree.get("tilts") or []]
    if tilts:
        if len(tilts) != len(items) - 1:
            raise ValueError("境目の傾きの数が正しくありません。")
        # 隣のコマが端で細くなりすぎない(つぶれない)範囲に収める
        limits = [min(MAX_TILT, (min(shares[i], shares[i + 1]) - MIN_SHARE / 2) / 2) for i in range(len(tilts))]
        tilts = [round(min(max(t, -limit), limit), 4) for t, limit in zip(tilts, limits)]
        if any(tilts):
            node["tilts"] = tilts
    if tree_count(node) > MAX_TREE_PANELS:
        raise ValueError(f"1ページのコマは {MAX_TREE_PANELS} までです。")
    return node


# ---- 木から、ページの上のコマの輪郭を作る ----

Polygon = list[tuple[float, float]]


def _clip(polygon: Polygon, a: tuple[float, float], b: tuple[float, float], keep_left: bool) -> Polygon:
    """凸多角形を、点 a→b の直線の片側(進む向きの左なら keep_left)だけに切る。"""
    def side(p: tuple[float, float]) -> float:
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

    def inside(p: tuple[float, float]) -> bool:
        return side(p) <= 0 if keep_left else side(p) >= 0

    result: Polygon = []
    for i, current in enumerate(polygon):
        previous = polygon[i - 1]
        if inside(current):
            if not inside(previous):
                result.append(_cross(previous, current, side))
            result.append(current)
        elif inside(previous):
            result.append(_cross(previous, current, side))
    return result


def _cross(p: tuple[float, float], q: tuple[float, float], side: Any) -> tuple[float, float]:
    sp, sq = side(p), side(q)
    t = sp / (sp - sq)
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _bounds(polygon: Polygon) -> tuple[float, float, float, float]:
    xs = [x for x, _ in polygon]
    ys = [y for _, y in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _tree_polygons(
    tree: LayoutTree,
    region: Polygon,
    box: tuple[float, float, float, float],
    gutters: tuple[int, int] = (GUTTER_X, GUTTER_Y),
) -> list[tuple[Polygon, LayoutTree, tuple[float, float, float, float]]]:
    """
    木のコマを、ページの上の輪郭(ピクセル)・葉・そのコマの傾きの無いときの枠の組にして読む順に並べる。box は分割の基準の枠(本文領域から
    たどった、傾きの無いときの枠)。境目は box の上の直線で、両側をコマの間隔の半分ずつ空ける(傾きが無ければ
    _content_rects と同じ位置)。
    """
    if is_leaf(tree):
        return [(region, tree, box)]
    gutter_x, gutter_y = gutters
    x0, y0, x1, y1 = box
    count = len(tree["items"])
    tilts = tree.get("tilts") or [0.0] * (count - 1)
    result: list[tuple[Polygon, LayoutTree, tuple[float, float, float, float]]] = []
    start = 0.0
    for index, (share, item) in enumerate(zip(tree["sizes"], tree["items"])):
        end = 1.0 if index == count - 1 else start + share
        piece = region
        if tree["dir"] == "rows":
            gap = gutter_y // 2
            # 上の境目(下へ間隔の半分)・下の境目(上へ間隔の半分)で切る。傾きは左端が下がる向きが正
            if index > 0:
                t = tilts[index - 1]
                ya, yb = round(y0 + (y1 - y0) * (start + t)), round(y0 + (y1 - y0) * (start - t))
                piece = _clip(piece, (x0, ya + gap), (x1, yb + gap), keep_left=False)
            if index < count - 1:
                t = tilts[index]
                ya, yb = round(y0 + (y1 - y0) * (end + t)), round(y0 + (y1 - y0) * (end - t))
                piece = _clip(piece, (x0, ya - gap), (x1, yb - gap), keep_left=True)
            child = (x0, y0 + (y1 - y0) * start, x1, y0 + (y1 - y0) * end)
        else:  # cols: 右から左へ。傾きは上端が左へ寄る向きが正
            gap = gutter_x // 2
            if index > 0:
                t = tilts[index - 1]
                xa, xb = round(x1 - (x1 - x0) * (start + t)), round(x1 - (x1 - x0) * (start - t))
                piece = _clip(piece, (xa - gap, y0), (xb - gap, y1), keep_left=False)
            if index < count - 1:
                t = tilts[index]
                xa, xb = round(x1 - (x1 - x0) * (end + t)), round(x1 - (x1 - x0) * (end - t))
                piece = _clip(piece, (xa + gap, y0), (xb + gap, y1), keep_left=True)
            child = (x1 - (x1 - x0) * end, y0, x1 - (x1 - x0) * start, y1)
        result.extend(_tree_polygons(item, piece, child, gutters))
        start = end
    return result


def _leaf_outline(leaf: LayoutTree, region: Polygon, cell: tuple[float, float, float, float]) -> Polygon:
    """
    葉の形の輪郭。rect は区切られた領域そのもの。それ以外は、そのコマの傾きの無いときの枠(と領域の外接矩形の重なり)に
    収めてから、領域で切る(斜めの境目の隣のコマや、コマの間隔にはみ出さないように)。
    """
    shape = leaf.get("shape", "rect") if isinstance(leaf, Mapping) else "rect"
    if shape == "rect":
        return region
    rx0, ry0, rx1, ry1 = _bounds(region)
    x0, y0, x1, y1 = max(rx0, cell[0]), max(ry0, cell[1]), min(rx1, cell[2]), min(ry1, cell[3])
    if x1 - x0 < 1 or y1 - y0 < 1:
        return region
    outline = _shape_outline(leaf, shape, x0, y0, x1, y1)
    for a, b in zip(region, region[1:] + region[:1]):
        outline = _clip(outline, a, b, keep_left=False) if _is_clockwise(region) else _clip(outline, a, b, keep_left=True)
        if len(outline) < 3:
            return region
    return outline


def _is_clockwise(polygon: Polygon) -> bool:
    """画面の座標(y が下向き)で、時計回りに並んでいるか。"""
    return sum((bx - ax) * (by + ay) for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1])) < 0


def _shape_outline(leaf: Mapping[str, Any], shape: str, x0: float, y0: float, x1: float, y1: float) -> Polygon:
    w, h = x1 - x0, y1 - y0
    if shape == "ellipse":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return [(cx + w / 2 * math.cos(2 * math.pi * i / 64), cy + h / 2 * math.sin(2 * math.pi * i / 64)) for i in range(64)]
    if shape == "triangle":
        return {
            "up": [((x0 + x1) / 2, y0), (x1, y1), (x0, y1)],
            "down": [(x0, y0), (x1, y0), ((x0 + x1) / 2, y1)],
            "left": [(x0, (y0 + y1) / 2), (x1, y0), (x1, y1)],
            "right": [(x0, y0), (x1, (y0 + y1) / 2), (x0, y1)],
        }[leaf.get("point", "up")]
    return [(x0 + w * x, y0 + h * y) for x, y in leaf["points"]]


# ページのコマの間隔と余白(ピクセル)。0 にするとコマどうしが接する・ページの端まで(裁ち落とし)になる
DEFAULT_GAPS: dict[str, int] = {"gutter_x": GUTTER_X, "gutter_y": GUTTER_Y, "margin": PAGE_MARGIN}
_MAX_GUTTER = 120
_MAX_MARGIN = 200


def normalize_gaps(gaps: Mapping[str, Any] | None) -> dict[str, int]:
    """間隔・余白を整数にして範囲に収める(無い項目は既定の値)。"""
    result = dict(DEFAULT_GAPS)
    for key, limit in (("gutter_x", _MAX_GUTTER), ("gutter_y", _MAX_GUTTER), ("margin", _MAX_MARGIN)):
        if gaps is not None and gaps.get(key) is not None:
            result[key] = min(max(int(round(float(gaps[key]))), 0), limit)
    return result


# ページの見た目: 地の色(余白・コマの間隔)と、枠線の色・太さ(コマごとに変えられる)
DEFAULT_STYLE: dict[str, Any] = {"background": "#ffffff", "border_color": "#000000", "border_width": 4}
_MAX_BORDER_WIDTH = 20


def _color(value: Any) -> str:
    text = str(value).strip().lower()
    if len(text) != 7 or text[0] != "#" or any(c not in "0123456789abcdef" for c in text[1:]):
        raise ValueError(f"色は #rrggbb で指定してください: {value}")
    return text


def _border_width(value: Any) -> int:
    return min(max(int(round(float(value))), 0), _MAX_BORDER_WIDTH)


def hex_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def normalize_style(style: Mapping[str, Any] | None) -> dict[str, Any]:
    """地の色・枠線の色と太さを検査して整える(無い項目は既定の値)。おかしな色は ValueError。"""
    result = dict(DEFAULT_STYLE)
    if style:
        for key in ("background", "border_color"):
            if style.get(key) is not None:
                result[key] = _color(style[key])
        if style.get("border_width") is not None:
            result["border_width"] = _border_width(style["border_width"])
    return result


def tree_outlines(tree: LayoutTree, gaps: Mapping[str, Any] | None = None) -> list[tuple[Polygon, bool]]:
    """木のコマの輪郭(ページのピクセル)と枠線の有無を、読む順に。gaps はコマの間隔と余白(無ければ既定)。"""
    g = normalize_gaps(gaps)
    margin = g["margin"]
    content = (margin, margin, PAGE_WIDTH - margin, PAGE_HEIGHT - margin)
    region: Polygon = [(content[0], content[1]), (content[2], content[1]), (content[2], content[3]), (content[0], content[3])]
    result = []
    for polygon, leaf, cell in _tree_polygons(tree, region, content, (g["gutter_x"], g["gutter_y"])):
        border = not (isinstance(leaf, Mapping) and leaf.get("border") is False)
        result.append((_tidy(_leaf_outline(leaf, polygon, cell)), border))
    return result


def _tidy(polygon: Polygon) -> Polygon:
    """
    切り分けで重なった頂点を除き、傾きの無い長方形は左上から時計回りの4点にそろえる(PanelShape.is_rect で
    ふつうのコマとして描かれるように)。
    """
    points: Polygon = []
    for x, y in polygon:
        point = (round(x, 2), round(y, 2))
        if not points or point != points[-1]:
            points.append(point)
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    xs = sorted({round(x) for x, _ in points})
    ys = sorted({round(y) for _, y in points})
    if len(xs) == 2 and len(ys) == 2:
        return [(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])]
    return points


# ---- 重ねて置くコマ(コマ割りのマス目とは別に、ページの好きな位置に置く) ----

MAX_FLOATS = 8
_MIN_FLOAT_SIZE = 0.04
_MAX_Z = 99


def _z(value: Any) -> int:
    return min(max(int(value), -_MAX_Z), _MAX_Z)


def normalize_floats(floats: Sequence[Mapping[str, Any]] | None, grid_count: int) -> list[dict[str, Any]]:
    """
    重ねて置くコマを検査して整える。rect はページに対する割合の (x0, y0, x1, y1)、after は読む順でマス目の何コマ目の
    後ろか(0 なら先頭)、z は重なり順(既定 1。マス目のコマの前)。形・枠線は葉と同じ項目。
    """
    result: list[dict[str, Any]] = []
    for item in floats or []:
        if len(result) >= MAX_FLOATS:
            raise ValueError(f"重ねて置くコマは1ページに {MAX_FLOATS} までです。")
        rect = [min(max(float(v), 0.0), 1.0) for v in item.get("rect") or []]
        if len(rect) != 4 or rect[2] - rect[0] < _MIN_FLOAT_SIZE or rect[3] - rect[1] < _MIN_FLOAT_SIZE:
            raise ValueError("重ねて置くコマの位置・大きさが正しくありません。")
        leaf = _normalize_leaf({k: v for k, v in item.items() if k not in ("rect", "after", "z")} or PANEL)
        node: dict[str, Any] = {} if leaf == PANEL else dict(leaf)
        node["rect"] = [round(v, 4) for v in rect]
        node["after"] = min(max(int(item.get("after", grid_count)), 0), grid_count)
        node["z"] = _z(item.get("z", 1))
        result.append(node)
    return result


def _float_outline(item: Mapping[str, Any]) -> Polygon:
    x0, y0, x1, y1 = item["rect"]
    box = (x0 * PAGE_WIDTH, y0 * PAGE_HEIGHT, x1 * PAGE_WIDTH, y1 * PAGE_HEIGHT)
    shape = item.get("shape", "rect")
    if shape == "rect":
        return [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
    return _shape_outline(item, shape, *box)


def tree_layout(
    tree: LayoutTree,
    gaps: Mapping[str, Any] | None = None,
    style: Mapping[str, Any] | None = None,
    floats: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    木のコマ割りを、ページごとのコマ割り(manga_v2_page_layouts の今の形)にする。合成は panels の輪郭を使い、
    画面で直せるように木と間隔・余白・見た目・重ねて置くコマ(既定と違うときだけ)も一緒に持つ。
    panels は読む順: マス目のコマの間に、重ねて置くコマを after の位置で入れる。
    """
    g = normalize_gaps(gaps)
    look = normalize_style(style)
    grid = []
    for (outline, border), leaf in zip(tree_outlines(tree, g), tree_leaves(tree)):
        grid.append(_panel_entry(outline, border, leaf if isinstance(leaf, Mapping) else {}))
    extra = normalize_floats(floats, len(grid))
    panels: list[dict[str, Any]] = []
    for index in range(len(grid) + 1):
        for item in extra:
            if item["after"] == index:
                panels.append(_panel_entry(_tidy(_float_outline(item)), item.get("border") is not False, item))
        if index < len(grid):
            panels.append(grid[index])
    layout: dict[str, Any] = {"spread": False, "panels": panels, "tree": tree}
    if extra:
        layout["floats"] = extra
    if g != DEFAULT_GAPS:
        layout["gaps"] = g
    if look != DEFAULT_STYLE:
        layout["style"] = look
    return layout


_MAX_OUTLINE = 40


CUTOUT_FITS = ("cover", "contain")


def normalize_cutout(value: Any) -> dict[str, Any]:
    """
    背景を消す設定: 縁取り(outline)の有無・色・太さ(ピクセル)、収め方(fit: cover は枠いっぱいに拡大してはみ出しを切る、
    contain は人物の写っている範囲を枠に収めて足元を下にそろえる)、下端をぼかすか(fade)。True だけなら既定の設定。
    """
    options = value if isinstance(value, Mapping) else {}
    fit = options.get("fit") or "cover"
    if fit not in CUTOUT_FITS:
        raise ValueError(f"収め方が正しくありません: {fit}")
    return {
        "outline": bool(options.get("outline", False)),
        "outline_color": _color(options.get("outline_color") or "#ffffff"),
        "outline_width": min(max(int(round(float(options.get("outline_width", 8)))), 1), _MAX_OUTLINE),
        "fit": fit,
        "fade": bool(options.get("fade", False)),
    }


def _panel_entry(outline: Polygon, border: bool, props: Mapping[str, Any]) -> dict[str, Any]:
    panel: dict[str, Any] = {"points": [[round(x / PAGE_WIDTH, 4), round(y / PAGE_HEIGHT, 4)] for x, y in outline], "border": border}
    for key in ("border_color", "border_width", "z", "cutout"):
        if props.get(key):
            panel[key] = props[key]
    return panel


def _rows(counts: Sequence[int], heights: Sequence[float] | None = None) -> LayoutTree:
    """段ごとのコマ数(上から)の木。heights が無ければ同じ高さ。"""
    items = [PANEL if n == 1 else {"dir": "cols", "sizes": [1 / n] * n, "items": [PANEL] * n} for n in counts]
    if len(items) == 1:
        return items[0]
    shares = list(heights) if heights else [1.0] * len(items)
    total = sum(shares)
    return {"dir": "rows", "sizes": [round(s / total, 4) for s in shares], "items": items}


def _compositions(n: int, parts: int, most: int) -> list[tuple[int, ...]]:
    """n を parts 個の 1〜most の和に分ける並び(上の段から)。"""
    if parts == 1:
        return [(n,)] if 1 <= n <= most else []
    return [(first, *rest) for first in range(1, most + 1) for rest in _compositions(n - first, parts - 1, most)]


def layout_patterns(count: int) -> list[dict[str, Any]]:
    """
    コマ数 count のページのコマ割りの候補(id・名前・木)。段に分けて各段を1〜3コマにする形を、段のコマ数が
    そろったものから並べ、1つの段を大きくした形と、右に縦長の大ゴマを置く形を加える。
    """
    if not 1 <= count <= MAX_TREE_PANELS:
        raise ValueError(f"コマ数は 1〜{MAX_TREE_PANELS} にしてください({count})。")
    if count == 1:
        return [{"id": "single", "label": "1ページ1コマ", "tree": PANEL}]
    ideal = max(1, round(math.sqrt(count * 1.5)))
    plans = [
        c for rows in range(1, min(count, 5) + 1) for c in _compositions(count, rows, 3)
    ]
    plans.sort(key=lambda c: (abs(len(c) - ideal), max(c) - min(c), c))
    patterns: list[dict[str, Any]] = []
    for c in plans[:8]:
        counts = "・".join(str(n) for n in c)
        patterns.append({"id": "r" + "-".join(map(str, c)), "label": f"{len(c)}段({counts})", "tree": _rows(c)})
    # 1つの段を大きく: コマの少ない段(導入・見せ場)を広くする
    for c in plans[:3]:
        if len(c) < 2:
            continue
        big = c.index(min(c))
        heights = [1.7 if i == big else 1.0 for i in range(len(c))]
        counts = "・".join(str(n) for n in c)
        patterns.append({
            "id": "r" + "-".join(map(str, c)) + f"-big{big}",
            "label": f"{len(c)}段({counts})・{big + 1}段目を大きく",
            "tree": _rows(c, heights),
        })
    # 右に縦長の大ゴマ、左に残りを積む(縦の見せゴマ)
    if 2 <= count <= 5:
        left = PANEL if count == 2 else {"dir": "rows", "sizes": [1 / (count - 1)] * (count - 1), "items": [PANEL] * (count - 1)}
        patterns.append({
            "id": f"tall-right-{count}",
            "label": f"右に縦長1コマ+左に{count - 1}コマ",
            "tree": {"dir": "cols", "sizes": [0.55, 0.45], "items": [PANEL, left]},
        })
    seen: set[str] = set()
    unique = []
    for pattern in patterns:
        if pattern["id"] not in seen:
            seen.add(pattern["id"])
            pattern["tree"] = normalize_tree(pattern["tree"])
            unique.append(pattern)
    return unique


# テンプレートと同じ形の木(テンプレートのページを、画面で直せるコマ割りにするとき)
TEMPLATE_TREES: dict[str, LayoutTree] = {
    "vertical4": _rows((1, 1, 1, 1)),
    "grid4": _rows((2, 2)),
    "tri3": _rows((1, 2), (0.55, 0.45)),
    "stack2": _rows((1, 1)),
    "single1": PANEL,
}


# ---- 絵の縦横比に合わせたコマ割り ----

# 段の高さの下限・上限(本文の高さに対する割合)。これを外れる案は出さない
_MIN_ROW = 0.12
_MAX_ROW = 0.8


def frame_aspects(tree: LayoutTree, gaps: Mapping[str, Any] | None = None) -> list[float]:
    """木のコマの枠の縦横比(幅/高さ)を読む順に。間隔・余白はページの既定(または gaps)で引く。"""
    g = normalize_gaps(gaps)
    content_w = PAGE_WIDTH - g["margin"] * 2
    content_h = PAGE_HEIGHT - g["margin"] * 2
    result = []
    for x0, y0, x1, y1 in tree_rects(tree):
        # 内側の辺は、間隔の半分ずつ狭まる
        width = (x1 - x0) * content_w - g["gutter_x"] / 2 * ((x0 > 0) + (x1 < 1))
        height = (y1 - y0) * content_h - g["gutter_y"] / 2 * ((y0 > 0) + (y1 < 1))
        result.append(max(width, 1.0) / max(height, 1.0))
    return result


def aspect_mismatch(frames: Sequence[float], pictures: Sequence[float]) -> float:
    """
    枠と絵の縦横比のずれ(コマごとの |log(枠/絵)| の平均)。0 なら絵がそのまま入る。0.4 を超えると、絵の 3 割以上が
    切り落とされる(横長の絵を縦に細い枠へ入れて、口元だけ残るなど)。
    """
    pairs = list(zip(frames, pictures))
    return sum(abs(math.log(f / p)) for f, p in pairs) / len(pairs) if pairs else 0.0


# 1つの段の中の、コマの幅の下限(段の幅に対する割合)。絵の縦横比に合わせて幅を変えるときも、これより細くしない
_MIN_COL = 0.25
# 段の高さを細かく詰める案の数(大まかなずれで絞ってから詰める。全部詰めると数秒かかる)
_REFINE = 6


def _fit_tree(plan: Sequence[int], widths: Sequence[Sequence[float]], heights: Sequence[float]) -> LayoutTree:
    """段ごとのコマ数・コマの幅(段ごとの割合)・段の高さから木を作る。"""
    items: list[LayoutTree] = [
        PANEL if n == 1 else {"dir": "cols", "sizes": [round(w, 4) for w in row], "items": [PANEL] * n}
        for n, row in zip(plan, widths)
    ]
    if len(items) == 1:
        return items[0]
    total = sum(heights)
    return {"dir": "rows", "sizes": [round(h / total, 4) for h in heights], "items": items}


def _row_widths(row: Sequence[float]) -> list[float]:
    """段の中のコマの幅。絵の縦横比に比例させる(縦長の絵は細く、横長の絵は広く)。細すぎるコマは _MIN_COL にする。"""
    widths = [a / sum(row) for a in row]
    if len(row) > 1 and min(widths) < _MIN_COL:
        thin = [w < _MIN_COL for w in widths]
        rest = 1 - _MIN_COL * sum(thin)
        wide = sum(w for w, t in zip(widths, thin) if not t)
        widths = [_MIN_COL if t else w * rest / wide for w, t in zip(widths, thin)]
    return widths


def _fit_heights(
    plan: Sequence[int],
    widths: Sequence[Sequence[float]],
    ideal: Sequence[float],
    pictures: Sequence[float],
    gaps: Mapping[str, Any] | None,
) -> list[float]:
    """
    段の高さ(合計 1)を、枠と絵のずれ(aspect_mismatch)が一番小さくなるように決める。ideal(その段の絵がそのまま入る
    高さ)の比から始めて、間隔の分も含めて詰める。
    """
    if len(plan) == 1:
        return [1.0]
    from scipy.optimize import minimize

    def shares(z: Sequence[float]) -> list[float]:
        top = max(z)
        e = [math.exp(v - top) for v in z]
        return [v / sum(e) for v in e]

    def cost(z: Sequence[float]) -> float:
        rows = shares(z)
        penalty = sum(max(0.0, _MIN_ROW - r) + max(0.0, r - _MAX_ROW) for r in rows) * 10
        return aspect_mismatch(frame_aspects(_fit_tree(plan, widths, rows), gaps), pictures) + penalty

    best = minimize(cost, [math.log(h) for h in ideal], method="Nelder-Mead", options={"xatol": 5e-3, "fatol": 1e-4, "maxiter": 300})
    return [round(r, 4) for r in shares(best.x)]


def aspect_layouts(pictures: Sequence[float], limit: int = 3, gaps: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """
    ページに入る絵の縦横比(読む順)に合う、コマ割りの候補(id・名前・木・ずれ)をずれの小さい順に。段ごとに 1〜3 コマで、
    コマの幅は段の中の絵の縦横比に比例させ、段の高さはずれが一番小さくなるように決める。
    パターン(layout_patterns)も同じ物差しで比べる。
    """
    count = len(pictures)
    g = normalize_gaps(gaps)
    ratio = (PAGE_WIDTH - g["margin"] * 2) / (PAGE_HEIGHT - g["margin"] * 2)
    rough: list[tuple[float, tuple[int, ...], list[list[float]], list[float]]] = []
    for rows in range(1, min(count, 5) + 1):
        for plan in _compositions(count, rows, 3):
            widths, ideal, start = [], [], 0
            for n in plan:
                row = pictures[start : start + n]
                start += n
                widths.append(_row_widths(row))
                # 段の中の絵が、そのまま入る高さ(本文の高さに対する割合)
                ideal.append(ratio / sum(row))
            total = sum(ideal)
            tree = _fit_tree(plan, widths, [h / total for h in ideal])
            rough.append((aspect_mismatch(frame_aspects(tree, gaps), pictures), plan, widths, ideal))
    rough.sort(key=lambda r: r[0])
    candidates: list[dict[str, Any]] = []
    for _, plan, widths, ideal in rough[:_REFINE]:
        shares = _fit_heights(plan, widths, ideal, pictures, gaps)
        if min(shares) < _MIN_ROW or max(shares) > _MAX_ROW:
            continue
        counts = "・".join(str(n) for n in plan)
        candidates.append({
            "id": "fit-" + "-".join(map(str, plan)),
            "label": f"{len(plan)}段({counts})・絵に合わせた大きさ",
            "tree": normalize_tree(_fit_tree(plan, widths, shares)),
        })
    if count <= MAX_TREE_PANELS:
        candidates += layout_patterns(count)
    for candidate in candidates:
        candidate["mismatch"] = round(aspect_mismatch(frame_aspects(candidate["tree"], gaps), pictures), 4)
    candidates.sort(key=lambda c: c["mismatch"])
    seen: set[str] = set()
    result = []
    for candidate in candidates:
        key = json.dumps(candidate["tree"], sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result[:limit]
