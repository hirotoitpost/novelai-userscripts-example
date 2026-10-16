"""
アニメ絵の人物を背景から切り分ける(うごイラの D. 変形で、人物だけを動かし、背景は動かさないため)。

モデルは skytnt/anime-seg の isnetis.onnx(Apache-2.0、約176MB)。初めて使うときに data/models/anime-seg/ へ取ってくる。
この PC の CPU で動かす。入力は 1024×1024 の正方形(長い辺を 1024 に縮め、残りは 0 で埋める)、0〜1 の RGB。
出力は人物らしさ(0〜1)。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_FILE = "isnetis.onnx"
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / "anime-seg"
_MODEL_URL = f"https://huggingface.co/skytnt/anime-seg/resolve/main/{MODEL_FILE}"
_SIZE = 1024

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

            options = ort.SessionOptions()
            options.log_severity_level = 3
            _session = ort.InferenceSession(str(_ensure_model()), options, providers=["CPUExecutionProvider"])
    return _session


def person_mask(image: Image.Image) -> np.ndarray:
    """人物らしさ(0〜1、float32)。大きさは image と同じ。"""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    h, w = rgb.shape[:2]
    scale = _SIZE / max(h, w)
    nh, nw = round(h * scale), round(w * scale)
    top, left = (_SIZE - nh) // 2, (_SIZE - nw) // 2
    canvas = np.zeros((_SIZE, _SIZE, 3), np.float32)
    canvas[top : top + nh, left : left + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    (mask,) = _get_session().run(None, {"img": canvas.transpose(2, 0, 1)[None]})
    mask = mask[0, 0, top : top + nh, left : left + nw]
    return np.clip(cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR), 0.0, 1.0).astype(np.float32)
