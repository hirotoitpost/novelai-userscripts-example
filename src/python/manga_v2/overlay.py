"""
コマの絵に重ねるもの(重ね): 文字や、別のコマの絵(写真)を、四隅を決めてコマの絵の上に貼る。

AI に文字を描かせると読めない字になる(命名書・手紙・チラシ)。写真立ての中の写真も、別に描いたコマの絵を
貼った方が狙いどおりになる。そこで、貼るもの(文字なら単体で作った画像)を作ってから、四隅の位置に合わせて
拡大・縮小・回転・遠近の変形をして貼る(射影変換)。

四隅はコマの絵(生成した元の絵)に対する割合で決める。ページに嵌めるときに絵が切り抜かれても、絵の中の
写真立てなどに貼ったものがずれないように、絵と同じ変換で置く。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

Point = tuple[float, float]

# 文字を作るときの1文字の大きさ(貼るときに縮めるので、大きめに作って粗くならないようにする)
_TEXT_SIZE = 160


@dataclass
class OverlayPiece:
    """貼るものと、貼る四隅(コマの絵に対する割合。左上・右上・右下・左下)。"""

    image: Image.Image
    quad: tuple[Point, Point, Point, Point]
    opacity: float = 1.0


def normalize_quad(value: Any) -> tuple[Point, Point, Point, Point]:
    """四隅の検査。4点の [x, y](0〜1。少しはみ出してもよい)でなければ ValueError。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("quad は4点 [[x, y], ...](左上・右上・右下・左下)で指定してください")
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("quad の各点は [x, y] で指定してください")
        x, y = float(point[0]), float(point[1])
        if not (-0.5 <= x <= 1.5 and -0.5 <= y <= 1.5):
            raise ValueError("quad の点はコマの絵に対する割合(0〜1)で指定してください")
        points.append((x, y))
    return points[0], points[1], points[2], points[3]


