"""
漫画の取り込み: PDF や画像のページからコマを見つけ、コマごとの「構成」だけを読み取る。

読み取るのは、人数・構図(寄り/引き)・セリフの量・コマの役割(導入/ボケ/オチ…)・感情。
セリフの文字や、何が起きているか(筋書き)は読まない。市販の作品を取り込んでも、新しく作る漫画は
コマ運びとテンポを参考にするだけで、セリフ・設定・絵は写さないため。

- コマ: 枠線(長い水平・垂直の線)に囲まれた領域。吹き出しが枠をまたいでも、曲線なので枠と見なさない。
  絵の中の直線で1コマが割れたときは、すき間が狭いので継ぎ直す。枠が見つからなければページ全体を1コマとする。
- 人数・構図: 頭の検出(manga_v2/detect.py)。頭の大きさがコマの高さに占める割合で寄り/引きを決める。
- セリフの量: RapidOCR の文字領域の検出だけを使う(文字は読まない)。
- 役割・感情: ローカルの画像モデル(Ollama の qwen3-vl:2b。1コマ十数秒)。使えなければ空のままにする。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import threading
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image

from .manga_elements import detect_elements
from .manga_v2.detect import detect_heads

logger = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]  # x, y, 幅, 高さ

# 取り込むページの幅(これより大きいページは縮める)と、1回に取り込めるページ数
PAGE_WIDTH = 1200
MAX_PAGES = 60
# 枠線とみなす線の長さ(ページの幅・高さに対する割合)と、黒とみなす明るさ
_H_LINE = 0.12
_V_LINE = 0.08
_DARK = 110
# コマとみなす最小の面積(ページに対する割合)
_MIN_PANEL_AREA = 0.02
# コマとみなす最小の幅・高さ(ページに対する割合)
_MIN_PANEL_SIDE = 0.06
# 途切れた枠線を継ぐ長さ(ページの幅・高さに対する割合)。枠をまたぐ吹き出しの幅くらい
_BRIDGE = 0.25
# どのコマにも入っていない絵がページのこの割合を超えたら、枠線を継いで見落としたコマを探す
_MAX_UNCOVERED = 0.08
# 絵の塊をコマとみなすのに必要な詰まり具合(塊の外枠に対する絵の割合)
_MIN_BLOCK_FILL = 0.6
# 見つかったコマの外側の、枠線とみなす幅(ピクセル)
_FRAME_MARGIN = 14
# 白とみなす明るさ。隣り合う領域の間がこの割合以上白ければ、コマの間のすき間とみなす
_WHITE = 230
_GUTTER_WHITE = 0.85
# 端がそろっているとみなすずれ(ピクセル)と、コマを広げる刻み
_ALIGN = 8
_GROW_STEP = 4
# 同じ段とみなす縦の重なり
_ROW_OVERLAP = 0.5

ROLES = ["導入", "会話", "驚き", "ボケ", "ツッコミ", "感動", "オチ", "場面転換"]


@dataclass
class PanelInfo:
    """1コマの構成。box はページ上の位置(ピクセル)。"""

    box: Rect
    people: int
    shot: str  # close-up / medium / long / no humans
    text_blocks: int
    role: str = ""
    emotion: str = ""


# ---- ページの読み込み ----


def _fit_width(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    if image.width > PAGE_WIDTH:
        image = image.resize((PAGE_WIDTH, round(image.height * PAGE_WIDTH / image.width)))
    return image


# zip の中で読まないもの(macOS の付属ファイル・サムネイル・隠しファイル)
_ZIP_JUNK = re.compile(r"(^|/)(__MACOSX/|\.|thumbs\.db$|desktop\.ini$)", re.IGNORECASE)
_PAGE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")
# zip を展開した合計の上限(zip 爆弾の対策)と、読む項目数の上限
MAX_ZIP_BYTES = 1024 * 1024 * 1024
_MAX_ZIP_ENTRIES = 2000


def _natural_key(name: str) -> list[object]:
    """ファイル名の自然な順(page2 → page10)。数字の部分は数として比べる。"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _is_zip(name: str, data: bytes) -> bool:
    return name.lower().endswith((".zip", ".cbz")) or data[:4] == b"PK\x03\x04"


