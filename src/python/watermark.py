"""
見えない透かし(電子透かし)。絵の明るさ(輝度)の 8×8 ブロックの DCT の中ほどの周波数に、64 ビットの印を
量子化(QIM)で埋める。印は「目印 16 ビット + 透かしの番号 32 ビット + 検査用 16 ビット(CRC)」。
同じビットを絵じゅうの多くのブロックに散らして繰り返すので、X の JPEG 再圧縮や軽い縮小のあとでも、
多数決で読み戻せる。切り抜き・回転・大きな縮小には弱い(そのときは台帳の知覚ハッシュで照合する。watermark_registry.py)。

どのブロックがどのビットを受け持つかは鍵(WATERMARK_KEY。無ければ既定の鍵)で決まる。鍵が違うと読めない。
量子化の幅は全ブロックで同じにする(ブロックの平らさで幅を変えると、JPEG で平らさが変わったときに
読む側の幅がずれて読めなくなる。実機の絵と合成の絵で確かめた)。
"""

from __future__ import annotations

import functools
import hashlib
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

BLOCK = 8
BITS = 64
MAGIC = 0x5D1A
_DEFAULT_KEY = "novelai-userscripts-watermark-v1"


@dataclass(frozen=True)
class Profile:
    """埋め方。step: 量子化の幅(DCT の値。大きいほど丈夫で、変化も大きい)。coeffs: 埋める 8×8 DCT の係数。
    flat_std: 明るさのばらつきがこれより小さいブロック(ベタ塗り)には入れない。入れなかったブロックの係数は
    ほぼ 0 で、読むときは 0 と 1 の格子のちょうど間になるので、票を偏らせない。"""

    name: str
    step: float
    coeffs: tuple[tuple[int, int], ...]
    flat_std: float = 3.0
    # DCT のブロックの大きさ(大きいほど模様が粗く、縮小・動画の圧縮に強い)
    block: int = 8


# 画像: 中ほどの周波数。PSNR 約 44dB、JPEG 画質 60・0.75 倍の縮小(戻して読む)まで読める
IMAGE = Profile("image", 14.0, ((1, 2), (2, 1), (2, 2), (1, 3), (3, 1)))
# 動画: 動画の圧縮は中ほどの周波数を消すので、16×16 のブロックの低い周波数に強めに入れる。PSNR 約 40dB。
# 1989 番の動画(784x1152)を X と同じ 720x1056 に縮めて弱いエンコーダー 2.2M bps・1.0M bps で圧縮し直しても、
# 48 コマの多数決で読めた(8×8 の前の入れ方は、実際の X の動画で 64 ビット中 15 ビット誤って読めなかった)
VIDEO = Profile("video2", 80.0, ((0, 1), (1, 0), (1, 1)), 3.0, 16)
# 前の動画の入れ方(2026-11-23 に X へ投稿した動画。読むときだけ使う)
VIDEO_V1 = Profile("video", 32.0, ((0, 1), (1, 0), (1, 1)))
PROFILES = (IMAGE, VIDEO, VIDEO_V1)
VIDEO_PROFILES = (VIDEO, VIDEO_V1)


@functools.lru_cache(maxsize=4)
def _dct_matrix(n: int = BLOCK) -> np.ndarray:
    m = np.zeros((n, n))
    for k in range(n):
        scale = np.sqrt(1 / n) if k == 0 else np.sqrt(2 / n)
        for i in range(n):
            m[k, i] = scale * np.cos(np.pi * (2 * i + 1) * k / (2 * n))
    return m


def _key() -> str:
    return os.environ.get("WATERMARK_KEY", "").strip() or _DEFAULT_KEY


