"""
manga_pages(V5で生成したコマ割り済みページ画像)を縦に連結し、各ページの下に
ナレーション帯を描き足して1枚の縦長PNGにまとめる。

V5自体の画像内文字描画は信頼できない(diffusionモデルで任意の日本語キャプションを
正確に描かせるのは不安定)ため、キャプションはPillowで確実に描画する。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_CAPTION_FONT_CANDIDATES = [
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/YuGothR.ttc",
    "C:/Windows/Fonts/NotoSansJP-VF.ttf",
]
_CAPTION_FONT_SIZE = 28
_CAPTION_PADDING = 24
_CAPTION_BG = (20, 20, 20)
_CAPTION_FG = (240, 240, 240)


def _load_caption_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _CAPTION_FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, _CAPTION_FONT_SIZE)
    return ImageFont.load_default(size=_CAPTION_FONT_SIZE)


def _wrap_to_pixel_width(
    text: str, max_width: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, draw: ImageDraw.ImageDraw
) -> list[str]:
    """
    日本語には単語間スペースが無いため textwrap.fill の文字数ベース改行は使えない
    (全角文字は半角の約2倍幅があり、はみ出す)。1文字ずつ実測して折り返す。
    """

    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for ch in paragraph:
            candidate = current + ch
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = ch
            else:
                current = candidate
        lines.append(current)
    return lines


def _render_caption_band(width: int, caption: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> Image.Image:
    dummy = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(dummy)
    lines = _wrap_to_pixel_width(caption, width - _CAPTION_PADDING * 2, font, draw)
    line_height = max(draw.textbbox((0, 0), line, font=font)[3] for line in lines) + 6

    band_height = _CAPTION_PADDING * 2 + line_height * len(lines)
    band = Image.new("RGB", (width, band_height), _CAPTION_BG)
    draw = ImageDraw.Draw(band)
    y = _CAPTION_PADDING
    for line in lines:
        draw.text((_CAPTION_PADDING, y), line, font=font, fill=_CAPTION_FG)
        y += line_height
    return band


def assemble_manga(pages: list[dict]) -> bytes:
    """
    pages: [{"image_path": <ページ画像の絶対パス>, "caption": <このページのナレーション要約>}, ...]
    page_index順に並んでいる前提。1枚の縦長PNGにまとめて返す。
    """

    if not pages:
        raise ValueError("pages is empty")

    font = _load_caption_font()
    images = [Image.open(p["image_path"]).convert("RGB") for p in pages]
    width = max(img.width for img in images)

    strips: list[Image.Image] = []
    total_height = 0
    for page, img in zip(pages, images):
        if img.width != width:
            new_height = int(img.height * (width / img.width))
            img = img.resize((width, new_height))
        strips.append(img)
        total_height += img.height

        caption = page.get("caption", "").strip()
        if caption:
            band = _render_caption_band(width, caption, font)
            strips.append(band)
            total_height += band.height

    canvas = Image.new("RGB", (width, total_height), (255, 255, 255))
    y = 0
    for strip in strips:
        canvas.paste(strip, (0, y))
        y += strip.height

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
