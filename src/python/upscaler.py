"""
アニメ絵向けの AI 拡大(超解像)。CDC のアニメ向けモデル(ONNX。https://huggingface.co/deepghs/cdc_anime_onnx 、
MIT ライセンス)で、縦横4倍にする。NovelAI を使わずこの PC で動くので、Anlas はかからず、絵の中身も変わらない
(描き足しはしない)。初めて使うときにモデル(約160MB)を data/models/cdc_anime/ に取ってくる。

単純な拡大(LANCZOS)より線や境目がくっきりする。範囲の描き直し(region_redraw)では、img2img の元にも使う
(元がくっきりしていると、弱い強さでも細部まできれいに描き直される)。docs/trials/2026-10-15_region-redraw.md を参照。
"""

from __future__ import annotations

import threading
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_FILE = "HGSR-MHR-anime-aug_X4_320.onnx"
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / "cdc_anime"
_MODEL_URL = f"https://huggingface.co/deepghs/cdc_anime_onnx/resolve/main/{MODEL_FILE}"
SCALE = 4
# 一度に通す大きさ(メモリを抑える)と、つなぎ目が出ないよう周りに足す幅
_TILE = 128
_PAD = 16
# モデルに渡す大きさは、縦横とも この倍数でないと動かない(実機で確認)
_MULTIPLE = 16
# これより大きい画像は、先に縮めてから拡大する(CPU で 0.16MP に約 20 秒かかる)
MAX_INPUT_PIXELS = 512 * 512

_session: Any = None
_lock = threading.Lock()


def _ensure_model() -> Path:
    model = _MODEL_DIR / MODEL_FILE
    if model.is_file():
        return model
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _MODEL_DIR / f"{MODEL_FILE}.part"
    with httpx.Client(follow_redirects=True, timeout=600) as client:
        with client.stream("GET", _MODEL_URL) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_bytes(1 << 20):
                    f.write(chunk)
    tmp.replace(model)
    return model


def _get_session() -> Any:
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort

            _session = ort.InferenceSession(str(_ensure_model()), providers=["CPUExecutionProvider"])
    return _session


def _run(tile: np.ndarray) -> np.ndarray:
    """
    (高さ, 幅, 3) の 0〜255 → 4倍の (高さ, 幅, 3) の 0〜1。モデルに渡す前に、縦横を 16 の倍数まで端の画素で広げ、
    結果から広げた分を切り落とす。モデルの出力の形は (1, 3, 4, 高さ, 4, 幅) と書かれているが、中身はそのまま
    (3, 4×高さ, 4×幅) に並べ直したものが正しい(実機で確かめた)。
    """
    h, w, _ = tile.shape
    ph, pw = -h % _MULTIPLE, -w % _MULTIPLE
    padded = np.pad(tile, ((0, ph), (0, pw), (0, 0)), mode="edge")
    x = padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    (y,) = _get_session().run(["output"], {"input": x})
    _, c, _, hh, _, ww = y.shape
    return y.reshape(c, SCALE * hh, SCALE * ww).transpose(1, 2, 0)[: SCALE * h, : SCALE * w]


def upscale(
    image: Image.Image, max_input: int = MAX_INPUT_PIXELS, progress: Callable[[int, int], None] | None = None
) -> Image.Image:
    """
    縦横4倍にする(max_input より大きい画像は、先にそこまで縮める)。progress は (済んだタイルの数, タイルの数) で
    呼ぶ(画像全体を拡大するときの進み具合)。
    """
    image = image.convert("RGB")
    if image.width * image.height > max_input:
        ratio = (max_input / (image.width * image.height)) ** 0.5
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
    arr = np.asarray(image)
    h, w, _ = arr.shape
    out = np.zeros((h * SCALE, w * SCALE, 3), np.float32)
    total = -(-h // _TILE) * -(-w // _TILE)
    done = 0
    for y0 in range(0, h, _TILE):
        for x0 in range(0, w, _TILE):
            ys, xs = max(y0 - _PAD, 0), max(x0 - _PAD, 0)
            ye, xe = min(y0 + _TILE + _PAD, h), min(x0 + _TILE + _PAD, w)
            result = _run(arr[ys:ye, xs:xe])
            th, tw = min(_TILE, h - y0), min(_TILE, w - x0)
            oy, ox = (y0 - ys) * SCALE, (x0 - xs) * SCALE
            out[y0 * SCALE:(y0 + th) * SCALE, x0 * SCALE:(x0 + tw) * SCALE] = result[oy:oy + th * SCALE, ox:ox + tw * SCALE]
            done += 1
            if progress is not None:
                progress(done, total)
    return Image.fromarray((out.clip(0, 1) * 255).round().astype(np.uint8))


def enlarge(
    image: Image.Image, size: tuple[int, int], max_input: int = MAX_INPUT_PIXELS,
    progress: Callable[[int, int], None] | None = None,
) -> Image.Image:
    """AI 拡大してから、指定の大きさに合わせる。"""
    return upscale(image, max_input, progress).resize(size, Image.Resampling.LANCZOS)
