"""
動画モデル(うごイラの F)で作ったコマを整える。どれもこの PC で動かす(中割りのモデル RIFE と、OpenCV のオプティカルフロー)。

Wan 2.2 で作ったコマに、実機(L4・2秒・繰り返す)で3つの癖があった:
  - 明るさがだんだん上がる(平均 152 → 155)。繰り返すと、最後から最初へ戻るときに跳ねる
  - 「最初と最後を元の絵にする」ワークフローでも、外した最後のコマの1つ前は、顔の向き・毛先が最初のコマと違う
    (継ぎ目の差 6.0。ふだんのコマのあいだは 1〜2)
  - 4コマごとに細部(線・毛先)が変わる(VAE が4コマずつまとめるため。境目の差 2.0、ほかは 1.1)

それぞれ、明るさ・色を最初のコマに合わせる、最後の数コマを最初のコマへの中割りに置き換える、前後のコマを動きに合わせて
ずらして少し重ねる、で減らす。#49 のコマで、継ぎ目の差 6.0 → 0.8、境目とほかの差の比 1.78 → 1.45 になった
(くっきりさは 4% 落ちる。髪が二重には見えなかった)。
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np
from PIL import Image

# 継ぎ目: 最後の何コマを、最初のコマへの中割りに置き換えるか
CLOSE_FRAMES = 4
# ちらつき: 前後のコマを重ねる割合(それぞれ)と、ずらしても合わない所(新しく見えた所など)とみなす差
BLEND = 0.25
_MISMATCH = 24.0


def match_color(frames: list[np.ndarray]) -> list[np.ndarray]:
    """コマごとに、チャンネルごとの平均・ばらつきを最初のコマに合わせる(だんだん明るくなる癖を消す)。"""
    ref_mean, ref_std = frames[0].mean(axis=(0, 1)), frames[0].std(axis=(0, 1))
    return [(f - f.mean(axis=(0, 1))) / (f.std(axis=(0, 1)) + 1e-6) * ref_std + ref_mean for f in frames]


def close_loop(
    frames: list[np.ndarray], between: Callable[[Image.Image, Image.Image, float], Image.Image], count: int = CLOSE_FRAMES
) -> list[np.ndarray]:
    """最後の count コマを、その1つ前のコマから最初のコマへの中割りに置き換える(繰り返したとき、最初のコマへなめらかに戻る)。"""
    if len(frames) < count + 3:
        return frames
    a, b = _image(frames[-count - 1]), _image(frames[0])
    tail = [np.asarray(between(a, b, j / (count + 1)), np.float32) for j in range(1, count + 1)]
    return frames[:-count] + tail


def _gray(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame.clip(0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)


def deflicker(frames: list[np.ndarray], loop: bool) -> list[np.ndarray]:
    """
    前後のコマを、動き(オプティカルフロー)に合わせて今のコマの形にずらし、少しだけ重ねる(4コマごとの細部の変わり目を
    ならす)。ずらしても合わない所は重ねない(髪が二重に見えないように)。loop なら最初と最後のコマも互いを前後とみなす。
    """
    flow = cv2.DISOpticalFlow.create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    grays = [_gray(f) for f in frames]
    h, w = grays[0].shape
    xs, ys = (g.astype(np.float32) for g in np.meshgrid(np.arange(w), np.arange(h)))
    out: list[np.ndarray] = []
    for i, frame in enumerate(frames):
        mixed = frame.copy()
        for j in (i - 1, i + 1):
            if not loop and not 0 <= j < len(frames):
                continue
            j %= len(frames)
            motion = flow.calc(grays[i], grays[j], np.empty(0))
            moved = cv2.remap(
                frames[j], xs + motion[..., 0], ys + motion[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
            )
            fits = (np.abs(moved - frame).mean(axis=2, keepdims=True) < _MISMATCH).astype(np.float32) * BLEND
            mixed += (moved - frame) * fits
        out.append(mixed)
    return out


def _image(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(frame.clip(0, 255).round().astype(np.uint8))


def smooth(
    images: list[Image.Image], loop: bool, between: Callable[[Image.Image, Image.Image, float], Image.Image] | None = None
) -> list[Image.Image]:
    """
    動画モデルのコマを整える。loop(最初と最後を元の絵にした、繰り返すうごイラ)は、明るさ・色を最初のコマに合わせ、継ぎ目を
    中割りで閉じる。どちらも、4コマごとのちらつきをならす。between は中割り(既定は RIFE の frame_interpolation.between)。
    """
    if len(images) < 3:
        return images
    frames = [np.asarray(i.convert("RGB"), np.float32) for i in images]
    if loop:
        if between is None:
            from . import frame_interpolation

            between = frame_interpolation.between
        frames = close_loop(match_color(frames), between)
    return [_image(f) for f in deflicker(frames, loop)]
