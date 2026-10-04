"""
チャットアプリのスクリーンショットから会話を読み取り、物語の本文にする(OCR)。

対象は、キャラの台詞が左寄せの暗い吹き出し、地の文が吹き出しなしの白文字(左に縦線)、
自分の発言が右寄せの赤い吹き出し(1行目が斜体の行動、2行目が台詞)という形式。

- 文字認識は RapidOCR(PP-OCRv6 small、パッケージ同梱のモデル)。1枚 2 秒ほど。
  ローカルの画像モデル(qwen2.5vl)や Windows OCR より速く正確だった。
- 行の種類は、位置(左端)と色(吹き出しの背景色・文字色)で見分ける。
- 続けて撮ったスクショは重なっているので、merge_pages() で重複を除いてつなぐ。
  スクロールで戻って撮った分も、既に読んだ発言と一致すれば捨てる。

濁点付きの母音(「あ゛」など)は認識できず、濁点が落ちたり別の文字になったりする。
"""

from __future__ import annotations

import io
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Literal

import numpy as np
from PIL import Image

BlockKind = Literal["character", "narration", "user_action", "user_speech"]

# 画面の上下(時計・ツールバー・進捗、入力欄・ボタン)を除く範囲。高さに対する割合。
CONTENT_TOP = 0.165
CONTENT_BOTTOM = 0.885
# 台詞の吹き出しの文字は幅の約5%、地の文は約8.5%から始まる
DIALOGUE_MAX_X = 0.075
NARRATION_MAX_X = 0.12
# 右端のボタン(↓ など)
RIGHT_EDGE_X = 0.9

# 画面下のボタン類(切り取り範囲に入り込んだとき用)
_UI_TEXT_RE = re.compile(r"話しかけてみよう|おすすめ|状況|状况|どきどき")

# 句読点・感嘆符(「....」「!!」のように半角で読まれることがある)も日本語の文の一部として数える
_JA_CHAR_RE = re.compile(r"[぀-ヿ一-鿿　-〿！-｠…‥ー〜.!?]")


def _japanese_ratio(text: str) -> float:
    chars = text.replace(" ", "")
    return len(_JA_CHAR_RE.findall(chars)) / len(chars) if chars else 0.0


_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR  # 読み込みが重いので使うときだけ

        _engine = RapidOCR()
    return _engine


@dataclass
class _Row:
    x0: int
    x1: int
    y0: int
    y1: int
    text: str
    kind: BlockKind | None


@dataclass
class ChatBlock:
    kind: BlockKind
    text: str
    # 画面の上端・下端で切れているかもしれない(続きが前後のスクショにある)
    cut_top: bool = False
    cut_bottom: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _median_color(pixels: np.ndarray) -> np.ndarray:
    return np.median(pixels.reshape(-1, 3), axis=0) if pixels.size else np.zeros(3)


def _classify(img: np.ndarray, x0: int, x1: int, y0: int, y1: int, width: int) -> BlockKind | None:
    region = img[y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)].reshape(-1, 3).astype(int)
    if len(region) < 4:
        return None
    luma = region.mean(axis=1)
    bg = _median_color(region[luma <= np.percentile(luma, 40)])
    fg = _median_color(region[luma >= np.percentile(luma, 92)])

    r, g, b = bg
    if r > 110 and r > g * 2 and r > b * 1.6:
        # 自分の発言(赤い吹き出し)。斜体の行動はピンク、台詞は白。
        return "user_action" if fg[0] - fg[1] > 30 else "user_speech"
    if x0 < width * DIALOGUE_MAX_X and bg.max() < 80:
        return "character"
    if x0 < width * NARRATION_MAX_X:
        return "narration"
    return None


