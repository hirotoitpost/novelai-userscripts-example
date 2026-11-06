"""
漫画v2: 1コマに何枚か描いた候補の絵を、番号付きで並べた一覧の画像にする(見比べて選ぶ用)。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .lettering import resolve_font

_COLUMNS = 3
_GAP = 8
_LABEL = 36


def candidate_sheet(items: list[tuple[str, Path]], tile: int = 360) -> Image.Image:
    """
    (番号, 絵のパス) を横 3 枚ずつ並べ、左上に番号を入れた一覧。絵は幅 tile にそろえる(縦は絵の比率のまま)。
    """
    images: list[tuple[str, Image.Image]] = []
    for label, path in items:
        with Image.open(path) as src:
            image = src.convert("RGB")
        image = image.resize((tile, max(1, round(image.height * tile / image.width))))
        images.append((label, image))
    if not images:
        return Image.new("RGB", (tile, tile), "white")
    columns = min(_COLUMNS, len(images))
    rows = (len(images) + columns - 1) // columns
    cell_h = max(image.height for _, image in images) + _LABEL
    sheet = Image.new("RGB", (columns * tile + (columns + 1) * _GAP, rows * cell_h + (rows + 1) * _GAP), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype(str(resolve_font(None)), 26)
    except (OSError, RuntimeError):
        font = ImageFont.load_default()
    for n, (label, image) in enumerate(images):
        x = _GAP + (n % columns) * (tile + _GAP)
        y = _GAP + (n // columns) * (cell_h + _GAP)
        draw.text((x + 4, y + 2), label, fill="black", font=font)
        sheet.paste(image, (x, y + _LABEL))
    return sheet
