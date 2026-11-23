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

import hashlib
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

BLOCK = 8
BITS = 64
MAGIC = 0x5D1A
# 量子化の幅(DCT の値。大きいほど丈夫で、変化も大きい)。14 で PSNR 約 43dB、JPEG 画質 60 まで読める
STEP = 14.0
# 明るさのばらつきがこれより小さいブロック(ベタ塗り)には入れない(4 倍に拡大すると紙の目のような模様が見えた)。
# 入れなかったブロックの係数はほぼ 0 で、読むときは 0 と 1 の格子のちょうど間になるので、票を偏らせない
FLAT_STD = 3.0
# 埋める係数(8×8 の DCT の中ほどの周波数。低すぎると見え、高すぎると JPEG で消える)
_COEFFS = ((1, 2), (2, 1), (2, 2), (1, 3), (3, 1))
_DEFAULT_KEY = "novelai-userscripts-watermark-v1"


def _dct_matrix() -> np.ndarray:
    n = BLOCK
    m = np.zeros((n, n))
    for k in range(n):
        scale = np.sqrt(1 / n) if k == 0 else np.sqrt(2 / n)
        for i in range(n):
            m[k, i] = scale * np.cos(np.pi * (2 * i + 1) * k / (2 * n))
    return m


_D = _dct_matrix()


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


def _assignment(rows: int, cols: int) -> np.ndarray:
    """ブロックごとに受け持つビット(鍵で決まる並べ替え)。"""
    count = rows * cols
    seed = int.from_bytes(hashlib.sha256(f"{_key()}:{rows}x{cols}".encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    order = np.arange(count) % BITS
    rng.shuffle(order)
    return order.reshape(rows, cols)


def _blocks(luma: np.ndarray) -> tuple[np.ndarray, int, int]:
    rows, cols = luma.shape[0] // BLOCK, luma.shape[1] // BLOCK
    cropped = luma[: rows * BLOCK, : cols * BLOCK]
    blocks = cropped.reshape(rows, BLOCK, cols, BLOCK).transpose(0, 2, 1, 3)
    return blocks, rows, cols


def _steps(blocks: np.ndarray) -> np.ndarray:
    """ブロックごとの量子化の幅(すべて同じ)。"""
    return np.full(blocks.shape[:2], STEP)


def embed(image: Image.Image, mark_id: int) -> Image.Image:
    """透かしを埋めた絵(RGB)を返す。色(Cb・Cr)は変えず、明るさだけを変える。"""
    ycc = np.asarray(image.convert("RGB").convert("YCbCr"), dtype=np.float64)
    luma = ycc[:, :, 0]
    blocks, rows, cols = _blocks(luma)
    if rows * cols < BITS * 4:
        raise ValueError("絵が小さすぎて透かしを入れられません")
    bits = encode_payload(mark_id)[_assignment(rows, cols)]
    steps = _steps(blocks)
    coeffs = _D @ blocks @ _D.T
    # 0 は -幅/4、1 は +幅/4 の格子(0 に丸められた係数は どちらにも寄らない)
    offset = (bits - 0.5) * (steps / 2)
    busy = blocks.std(axis=(2, 3)) >= FLAT_STD
    if busy.sum() < BITS * 8:
        raise ValueError("絵の模様が少なすぎて透かしを入れられません")
    for u, v in _COEFFS:
        c = coeffs[:, :, u, v]
        coeffs[:, :, u, v] = np.where(busy, np.round((c - offset) / steps) * steps + offset, c)
    restored = _D.T @ coeffs @ _D
    out = luma.copy()
    out[: rows * BLOCK, : cols * BLOCK] = restored.transpose(0, 2, 1, 3).reshape(rows * BLOCK, cols * BLOCK)
    ycc[:, :, 0] = np.clip(out, 0, 255)
    return Image.fromarray(np.round(ycc).astype(np.uint8), "YCbCr").convert("RGB")


@dataclass
class Reading:
    mark_id: int | None
    # ビットごとの票の偏り(0〜1。1 に近いほどはっきり読めた)の平均
    confidence: float


def extract(image: Image.Image) -> Reading:
    """絵から透かしを読む。読めなければ mark_id は None。"""
    luma = np.asarray(image.convert("RGB").convert("YCbCr"), dtype=np.float64)[:, :, 0]
    blocks, rows, cols = _blocks(luma)
    if rows * cols < BITS * 4:
        return Reading(None, 0.0)
    assign = _assignment(rows, cols)
    steps = _steps(blocks)
    coeffs = _D @ blocks @ _D.T
    votes = np.zeros(BITS)
    weight = np.zeros(BITS)
    for u, v in _COEFFS:
        c = coeffs[:, :, u, v]
        # 0 の格子(-幅/4)と 1 の格子(+幅/4)のどちらに近いか。+1 なら 1、-1 なら 0、0 は どちらでもない
        phase = np.mod(c / steps + 0.25, 1.0)  # 0 の格子で 0、1 の格子で 0.5
        score = -np.cos(2 * np.pi * phase)
        np.add.at(votes, assign.ravel(), score.ravel())
        np.add.at(weight, assign.ravel(), 1.0)
    mean = votes / np.maximum(weight, 1)
    bits = (mean > 0).astype(np.int8)
    return Reading(decode_payload(bits), float(np.abs(mean).mean()))