def _rows_from_ocr(img: np.ndarray, top: int, result) -> list[_Row]:
    """OCR の結果を行にまとめる。同じ高さに分かれて検出された断片は1行につなぐ。"""
    width = img.shape[1]
    boxes = []
    if result.boxes is None or result.txts is None:
        return []
    for box, text, score in zip(result.boxes, result.txts, result.scores):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, x1 = int(min(xs)), int(max(xs))
        y0, y1 = int(min(ys)) + top, int(max(ys)) + top
        text = text.strip()
        if not text or x0 > width * RIGHT_EDGE_X or _UI_TEXT_RE.search(text):
            continue
        # 濁点や記号だけの断片は、読めない文字のゴミ(「iic--单单单」など)になりやすい
        if score < 0.6 or _japanese_ratio(text) < 0.6:
            continue
        boxes.append((x0, x1, y0, y1, text))

    boxes.sort(key=lambda b: (b[2], b[0]))
    lines: list[list[tuple[int, int, int, int, str]]] = []
    for box in boxes:
        center = (box[2] + box[3]) / 2
        if lines:
            last = lines[-1]
            ly0 = min(b[2] for b in last)
            ly1 = max(b[3] for b in last)
            if ly0 <= center <= ly1:
                last.append(box)
                continue
        lines.append([box])

    rows = []
    for parts in lines:
        parts.sort(key=lambda b: b[0])
        x0 = parts[0][0]
        x1 = max(p[1] for p in parts)
        y0 = min(p[2] for p in parts)
        y1 = max(p[3] for p in parts)
        text = " ".join(p[4] for p in parts)
        rows.append(_Row(x0, x1, y0, y1, text, _classify(img, x0, x1, y0, y1, width)))
    return rows


_KANA = r"ぁ-ゖァ-ヺー"
_KATAKANA = r"ァ-ヺー"


def _fix_kana(text: str) -> str:
    """OCR が取り違えやすい、かなと形の似た字を直す。"""
    # 「二ギニギ」→「ニギニギ」、「力タ」→「カタ」(カタカナに挟まれた・隣り合う漢字)
    for wrong, right in (("二", "ニ"), ("力", "カ"), ("口", "ロ"), ("夕", "タ"), ("卜", "ト")):
        text = re.sub(rf"(?<=[{_KATAKANA}]){wrong}|{wrong}(?=[{_KATAKANA}])", right, text)
    # 「な一んだ」→「なーんだ」(かなの隣の漢数字の一は長音)
    text = re.sub(rf"(?<=[{_KANA}])一|一(?=[{_KANA}])", "ー", text)
    # 三点リーダが「....」と半角で読まれる
    text = re.sub(r"\.{3,}", "……", text)
    # 「ほおおおつ……」「……つ、」→「っ」(感嘆の小さい「っ」が大きく読まれる)
    text = re.sub(r"つ(?=[…‥!！?？~〜」]|$)", "っ", text)
    text = re.sub(r"(?<=[…‥])つ", "っ", text)
    return text


def _join_lines(lines: list[str]) -> str:
    """折り返された行をつなぐ。日本語なので区切りは入れない。"""
    text = "".join(lines)
    text = text.replace("　", " ")
    return _fix_kana(re.sub(r" {2,}", " ", text).strip())


def _blocks_from_rows(rows: list[_Row], top: int, bottom: int) -> list[ChatBlock]:
    rows = [r for r in rows if r.kind is not None]
    if not rows:
        return []
    heights = sorted(r.y1 - r.y0 for r in rows)
    line_height = heights[len(heights) // 2]

    groups: list[list[_Row]] = []
    for row in rows:
        if groups:
            prev = groups[-1][-1]
            if prev.kind == row.kind and row.y0 - prev.y1 < line_height * 0.8:
                groups[-1].append(row)
                continue
        groups.append([row])

    blocks = []
    for i, group in enumerate(groups):
        kind = group[0].kind
        assert kind is not None
        blocks.append(
            ChatBlock(
                kind=kind,
                text=_join_lines([r.text for r in group]),
                # 先頭・末尾の塊は画面の端で切れている可能性がある
                cut_top=i == 0 and group[0].y0 - top < line_height * 3,
                cut_bottom=i == len(groups) - 1 and bottom - group[-1].y1 < line_height * 3,
            )
        )
    return blocks


def read_screenshot(data: bytes) -> list[ChatBlock]:
    """スクリーンショット1枚を読み、上から順の発言の塊にする。"""
    img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    height = img.shape[0]
    top, bottom = int(height * CONTENT_TOP), int(height * CONTENT_BOTTOM)
    crop = np.ascontiguousarray(img[top:bottom, :, ::-1])  # RapidOCR は BGR
    with _engine_lock:
        result = _get_engine()(crop)
    return _blocks_from_rows(_rows_from_ocr(img, top, result), top, bottom)


# --- 複数枚をつなぐ ------------------------------------------------------------


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s…・.,、。!！?？~〜ー\-「」『』()（）]", "", text)


