"""
吹き出しと縦書きセリフ、効果音(描き文字)の描画。

Pillow の縦書き(direction="ttb")は libraqm が要り、Windows の標準ビルドには入って
いないため、1文字ずつ自前で並べる。縦書きで向きや位置が変わる約物(長音・三点リーダ・
括弧・句読点・小書き仮名)だけ個別に扱う。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# 選べるフォント。PCに入っているものだけを候補として返す。
_FONT_DIR = Path("C:/Windows/Fonts")
_FONT_CANDIDATES: list[tuple[str, str, str]] = [
    ("yu-mincho-demibold", "游明朝 Demibold", "yumindb.ttf"),
    ("yu-mincho", "游明朝", "yumin.ttf"),
    ("ms-mincho", "MS 明朝", "msmincho.ttc"),
    ("biz-ud-mincho", "BIZ UD明朝", "BIZ-UDMinchoM.ttc"),
    ("noto-serif-jp", "Noto Serif JP", "NotoSerifJP-VF.ttf"),
    ("yu-gothic", "游ゴシック Medium", "YuGothM.ttc"),
    ("yu-gothic-bold", "游ゴシック Bold", "YuGothB.ttc"),
    ("meiryo", "メイリオ", "meiryo.ttc"),
    ("ms-gothic", "MS ゴシック", "msgothic.ttc"),
    ("biz-ud-gothic", "BIZ UDゴシック", "BIZ-UDGothicR.ttc"),
    ("biz-ud-gothic-bold", "BIZ UDゴシック Bold", "BIZ-UDGothicB.ttc"),
    ("noto-sans-jp", "Noto Sans JP", "NotoSansJP-VF.ttf"),
    ("ud-kyokasho", "UDデジタル教科書体", "UDDigiKyokashoN-R.ttc"),
    ("ud-kyokasho-bold", "UDデジタル教科書体 Bold", "UDDigiKyokashoN-B.ttc"),
    # 効果音(描き文字)向きの太い・崩した書体
    ("hg-soei-kakugothic-ub", "HG創英角ゴシックUB", "HGRSGU.TTC"),
    ("hg-soei-presence-eb", "HG創英プレゼンスEB", "HGRPRE.TTC"),
    ("hg-soei-kakupop", "HG創英角ポップ体", "HGRPP1.TTC"),
    ("hg-gothic-e", "HGゴシックE", "HGRGE.TTC"),
    ("hg-mincho-e", "HG明朝E", "HGRME.TTC"),
    ("hg-maru-gothic", "HG丸ゴシックM-PRO", "HGRSMP.TTF"),
    ("hg-gyosho", "HG行書体", "HGRGY.TTC"),
    ("hg-seikaisho", "HG正楷書体-PRO", "HGRSKP.TTF"),
]
DEFAULT_FONT_ID = "yu-mincho-demibold"
DEFAULT_SFX_FONT_ID = "hg-soei-kakugothic-ub"


@dataclass(frozen=True)
class FontChoice:
    id: str
    label: str
    path: Path


def available_fonts() -> list[FontChoice]:
    return [
        FontChoice(font_id, label, _FONT_DIR / filename)
        for font_id, label, filename in _FONT_CANDIDATES
        if (_FONT_DIR / filename).is_file()
    ]


def resolve_font(font_id: str | None, default_id: str = DEFAULT_FONT_ID) -> Path:
    fonts = {f.id: f.path for f in available_fonts()}
    if font_id and font_id in fonts:
        return fonts[font_id]
    for fallback in (default_id, DEFAULT_FONT_ID):
        if fallback in fonts:
            return fonts[fallback]
    if fonts:
        return next(iter(fonts.values()))
    raise RuntimeError("日本語フォントが見つかりません(C:/Windows/Fonts)。")


# 縦書きで90度回す文字(横向きの線・括弧類)
_ROTATE = set("ー－—―…‥〜～-（）()「」『』【】〈〉《》［］[]")
# 縦書きでは右上に寄せる句読点
_UPPER_RIGHT = set("、。，．")
# 縦書きでは右上に少し寄せる小書き仮名
_SMALL = set("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ")
# 行頭に来てはいけない文字(禁則)。来そうなら前の行へ追い出す(ぶら下げ)。
_NO_LINE_START = _UPPER_RIGHT | _SMALL | set("ー…‥？！?!」』）)〜～")
# この直後なら改行してよい(文節の切れ目の近似)
_BREAK_AFTER = set("、。？！?!…‥」』）)")
_SPACES = set(" 　")


def _phrases(text: str) -> list[str]:
    """改行してよい位置で区切った断片。空白は強制改行として扱い、断片に含めない。"""
    phrases: list[str] = []
    current = ""
    for i, ch in enumerate(text):
        if ch in _SPACES or ch == "\n":
            if current:
                phrases.append(current)
            phrases.append("\n")
            current = ""
            continue
        current += ch
        nxt = text[i + 1] if i + 1 < len(text) else ""
        # 「……」「？！」のような連続は分けない
        if ch in _BREAK_AFTER and nxt not in _BREAK_AFTER:
            phrases.append(current)
            current = ""
    if current:
        phrases.append(current)
    return phrases


def layout_columns(text: str, rows: int) -> list[str]:
    """縦書きの各列(右から順)。1列は rows 文字まで(禁則のぶら下げで1字超えることがある)。"""
    rows = max(rows, 1)
    columns: list[str] = []
    current = ""
    for phrase in _phrases(text.strip()):
        if phrase == "\n":
            if current:
                columns.append(current)
                current = ""
            continue
        if len(current) + len(phrase) <= rows:
            current += phrase
            continue
        if current and len(phrase) <= rows:
            columns.append(current)
            current = phrase
            continue
        # 1列に収まらない長い断片は文字単位で折り返す
        for ch in phrase:
            if len(current) >= rows and not (ch in _NO_LINE_START and len(current) <= rows):
                columns.append(current)
                current = ""
            current += ch
    if current:
        columns.append(current)
    return columns


@dataclass
class TextBlock:
    columns: list[str]
    size: int

    @property
    def column_width(self) -> float:
        return self.size * 1.2

    @property
    def width(self) -> int:
        return round(len(self.columns) * self.column_width)

    @property
    def height(self) -> int:
        return max((len(c) for c in self.columns), default=0) * self.size


def fit_text(text: str, max_width: int, max_height: int, size: int, min_size: int) -> TextBlock:
    """指定サイズから始めて、max_width × max_height に収まるまで文字を小さくする。"""
    block = TextBlock(layout_columns(text, max_height // size), size)
    while size > min_size and (block.width > max_width or block.height > max_height):
        size -= 1
        block = TextBlock(layout_columns(text, max_height // size), size)
    return _balanced(text, block)


def _balanced(text: str, block: TextBlock) -> TextBlock:
    """
    列の長さを揃える。上限いっぱいに詰めると最後の列に1〜2字だけ残ることがある
    (例:「過去と現在を重ねて考え/た」)ので、同じ列数に収まる範囲で1列の字数を減らす。
    """
    if len(block.columns) < 2:
        return block
    longest = max(len(c) for c in block.columns)
    for rows in range(-(-sum(len(c) for c in block.columns) // len(block.columns)), longest):
        columns = layout_columns(text, rows)
        if len(columns) <= len(block.columns):
            return TextBlock(columns, block.size)
    return block


def _rotated_glyph(ch: str, font: ImageFont.FreeTypeFont, size: int, stroke: int, angle: float) -> Image.Image:
    """1文字を透明背景のRGBA画像にして回転させる(黒文字・白フチ)。"""
    pad = stroke + 2
    glyph = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(glyph).text(
        (glyph.width / 2, glyph.height / 2),
        ch,
        font=font,
        fill=(0, 0, 0, 255),
        anchor="mm",
        stroke_width=stroke,
        stroke_fill=(255, 255, 255, 255),
    )
    return glyph.rotate(angle, resample=Image.Resampling.BICUBIC) if angle else glyph


def draw_text_block(
    img: Image.Image, block: TextBlock, center: tuple[float, float], font_path: Path, stroke: int = 0
) -> None:
    """stroke > 0 なら文字に白フチを付ける(透過した吹き出しの上でも読めるように)。"""
    size = block.size
    font = ImageFont.truetype(str(font_path), size)
    draw = ImageDraw.Draw(img)
    cx, cy = center
    col_w = block.column_width
    right = cx + block.width / 2
    top = cy - block.height / 2
    outline: dict[str, object] = {"stroke_width": stroke, "stroke_fill": (255, 255, 255)} if stroke else {}
    for ci, column in enumerate(block.columns):
        x = right - (ci + 1) * col_w + (col_w - size) / 2
        for ri, ch in enumerate(column):
            y = top + ri * size
            if ch in _ROTATE:
                glyph = _rotated_glyph(ch, font, size, stroke, -90)
                img.paste(glyph, (round(x + (size - glyph.width) / 2), round(y + (size - glyph.height) / 2)), glyph)
            elif ch in _UPPER_RIGHT:
                draw.text((x + size * 0.6, y - size * 0.55), ch, font=font, fill=(0, 0, 0), **outline)
            elif ch in _SMALL:
                draw.text((x + size * 0.12, y - size * 0.12), ch, font=font, fill=(0, 0, 0), **outline)
            else:
                draw.text((x, y), ch, font=font, fill=(0, 0, 0), **outline)


# 吹き出し(楕円)の内側に文字ブロックを収めるための倍率。楕円に内接する長方形は
# 外接長方形の約0.7倍なので、文字ブロックの1/0.7倍強の楕円にする。
_BUBBLE_SCALE_X = 1.45
_BUBBLE_SCALE_Y = 1.3
_BUBBLE_PAD = 10
_OUTLINE = 3


# 1列だけのセリフでも楕円が細くなりすぎないよう、幅は高さのこの割合以上にする
_BUBBLE_MIN_ASPECT = 0.5


def bubble_size(block: TextBlock) -> tuple[int, int]:
    height = round(block.height * _BUBBLE_SCALE_Y + _BUBBLE_PAD * 2)
    width = round(block.width * _BUBBLE_SCALE_X + _BUBBLE_PAD * 2)
    return max(width, round(height * _BUBBLE_MIN_ASPECT)), height


def draw_bubble(
    img: Image.Image,
    box: tuple[int, int, int, int],
    block: TextBlock,
    font_path: Path,
    tail_toward: tuple[float, float] | None = None,
    opacity: float = 1.0,
) -> None:
    """
    box(外接長方形)に楕円の吹き出しを描き、中央に縦書きで文字を置く。

    opacity は白い地の不透明度(0で輪郭線だけ)。輪郭線と文字は常に不透明。
    楕円としっぽを1つの形(マスク)にまとめてから輪郭を取るので、地を透かしても
    しっぽの付け根に楕円の線が残らない。
    """
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    tail_len = min(rx, ry) * 0.55

    # 吹き出しとしっぽが収まる範囲だけで描いて貼る
    margin = round(tail_len) + _OUTLINE * 4
    ox, oy = x0 - margin, y0 - margin
    mask = Image.new("L", (x1 - x0 + margin * 2, y1 - y0 + margin * 2), 0)
    shape = ImageDraw.Draw(mask)
    shape.ellipse((margin, margin, margin + x1 - x0, margin + y1 - y0), fill=255)
    if tail_toward is not None:
        tx, ty = tail_toward
        dx, dy = tx - cx, ty - cy
        length = max((dx * dx + dy * dy) ** 0.5, 1)
        ux, uy = dx / length, dy / length
        edge = 1 / max(((ux / rx) ** 2 + (uy / ry) ** 2) ** 0.5, 1e-6)
        # 付け根は楕円の少し内側から出し、形の継ぎ目ができないようにする
        ex = cx + ux * (edge - _OUTLINE * 3) - ox
        ey = cy + uy * (edge - _OUTLINE * 3) - oy
        tip = (ex + ux * (tail_len + _OUTLINE * 3), ey + uy * (tail_len + _OUTLINE * 3))
        base_half = min(rx, ry) * 0.16
        px, py = -uy, ux
        shape.polygon(
            [(ex + px * base_half, ey + py * base_half), tip, (ex - px * base_half, ey - py * base_half)],
            fill=255,
        )
    inner = mask.filter(ImageFilter.MinFilter(_OUTLINE * 2 + 1))
    outline = ImageChops.subtract(mask, inner)

    region = (ox, oy, ox + mask.width, oy + mask.height)
    if opacity < 0.95:
        # 地を透かすと暗い背景では黒い輪郭が見えなくなるので、外側に細い白フチを足す
        halo = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(5)), mask)
        img.paste((255, 255, 255), region, halo)
    if opacity > 0:
        img.paste((255, 255, 255), region, inner.point(lambda v: round(v * opacity)))
    img.paste((0, 0, 0), region, outline)
    # 地が透けるほど背景の線と文字が混ざるので、白フチを付けて読めるようにする
    stroke = 0 if opacity >= 0.95 else max(2, block.size // 10)
    draw_text_block(img, block, (cx, cy), font_path, stroke=stroke)


# ---- ナレーション ----

_NARRATION_PAD = 12
_NARRATION_BORDER = 2


def narration_size(block: TextBlock) -> tuple[int, int]:
    return block.width + _NARRATION_PAD * 2, block.height + _NARRATION_PAD * 2


def draw_narration(img: Image.Image, box: tuple[int, int, int, int], block: TextBlock, font_path: Path) -> None:
    """地の文を入れる四角い枠(白地・細い黒枠)。吹き出しと違って透過させない。"""
    draw = ImageDraw.Draw(img)
    draw.rectangle(box, fill=(255, 255, 255), outline=(0, 0, 0), width=_NARRATION_BORDER)
    x0, y0, x1, y1 = box
    draw_text_block(img, block, ((x0 + x1) / 2, (y0 + y1) / 2), font_path)


# ---- 効果音(描き文字) ----

# 描き文字の大きさ(コマの短辺に対する割合)と上限・下限
_SFX_SIZE_RATIO = 0.16
_SFX_MAX_SIZE = 130
SFX_MIN_SIZE = 40
# 1列に並べる最大文字数。長い効果音は2列以上にする。
_SFX_MAX_ROWS = 6
_SFX_COLUMN_GAP = 1.05
_SFX_CHAR_STEP = 0.92


@dataclass
class SfxLayout:
    columns: list[str]
    size: int

    @property
    def width(self) -> int:
        return round(len(self.columns) * self.size * _SFX_COLUMN_GAP + self.size * 0.3)

    @property
    def height(self) -> int:
        # 描き文字は字間を詰める。後ろの文字ほど大きくするぶんの余裕も見る。
        return round(max((len(c) for c in self.columns), default=0) * self.size * _SFX_CHAR_STEP + self.size * 0.4)


def fit_sfx(text: str, panel_w: int, panel_h: int, max_size: int = _SFX_MAX_SIZE) -> SfxLayout:
    size = max(min(round(min(panel_w, panel_h) * _SFX_SIZE_RATIO), max_size), SFX_MIN_SIZE)
    chars = "".join(ch for ch in text if ch not in _SPACES)
    while True:
        columns = [chars[i : i + _SFX_MAX_ROWS] for i in range(0, len(chars), _SFX_MAX_ROWS)] or [""]
        layout = SfxLayout(columns, size)
        if size <= SFX_MIN_SIZE or (layout.height <= panel_h * 0.85 and layout.width <= panel_w * 0.6):
            return layout
        size -= 4


def draw_sfx(img: Image.Image, box: tuple[int, int, int, int], layout: SfxLayout, font_path: Path) -> None:
    """
    吹き出しを使わない描き文字。黒字に太い白フチで、文字ごとに少し傾けて揺らす。
    揺れは文字列から決まる乱数で付けるので、同じ効果音は何度合成しても同じ形になる。
    """
    x0, y0, x1, _ = box
    size = layout.size
    stroke = max(4, size // 9)
    rng = random.Random("".join(layout.columns))
    for ci, column in enumerate(layout.columns):
        col_center = x1 - size * 0.15 - (ci + 0.5) * size * _SFX_COLUMN_GAP
        for ri, ch in enumerate(column):
            # 後の文字ほど少し大きく(勢いが増していくように)
            glyph_size = round(size * (1 + 0.15 * ri / max(len(column) - 1, 1)))
            font = ImageFont.truetype(str(font_path), glyph_size)
            angle = rng.uniform(-14, 14) + (-90 if ch in _ROTATE else 0)
            glyph = _rotated_glyph(ch, font, glyph_size, stroke, angle)
            gx = col_center + rng.uniform(-0.08, 0.08) * size
            gy = y0 + size * 0.2 + ri * size * _SFX_CHAR_STEP + size / 2
            img.paste(glyph, (round(gx - glyph.width / 2), round(gy - glyph.height / 2)), glyph)
