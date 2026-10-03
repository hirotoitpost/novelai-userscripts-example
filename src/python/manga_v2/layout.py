"""ページのコマ割りテンプレートと、コマの形に合わせた生成サイズの決定。"""

from __future__ import annotations

import math
from dataclasses import dataclass

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
    )
}


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
