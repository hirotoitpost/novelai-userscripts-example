"""
取り込んだ漫画のページから、セリフ(吹き出しの中の文字)と、描き文字の場所と、コマごとの場面・所作を読み取る。

- セリフ: RapidOCR でページ全体を読む(縦書きの1列ごとに読める。実機で、吹き出しごとに切り出して読むより
  ずっと正確だった)。読めた列を吹き出しごとにまとめ、右から左へつないで1つのセリフにする。英語に訳した漫画
  (ページの文字の多くが英字)は、吹き出しの英語の行を上から下へ、語の間を空けてつなぐ(英字だけの行を雑音として
  捨てない。2026-10 実機: 捨てていたため、英訳の漫画でセリフが 25〜40% しか取れなかった)。手描きの効果音
  (描き文字)はほとんど読めず、読めても誤りが多いので、文字の領域の検出(manga_elements の text)に重なるものと、
  確からしさの高いものだけを残す。
- 描き文字(手描きの効果音): 読めないので、場所の候補だけを探す(文字らしい領域のうち、セリフでないもの)。
  読みは画面で入れてもらう。
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
        # 英字は横書き(「I」「A」のような1字の行は、縦長の枠になっても横書きの吹き出しの1行)
        if _LATIN.search(self.text):
            return False
        return (self.box[3] - self.box[1]) >= (self.box[2] - self.box[0])

    @property
    def mark(self) -> bool:
        """記号だけの短い列(「?」「!!」「…」)。向きが決まらないので、隣の列の向きに合わせる。"""
        return bool(_MARKS_ONLY.match(self.text.strip()))


def _ocr_columns(image: Image.Image) -> list[TextColumn]:
    bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
    with _ocr_lock:
        # 検出・向き・認識を毎回はっきり指定する。RapidOCR は直前の呼び出しの指定を覚えていて、場所だけを探す呼び出し
        # (use_rec=False)のあとに指定なしで呼ぶと、場所だけの結果を返す(文字が1つも読めなくなる。実機で確認)
        result = ocr_engine()(bgr, use_det=True, use_cls=True, use_rec=True)
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    # 文字が1つも見つからないページ
    if boxes is None or not texts:
        return []
    columns = []
    for points, text, score in zip(boxes, texts, getattr(result, "scores", None) or []):
        xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
        text = str(text).strip()
        if text:
            columns.append(TextColumn((min(xs), min(ys), max(xs), max(ys)), text, float(score)))
    return columns


def _intersects(a: Box, b: Box) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _same_bubble(a: TextColumn, b: TextColumn) -> bool:
    """隣り合う列か(縦書きなら横に並び、縦の範囲が重なる。横書きならその逆)。記号だけの列は相手の向きで見る。"""
    if a.mark and b.mark:
        return False
    if a.mark or b.mark:
        vertical = b.vertical if a.mark else a.vertical
    elif a.vertical != b.vertical:
        return False
    else:
        vertical = a.vertical
    along, across = (1, 0) if vertical else (0, 1)
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
        words = [c for c in members if not c.mark] or members
        if sum(c.vertical for c in words) * 2 >= len(words) and not any(_LATIN.search(c.text) for c in words):
            members.sort(key=lambda c: -(c.box[0] + c.box[2]))
        else:
            members.sort(key=lambda c: c.box[1] + c.box[3])
        result.append(members)
    return result


_NOISE = re.compile(r"^[\W_ー〜~・…。、！？!?♪♡♥☆★0-9a-zA-Z]*$")
_LATIN = re.compile(r"[A-Za-z]")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
# 記号だけの短い列(「?」「!?」「…」「♥」)
_MARKS_ONLY = re.compile(r"^[^\w\u3040-\u30ff\u3400-\u9fff]{1,3}$")
# 英語のセリフとみなす語(英字2字以上の語。「OS」「W,I」のような描き文字の読み違いのかけらは、1語だけなら捨てる)
_ENGLISH_WORD = re.compile(r"[A-Za-z][A-Za-z']+")


def _clean_text(text: str) -> str:
    """読み取りの揺れをそろえる(半角の記号を全角に、空白を除く。英字の語の間の空白は1つにして残す)。"""
    if _LATIN.search(text):
        text = re.sub(r"\s+([!?.,…~])", r"\1", re.sub(r"\s+", " ", text).strip())
    else:
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


def _join(members: list[TextColumn]) -> str:
    """
    吹き出しの列を1つのセリフにつなぐ。日本語は詰めてつなぐ。英語は語の間を空け、行末の「-」で切れた語
    (EVERY- / THING)は1語に戻す。「H-HMPH」のような言いよどみの「-」(前が1字)と、記号だけの列は詰める。
    """
    if not any(_LATIN.search(c.text) for c in members):
        return "".join(c.text for c in members)
    text = ""
    for c in members:
        part = c.text.strip()
        if not text:
            text = part
        elif c.mark:
            text += part
        elif re.search(r"[A-Za-z]{2,}-$", text) and part[:1].isalpha():
            text = text[:-1] + part
        elif text.endswith("-"):
            text += part
        else:
            text += " " + part
    return text


def _english_page(columns: list[TextColumn]) -> bool:
    """英語に訳した漫画のページか(英字の列が、日本語の列より多い)。日本語の漫画の、描き文字の読み違い(英字のかけら)はセリフにしない。"""
    latin = sum(1 for c in columns if _LATIN.search(c.text) and not _CJK.search(c.text))
    return latin > sum(1 for c in columns if _CJK.search(c.text))


def _is_english_speech(text: str) -> bool:
    words = _ENGLISH_WORD.findall(text)
    return len(words) >= 2 or any(len(w) >= 3 for w in words)


def read_speech(
    image: Image.Image, text_boxes: list[Box] | None, page_columns: list[TextColumn] | None = None
) -> list[Speech]:
    """
    ページのセリフ。text_boxes は文字の領域の検出(manga_elements の text。無ければ None)。
    検出があるときは、それに重なる列だけを使う(効果音や背景の文字を除く)。
    page_columns は、ページを読んだ結果(渡さなければここで読む)。
    """
    columns = []
    for column in page_columns if page_columns is not None else _ocr_columns(image):
        near_text = text_boxes is None or any(_intersects(column.box, b) for b in text_boxes)
        if column.score >= (_MIN_SCORE if near_text and text_boxes is not None else _MIN_SCORE_ALONE):
            columns.append(column)
    english = _english_page(columns)
    speeches = []
    for members in group_columns(columns):
        text = _clean_text(_join(members))
        # 記号や英数字だけのもの(効果音のかけら・ページ番号など)は捨てる。「……」「!?」だけのセリフと、英訳の漫画の英語のセリフは残す
        noise = _NOISE.match(text) and not re.search(r"[…！？!?]", text) and not (english and _is_english_speech(text))
        if not text or _NOT_SPEECH.match(text) or noise:
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


# 英語のセリフの長さを、日本語のセリフの字数の目安にする割合(英字の数 × これ。おおよその目安)
_EN_TO_JA = 0.6


def line_features(text: str) -> dict[str, Any]:
    """
    セリフの型(文面を使わずに、新しいセリフを作るための手がかり): 文字数・文末の型・口調。
    英語のセリフ(英訳の漫画)の文字数は、日本語のセリフにしたときの字数の目安(英字の数 × _EN_TO_JA)。
    """
    body = text.rstrip("♪♡♥☆★〜ー ")
    if re.search(r"[？?]", body[-3:]):
        ending = "問いかけ"
    elif re.search(r"[！!]", body[-3:]):
        ending = "強い調子"
    elif re.search(r"(…|‥|\.\.)$", body) or body.endswith("―"):
        ending = "言いよどみ"
    else:
        ending = "ふつう"
    if _LATIN.search(text) and not _CJK.search(text):
        chars = max(1, round(len(re.findall(r"[A-Za-z]", text)) * _EN_TO_JA))
    else:
        chars = len(text)
    return {"chars": chars, "ending": ending, "polite": bool(_POLITE.search(text))}


def order_in_panel(speeches: list[Speech], rect: tuple[int, int, int, int]) -> list[Speech]:
    """コマの中の読む順(上の段から、同じ段では右から)。段はコマの高さの3分の1ずつ。"""
    _, y, _, h = rect
    band = max(h / 3, 1)
    return sorted(speeches, key=lambda s: (int((s.box[1] - y) // band), -s.box[2]))


# ---- 描き文字 ----

# 描き文字を探すときの縮小率。大きな描き文字は、縮めると文字の検出器が知っている大きさに近づく
# (実機で、縦に長い大きな描き文字は縮めたときだけ見つかった)
_SFX_SCALES = (1.0, 0.5, 0.33)
# セリフの領域にこれ以上重なる候補は、セリフとみなして除く
_SFX_SPEECH_OVERLAP = 0.3
# 候補として残す大きさ(短い辺のピクセル、ページに対する面積の割合)
_SFX_MIN_SIDE = 24
_SFX_MIN_AREA = 0.001
_SFX_MAX_AREA = 0.35
# 別の縮小率で見つけた候補が、これ以上重なっていれば同じものとみなす
_SFX_SAME = 0.5


def _covered(a: Box, b: Box) -> float:
    """a のうち b に重なっている割合。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0.0) * max(h, 0.0) / max((a[2] - a[0]) * (a[3] - a[1]), 1.0)


