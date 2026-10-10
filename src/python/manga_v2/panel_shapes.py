"""
取り込んだページの画像から、コマの形(斜めの枠・枠なし・裁ち落とし)と見開きかどうかを読み取る。

コマの検出(manga109_yolo)は外接矩形しか返さないので、矩形の各辺の近くで枠線を探す。
辺に沿って何か所かで「外側から内側へ見て最初の黒い画素」を拾い、それが一直線に並べば枠線がある
(並びが傾いていれば斜めの枠)。一直線に並ばなければ、その辺は絵がそのまま切れている(枠なし)。
ページの端に接する辺は裁ち落としとして、ページの端まで伸ばす。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image

# 横長のページは見開き(2ページ分)として扱う
SPREAD_ASPECT = 1.2
# ページの端からこの割合以内に接していれば裁ち落とし
_BLEED = 0.015
# 枠線とみなす暗さ(0〜255)
_DARK = 110
# 1辺で調べる位置の数と、枠線とみなすのに要る数
_SAMPLES = 11
_MIN_HITS = 8
# 拾った位置が直線からこの画素数以内にそろっていれば枠線
_MAX_RESIDUAL = 3.0
# 辺の両端の位置の差がこの割合(辺の長さに対して)を超えれば斜めの枠
_SLANT = 0.012
# 外接矩形の辺から内側へ探す深さ(コマの幅・高さに対する割合)。斜めの枠の傾きの分だけ要る
_SEARCH_IN = 0.3
_SEARCH_OUT = 6
# 枠線は辺の全体に途切れずに続く。直線の上(±2画素)が暗い画素の割合がこれ未満なら枠線ではない
_CONTINUITY = 0.9


def _first_dark(line: np.ndarray) -> int | None:
    hits = np.flatnonzero(line < _DARK)
    return int(hits[0]) if hits.size else None


def _side_line(gray: np.ndarray, box: tuple[int, int, int, int], side: str) -> tuple[float, float] | None:
    """
    辺の枠線の位置を、辺の始点側と終点側の座標で返す(上下の辺は y、左右の辺は x)。
    始点・終点は、上下の辺なら左端・右端、左右の辺なら上端・下端。枠線が無ければ None。
    """
    height, width = gray.shape
    x, y, w, h = box
    along, depth = (w, h) if side in ("top", "bottom") else (h, w)
    reach = max(int(depth * _SEARCH_IN), 8)
    ts = np.linspace(0.12, 0.88, _SAMPLES)
    found: list[tuple[float, float]] = []
    for t in ts:
        if side in ("top", "bottom"):
            col = min(max(int(x + t * w), 0), width - 1)
            if side == "top":
                start, stop = max(y - _SEARCH_OUT, 0), min(y + reach, height)
                hit = _first_dark(gray[start:stop, col])
                pos = None if hit is None else start + hit
            else:
                start, stop = min(y + h + _SEARCH_OUT, height), max(y + h - reach, 0)
                hit = _first_dark(gray[stop:start, col][::-1])
                pos = None if hit is None else start - 1 - hit
        else:
            row = min(max(int(y + t * h), 0), height - 1)
            if side == "left":
                start, stop = max(x - _SEARCH_OUT, 0), min(x + reach, width)
                hit = _first_dark(gray[row, start:stop])
                pos = None if hit is None else start + hit
            else:
                start, stop = min(x + w + _SEARCH_OUT, width), max(x + w - reach, 0)
                hit = _first_dark(gray[row, stop:start][::-1])
                pos = None if hit is None else start - 1 - hit
        if pos is not None:
            found.append((float(t), float(pos)))
    if len(found) < _MIN_HITS:
        return None
    t_values = np.array([t for t, _ in found])
    positions = np.array([p for _, p in found])
    slope, intercept = np.polyfit(t_values, positions, 1)
    residual = float(np.median(np.abs(positions - (slope * t_values + intercept))))
    if residual > _MAX_RESIDUAL:
        return None
    # 外れ値(セリフの文字などを拾った点)を除いてもう一度
    keep = np.abs(positions - (slope * t_values + intercept)) <= _MAX_RESIDUAL * 2
    if keep.sum() >= _MIN_HITS:
        slope, intercept = np.polyfit(t_values[keep], positions[keep], 1)
    begin, end = float(intercept), float(slope + intercept)
    if _continuity(gray, box, side, begin, end) < _CONTINUITY:
        return None
    if abs(end - begin) <= along * _SLANT:
        middle = (begin + end) / 2
        return middle, middle
    return begin, end


def _continuity(gray: np.ndarray, box: tuple[int, int, int, int], side: str, begin: float, end: float) -> float:
    """辺の 10%〜90% の区間で、直線の上(±2画素)に暗い画素がある位置の割合。"""
    height, width = gray.shape
    x, y, w, h = box
    along = w if side in ("top", "bottom") else h
    steps = max(int(along * 0.8), 1)
    dark = 0
    for i in range(steps):
        t = 0.1 + 0.8 * i / steps
        pos = round(begin + (end - begin) * t)
        if side in ("top", "bottom"):
            col = min(max(int(x + t * w), 0), width - 1)
            window = gray[max(pos - 2, 0) : min(pos + 3, height), col]
        else:
            row = min(max(int(y + t * h), 0), height - 1)
            window = gray[row, max(pos - 2, 0) : min(pos + 3, width)]
        if window.size and window.min() < _DARK:
            dark += 1
    return dark / steps


def _corner(
    vertical: tuple[float, float],
    horizontal: tuple[float, float],
    box: tuple[int, int, int, int],
    t_v: float,
    t_h: float,
) -> tuple[float, float]:
    """左右の辺(x が上端→下端で変わる)と上下の辺(y が左端→右端で変わる)の交点。"""
    x, y, w, h = box
    px = vertical[0] + (vertical[1] - vertical[0]) * t_v
    py = horizontal[0] + (horizontal[1] - horizontal[0]) * t_h
    for _ in range(4):
        t_h = (px - x) / w if w else 0.0
        t_v = (py - y) / h if h else 0.0
        px = vertical[0] + (vertical[1] - vertical[0]) * t_v
        py = horizontal[0] + (horizontal[1] - horizontal[0]) * t_h
    return px, py


def trace_panel(gray: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, Any]:
    """
    1コマの輪郭(ページに対する割合の4点、左上から時計回り)と枠線の有無。
    外接矩形の辺ごとに、裁ち落とし → ページの端、枠線あり → その線(斜めも)、枠なし → 矩形の辺。
    """
    height, width = gray.shape
    x, y, w, h = box
    bleed = {
        "left": x <= width * _BLEED,
        "top": y <= height * _BLEED,
        "right": x + w >= width * (1 - _BLEED),
        "bottom": y + h >= height * (1 - _BLEED),
    }
    straight = {"left": (x, x), "right": (x + w, x + w), "top": (y, y), "bottom": (y + h, y + h)}
    edge = {"left": (0, 0), "top": (0, 0), "right": (width, width), "bottom": (height, height)}
    lines: dict[str, tuple[float, float]] = {}
    framed = 0
    inner = 0
    for side in ("left", "top", "right", "bottom"):
        if bleed[side]:
            lines[side] = edge[side]
            continue
        inner += 1
        found = _side_line(gray, box, side)
        if found is None:
            lines[side] = straight[side]
        else:
            lines[side] = found
            framed += 1
    corners = [
        _corner(lines["left"], lines["top"], box, 0.0, 0.0),
        _corner(lines["right"], lines["top"], box, 0.0, 1.0),
        _corner(lines["right"], lines["bottom"], box, 1.0, 1.0),
        _corner(lines["left"], lines["bottom"], box, 1.0, 0.0),
    ]
    points = [
        [round(min(max(px / width, 0.0), 1.0), 4), round(min(max(py / height, 0.0), 1.0), 4)] for px, py in corners
    ]
    # 枠線が見つかった辺が内側の辺の過半数でなければ、枠なしのコマ(絵の端がたまたま
    # まっすぐな辺もあるので、半分では枠ありにしない)
    return {"points": points, "border": inner > 0 and framed * 2 > inner}


def trace_page(image: Image.Image, boxes: Sequence[Sequence[int]]) -> dict[str, Any]:
    """取り込んだ1ページのコマ割り(layout.PageLayout の今の形)。"""
    gray = np.asarray(image.convert("L"))
    panels = []
    for box in boxes:
        x, y, w, h = (int(v) for v in box)
        if w > 0 and h > 0:
            panels.append(trace_panel(gray, (x, y, w, h)))
    return {"spread": image.width / image.height >= SPREAD_ASPECT, "panels": panels}