def _zip_entries(data: bytes) -> list[tuple[str, bytes]]:
    """
    zip(.cbz も)の中の画像と PDF を、ファイル名の自然な順に取り出す。フォルダに分かれていても
    パスごと並べるので、フォルダ順・ページ順になる。中の zip は開かない。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and not _ZIP_JUNK.search(info.filename)
            and info.filename.lower().endswith(_PAGE_SUFFIXES)
        ][:_MAX_ZIP_ENTRIES]
        if sum(info.file_size for info in infos) > MAX_ZIP_BYTES:
            raise ValueError("zip の中身が大きすぎます(展開後 1GB まで)。")
        infos.sort(key=lambda info: _natural_key(info.filename))
        return [(info.filename, archive.read(info)) for info in infos]


def load_pages(files: list[tuple[str, bytes]]) -> list[Image.Image]:
    """
    PDF(全ページ)・画像・zip(.cbz も。中の画像と PDF をファイル名の自然な順に)を、渡された順に
    ページ画像にする。
    """
    pages: list[Image.Image] = []
    queue = list(files)
    while queue and len(pages) < MAX_PAGES:
        name, data = queue.pop(0)
        if _is_zip(name, data):
            queue[0:0] = _zip_entries(data)
            continue
        if name.lower().endswith(".pdf") or data[:5] == b"%PDF-":
            import pypdfium2 as pdfium  # 読み込みが重いので使うときだけ

            document = pdfium.PdfDocument(data)
            try:
                for page in document:
                    width = page.get_width()
                    pages.append(_fit_width(page.render(scale=PAGE_WIDTH / width if width else 1).to_pil()))
                    if len(pages) >= MAX_PAGES:
                        break
            finally:
                document.close()
        else:
            pages.append(_fit_width(Image.open(io.BytesIO(data))))
    return pages[:MAX_PAGES]


# ---- コマの検出 ----


def _white_fraction(gray: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    strip = gray[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)]
    return float((strip > _WHITE).mean()) if strip.size else 1.0


def _has_gutter(gray: np.ndarray, x0: int, y0: int, x1: int, y1: int, *, across_rows: bool) -> bool:
    """
    2つの領域の間の帯に、コマの間の白いすき間(ほぼ白い1行/1列)があるか。帯には枠線(黒)も入るので、
    帯全体の平均ではなく、白い行(上下に並ぶとき)・白い列(左右に並ぶとき)が1本でもあるかで見る。
    """
    strip = gray[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)]
    if strip.size == 0:
        return False
    white = (strip > _WHITE).mean(axis=1 if across_rows else 0)
    return bool((white >= _GUTTER_WHITE).any())


def _join_split_panels(rects: list[Rect], gray: np.ndarray) -> list[Rect]:
    """
    絵の中の線(柱・髪の流れなど)で割れた1コマを継ぎ直す。上下か左右の端がそろって隣り合い、
    間が白いすき間(コマとコマの間)でなければ同じコマとみなす。
    """
    rects = list(rects)
    joined = True
    while joined:
        joined = False
        for i, a in enumerate(rects):
            for j, b in enumerate(rects):
                if i == j:
                    continue
                ax, ay, aw, ah = a
                bx, by, bw, bh = b
                same_cols = abs(ax - bx) <= _ALIGN and abs((ax + aw) - (bx + bw)) <= _ALIGN
                same_rows = abs(ay - by) <= _ALIGN and abs((ay + ah) - (by + bh)) <= _ALIGN
                if same_cols and by >= ay + ah:
                    gutter = _has_gutter(gray, max(ax, bx), ay + ah, min(ax + aw, bx + bw), by, across_rows=True)
                elif same_rows and bx >= ax + aw:
                    gutter = _has_gutter(gray, ax + aw, max(ay, by), bx, min(ay + ah, by + bh), across_rows=False)
                else:
                    continue
                if not gutter:
                    x0, y0 = min(ax, bx), min(ay, by)
                    x1, y1 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
                    rects = [r for k, r in enumerate(rects) if k not in (i, j)] + [(x0, y0, x1 - x0, y1 - y0)]
                    joined = True
                    break
            if joined:
                break
    return rects


def _grow_to_gutter(rect: Rect, gray: np.ndarray) -> Rect:
    """
    絵の中の線で削れたコマを、外側の白いすき間(またはページの端)に当たるまで広げて取り戻す。
    枠線そのもの(黒)は越え、コマの間の白いすき間で止まる。
    """
    height, width = gray.shape
    x0, y0, x1, y1 = rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]
    step = _GROW_STEP
    while x0 - step >= 0 and _white_fraction(gray, x0 - step, y0, x0, y1) < _GUTTER_WHITE:
        x0 -= step
    while x1 + step <= width and _white_fraction(gray, x1, y0, x1 + step, y1) < _GUTTER_WHITE:
        x1 += step
    while y0 - step >= 0 and _white_fraction(gray, x0, y0 - step, x1, y0) < _GUTTER_WHITE:
        y0 -= step
    while y1 + step <= height and _white_fraction(gray, x0, y1, x1, y1 + step) < _GUTTER_WHITE:
        y1 += step
    return x0, y0, x1 - x0, y1 - y0


def _dedupe(rects: list[Rect]) -> list[Rect]:
    """広げた結果ほぼ重なったコマ(同じコマの断片)を1つにまとめる。"""
    result: list[Rect] = []
    for rect in sorted(rects, key=lambda r: -r[2] * r[3]):
        x, y, w, h = rect
        if any(
            max(0, min(x + w, rx + rw) - max(x, rx)) * max(0, min(y + h, ry + rh) - max(y, ry)) > 0.5 * w * h
            for rx, ry, rw, rh in result
        ):
            continue
        result.append(rect)
    return result


def _panel_regions(gray: np.ndarray, bridge: bool) -> list[Rect]:
    """枠線に囲まれた領域。bridge なら、途切れた枠線を線の向きに沿って継いでから探す。"""
    height, width = gray.shape
    dark = (gray < _DARK).astype(np.uint8) * 255
    horizontal = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(int(width * _H_LINE), 2), 1))
    )
    vertical = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(int(height * _V_LINE), 2)))
    )
    if bridge:
        horizontal = cv2.morphologyEx(
            horizontal, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (int(width * _BRIDGE), 1))
        )
        vertical = cv2.morphologyEx(
            vertical, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(height * _BRIDGE)))
        )
    lines = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats((lines == 0).astype(np.uint8), connectivity=4)
    rects: list[Rect] = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < width * height * _MIN_PANEL_AREA or w < width * _MIN_PANEL_SIDE or h < height * _MIN_PANEL_SIDE:
            continue  # 小さすぎる領域や、コマの間のすき間の切れ端
        if x == 0 or y == 0 or x + w >= width or y + h >= height:
            continue  # 枠の外の余白
        rects.append((x, y, w, h))
    return rects


def _uncovered_ink(rects: list[Rect], gray: np.ndarray) -> float:
    """どのコマにも入っていない、白くない部分(絵)のページに対する割合。"""
    mask = gray <= _WHITE
    for x, y, w, h in rects:
        mask[y : y + h, x : x + w] = False
    return float(mask.mean())


def _ink_blocks(rects: list[Rect], gray: np.ndarray) -> list[Rect]:
    """
    どのコマにも入っていない絵の塊のうち、コマくらいの大きさで中身が詰まったもの。枠線が描き文字や
    吹き出しで大きく欠けたコマや、枠の無いコマを拾う(散らばった文字や効果音は詰まっていないので拾わない)。
    """
    height, width = gray.shape
    ink = (gray <= _WHITE).astype(np.uint8)
    # 見つかったコマは枠線ごと除く(枠線が残ると、隣のコマとつながった大きな塊になる)
    m = _FRAME_MARGIN
    for x, y, w, h in rects:
        ink[max(y - m, 0) : y + h + m, max(x - m, 0) : x + w + m] = 0
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    blocks: list[Rect] = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w < width * _MIN_PANEL_SIDE or h < height * _MIN_PANEL_SIDE or w * h < width * height * _MIN_PANEL_AREA:
            continue
        if area >= w * h * _MIN_BLOCK_FILL:
            blocks.append((x, y, w, h))
    return blocks


def _overlap(a: Rect, b: Rect) -> int:
    return max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(
        0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    )


def detect_panels_by_lines(image: Image.Image) -> list[Rect]:
    """
    枠線からコマを探す(学習済みモデルが使えないときの方法)。読む順に返し、枠が無ければページ全体を1コマとする。
    吹き出しが枠線を白く塗りつぶしてまたいでいると、コマの内側が外の余白とつながって見つからない。
    どのコマにも入らない絵が多いときだけ、途切れた枠線を継いで探し直し、見落としたコマを足す
    (いつも継ぐと、絵の中の線まで伸びてコマが割れる)。
    """
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    rects = _panel_regions(gray, bridge=False)
    if _uncovered_ink(rects, gray.copy()) > _MAX_UNCOVERED:
        # 見つからなかった所にあるコマだけを足す(見つかったコマは継がない方が正確)。まず途切れた
        # 枠線を継いで探し、それでも残った絵は、コマくらいの大きさの詰まった塊をコマとみなす
        for extra in _panel_regions(gray, bridge=True):
            if all(_overlap(extra, r) < 0.2 * extra[2] * extra[3] for r in rects):
                rects.append(extra)
        rects.extend(_ink_blocks(rects, gray))
    rects = _dedupe([_grow_to_gutter(r, gray) for r in _join_split_panels(rects, gray)])
    return reading_order(rects) or [(0, 0, width, height)]


def split_overlays(frames: list[Rect]) -> tuple[list[Rect], list[Rect]]:
    """
    (コマ, コマにまたがって描かれた絵)に分ける。モデルは、何段にもまたがって立つ人物のような絵も
    1つの frame として返す。ほかの2つ以上のコマに大きく重なる frame は、コマではなくまたがる絵とみなす。
    """
    panels: list[Rect] = []
    overlays: list[Rect] = []
    for frame in frames:
        covered = [
            other
            for other in frames
            if other is not frame and _overlap(frame, other) > _OVERLAY_SHARE * other[2] * other[3]
        ]
        (overlays if len(covered) >= 2 else panels).append(frame)
    return panels, overlays


def _to_rect(box: tuple[float, float, float, float]) -> Rect:
    x0, y0, x1, y1 = (round(v) for v in box)
    return x0, y0, x1 - x0, y1 - y0


def detect_panels(image: Image.Image, elements: dict[str, list] | None = None) -> list[Rect]:
    """
    ページのコマを読む順(上の段から、同じ段は右から)に返す。学習済みモデル(manga_elements)の frame を
    使い、モデルが使えないか1つも見つからなければ枠線から探す。elements はモデルの検出結果(省略時は検出する)。
    """
    if elements is None:
        elements = detect_elements(image)
    if elements and elements["frame"]:
        panels, _ = split_overlays([_to_rect(b) for b in elements["frame"]])
        if panels:
            return reading_order(panels)
    return detect_panels_by_lines(image)


def reading_order(rects: list[Rect]) -> list[Rect]:
    """日本の漫画の読む順: 上の段から。同じ段(縦に半分以上重なる)は右から。"""
    rows: list[list[Rect]] = []
    for rect in sorted(rects, key=lambda r: r[1]):
        for row in rows:
            top = min(r[1] for r in row)
            bottom = max(r[1] + r[3] for r in row)
            overlap = min(bottom, rect[1] + rect[3]) - max(top, rect[1])
            if overlap > min(rect[3], bottom - top) * _ROW_OVERLAP:
                row.append(rect)
                break
        else:
            rows.append([rect])
    return [r for row in rows for r in sorted(row, key=lambda r: -(r[0] + r[2]))]


# ---- コマの構成 ----

# ほかのコマの面積のこの割合以上に重なる frame を「重なっている」とみなす(またがる絵の判定)
_OVERLAY_SHARE = 0.25
# 構図: 顔の面積がコマに占める割合(これ以上で寄り、これ未満で引き)
_FACE_CLOSE_UP = 0.06
_FACE_LONG = 0.008
# 顔が見えず体だけのとき: 体の高さがコマの高さに占める割合がこれ未満なら引き
_BODY_LONG = 0.6

_ocr_engine = None
_ocr_lock = threading.Lock()


def _count_text_blocks(crop: Image.Image) -> int:
    """文字の塊(縦書きの1列や描き文字)の数。文字そのものは読まない。"""
    global _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            from rapidocr import RapidOCR  # 読み込みが重いので使うときだけ

            _ocr_engine = RapidOCR()
        bgr = np.ascontiguousarray(np.asarray(crop.convert("RGB"))[:, :, ::-1])
        result = _ocr_engine(bgr, use_det=True, use_cls=False, use_rec=False)
    boxes = getattr(result, "boxes", None)
    return 0 if boxes is None else len(boxes)


def _inside(box: tuple[float, float, float, float], rect: Rect) -> bool:
    """要素の中心がコマの中にあるか。"""
    x, y, w, h = rect
    return x <= (box[0] + box[2]) / 2 <= x + w and y <= (box[1] + box[3]) / 2 <= y + h


def _area(box: tuple[float, float, float, float]) -> float:
    return max(box[2] - box[0], 0) * max(box[3] - box[1], 0)


def _shot(faces: list, bodies: list, rect: Rect) -> str:
    """構図。顔の大きさ(コマの面積に対する割合)で決める。顔が無く体だけなら体の高さで決める。"""
    _, _, w, h = rect
    if faces:
        ratio = max(_area(f) for f in faces) / max(w * h, 1)
        if ratio >= _FACE_CLOSE_UP:
            return "close-up"
        return "long" if ratio < _FACE_LONG else "medium"
    if bodies:
        return "long" if max(b[3] - b[1] for b in bodies) / max(h, 1) < _BODY_LONG else "medium"
    return "no humans"


def _head_shot(heads: list, rect: Rect) -> str:
    """モデルが使えないとき: 頭の検出から構図を決める(頭は顔より大きいので基準を緩める)。"""
    return _shot(heads, [], rect) if heads else "no humans"


def analyze_page(page_path: Path) -> tuple[list[PanelInfo], int]:
    """
    ページのコマと、コマごとの人数・構図・文字の塊の数(役割・感情は classify_panel で別に読む)。
    2つ目はコマにまたがって描かれた絵の数。学習済みモデルが使えなければ、枠線・頭の検出・文字検出で代える。
    """
    with Image.open(page_path) as src:
        image = src.convert("RGB")
    elements = detect_elements(image)
    if elements is not None and elements["frame"]:
        rects, overlays = split_overlays([_to_rect(b) for b in elements["frame"]])
        if rects:
            panels: list[PanelInfo] = []
            for rect in reading_order(rects):
                faces = [b for b in elements["face"] if _inside(b, rect)]
                bodies = [b for b in elements["body"] if _inside(b, rect)]
                texts = [b for b in elements["text"] if _inside(b, rect)]
                people = max(len(faces), len(bodies))
                panels.append(PanelInfo(rect, people, _shot(faces, bodies, rect), len(texts)))
            return panels, len(overlays)

    heads = detect_heads(page_path)
    panels = []
    for rect in detect_panels_by_lines(image):
        x, y, w, h = rect
        inside = [hd for hd in heads if _inside(hd, rect)]
        crop = image.crop((x, y, x + w, y + h))
        panels.append(PanelInfo(rect, len(inside), _head_shot(inside, rect), _count_text_blocks(crop)))
    return panels, 0


_CLASSIFY_PROMPT = (
    "この漫画のコマの、話の中での役割と感情だけを答えてください。何が起きているか・セリフは書かないこと。"
    'JSONだけで答える: {"role": "' + " / ".join(ROLES) + ' のどれか", "emotion": "主な感情を1語(例: 喜び)"}'
)


def _vision_endpoint() -> tuple[str, str] | None:
    """Ollama の(ホスト, モデル)。.env の VLLM_VISION_BASE_URL(…/v1)から作る。未設定なら None。"""
    base = os.environ.get("VLLM_VISION_BASE_URL", "").strip().rstrip("/")
    if not base:
        return None
    host = base[: -len("/v1")] if base.endswith("/v1") else base
    return host, os.environ.get("VLLM_VISION_MODEL", "qwen3-vl:2b")


# qwen3-vl は思考を切る指定をしても内部で考え続けることがあり、出力枠を思考に使い切ると答えが空になる
# (2026-10 実機: 同じ指定で8秒で答える回と、554秒かけて空になる回があった)。そこで出力の長さを
# 抑え、空なら思考の中の JSON を探し、それでも無ければ指定を変えて聞き直す。
_VISION_ATTEMPTS: list[dict[str, Any]] = [{"format": "json", "think": False}, {"format": "json", "think": False}, {}]
_VISION_MAX_TOKENS = 1024


def parse_role(message: dict[str, Any]) -> tuple[str, str] | None:
    """画像モデルの応答から(役割, 感情)を取り出す。答えが空なら思考の中の JSON を探す。読めなければ None。"""
    for text in (message.get("content") or "", message.get("thinking") or ""):
        start, end = text.rfind("{"), text.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        # 「会話シーン」「オチ(笑い)」のように選択肢そのままで答えないことがあるので、含まれる語で拾う
        answer = str(data.get("role") or "").strip()
        role = next((r for r in ROLES if r in answer), "")
        emotion = str(data.get("emotion") or "").strip()[:10]
        if role or emotion:
            return role, emotion
    return None


async def classify_panel(crop: Image.Image) -> tuple[str, str]:
    """コマの役割と感情(読めなければ空)。ローカルの画像モデルで1コマ十数秒(聞き直すとさらにかかる)。"""
    endpoint = _vision_endpoint()
    if endpoint is None:
        return "", ""
    host, model = endpoint
    buf = io.BytesIO()
    image = crop.convert("RGB")
    image.thumbnail((768, 768))
    image.save(buf, format="JPEG", quality=85)
    message = {"role": "user", "content": _CLASSIFY_PROMPT, "images": [base64.b64encode(buf.getvalue()).decode()]}
    for extra in _VISION_ATTEMPTS:
        body = {
            "model": model,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": _VISION_MAX_TOKENS},
            "messages": [message],
            **extra,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
                response = await client.post(f"{host}/api/chat", json=body)
            parsed = parse_role(response.json().get("message") or {})
        except Exception as exc:  # noqa: BLE001 読めなくても構成の他の項目は使える
            logger.warning("コマの役割を読み取れませんでした: %s", exc)
            return "", ""
        if parsed is not None:
            return parsed
    return "", ""


def panel_dict(panel: PanelInfo) -> dict:
    return asdict(panel)


# ---- 台本作りに渡す構成 ----

_SHOT_LABELS = {"close-up": "寄り", "medium": "中くらい", "long": "引き", "no humans": "人物なし"}


def text_amount(blocks: int) -> str:
    if blocks == 0:
        return "セリフなし"
    return "セリフ少なめ" if blocks <= 2 else "セリフ多め"


def structure_lines(panels: list[dict]) -> list[str]:
    """コマの構成を、台本AIに渡す1行ずつの説明にする(筋書きは含まない)。"""
    lines = []
    for panel in panels:
        parts: list[str] = [_SHOT_LABELS.get(panel["shot"]) or str(panel["shot"])]
        if panel["people"]:
            parts.append(f"{panel['people']}人")
        parts.append(text_amount(panel["text_blocks"]))
        if panel.get("role"):
            parts.append(f"役割: {panel['role']}")
        if panel.get("emotion"):
            parts.append(f"感情: {panel['emotion']}")
        lines.append("・".join(parts))
    return lines
