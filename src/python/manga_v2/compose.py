"""コマ画像をテンプレートに嵌め込み、吹き出しと描き文字を置いてページ画像にする。"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from .detect import detect_heads
from .layout import PAGE_HEIGHT, PAGE_WIDTH, Rect, fill_template, panel_rects
from .lettering import (
    SFX_MIN_SIZE,
    bubble_shape,
    draw_stamp,
    TextBlock,
    bubble_size,
    draw_bubble,
    draw_narration,
    draw_sfx,
    fit_sfx,
    fit_text,
    narration_size,
)

_BORDER = 4
_BUBBLE_MARGIN = 14
# セリフの文字サイズ(ページ幅1200px基準)。長いセリフは最小サイズまで縮める。
_TEXT_SIZE = 26
_TEXT_MIN_SIZE = 16
# 1列の最大文字数。長い列は読みにくく吹き出しも細長くなるので、超える分は次の列へ送る。
_MAX_ROWS = 11
# ナレーションの文字サイズと、コマ幅に対する枠の最大幅
_NARRATION_SIZE = 22
_NARRATION_MAX_WIDTH = 0.4
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
    # ナレーション枠の文(空なら出さない)
    narration: str = ""


@dataclass
class Element:
    """ページ上に置いた吹き出し/描き文字。画面でドラッグして位置を直すために返す。"""

    key: str
    kind: str  # "bubble" | "sfx" | "narration"
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
    # 効果音の文字列ごとのフォント(無ければ sfx_font_path)
    sfx_font_paths: dict[str, Path] = field(default_factory=dict)
    # 効果音の文字列ごとのスタンプ画像。フォントより優先する。
    sfx_stamps: dict[str, Path] = field(default_factory=dict)


# 寄りのコマの拡大率(1段ごと)と上限、切り抜く中心(横は段ごとに左右へ振る)
_ZOOM_PER_STEP = 0.45
_MAX_ZOOM = 2.2
_ZOOM_FOCUS_X = (0.5, 0.5, 0.42, 0.58)
_ZOOM_FOCUS_Y = 0.35


def _cover(
    img: Image.Image, width: int, height: int, zoom_step: int = 0, heads: list[Rect] | None = None
) -> tuple[Image.Image, float, int, int]:
    """
    縦横比を保ったまま width×height を覆うよう拡大し、はみ出しを切り落とす。
    zoom_step > 0 なら更に拡大して寄りにする。頭が見つかっていればその辺りを、無ければ
    上寄りの中央付近(人物の顔がありやすい)を切り抜く。寄りの段ごとに別の頭を中心にする。

    戻り値は (切り抜いた画像, 拡大率, 切り抜きの左端, 上端)。元画像の座標をコマの座標へ
    直すのに使う(コマ座標 = 元座標 × 拡大率 − 左端/上端)。
    """
    zoom = min(1 + _ZOOM_PER_STEP * zoom_step, _MAX_ZOOM)
    scale = max(width / img.width, height / img.height) * zoom
    resized = img.resize((max(round(img.width * scale), width), max(round(img.height * scale), height)))
    if zoom_step == 0:
        left = (resized.width - width) // 2
        top = round((resized.height - height) * _CROP_VERTICAL_BIAS)
    else:
        if heads:
            hx0, hy0, hx1, hy1 = heads[(zoom_step - 1) % len(heads)]
            fx = (hx0 + hx1) / 2 * scale
            fy = (hy0 + hy1) / 2 * scale + height * 0.1  # 顔の少し下を中心にして頭上を空ける
        else:
            fx = resized.width * _ZOOM_FOCUS_X[zoom_step % len(_ZOOM_FOCUS_X)]
            fy = resized.height * _ZOOM_FOCUS_Y
        left = min(max(round(fx - width / 2), 0), resized.width - width)
        top = min(max(round(fy - height / 2), 0), resized.height - height)
    return resized.crop((left, top, left + width, top + height)), scale, left, top


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
                    narration=panel.narration if step == 0 else "",
                )
            )
    return result


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _overlap_area(a: Rect, b: Rect) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0)


def _place(
    size: tuple[int, int],
    panel: Rect,
    placed: list[Rect],
    *,
    sfx: bool = False,
    narration: bool = False,
    heads: list[Rect] | None = None,
) -> tuple[Rect, bool]:
    """
    吹き出しの置き場所。日本の漫画は右から読むので、コマの上辺に沿って右から左へ、
    次に下辺に沿って右から左へ探し、既存の吹き出しと重ならない最初の位置に置く。
    どこにも空きが無ければ重なりが最小の位置にする。2つ目の戻り値は重ならずに置けたか。
    heads(絵の中の頭の位置)を渡すと、頭に被らない位置を優先し、被るしかなければ
    被る面積が最小の位置にする(この場合も「重ならずに置けた」には数えない)。

    描き文字(sfx=True)は吹き出しと取り合わないよう、コマの中ほど・左側から探す。
    ナレーション(narration=True)はコマの左上の角に置く(右上は吹き出しが使う)。空きが無ければ左下。
    """
    bw, bh = size
    px0, py0, px1, py1 = panel
    left_limit = px0 + _BUBBLE_MARGIN
    right_limit = px1 - _BUBBLE_MARGIN
    top_row, bottom_row = py0 + _BUBBLE_MARGIN, py1 - _BUBBLE_MARGIN - bh
    step = max(bw // 4, 8)
    candidates: list[Rect] = []
    if narration:
        # コマの枠にぴったり付ける(余白なし)のがナレーションらしい
        candidates = [(px0, py0, px0 + bw, py0 + bh), (px0, py1 - bh, px0 + bw, py1)]
    elif sfx:
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
    heads = heads or []
    free = [c for c in candidates if not any(_overlaps(c, other) for other in placed)]
    for candidate in free:
        if not any(_overlaps(candidate, head) for head in heads):
            return candidate, True
    if free:
        return min(free, key=lambda c: sum(_overlap_area(c, head) for head in heads)), False
    return min(
        candidates,
        key=lambda c: (sum(_overlap_area(c, o) for o in placed), sum(_overlap_area(c, h) for h in heads)),
    ), False


def _covers_head(box: Rect, heads: list[Rect]) -> bool:
    """頭の面積の1割以上を覆っているか(縁を少しかすめる程度は気にしない)。"""
    return any(_overlap_area(box, h) > 0.1 * (h[2] - h[0]) * (h[3] - h[1]) for h in heads)


def _speaker_point(box: Rect, heads: list[Rect], default: tuple[float, float]) -> tuple[float, float]:
    """しっぽを向ける先: 吹き出しに一番近い頭の口元あたり。頭が無ければ既定の位置。"""
    if not heads:
        return default
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    hx0, hy0, hx1, hy1 = min(heads, key=lambda h: ((h[0] + h[2]) / 2 - cx) ** 2 + ((h[1] + h[3]) / 2 - cy) ** 2)
    return (hx0 + hx1) / 2, hy0 + (hy1 - hy0) * 0.7


def _fit_bubble(
    text: str, panel: Rect, text_size: int = _TEXT_SIZE, shape: str = "ellipse"
) -> tuple[TextBlock, tuple[int, int]]:
    pw, ph = panel[2] - panel[0], panel[3] - panel[1]
    # 楕円の倍率と余白を見込んで、文字ブロックの上限をコマの大きさから決める
    max_text_w = int((pw * 0.45) / 1.45)
    max_text_h = min(int((ph - _BUBBLE_MARGIN * 2 - 20) / 1.3), text_size * _MAX_ROWS)
    block = fit_text(text, max_text_w, max_text_h, text_size, _TEXT_MIN_SIZE)
    return block, bubble_size(block, shape)


# 頭に被らざるを得ない吹き出しの、白い地の不透明度の上限(絵が透けて見えるようにする)
_OVER_HEAD_OPACITY = 0.55


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


# スタンプの大きさ(コマの高さ・幅に対する上限)。空きが無ければこの刻みで縮める。
_STAMP_MAX_H = 0.55
_STAMP_MAX_W = 0.4
_STAMP_SHRINK = 0.85
_STAMP_MIN_H = 0.2


def _place_stamp(
    natural: tuple[int, int],
    panel: Rect,
    placed: list[Rect],
    override: tuple[float, float] | None,
    heads: list[Rect] | None = None,
) -> Rect:
    """スタンプの縦横比のまま、コマに収まる大きさで描き文字と同じ探し方で置き場所を決める。"""
    pw, ph = panel[2] - panel[0], panel[3] - panel[1]
    sw, sh = natural
    ratio = min(ph * _STAMP_MAX_H / sh, pw * _STAMP_MAX_W / sw)
    while True:
        size = (max(1, round(sw * ratio)), max(1, round(sh * ratio)))
        if override is not None:
            return _overridden_box(size, panel, override)
        box, free = _place(size, panel, placed, sfx=True, heads=heads)
        if free or size[1] <= ph * _STAMP_MIN_H:
            return box
        ratio *= _STAMP_SHRINK


def _draw_panel(page: Image.Image, rect: Rect, content: PanelContent, style: LetteringStyle) -> list[Element]:
    x0, y0, x1, y1 = rect
    width, height = x1 - x0, y1 - y0
    draw = ImageDraw.Draw(page)
    # 絵の中の頭の位置(ページ座標)。吹き出し・描き文字はここを避ける
    heads: list[Rect] = []
    if content.image_path is not None and content.image_path.is_file():
        source_heads = detect_heads(content.image_path)
        with Image.open(content.image_path) as src:
            picture, scale, left, top = _cover(src.convert("RGB"), width, height, content.zoom_step, source_heads)
        page.paste(picture, (x0, y0))
        for hx0, hy0, hx1, hy1 in source_heads:
            box = (
                max(round(hx0 * scale - left) + x0, x0),
                max(round(hy0 * scale - top) + y0, y0),
                min(round(hx1 * scale - left) + x0, x1),
                min(round(hy1 * scale - top) + y0, y1),
            )
            if box[2] > box[0] and box[3] > box[1]:
                heads.append(box)
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
    narration_box: Rect | None = None
    narration_block: TextBlock | None = None
    if content.narration.strip():
        # ナレーションは先に場所を取り、吹き出しはそれを避けて置く。描くのは最後(枠線が上に来るように)。
        key = f"{key_base}:narration:0"
        narration_block = fit_text(
            content.narration.strip(),
            int(width * _NARRATION_MAX_WIDTH),
            min(int(height * 0.7), _NARRATION_SIZE * _MAX_ROWS),
            _NARRATION_SIZE,
            _TEXT_MIN_SIZE,
        )
        size = narration_size(narration_block)
        override = style.overrides.get(key)
        narration_box = (
            _overridden_box(size, rect, override)
            if override is not None
            else _place(size, rect, placed, narration=True, heads=heads)[0]
        )
        placed.append(narration_box)
        elements.append(Element(key, "narration", content.narration.strip(), narration_box, rect))
    # 吹き出し・描き文字とも、既に置いたものと重なるなら文字を小さくして空きを探し直す。
    # 最小サイズでも空きが無ければ、重なりが最小の位置に置く。手動で動かしたものはその位置に置く。
    for index, line in enumerate(lines):
        key = f"{key_base}:bubble:{index}"
        override = style.overrides.get(key)
        shape, text = bubble_shape(line)
        # 空きが無ければ、まず同じ大きさの角丸の四角(場所を取らない)を試し、それでも駄目なら
        # 文字を小さくして探し直す。最後まで空きが無ければ、頭への被りが最小の位置にする。
        shapes = [shape] if shape in ("box", "cloud") else [shape, "box"]
        best: tuple[Rect, TextBlock, str] | None = None
        text_size = _TEXT_SIZE
        while best is None:
            for candidate_shape in shapes:
                block, size = _fit_bubble(text, rect, text_size, candidate_shape)
                if override is not None:
                    best = (_overridden_box(size, rect, override), block, candidate_shape)
                    break
                box, free = _place(size, rect, placed, heads=heads)
                if free:
                    best = (box, block, candidate_shape)
                    break
            if best is None and text_size <= _TEXT_MIN_SIZE:
                block, size = _fit_bubble(text, rect, text_size, shape)
                best = (_place(size, rect, placed, heads=heads)[0], block, shape)
            text_size -= _SHRINK_STEP_TEXT
        box, block, final_shape = best
        placed.append(box)
        elements.append(Element(key, "bubble", line, box, rect))
        # 頭に被ってしまう吹き出しだけ地を透かし、絵が見えるようにする
        opacity = min(style.bubble_opacity, _OVER_HEAD_OPACITY) if _covers_head(box, heads) else style.bubble_opacity
        draw_bubble(
            page,
            box,
            block,
            style.font_path,
            tail_toward=_speaker_point(box, heads, speaker),
            opacity=opacity,
            shape=final_shape,
        )

    for index, text in enumerate(t for t in content.sfx if t.strip()):
        key = f"{key_base}:sfx:{index}"
        override = style.overrides.get(key)
        stamp_path = style.sfx_stamps.get(text)
        if stamp_path is not None and stamp_path.is_file():
            with Image.open(stamp_path) as stamp:
                stamp.load()
                box = _place_stamp(stamp.size, rect, placed, override, heads)
                placed.append(box)
                elements.append(Element(key, "sfx", text, box, rect))
                draw_stamp(page, box, stamp)
            continue
        layout = fit_sfx(text, width, height)
        while True:
            if override is not None:
                box = _overridden_box((layout.width, layout.height), rect, override)
                break
            box, free = _place((layout.width, layout.height), rect, placed, sfx=True, heads=heads)
            if free or layout.size <= SFX_MIN_SIZE:
                break
            layout = fit_sfx(text, width, height, max_size=layout.size - _SHRINK_STEP_SFX)
        placed.append(box)
        elements.append(Element(key, "sfx", text, box, rect))
        draw_sfx(page, box, layout, style.sfx_font_paths.get(text, style.sfx_font_path))

    if narration_box is not None and narration_block is not None:
        draw_narration(page, narration_box, narration_block, style.font_path)

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
    """
    シーン順のコマをテンプレートのコマ数ずつページに割り付ける。最後のページのコマが
    足りないときは、空きゴマを残さないよう少ないコマ数のテンプレートに切り替える。
    """
    per_page = len(panel_rects(template_id))
    pages = []
    for i in range(0, len(panels), per_page):
        chunk = panels[i : i + per_page]
        pages.append(compose_page(panel_rects(fill_template(template_id, len(chunk))), chunk, style))
    return pages


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