def _detect_text_boxes(image: Image.Image, scale: float) -> list[Box]:
    """文字らしい領域(読まずに、場所だけ)。scale で縮めてから探し、元の大きさの座標で返す。"""
    small = image if scale == 1.0 else image.resize((max(round(image.width * scale), 1), max(round(image.height * scale), 1)))
    bgr = np.ascontiguousarray(np.asarray(small.convert("RGB"))[:, :, ::-1])
    with _ocr_lock:
        result = ocr_engine()(bgr, use_det=True, use_cls=False, use_rec=False)
    boxes = getattr(result, "boxes", None)
    found = []
    for points in boxes if boxes is not None else []:
        xs, ys = [float(p[0]) / scale for p in points], [float(p[1]) / scale for p in points]
        found.append((min(xs), min(ys), max(xs), max(ys)))
    return found


def end_mark_boxes(page_columns: list[TextColumn]) -> list[Box]:
    """
    「おわり」「つづく」などの印の場所。ページ全体を読んだ結果から探す(活字なので読める。印だけを切り抜いて
    読み直すと、余白や大きさを変えても読めなかった)。
    """
    return [c.box for c in page_columns if _NOT_SPEECH.match(_clean_text(c.text))]


def _without_speech(image: Image.Image, speech_boxes: list[Box]) -> Image.Image:
    """セリフの文字を白く塗った絵。目立つ活字を消すと、手描きの文字に検出器が反応しやすくなる(実機で確認)。"""
    from PIL import ImageDraw

    copy = image.convert("RGB").copy()
    draw = ImageDraw.Draw(copy)
    for b in speech_boxes:
        draw.rectangle((b[0] - 4, b[1] - 4, b[2] + 4, b[3] + 4), fill=(255, 255, 255))
    return copy