def _coverage(short: str, long: str) -> float:
    """short が long の中にどれだけ含まれているか(OCR の揺れを許す)。"""
    if not short:
        return 0.0
    matcher = SequenceMatcher(None, short, long, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(short)


def _family(kind: BlockKind) -> str:
    return "user" if kind.startswith("user") else kind


def _same_block(a: ChatBlock, b: ChatBlock) -> bool:
    if _family(a.kind) != _family(b.kind):
        return False
    na, nb = _normalize(a.text), _normalize(b.text)
    if not na or not nb:
        return False
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    # 短い発言(「あっ！」など)は本当に繰り返されることがあるので、ほぼ一致のときだけ
    if len(short) < 8:
        return len(long) - len(short) <= 2 and _coverage(short, long) >= 0.85
    # 画面端で切れた塊は、もう一方の一部分に当たる
    return _coverage(short, long) >= 0.8


def _stitch(first: ChatBlock, second: ChatBlock) -> ChatBlock | None:
    """画面の下端で切れた塊と、次のスクショの上端で切れた塊を、重なりでつなぐ。"""
    matcher = SequenceMatcher(None, first.text, second.text, autojunk=False)
    m = matcher.find_longest_match(0, len(first.text), 0, len(second.text))
    if m.size < 6:
        return None
    return ChatBlock(first.kind, first.text[: m.a] + second.text[m.b :], first.cut_top, second.cut_bottom)


def _better(existing: ChatBlock, new: ChatBlock) -> ChatBlock:
    """同じ発言の2つの読み取りのうち、切れていない・長い方を残す。"""
    na, nb = _normalize(existing.text), _normalize(new.text)
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    if _coverage(short, long) < 0.95:
        # どちらも一部分しか写っていない: 前半(下が切れた方)と後半(上が切れた方)をつなぐ
        first, second = (existing, new) if existing.cut_bottom or new.cut_top else (new, existing)
        stitched = _stitch(first, second)
        if stitched is not None:
            return stitched
    # 片方がもう一方を含む: 長い方が多く写っている(切れていない方)
    chosen = new if len(nb) > len(na) else existing
    return ChatBlock(chosen.kind, chosen.text, existing.cut_top and new.cut_top, existing.cut_bottom and new.cut_bottom)


def merge_pages(pages: list[list[ChatBlock]]) -> list[ChatBlock]:
    """撮った順のスクショを、重複を除いて1本の会話にする。"""
    merged: list[ChatBlock] = []
    for page in pages:
        cursor = len(merged) - 1  # 新しい塊を入れる位置の手前
        for block in page:
            match = None
            # 直前に一致した位置の近くから探し、なければ全体(スクロールで戻った分)
            for index in [*range(cursor + 1, min(cursor + 4, len(merged))), *range(len(merged) - 1, -1, -1)]:
                if 0 <= index < len(merged) and _same_block(merged[index], block):
                    match = index
                    break
            if match is not None:
                merged[match] = _better(merged[match], block)
                cursor = match
            else:
                merged.insert(cursor + 1, block)
                cursor += 1
    return merged


def blocks_to_text(blocks: list[ChatBlock]) -> str:
    """物語の本文にする。台詞は「」で囲み、塊ごとに改行する。"""
    lines = []
    for block in blocks:
        text = block.text.strip()
        if block.kind in ("character", "user_speech"):
            text = text.strip("「」")
            text = f"「{text}」"
        lines.append(text)
    return "\n".join(lines)
