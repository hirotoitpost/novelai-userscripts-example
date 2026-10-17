"""video_smooth: 動画モデルのコマを整える(明るさ・継ぎ目・ちらつき)。"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from python import video_smooth


def _blend(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
    return Image.blend(a, b, t)


def _frame(x: int, brightness: int = 0, noise: np.ndarray | None = None) -> Image.Image:
    """空色の背景に、x の位置の四角(動くもの)。brightness で全体を明るく、noise で細部を足す。"""
    image = Image.new("RGB", (96, 64), (60, 120, 200))
    ImageDraw.Draw(image).rectangle((x, 20, x + 16, 40), fill=(240, 220, 200))
    array = np.asarray(image, np.float32) + brightness
    if noise is not None:
        array += noise
    return Image.fromarray(array.clip(0, 255).astype(np.uint8))


def _mean(image: Image.Image) -> float:
    return float(np.asarray(image, np.float32).mean())


def _diff(a: Image.Image, b: Image.Image) -> float:
    return float(np.abs(np.asarray(a, np.float32) - np.asarray(b, np.float32)).mean())


def test_loop_matches_brightness_and_closes_the_seam() -> None:
    # だんだん明るくなり、最後のコマは最初と離れた位置で終わる(継ぎ目で跳ねる)
    frames = [_frame(10 + 3 * i, brightness=i) for i in range(12)]
    smoothed = video_smooth.smooth(frames, loop=True, between=_blend)
    assert len(smoothed) == len(frames)
    # 明るさは最初のコマにそろう
    assert max(abs(_mean(f) - _mean(smoothed[0])) for f in smoothed) < abs(_mean(frames[-1]) - _mean(frames[0])) / 4
    # 最後から最初へ戻るときの差が、ふだんのコマのあいだと同じくらいになる
    assert _diff(smoothed[-1], smoothed[0]) < _diff(frames[-1], frames[0]) / 2


def test_deflicker_evens_out_every_fourth_frame_without_ghosting() -> None:
    rng = np.random.default_rng(0)
    # 4コマごとに細部(ノイズ)が入れ替わる。四角は1コマに2画素ずつ動く
    noises = [rng.normal(0, 12, (64, 96, 3)) for _ in range(4)]
    frames = [_frame(8 + 2 * i, noise=noises[i // 4]) for i in range(16)]
    smoothed = video_smooth.smooth(frames, loop=False)

    def boundary_ratio(seq: list[Image.Image]) -> float:
        steps = [_diff(seq[i - 1], seq[i]) for i in range(1, len(seq))]
        boundary = [s for i, s in enumerate(steps, start=1) if i % 4 == 0]
        inner = [s for i, s in enumerate(steps, start=1) if i % 4 != 0]
        return float(np.mean(boundary) / np.mean(inner))

    assert boundary_ratio(smoothed) < boundary_ratio(frames)
    # 動く四角は二重にならない(コマ8の四角は x=24〜40。中は四角の色のまま、前後のコマの四角の外は背景のまま)
    middle = np.asarray(smoothed[8], np.float32)
    assert middle[30, 32].mean() > 180 and middle[30, 18].mean() < 150 and middle[30, 46].mean() < 150


def test_free_motion_keeps_colors_and_short_clips_untouched() -> None:
    # 自由に動かす(i2v)は、明るさが変わっていくのも動きのうちなので、そろえない
    frames = [_frame(10, brightness=4 * i) for i in range(6)]
    smoothed = video_smooth.smooth(frames, loop=False)
    assert _mean(smoothed[-1]) - _mean(smoothed[0]) > 10
    two = [_frame(10), _frame(12)]
    assert video_smooth.smooth(two, loop=True, between=_blend) == two
