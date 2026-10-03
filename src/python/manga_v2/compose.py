"""コマ画像をテンプレートに嵌め込み、吹き出しと描き文字を置いてページ画像にする。"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from .layout import PAGE_HEIGHT, PAGE_WIDTH, Rect, panel_rects
from .lettering import SFX_MIN_SIZE, TextBlock, bubble_size, draw_bubble, draw_sfx, fit_sfx, fit_text

_BORDER = 4
_BUBBLE_MARGIN = 14
# セリフの文字サイズ(ページ幅1200px基準)。長いセリフは最小サイズまで縮める。
_TEXT_SIZE = 26
_TEXT_MIN_SIZE = 16
# 1列の最大文字数。長い列は読みにくく吹き出しも細長くなるので、超える分は次の列へ送る。
_MAX_ROWS = 11
# 1コマに置く吹き出しの上限。超えた分は最後の吹き出しにまとめる。
_MAX_BUBBLES = 4
# 切り抜きの縦位置(0=上端, 0.5=中央)。人物の頭が切れにくいよう上寄りにする。
_CROP_VERTICAL_BIAS = 0.25


@dataclass
class PanelContent:
    image_path: Path | None
    dialogue: list[str] = field(default_factory=list)
    # 吹き出しを使わず描き文字にする効果音・オノマトペ
    sfx: list[str] = field(default_factory=list)
    # セリフが多いシーンを分けたときの2コマ目以降の番号。同じ絵を寄りで切り抜く(0は通常)。
    zoom_step: int = 0
    # 手動配置(ドラッグ)の保存キーの元。シーンIDを入れる。
    key: str = ""


@dataclass
class Element:
    """ページ上に置いた吹き出し/描き文字。画面でドラッグして位置を直すために返す。"""

    key: str
    kind: str  # "bubble" | "sfx"
    text: str
    box: Rect
    panel: Rect


@dataclass
class LetteringStyle:
    font_path: Path
    sfx_font_path: Path
    # 吹き出しの白い地の不透明度(0で輪郭線だけ)
    bubble_opacity: float = 1.0
    # 手動配置: Element.key → コマ内での左上の位置(コマの幅・高さに対する割合)
    overrides: dict[str, tuple[float, float]] = field(default_factory=dict)


# 寄りのコマの拡大率(1段ごと)と上限、切り抜く中心(横は段ごとに左右へ振る)
_ZOOM_PER_STEP = 0.45
_MAX_ZOOM = 2.2
_ZOOM_FOCUS_X = (0.5, 0.5, 0.42, 0.58)
_ZOOM_FOCUS_Y = 0.35


def _cover(img: Image.Image, width: int, height: int, zoom_step: int = 0) -> Image.Image:
    """
    縦横比を保ったまま width×height を覆うよう拡大し、はみ出しを切り落とす。
    zoom_step > 0 なら更に拡大して上寄りの中央付近(人物の顔がありやすい)を切り抜く。
    """
    zoom = min(1 + _ZOOM_PER_STEP * zoom_step, _MAX_ZOOM)
    scale = max(width / img.width, height / img.height) * zoom
    resized = img.resize((max(round(img.width * scale), width), max(round(img.height * scale), height)))
    if zoom_step == 0:
        left = (resized.width - width) // 2
        top = round((resized.height - height) * _CROP_VERTICAL_BIAS)
    else:
        fx = _ZOOM_FOCUS_X[zoom_step % len(_ZOOM_FOCUS_X)]
        left = min(max(round(resized.width * fx - width / 2), 0), resized.width - width)
        top = min(max(round(resized.height * _ZOOM_FOCUS_Y - height / 2), 0), resized.height - height)
    return resized.crop((left, top, left + width, top + height))


def split_dense_panels(panels: list[PanelContent], max_lines: int) -> list[PanelContent]:
    """
    セリフが max_lines を超えるシーンを複数のコマに分ける(0なら分けない)。
    分けたコマには同じ絵の寄りを使うので、追加の生成は要らない。効果音は最初のコマに置く。
    """
    if max_lines <= 0:
        return panels
    result: list[PanelContent] = []
    for panel in panels:
        lines = [line for line in panel.dialogue if line.strip()]
        if len(lines) <= max_lines:
            result.append(panel)
            continue
        for step, start in enumerate(range(0, len(lines), max_lines)):
            result.append(
                PanelContent(
                    panel.image_path,
                    lines[start : start + max_lines],
                    panel.sfx if step == 0 else [],
                    zoom_step=step,
                    key=panel.key,
                )
            )
    return result


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _overlap_area(a: Rect, b: Rect) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0)


def _place(size: tuple[int, int], panel: Rect, placed: list[Rect], *, sfx: bool = False) -> tuple[Rect, bool]:
    """
    吹き出しの置き場所。日本の漫画は右から読むので、コマの上辺に沿って右から左へ、
    次に下辺に沿って右から左へ探し、既存の吹き出しと重ならない最初の位置に置く。
    どこにも空きが無ければ重なりが最小の位置にする。2つ目の戻り値は重ならずに置けたか。

    描き文字(sfx=True)は吹き出しと取り合わないよう、コマの中ほど・左側から探す。
    """
    bw, bh = size
    px0, py0, px1, py1 = panel
    left_limit = px0 + _BUBBLE_MARGIN
    right_limit = px1 - _BUBBLE_MARGIN
    top_row, bottom_row = py0 + _BUBBLE_MARGIN, py1 - _BUBBLE_MARGIN - bh
    step = max(bw // 4, 8)
    candidates: list[Rect] = []
    if sfx:
        middle_row = (py0 + py1 - bh) // 2
        for top in (middle_row, bottom_row, top_row):
            x0 = left_limit
            while x0 + bw <= right_limit:
                candidates.append((x0, top, x0 + bw, top + bh))
                x0 += step
        if not candidates:
            candidates.append((left_limit, top_row, left_limit + bw, top_row + bh))
    else:
        for top in (top_row, bottom_row):
            x1 = right_limit
            while x1 - bw >= left_limit:
                candidates.append((x1 - bw, top, x1, top + bh))
                x1 -= step
            candidates.append((left_limit, top, left_limit + bw, top + bh))
    for candidate in candidates:
        if not any(_overlaps(candidate, other) for other in placed):
            return candidate, True
    return min(candidates, key=lambda c: sum(_overlap_area(c, other) for other in placed)), False


def _fit_bubble(text: str, panel: Rect, text_size: int = _TEXT_SIZE) -> tuple[TextBlock, tuple[int, int]]:
    pw, ph = panel[2] - panel[0], panel[3] - panel[1]
    # 楕円の倍率と余白を見込んで、文字ブロックの上限をコマの大きさから決める
    max_text_w = int((pw * 0.45) / 1.45)
    max_text_h = min(int((ph - _BUBBLE_MARGIN * 2 - 20) / 1.3), text_size * _MAX_ROWS)
    block = fit_text(text, max_text_w, max_text_h, text_size, _TEXT_MIN_SIZE)
    return block, bubble_size(block)


# 空きが無いときに文字を小さくして置き直す刻み
_SHRINK_STEP_TEXT = 2
_SHRINK_STEP_SFX = 8


def _overridden_box(size: tuple[int, int], panel: Rect, position: tuple[float, float]) -> Rect:
    """手動配置の位置(コマに対する割合)を、コマからはみ出さないページ座標にする。"""
    x0, y0, x1, y1 = panel
    bw, bh = size
    left = min(max(round(x0 + position[0] * (x1 - x0)), x0), max(x1 - bw, x0))
    top = min(max(round(y0 + position[1] * (y1 - y0)), y0), max(y1 - bh, y0))
    return left, top, left + bw, top + bh


def _draw_panel(page: Image.Image, rect: Rect, content: PanelContent, style: LetteringStyle) -> list[Element]:
    x0, y0, x1, y1 = rect
    width, height = x1 - x0, y1 - y0
    draw = ImageDraw.Draw(page)
    if content.image_path is not None and content.image_path.is_file():
        with Image.open(content.image_path) as src:
            page.paste(_cover(src.convert("RGB"), width, height, content.zoom_step), (x0, y0))
    else:
        draw.rectangle(rect, fill=(225, 225, 225))
        draw.text((x0 + 12, y0 + 10), "(未生成)", fill=(120, 120, 120))

    lines = [line for line in content.dialogue if line.strip()]
    if len(lines) > _MAX_BUBBLES:
        lines = lines[: _MAX_BUBBLES - 1] + ["　".join(lines[_MAX_BUBBLES - 1 :])]
    placed: list[Rect] = []
    speaker = ((x0 + x1) / 2, y0 + height * 0.6)
    elements: list[Element] = []
    key_base = f"{content.key}:{content.zoom_step}"
    # 吹き出し・描き文字とも、既に置いたものと重なるなら文字を小さくして空きを探し直す。
    # 最小サイズでも空きが無ければ、重なりが最小の位置に置く。手動で動かしたものはその位置に置く。
    for index, line in enumerate(lines):
        key = f"{key_base}:bubble:{index}"
        override = style.overrides.get(key)
        text_size = _TEXT_SIZE
        while True:
            block, size = _fit_bubble(line, rect, text_size)
            if override is not None:
                box = _overridden_box(size, rect, override)
                break
            box, free = _place(size, rect, placed)
            if free or text_size <= _TEXT_MIN_SIZE:
                break
            text_size -= _SHRINK_STEP_TEXT
        placed.append(box)
        elements.append(Element(key, "bubble", line, box, rect))
        draw_bubble(page, box, block, style.font_path, tail_toward=speaker, opacity=style.bubble_opacity)

    for index, text in enumerate(t for t in content.sfx if t.strip()):
        key = f"{key_base}:sfx:{index}"
        override = style.overrides.get(key)
        layout = fit_sfx(text, width, height)
        while True:
            if override is not None:
                box = _overridden_box((layout.width, layout.height), rect, override)
                break
            box, free = _place((layout.width, layout.height), rect, placed, sfx=True)
            if free or layout.size <= SFX_MIN_SIZE:
                break
            layout = fit_sfx(text, width, height, max_size=layout.size - _SHRINK_STEP_SFX)
        placed.append(box)
        elements.append(Element(key, "sfx", text, box, rect))
        draw_sfx(page, box, layout, style.sfx_font_path)

    draw.rectangle(rect, outline=(0, 0, 0), width=_BORDER)
    return elements


def compose_page(
    rects: list[Rect], panels: list[PanelContent], style: LetteringStyle
) -> tuple[Image.Image, list[Element]]:
    page = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), (255, 255, 255))
    elements: list[Element] = []
    for rect, content in zip(rects, panels):
        elements.extend(_draw_panel(page, rect, content, style))
    return page, elements


def compose_pages(
    template_id: str, panels: list[PanelContent], style: LetteringStyle
) -> list[tuple[Image.Image, list[Element]]]:
    """シーン順のコマをテンプレートのコマ数ずつページに割り付ける。"""
    rects = panel_rects(template_id)
    per_page = len(rects)
    return [compose_page(rects, panels[i : i + per_page], style) for i in range(0, len(panels), per_page)]


_PAGE_GAP = 40


def concat_pages(pages: list[Image.Image]) -> bytes:
    """全ページを縦に並べた1枚のPNG(スマホで縦スクロールして読む用)。"""
    if not pages:
        raise ValueError("pages is empty")
    total = sum(p.height for p in pages) + _PAGE_GAP * (len(pages) - 1)
    canvas = Image.new("RGB", (PAGE_WIDTH, total), (255, 255, 255))
    y = 0
    for page in pages:
        canvas.paste(page, (0, y))
        y += page.height + _PAGE_GAP
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def page_png(page: Image.Image) -> bytes:
    buf = io.BytesIO()
    page.save(buf, format="PNG")
    return buf.getvalue()
