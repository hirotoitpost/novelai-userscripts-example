"""
コマのあいだの絵を作る(中割り。うごイラの C)。RIFE(フレーム補間のモデル)を ONNX で、この PC の CPU で動かす。

モデルは SOR2171/RIFE-ONNX(MIT。Practical-RIFE 4.26 を ONNX にしたもの、約23MB)。初めて使うときに
data/models/rife/ へ取ってくる。入力は2枚の絵(0〜1 の RGB を縦に6チャンネル)と、あいだの位置(0〜1)。縦横は 64 の
倍数でないといけないので、端の画素で広げてから通し、広げた分を切り落とす。832×1216 で1枚 2.6 秒ほど(実機)。
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
MODEL_FILE = "rife2_26h.onnx"
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / "rife"
_MODEL_URL = f"https://huggingface.co/SOR2171/RIFE-ONNX/resolve/main/{MODEL_FILE}"
_MULTIPLE = 64
# 中割りでモデルの絵を使う範囲: a と b の差がこれより大きい所を、_GROW 画素ほど広げる
_CHANGED = 6
_GROW = 25

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


def _array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.float32).transpose(2, 0, 1)[None] / 255.0


def between(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
    """a と b のあいだ(t=0 で a、1 で b)の絵。大きさが違えば b を a に合わせる。"""
    a, b = a.convert("RGB"), b.convert("RGB")
    if b.size != a.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    w, h = a.size
    x = np.concatenate([_array(a), _array(b)], axis=1)
    x = np.pad(x, ((0, 0), (0, 0), (0, -h % _MULTIPLE), (0, -w % _MULTIPLE)), mode="edge")
    (y,) = _get_session().run(None, {"input": x, "timestep": np.full((1, 1, 1, 1), t, np.float32)})
    out = (y[0, :3, :h, :w].transpose(1, 2, 0).clip(0, 1) * 255).round().astype(np.uint8)
    # モデルの出力は、変わっていない所も少しだけずれる(まばたきで、コマ全体がわずかにちらついた。実機で確認)。
    # a と b が違う所(とその周り)だけモデルの絵を使い、ほかは a のまま
    changed = np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).max(axis=2) > _CHANGED
    if not changed.any():
        return a.copy()
    area = cv2.GaussianBlur(cv2.dilate(changed.astype(np.uint8) * 255, np.ones((_GROW, _GROW), np.uint8)), (0, 0), _GROW / 3)
    alpha = (area.astype(np.float32) / 255.0)[..., None]
    mixed = out * alpha + np.asarray(a, np.float32) * (1 - alpha)
    return Image.fromarray(mixed.round().astype(np.uint8))


def same(a: Image.Image, b: Image.Image) -> bool:
    """
    ほとんど同じ絵か(中割りしても変わらないので、飛ばす)。目・口だけが変わるコマは、絵全体の平均では差が小さすぎる
    (まばたき・口パクで平均 0.5 未満だった。実機で確認)ので、いちばん変わった所(差の大きい方から 0.5%)で比べる。
    """
    if a.size != b.size:
        return False
    small = (256, 256)
    diff = np.abs(np.asarray(a.convert("L").resize(small), float) - np.asarray(b.convert("L").resize(small), float))
    return float(np.percentile(diff, 99.5)) < 4.0