def hex_color(value: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    text = (value or "").strip().lstrip("#")
    if len(text) != 6:
        return default
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return default


def render_text(
    text: str,
    font_path: Path,
    *,
    color: str | None = None,
    vertical: bool = False,
    stroke_color: str | None = None,
    stroke_width: float = 0.0,
) -> Image.Image:
    """
    文字だけの画像(透明の地)を作る。縦書きは1文字ずつ下へ並べ、改行で左の列へ移る(右の列から読む)。
    stroke_width は文字の大きさに対する縁取りの太さの割合(0 で縁取りなし)。
    """
    font = ImageFont.truetype(str(font_path), _TEXT_SIZE)
    fill = hex_color(color, (0, 0, 0))
    stroke = round(_TEXT_SIZE * max(0.0, min(stroke_width, 0.3)))
    stroke_fill = hex_color(stroke_color, (255, 255, 255))
    lines = [line for line in text.split("\n")] or [""]
    pad = stroke + _TEXT_SIZE // 8
    step = round(_TEXT_SIZE * 1.15)
    if vertical:
        rows = max(len(line) for line in lines) or 1
        width, height = step * len(lines) + pad * 2, step * rows + pad * 2
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for column, line in enumerate(lines):
            x = width - pad - step * (column + 1) + (step - _TEXT_SIZE) // 2
            for row, ch in enumerate(line):
                # 長音・ダッシュは縦書きでは縦棒にする
                glyph = {"ー": "｜", "—": "｜", "-": "｜", "〜": "≀"}.get(ch, ch)
                draw.text((x, pad + step * row), glyph, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    else:
        boxes = [font.getbbox(line or " ", stroke_width=stroke) for line in lines]
        width = max(b[2] - b[0] for b in boxes) + pad * 2
        height = step * len(lines) + pad * 2
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for row, (line, box) in enumerate(zip(lines, boxes)):
            draw.text((pad - box[0], pad + step * row), line, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    bbox = image.getbbox()
    return image.crop(bbox) if bbox else image


def photo_print(image: Image.Image, frame: float = 0.0, monochrome: bool = False) -> Image.Image:
    """写真として貼る絵。frame は白い縁の太さ(短い辺に対する割合。0 で縁なし)。"""
    picture = ImageOps.grayscale(image).convert("RGB") if monochrome else image.convert("RGB")
    border = round(min(picture.size) * max(0.0, min(frame, 0.25)))
    if border:
        picture = ImageOps.expand(picture, border=border, fill=(255, 255, 255))
    return picture.convert("RGBA")


def _edge(a: Point, b: Point) -> float:
    return float(np.hypot(b[0] - a[0], b[1] - a[1]))


def fit_aspect(image: Image.Image, quad: Sequence[Point]) -> Image.Image:
    """
    四隅の形(上下・左右の辺の長さの平均)と同じ縦横比になるよう、透明の余白を足す。文字が四隅に合わせて
    伸び縮みしないように(四隅の中で、文字の形を保ったまま一番大きく置く)。
    """
    tl, tr, br, bl = quad
    target_w = (_edge(tl, tr) + _edge(bl, br)) / 2
    target_h = (_edge(tl, bl) + _edge(tr, br)) / 2
    if target_w <= 0 or target_h <= 0:
        return image
    ratio = target_w / target_h
    w, h = image.size
    if w / h > ratio:
        new_w, new_h = w, round(w / ratio)
    else:
        new_w, new_h = round(h * ratio), h
    canvas = Image.new("RGBA", (max(new_w, 1), max(new_h, 1)), (0, 0, 0, 0))
    canvas.paste(image, ((new_w - w) // 2, (new_h - h) // 2), image if image.mode == "RGBA" else None)
    return canvas


def _perspective_coefficients(dst: Sequence[Point], src: Sequence[Point]) -> list[float]:
    """出力(dst)の点を入力(src)の点へ写す射影変換の係数(PIL の Image.transform PERSPECTIVE の形)。"""
    rows = []
    rhs = []
    for (x, y), (u, v) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.extend([u, v])
    solution = np.linalg.solve(np.array(rows, dtype=float), np.array(rhs, dtype=float))
    return [float(c) for c in solution]


def paste_warped(target: Image.Image, piece: Image.Image, quad_px: Sequence[Point], opacity: float = 1.0) -> None:
    """piece を、target の上の四隅 quad_px(ピクセル座標。左上・右上・右下・左下)に合わせて変形して貼る。"""
    xs = [p[0] for p in quad_px]
    ys = [p[1] for p in quad_px]
    x0, y0 = max(int(np.floor(min(xs))), 0), max(int(np.floor(min(ys))), 0)
    x1, y1 = min(int(np.ceil(max(xs))), target.width), min(int(np.ceil(max(ys))), target.height)
    if x1 <= x0 or y1 <= y0:
        return
    w, h = piece.size
    local = [(x - x0, y - y0) for x, y in quad_px]
    try:
        coefficients = _perspective_coefficients(local, [(0, 0), (w, 0), (w, h), (0, h)])
    except np.linalg.LinAlgError:
        # 四隅が一直線に並ぶなど、変形できない形
        return
    source = piece.convert("RGBA")
    warped = source.transform((x1 - x0, y1 - y0), Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)
    alpha = warped.getchannel("A")
    if opacity < 1.0:
        alpha = alpha.point(lambda a: round(a * max(0.0, opacity)))
    region = target.crop((x0, y0, x1, y1)).convert("RGBA")
    region.paste(warped, (0, 0), alpha)
    target.paste(region.convert(target.mode), (x0, y0))


def draw_pieces(
    target: Image.Image,
    pieces: Sequence[OverlayPiece],
    to_target: Any,
) -> None:
    """
    重ねるものを順に貼る。to_target は (コマの絵に対する割合の x, y) → target のピクセル座標 の関数
    (ページに嵌めるときは、絵と同じ拡大・切り抜きの変換)。
    """
    for piece in pieces:
        quad_px = [to_target(x, y) for x, y in piece.quad]
        paste_warped(target, piece.image, quad_px, piece.opacity)
