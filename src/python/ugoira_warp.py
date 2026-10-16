"""
うごイラの D: 絵を変形して動かす(描き直さない)。

元の絵の画素をそのまま少しずつ動かすので、線・色・トーンはどのコマでも同じで、ちらつかない。コマ数も自由に増やせる
(NovelAI は使わない。この PC で一瞬で作れる)。小さく繰り返す動き(呼吸・髪がなびく・首をかしげる・うなずく)に向く。

動かし方は、出力の画素ごとに「元の絵のどこから取るか」を決める(逆向きの写像。cv2.remap)。動かす量には、頭の位置から
決めた重み(1 なら全部動く、0 なら動かない)を掛けるので、動く所と動かない所のあいだは滑らかにつながる。
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

# 種類 → (表示名, 1周のミリ秒の既定)
WARPS: dict[str, tuple[str, int]] = {
    "breath": ("呼吸", 3000),
    "hair": ("髪がなびく", 1600),
    "tilt": ("首をかしげる", 2400),
    "nod": ("うなずく", 1400),
}
MIN_FRAMES = 4
MAX_FRAMES = 48


class WarpError(ValueError):
    pass


def _smooth(x: np.ndarray) -> np.ndarray:
    """0〜1 に切って、端をなめらかにする(smoothstep)。"""
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _grid(size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    w, h = size
    return np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))


def head_weight(head: tuple[float, float, float, float], size: tuple[int, int]) -> np.ndarray:
    """頭(と首の上・頭にかかる髪)で 1、そこから離れると 0 になる重み。"""
    x0, y0, x1, y1 = head
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2 * 1.25, (y1 - y0) / 2 * 1.2
    xs, ys = _grid(size)
    d = np.sqrt(((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2)
    return _smooth((1.9 - d) / 0.9)


def hair_weight(head: tuple[float, float, float, float], size: tuple[int, int]) -> np.ndarray:
    """顔の横・下に垂れた髪で大きく(毛先ほど大きく)、顔と頭のてっぺんでは 0 の重み。"""
    x0, y0, x1, y1 = head
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    xs, ys = _grid(size)
    # 顔(楕円)の中は動かさない
    face = np.sqrt(((xs - cx) / (w * 0.42)) ** 2 + ((ys - (cy + 0.12 * h)) / (h * 0.5)) ** 2)
    outside_face = _smooth((face - 1.0) / 0.35)
    # 頭の横幅の少し外まで(髪の広がり)。背景はあまり動かさない
    side = _smooth((w * 1.25 - np.abs(xs - cx)) / (w * 0.35))
    # 頭の中ほどから下ほど大きく、毛先(頭の下 2 つ分)まで
    down = _smooth((ys - (y0 + 0.35 * h)) / (h * 1.2)) * _smooth((y1 + 2.2 * h - ys) / (h * 0.6))
    return outside_face * side * down


def body_weight(head: tuple[float, float, float, float], size: tuple[int, int]) -> np.ndarray:
    """呼吸で持ち上がる量: 頭・肩で 1、胸の下あたりで 0(その下と背景は動かない)。"""
    x0, y0, x1, y1 = head
    w, h = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2
    xs, ys = _grid(size)
    side = _smooth((w * 2.2 - np.abs(xs - cx)) / (w * 0.8))
    vertical = _smooth((y1 + 2.6 * h - ys) / (h * 2.0))
    above = _smooth((ys - (y0 - 0.6 * h)) / (h * 0.4))
    return side * vertical * above


def _remap(source: np.ndarray, map_x: np.ndarray, map_y: np.ndarray) -> np.ndarray:
    return cv2.remap(source, map_x.astype(np.float32), map_y.astype(np.float32), cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def _plate(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """人物を消して背景だけにした絵(人物が動いて見えるようになった所に使う)。"""
    hole = cv2.dilate((mask > 0.2).astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
    return cv2.inpaint(image, hole, 6, cv2.INPAINT_TELEA)


def render(
    source: Image.Image,
    head: tuple[float, float, float, float],
    kind: str,
    frames: int,
    amount: float = 1.0,
    mask: np.ndarray | None = None,
) -> list[Image.Image]:
    """
    1周分のコマ(最初のコマは元の絵と同じ)。amount は動きの大きさ(1 が標準)。
    mask(人物らしさ 0〜1。anime_seg.person_mask)があれば、人物だけを動かし、背景は動かさない(人物が動いて
    見えるようになった所は、人物を消して埋めた背景)。動いていない所は元の絵のまま。
    """
    if kind not in WARPS:
        raise WarpError(f"変形の種類は {', '.join(WARPS)} のどれかです。")
    if not MIN_FRAMES <= frames <= MAX_FRAMES:
        raise WarpError(f"コマ数は {MIN_FRAMES}〜{MAX_FRAMES} にしてください。")
    image = np.asarray(source.convert("RGB"))
    size = source.size
    x0, y0, x1, y1 = head
    w, h = x1 - x0, y1 - y0
    xs, ys = _grid(size)
    weight = {"breath": body_weight, "hair": hair_weight}.get(kind, head_weight)(head, size)
    plate = _plate(image, mask) if mask is not None else None
    result = []
    for index in range(frames):
        t = index / frames
        # 0 → 1 → 0 と行って戻る量(最初のコマは 0 = 元の絵)
        swing = (1 - math.cos(2 * math.pi * t)) / 2
        if kind == "breath":
            lift = amount * 0.035 * h * swing
            map_x, map_y = xs, ys + lift * weight
        elif kind == "nod":
            dip = amount * 0.07 * h * swing
            map_x, map_y = xs, ys - dip * weight
        elif kind == "tilt":
            # 首の付け根を中心に、重みの分だけ回す
            angle = math.radians(amount * 7.0) * swing * weight
            cx, cy = (x0 + x1) / 2, y1
            dx, dy = xs - cx, ys - cy
            cos, sin = np.cos(angle), np.sin(angle)
            map_x, map_y = cx + cos * dx + sin * dy, cy - sin * dx + cos * dy
        else:  # hair
            # 下へ行くほど遅れて揺れる(波が毛先へ伝わる)
            phase = 2 * math.pi * t - (ys - y0) / h * 1.4
            sway = amount * 0.06 * w * np.sin(phase)
            # 最初のコマを元の絵に合わせるため、t=0 の揺れを引く
            sway0 = amount * 0.06 * w * np.sin(-(ys - y0) / h * 1.4)
            map_x, map_y = xs - (sway - sway0) * weight, ys
        warped = _remap(image, map_x, map_y)
        if plate is not None and mask is not None:
            moved_mask = _remap(mask, map_x, map_y)[..., None]
            layered = warped * moved_mask + plate * (1 - moved_mask)
            # 動いた量が小さい所(1画素未満)は元の絵に寄せる(動かない所を、元の絵と同じにする)
            moved = np.hypot(map_x - xs, map_y - ys)[..., None]
            alpha = np.clip(moved, 0.0, 1.0)
            warped = np.clip(image * (1 - alpha) + layered * alpha, 0, 255).astype(np.uint8)
        result.append(Image.fromarray(warped))
    return result
