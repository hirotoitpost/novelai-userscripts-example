"""
動くページ(うごイラを嵌め込んだコマがあるページ)の時間割り。

1ページに動くコマがいくつあっても、ページは1本のアニメーションにする。うごイラごとにコマの表示時間が違うので、
それぞれを繰り返し再生したときの「どれかのコマが替わる時刻」をすべて区切りにし、区切りごとに各うごイラの
いまのコマを決める。ページの1周の長さは、全部のうごイラがちょうど1周し終わる長さ(各うごイラの1周の最小公倍数)。
長くなりすぎるときは一番長いうごイラの1周に合わせる(短いうごイラは途中で頭に戻る)。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from itertools import accumulate

# GIF などで表示できる一番短い時間(ミリ秒)。これより短い区切りは前の区切りに含める
MIN_DELAY = 20
# 1ページのコマ数の上限(ページを丸ごと描き直すので、多いと合成に時間がかかる)
MAX_STEPS = 120
# 最小公倍数で1周させる長さの上限(ミリ秒)
MAX_LOOP_MS = 20000


def _boundaries(delays: list[int], length: int) -> list[int]:
    times = []
    t = 0
    while t < length:
        for delay in delays:
            if t >= length:
                break
            times.append(t)
            t += delay
    return times


def _frame_at(starts: list[int], total: int, t: int) -> int:
    return bisect_right(starts, t % total) - 1


def _steps(tracks: list[list[int]], length: int) -> list[tuple[tuple[int, ...], int]]:
    times = sorted({t for delays in tracks for t in _boundaries(delays, length)})
    kept: list[int] = []
    for t in times:
        if not kept or t - kept[-1] >= MIN_DELAY:
            kept.append(t)
    if len(kept) > 1 and length - kept[-1] < MIN_DELAY:
        kept.pop()
    starts = [[0, *accumulate(delays)][:-1] for delays in tracks]
    totals = [sum(delays) for delays in tracks]
    steps: list[tuple[tuple[int, ...], int]] = []
    for i, t in enumerate(kept):
        end = kept[i + 1] if i + 1 < len(kept) else length
        frames = tuple(_frame_at(s, total, t) for s, total in zip(starts, totals))
        if steps and steps[-1][0] == frames:
            steps[-1] = (frames, steps[-1][1] + end - t)
        else:
            steps.append((frames, end - t))
    return steps


def merge_timeline(tracks: list[list[int]]) -> list[tuple[tuple[int, ...], int]]:
    """
    うごイラごとのコマの表示時間(ミリ秒)の並びから、ページのコマの並びを作る。
    返り値は (うごイラごとのコマ番号, そのページのコマの表示時間) の並び。
    """
    tracks = [[max(int(d), 1) for d in delays] for delays in tracks if delays]
    if not tracks:
        return []
    totals = [sum(delays) for delays in tracks]
    lcm = math.lcm(*totals)
    steps = _steps(tracks, lcm) if lcm <= MAX_LOOP_MS else []
    if not steps or len(steps) > MAX_STEPS:
        steps = _steps(tracks, max(totals))
    return steps[:MAX_STEPS]
