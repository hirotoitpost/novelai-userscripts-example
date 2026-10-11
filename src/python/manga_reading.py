"""
取り込んだ漫画のページから、セリフ(吹き出しの中の文字)と、コマごとの場面・所作を読み取る。

- セリフ: RapidOCR でページ全体を読む(縦書きの1列ごとに読める。実機で、吹き出しごとに切り出して読むより
  ずっと正確だった)。読めた列を吹き出しごとにまとめ、右から左へつないで1つのセリフにする。手描きの効果音
  (描き文字)はほとんど読めず、読めても誤りが多いので、文字の領域の検出(manga_elements の text)に重なるものと、
  確からしさの高いものだけを残す。
- 場面・所作: コマを切り出して判定モデル(image_tagger)にかけ、舞台・小物・構図のタグ(場面)と、表情・動作の
  タグ(所作)に分ける。髪や服などの見た目はキャラシートの側で持つので、ここでは取らない。体つき・露出・性的な
  タグは使わない(content_guard)。

取り込みの使い方(routes/manga_import.py の purpose)によって、残すものが違う:
- rebuild(自分の作品を作り直す): セリフの文面を残す。
- similar(似た漫画を作る): セリフの文面は残さず、数・長さ・文末の型・口調だけを残す(新しいセリフを作るための型)。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

Box = tuple[float, float, float, float]  # x0, y0, x1, y1(ページのピクセル座標)

# 読めた列として使う確からしさ(文字の領域の検出に重なるとき / 重ならないとき)
_MIN_SCORE = 0.6
_MIN_SCORE_ALONE = 0.9
# 同じ吹き出しの列とみなす、列どうしのすき間(列の太さに対する割合)と、重なり(短い方の長さに対する割合)
_COLUMN_GAP = 1.1
_COLUMN_OVERLAP = 0.3
# 1つのセリフの長さの上限(台本の1セリフの上限に合わせる)
MAX_LINE_CHARS = 80
# 場面・所作のタグとして使う確率と、1コマあたりの数
_TAG_PROB = 0.45
_MAX_SCENE_TAGS = 8
_MAX_ACTION_TAGS = 8

_ocr_engine = None
_ocr_lock = threading.Lock()


def ocr_engine() -> Any:
    """RapidOCR(読み込みが重いので使うときだけ作り、使い回す)。呼ぶ側で ocr_lock を取ること。"""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_lock() -> threading.Lock:
    return _ocr_lock


# ---- セリフ ----


@dataclass
class TextColumn:
    """読めた文字の1列(縦書きなら縦の1行、横書きなら横の1行)。"""

    box: Box
    text: str
    score: float

    @property
    def vertical(self) -> bool:
        return (self.box[3] - self.box[1]) >= (self.box[2] - self.box[0])


def _ocr_columns(image: Image.Image) -> list[TextColumn]:
    bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
    with _ocr_lock:
        result = ocr_engine()(bgr)
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    columns = []
    for points, text, score in zip(boxes, result.txts or [], result.scores or []):
        xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
        text = str(text).strip()
        if text:
            columns.append(TextColumn((min(xs), min(ys), max(xs), max(ys)), text, float(score)))
    return columns


def _intersects(a: Box, b: Box) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _same_bubble(a: TextColumn, b: TextColumn) -> bool:
    """隣り合う列か(縦書きなら横に並び、縦の範囲が重なる。横書きならその逆)。"""
    if a.vertical != b.vertical:
        return False
    along, across = (1, 0) if a.vertical else (0, 1)
    overlap = min(a.box[along + 2], b.box[along + 2]) - max(a.box[along], b.box[along])
    shorter = min(a.box[along + 2] - a.box[along], b.box[along + 2] - b.box[along])
    if overlap < shorter * _COLUMN_OVERLAP:
        return False
    gap = max(a.box[across], b.box[across]) - min(a.box[across + 2], b.box[across + 2])
    thickness = max(a.box[across + 2] - a.box[across], b.box[across + 2] - b.box[across])
    return gap <= thickness * _COLUMN_GAP


def group_columns(columns: list[TextColumn]) -> list[list[TextColumn]]:
    """列を吹き出しごとにまとめ、読む順(縦書きは右から左、横書きは上から下)に並べる。"""
    parent = list(range(len(columns)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            if _same_bubble(columns[i], columns[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[TextColumn]] = {}
    for index, column in enumerate(columns):
        groups.setdefault(find(index), []).append(column)
    result = []
    for members in groups.values():
        if members[0].vertical:
            members.sort(key=lambda c: -(c.box[0] + c.box[2]))
        else:
            members.sort(key=lambda c: c.box[1] + c.box[3])
        result.append(members)
    return result


_NOISE = re.compile(r"^[\W_ー〜~・…。、！？!?♪♡♥☆★0-9a-zA-Z]*$")


def _clean_text(text: str) -> str:
    """読み取りの揺れをそろえる(半角の記号を全角に、空白を除く)。"""
    text = re.sub(r"\s+", "", text)
    for src, dst in (
        ("!", "！"),
        ("?", "？"),
        ("...", "…"),
        ("。。。", "…"),
        ("・・・", "…"),
        ("~", "〜"),
        ("～", "〜"),
    ):
        text = text.replace(src, dst)
    return re.sub(r"…{3,}", "……", text)


@dataclass
class Speech:
    """吹き出し1つ分のセリフ。box はページ上の位置。"""

    box: Box
    text: str


def read_speech(image: Image.Image, text_boxes: list[Box] | None) -> list[Speech]:
    """
    ページのセリフ。text_boxes は文字の領域の検出(manga_elements の text。無ければ None)。
    検出があるときは、それに重なる列だけを使う(効果音や背景の文字を除く)。
    """
    columns = []
    for column in _ocr_columns(image):
        near_text = text_boxes is None or any(_intersects(column.box, b) for b in text_boxes)
        if column.score >= (_MIN_SCORE if near_text and text_boxes is not None else _MIN_SCORE_ALONE):
            columns.append(column)
    speeches = []
    for members in group_columns(columns):
        text = _clean_text("".join(c.text for c in members))
        # 記号や英数字だけのもの(効果音のかけら・ページ番号など)は捨てる。「……」「!?」だけのセリフは残す
        if not text or _NOT_SPEECH.match(text) or (_NOISE.match(text) and not re.search(r"[…！？!?]", text)):
            continue
        box = (
            min(c.box[0] for c in members),
            min(c.box[1] for c in members),
            max(c.box[2] for c in members),
            max(c.box[3] for c in members),
        )
        speeches.append(Speech(box, text[:MAX_LINE_CHARS]))
    return speeches


_POLITE = re.compile(r"(です|ます|ません|でした|ました|でしょう|ください|ございま)")


def line_features(text: str) -> dict[str, Any]:
    """セリフの型(文面を使わずに、新しいセリフを作るための手がかり): 文字数・文末の型・口調。"""
    body = text.rstrip("♪♡♥☆★〜ー")
    if re.search(r"[？?]", body[-3:]):
        ending = "問いかけ"
    elif re.search(r"[！!]", body[-3:]):
        ending = "強い調子"
    elif re.search(r"(…|‥|\.\.)$", body) or body.endswith("―"):
        ending = "言いよどみ"
    else:
        ending = "ふつう"
    return {"chars": len(text), "ending": ending, "polite": bool(_POLITE.search(text))}


def order_in_panel(speeches: list[Speech], rect: tuple[int, int, int, int]) -> list[Speech]:
    """コマの中の読む順(上の段から、同じ段では右から)。段はコマの高さの3分の1ずつ。"""
    _, y, _, h = rect
    band = max(h / 3, 1)
    return sorted(speeches, key=lambda s: (int((s.box[1] - y) // band), -s.box[2]))


# ---- 場面・所作 ----

# 構図のタグ(場面として残す)
_FRAMING = {
    "close-up",
    "upper body",
    "cowboy shot",
    "full body",
    "portrait",
    "from side",
    "from above",
    "from below",
    "from behind",
    "profile",
    "dutch angle",
    "pov",
}
# 場面にも所作にも使わないタグ(漫画の画面の作りや、絵柄)
_SKIP = {
    "1koma",
    "2koma",
    "3koma",
    "4koma",
    "segmented comic",
    "black border",
    "border",
    "thought bubble",
    "spoken ellipsis",
    "spoken heart",
    "spoken question mark",
    "spoken exclamation mark",
    "spoken interrobang",
    "spoken sweatdrop",
    "gradient background",
    "halftone",
    "halftone background",
    "screentone",
    "dated",
    "signature",
    "artist name",
    "watermark",
    "page number",
    "copyright name",
    "character name",
    "bag",
    "school bag",
    "jewelry",
}
# 見た目(髪型・小物)なのに、見た目の語の一覧に無いもの
_LOOK_EXTRA = re.compile(r"(side up|scrunchie|ahoge|sidelocks|hair between eyes|hair over|fang)")
# 表情・漫画の記号なのに、人物のタグの一覧に無いもの(所作に入れる)
_ACTION_EXTRA = {
    "anger vein",
    "shaded face",
    "sideways glance",
    "flying sweatdrops",
    "wavy mouth",
    "nose blush",
    "turn pale",
    "trembling",
    "surprised",
    "tearing up",
}
# ページの終わりの印など、セリフではない文字
_NOT_SPEECH = re.compile(r"^(おわり|終わり|おしまい|つづく|続く|完|end|fin)[。.!！]*$", re.IGNORECASE)
# 顔文字のタグ(:d, ;d, > < など)は表情
_EMOTICON = re.compile(r"^[^a-z0-9]*[a-z0-9]?[^a-z0-9]*$")


def describe_panel(crop: Image.Image) -> tuple[list[str], list[str], list[str]]:
    """
    コマの(場面のタグ, 所作のタグ, 写っている人のタグ)。場面は舞台・小物・構図、所作は表情・動作・人物どうしの
    関わり、写っている人は 1girl / 1boy など(だれを描くかを決める手がかり)。
    """
    from .image_tagger import _COUNT_TAGS, tag_image
    from .manga_similar import _NOT_SETTING, _is_look, _is_person_or_look, _is_safe

    scene: list[str] = []
    action: list[str] = []
    framing: list[str] = []
    cast: list[str] = []
    for score in tag_image(crop):
        tag = score.tag
        if score.category != "general" or score.probability < _TAG_PROB:
            continue
        if tag in _FRAMING:
            framing.append(tag)
            continue
        if re.match(r"^\d\+?(girls?|boys?|others?)$", tag):
            cast.append(tag)
            continue
        if (
            tag in _SKIP
            or tag in _COUNT_TAGS
            or re.match(r"^(\d\+?|multiple )(girls?|boys?|others?)$", tag)
            or not _is_safe(tag)
            or _is_look(tag)
            or _LOOK_EXTRA.search(tag)
        ):
            continue
        if _EMOTICON.match(tag) or tag in _ACTION_EXTRA or _is_person_or_look(tag):
            if tag not in _NOT_SETTING:
                action.append(tag)
        elif tag not in _NOT_SETTING:
            scene.append(tag)
    return [*framing[:2], *scene[:_MAX_SCENE_TAGS]], action[:_MAX_ACTION_TAGS], cast[:3]