def _crc16(value: int) -> int:
    """CRC-16/CCITT(目印と番号の 6 バイト)。"""
    crc = 0xFFFF
    for byte in value.to_bytes(6, "big"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def encode_payload(mark_id: int) -> np.ndarray:
    """番号(32 ビット)を 64 ビットの並び(0/1)にする。"""
    if not 0 <= mark_id < 2**32:
        raise ValueError("透かしの番号は 0〜2^32-1 です")
    head = (MAGIC << 32) | mark_id
    word = (head << 16) | _crc16(head)
    return np.array([(word >> (BITS - 1 - i)) & 1 for i in range(BITS)], dtype=np.int8)


def decode_payload(bits: np.ndarray) -> int | None:
    """64 ビットの並びから番号を取り出す(目印か CRC が合わなければ None)。"""
    word = 0
    for bit in bits:
        word = (word << 1) | int(bit)
    head, crc = word >> 16, word & 0xFFFF
    if head >> 32 != MAGIC or _crc16(head) != crc:
        return None
    return head & 0xFFFFFFFF


def _assignment(rows: int, cols: int, block: int = BLOCK) -> np.ndarray:
    """ブロックごとに受け持つビット(鍵で決まる並べ替え)。"""
    count = rows * cols
    tag = f"{rows}x{cols}" if block == BLOCK else f"{rows}x{cols}/{block}"
    seed = int.from_bytes(hashlib.sha256(f"{_key()}:{tag}".encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    order = np.arange(count) % BITS
    rng.shuffle(order)
    return order.reshape(rows, cols)


def _blocks(luma: np.ndarray, block: int = BLOCK) -> tuple[np.ndarray, int, int]:
    rows, cols = luma.shape[0] // block, luma.shape[1] // block
    cropped = luma[: rows * block, : cols * block]
    blocks = cropped.reshape(rows, block, cols, block).transpose(0, 2, 1, 3)
    return blocks, rows, cols


def embed(image: Image.Image, mark_id: int, profile: Profile = IMAGE) -> Image.Image:
    """透かしを埋めた絵(RGB)を返す。色(Cb・Cr)は変えず、明るさだけを変える。"""
    ycc = np.asarray(image.convert("RGB").convert("YCbCr"), dtype=np.float64)
    luma = ycc[:, :, 0]
    n = profile.block
    blocks, rows, cols = _blocks(luma, n)
    if rows * cols < BITS * 4:
        raise ValueError("絵が小さすぎて透かしを入れられません")
    bits = encode_payload(mark_id)[_assignment(rows, cols, n)]
    steps = np.full(blocks.shape[:2], profile.step)
    d = _dct_matrix(n)
    coeffs = d @ blocks @ d.T
    # 0 は -幅/4、1 は +幅/4 の格子(0 に丸められた係数は どちらにも寄らない)
    offset = (bits - 0.5) * (steps / 2)
    busy = blocks.std(axis=(2, 3)) >= profile.flat_std
    if busy.sum() < BITS * 8 * (BLOCK / n) ** 2:
        raise ValueError("絵の模様が少なすぎて透かしを入れられません")
    for u, v in profile.coeffs:
        c = coeffs[:, :, u, v]
        coeffs[:, :, u, v] = np.where(busy, np.round((c - offset) / steps) * steps + offset, c)
    restored = d.T @ coeffs @ d
    out = luma.copy()
    out[: rows * n, : cols * n] = restored.transpose(0, 2, 1, 3).reshape(rows * n, cols * n)
    ycc[:, :, 0] = np.clip(out, 0, 255)
    return Image.fromarray(np.round(ycc).astype(np.uint8), "YCbCr").convert("RGB")


@dataclass
class Reading:
    mark_id: int | None
    # ビットごとの票の偏り(0〜1。1 に近いほどはっきり読めた)の平均
    confidence: float
    # ビットごとの票(台帳の番号と照らし合わせるのに使う)
    votes: np.ndarray | None = None


def votes(image: Image.Image, profile: Profile = IMAGE) -> np.ndarray | None:
    """ビットごとの票(-1〜+1。+ なら 1)。動画では何コマか分を足してから decide で読む。小さすぎる絵は None。"""
    luma = np.asarray(image.convert("RGB").convert("YCbCr"), dtype=np.float64)[:, :, 0]
    n = profile.block
    blocks, rows, cols = _blocks(luma, n)
    if rows * cols < BITS * 4:
        return None
    assign = _assignment(rows, cols, n)
    steps = np.full(blocks.shape[:2], profile.step)
    d = _dct_matrix(n)
    coeffs = d @ blocks @ d.T
    total = np.zeros(BITS)
    weight = np.zeros(BITS)
    for u, v in profile.coeffs:
        c = coeffs[:, :, u, v]
        # 0 の格子(-幅/4)と 1 の格子(+幅/4)のどちらに近いか。+1 なら 1、-1 なら 0、0 は どちらでもない
        phase = np.mod(c / steps + 0.25, 1.0)  # 0 の格子で 0、1 の格子で 0.5
        score = -np.cos(2 * np.pi * phase)
        np.add.at(total, assign.ravel(), score.ravel())
        np.add.at(weight, assign.ravel(), 1.0)
    return total / np.maximum(weight, 1)


def decide(mean: np.ndarray) -> Reading:
    """票から番号を読む(目印か CRC が合わなければ None)。"""
    bits = (mean > 0).astype(np.int8)
    return Reading(decode_payload(bits), float(np.abs(mean).mean()), mean)


# 目印(先頭 16 ビット)はどの番号でも同じなので、照らし合わせには使わない
_ID_BITS = slice(16, BITS)


def agreement(reading: Reading, mark_id: int) -> float:
    """
    読んだ票が、その番号の透かしとどれだけ合うか(-1〜1)。番号と検査の 48 ビットで、票の強さで重みを付ける。
    関係ない番号ならほぼ 0(実際の X の動画で、ばらつきの幅は約 0.18)。
    """
    if reading.votes is None:
        return 0.0
    mean = reading.votes[_ID_BITS]
    total = float(np.abs(mean).sum())
    if total == 0:
        return 0.0
    expected = 2 * encode_payload(mark_id)[_ID_BITS].astype(np.float64) - 1
    return float((mean * expected).sum() / total)


def extract(image: Image.Image, profile: Profile | None = None) -> Reading:
    """
    絵から透かしを読む。読めなければ mark_id は None。profile を省くと、画像の埋め方、だめなら動画の埋め方で読む
    (動画から切り出した1コマも読めるように)。
    """
    best = Reading(None, 0.0)
    for candidate in (profile,) if profile else PROFILES:
        mean = votes(image, candidate)
        if mean is None:
            continue
        reading = decide(mean)
        if reading.mark_id is not None:
            return reading
        best = reading if reading.confidence > best.confidence else best
    return best
