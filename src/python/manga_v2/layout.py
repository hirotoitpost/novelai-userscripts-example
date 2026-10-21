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
# コマの境目をドラッグすると、その親の sizes の隣り合う2つが変わる。

LayoutTree = Any  # "panel" か {"dir", "sizes", "items"}

PANEL = "panel"
# 分けた1つの最小の割合(細すぎるコマにならないように)・木の深さ・1ページのコマ数の上限
MIN_SHARE = 0.08
_MAX_DEPTH = 4
MAX_TREE_PANELS = 12


def tree_count(tree: LayoutTree) -> int:
    return 1 if tree == PANEL else sum(tree_count(item) for item in tree["items"])


def tree_rects(tree: LayoutTree, box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)) -> list[tuple[float, float, float, float]]:
    """木のコマを、本文領域を単位正方形とした (x0, y0, x1, y1) にして読む順に並べる。"""
    if tree == PANEL:
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


def normalize_tree(tree: Any, depth: int = 0) -> LayoutTree:
    """
    送られた木を検査して整える(割合は合計1・最小 MIN_SHARE に)。おかしな形は ValueError。
    子が1つだけの分割は、その子にする。
    """
    if tree == PANEL:
        return PANEL
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
    node = {
        "dir": tree["dir"],
        "sizes": [round(s / total, 4) for s in shares],
        "items": [normalize_tree(item, depth + 1) for item in items],
    }
    if tree_count(node) > MAX_TREE_PANELS:
        raise ValueError(f"1ページのコマは {MAX_TREE_PANELS} までです。")
    return node


def tree_layout(tree: LayoutTree) -> dict[str, Any]:
    """
    木のコマ割りを、ページごとのコマ割り(manga_v2_page_layouts の今の形)にする。合成は panels の輪郭を使い、
    画面で直せるように木も一緒に持つ。
    """
    panels = []
    for x0, y0, x1, y1 in _content_rects(tree_rects(tree)):
        corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        panels.append({"points": [[round(x / PAGE_WIDTH, 4), round(y / PAGE_HEIGHT, 4)] for x, y in corners], "border": True})
    return {"spread": False, "panels": panels, "tree": tree}


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
