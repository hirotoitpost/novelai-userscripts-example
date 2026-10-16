"""
欠けた所を、まわりから描いて埋める(LaMa。うごイラの E で、髪や頭が動いて見えるようになった裏を埋めるため)。

モデルは Carve/LaMa-ONNX の lama_fp32.onnx(Apache-2.0。元の big-lama も Apache-2.0、約208MB)。初めて使うときに
data/models/lama/ へ取ってくる。この PC の CPU で動かす。入力は 512×512 に決まっているので、埋める所のまわりを
正方形に切り出して 512 にし、埋めた絵を元の大きさに戻して、埋める所にだけ貼る。
cv2.inpaint(まわりの色をのばすだけ)より、服のしわや空のような模様が続いて見える。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / "lama"
_MODEL_FILE = "lama_fp32.onnx"
_MODEL_URL = f"https://huggingface.co/Carve/LaMa-ONNX/resolve/main/{_MODEL_FILE}"
_SIZE = 512

_session: Any = None
_lock = threading.Lock()


def _ensure_model() -> Path:
    model = _MODEL_DIR / _MODEL_FILE
    if model.is_file():
        return model
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _MODEL_DIR / f"{_MODEL_FILE}.part"
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


def inpaint(image: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """image(高さ×幅×3 の uint8 RGB)の hole(True の所)を埋めた絵。hole の外は image のまま。"""
    if not hole.any():
        return image.copy()
    height, width = hole.shape
    ys, xs = np.nonzero(hole)
    # 埋める所のまわりに余白を付けた正方形(絵からはみ出す分は、絵の中へずらす)
    margin = max(32, int(0.15 * max(xs.max() - xs.min(), ys.max() - ys.min())))
    side = min(max(xs.max() - xs.min(), ys.max() - ys.min()) + 2 * margin, max(width, height))
    cx, cy = (xs.min() + xs.max()) // 2, (ys.min() + ys.max()) // 2
    left = int(min(max(cx - side // 2, 0), max(width - side, 0)))
    top = int(min(max(cy - side // 2, 0), max(height - side, 0)))
    crop = image[top : top + side, left : left + side]
    crop_hole = hole[top : top + side, left : left + side]
    ch, cw = crop_hole.shape
    small = cv2.resize(crop, (_SIZE, _SIZE), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    small_hole = (cv2.resize(crop_hole.astype(np.uint8), (_SIZE, _SIZE), interpolation=cv2.INTER_NEAREST) > 0).astype(np.float32)
    (out,) = _get_session().run(None, {"image": small.transpose(2, 0, 1)[None], "mask": small_hole[None, None]})
    out = out[0].transpose(1, 2, 0)
    if out.max() <= 2.0:
        out = out * 255.0
    filled = cv2.resize(np.clip(out, 0, 255).astype(np.uint8), (cw, ch), interpolation=cv2.INTER_CUBIC)
    result = image.copy()
    region = result[top : top + ch, left : left + cw]
    region[crop_hole] = filled[crop_hole]
    return result
