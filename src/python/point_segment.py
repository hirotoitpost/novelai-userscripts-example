"""
点で指して範囲を切り出す(Segment Anything の軽い版 MobileSAM。うごイラの E で、髪などの部位を分けるため)。

モデルは Acly/MobileSAM の ONNX(MIT。元の MobileSAM は Apache-2.0)。画像の特徴を作る部分(約28MB)と、
点から範囲を作る部分(約16MB)。初めて使うときに data/models/mobile-sam/ へ取ってくる。この PC の CPU で動かす。

使い方: features = encode(image) を一度作り、segment(features, 入れたい点, 外したい点) を何度でも呼べる。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / "mobile-sam"
_BASE_URL = "https://huggingface.co/Acly/MobileSAM/resolve/main"
_ENCODER = "mobile_sam_image_encoder.onnx"
_DECODER = "sam_mask_decoder_multi.onnx"
_SIZE = 1024

_sessions: dict[str, Any] = {}
_lock = threading.Lock()


def _ensure(name: str) -> Path:
    path = _MODEL_DIR / name
    if path.is_file():
        return path
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _MODEL_DIR / f"{name}.part"
    with httpx.Client(follow_redirects=True, timeout=600) as client:
        with client.stream("GET", f"{_BASE_URL}/{name}") as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_bytes(1 << 20):
                    f.write(chunk)
    tmp.replace(path)
    return path


def _session(name: str) -> Any:
    with _lock:
        if name not in _sessions:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.log_severity_level = 3
            _sessions[name] = ort.InferenceSession(str(_ensure(name)), options, providers=["CPUExecutionProvider"])
    return _sessions[name]


@dataclass
class Features:
    embeddings: np.ndarray
    size: tuple[int, int]  # 元の絵の (幅, 高さ)
    scale: float  # 元の絵 → 長い辺 1024 の倍率


def encode(image: Image.Image) -> Features:
    rgb = np.asarray(image.convert("RGB"), np.float32)
    h, w = rgb.shape[:2]
    scale = _SIZE / max(h, w)
    resized = cv2.resize(rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    (embeddings,) = _session(_ENCODER).run(None, {"input_image": resized})
    return Features(embeddings, (w, h), scale)


def segment(
    features: Features, positives: list[tuple[float, float]], negatives: list[tuple[float, float]] = ()
) -> list[tuple[np.ndarray, float]]:
    """点(元の絵の画素座標)から作った範囲の候補(True/False の配列, 確からしさ)。大きさの違う3つ。"""
    points = [*positives, *negatives]
    labels = [1.0] * len(positives) + [0.0] * len(negatives)
    # 箱を使わないときは、ラベル -1 の点を1つ足す(SAM の決まり)
    coords = np.array([[*points, (0.0, 0.0)]], np.float32) * features.scale
    label = np.array([[*labels, -1.0]], np.float32)
    w, h = features.size
    masks, scores, _ = _session(_DECODER).run(None, {
        "image_embeddings": features.embeddings,
        "point_coords": coords,
        "point_labels": label,
        "mask_input": np.zeros((1, 1, 256, 256), np.float32),
        "has_mask_input": np.zeros(1, np.float32),
        "orig_im_size": np.array([h, w], np.float32),
    })
    # 0 番目は「1つだけ返すとき」の答え。複数の点では 1〜3 番目(大きさの違う候補)を使う
    return [(masks[0, i] > 0, float(scores[0, i])) for i in range(1, masks.shape[1])]