def _merge_candidates(boxes: list[Box]) -> list[Box]:
    """
    同じ描き文字を指す候補(半分以上重なるもの)をまとめて、1つずつ選ぶ。縮めて探した枠はまわりを広く含みがちで、
    等倍で探した枠は一部だけのことがあるので、まとまりの中の標準的な大きさ(中央値の2倍まで)でいちばん大きい枠を選ぶ。
    """
    parent = list(range(len(boxes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _covered(boxes[i], boxes[j]) >= _SFX_SAME or _covered(boxes[j], boxes[i]) >= _SFX_SAME:
                parent[find(i)] = find(j)
    groups: dict[int, list[Box]] = {}
    for index, box in enumerate(boxes):
        groups.setdefault(find(index), []).append(box)

    def area(b: Box) -> float:
        return (b[2] - b[0]) * (b[3] - b[1])

    chosen = []
    for members in groups.values():
        areas = sorted(area(b) for b in members)
        median = areas[len(areas) // 2]
        chosen.append(max((b for b in members if area(b) <= 2 * median), key=area))
    return sorted(chosen, key=lambda b: (b[1], b[0]))


def detect_sfx(
    image: Image.Image,
    speech_boxes: list[Box],
    face_boxes: list[Box] | None = None,
    exclude_boxes: list[Box] | None = None,
) -> list[Box]:
    """
    ページの描き文字(手描きの効果音)の場所の候補。文字らしい領域のうち、セリフでないもの。
    元の絵と、セリフを白く塗った絵の両方を、縮小率を変えて探し、結果を合わせる。
    読みは分からない(手描きの文字は読めない)ので、場所だけを返す。見落としも、絵の一部(手など)を拾うことも
    あるので、画面で確かめて外せるようにしておく前提。docs/trials/2026-10-12_sfx-detection.md を参照。
    exclude_boxes は、描き文字ではないと分かっている場所(「おわり」の印など)。
    """
    page_area = image.width * image.height
    candidates: list[Box] = []
    for source in (image, _without_speech(image, speech_boxes)):
        for scale in _SFX_SCALES:
            for box in _detect_text_boxes(source, scale):
                w, h = box[2] - box[0], box[3] - box[1]
                if min(w, h) < _SFX_MIN_SIDE or not (_SFX_MIN_AREA <= w * h / page_area <= _SFX_MAX_AREA):
                    continue
                if any(_covered(box, s) >= _SFX_SPEECH_OVERLAP for s in speech_boxes):
                    continue
                # 顔の中の小さな候補(目など)
                if any(_covered(box, f) >= 0.8 and w * h < 0.5 * (f[2] - f[0]) * (f[3] - f[1]) for f in face_boxes or []):
                    continue
                candidates.append(box)
    return [
        box
        for box in _merge_candidates(candidates)
        if not any(_covered(e, box) >= _SFX_SAME or _covered(box, e) >= _SFX_SAME for e in exclude_boxes or [])
    ]


@dataclass
class PageReading:
    """ページから読んだもの: セリフと、描き文字の場所の候補。"""

    speeches: list[Speech]
    sfx_boxes: list[Box]


def read_page(image: Image.Image, text_boxes: list[Box] | None, face_boxes: list[Box] | None = None) -> PageReading:
    """
    ページのセリフと描き文字を読む。ページ全体の文字の読み取りは1回だけ行い、セリフと、終わりの印の両方に使う。
    text_boxes / face_boxes は manga_elements の検出(無ければ None)。
    """
    columns = _ocr_columns(image)
    speeches = read_speech(image, text_boxes, columns)
    # セリフの場所: 文字の領域の検出と、読めたセリフの場所(検出に漏れた吹き出しの文字を、描き文字にしない)
    speech_boxes = [*(text_boxes or []), *(s.box for s in speeches)]
    return PageReading(speeches, detect_sfx(image, speech_boxes, face_boxes, end_mark_boxes(columns)))


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
