"""
アニメ絵の体の部位分け(髪・顔・首・上の服・手・下の服・脚・足 など 19 部位)。see-through(Live2D 用の層分け)の
SAM Body Parsing(24yearsold/l2d_sam_iter2、HQ-SAM の微調整、Apache-2.0)を ONNX にしたものを、この PC の CPU で動かす
(1枚 20 秒ほど)。

色や骨からの距離の決まりで切り出すより、影で黒くなった髪や、体の後ろに垂れた髪まで正しく取れた(2026-10 実機: 九条ゆらの
長い髪)。モデルは data/models/body-parsing/ に置く。無ければ scripts/export_body_parsing.py で作る(元の重みと see-through の
コードを一時的な環境で取ってきて書き出す。アプリには torch は要らない)。無いときは available() が False を返し、呼び出し側は
これまでの切り出しを使う。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_PATH = _PROJECT_ROOT / "data" / "models" / "body-parsing" / "body_parsing.onnx"
_SIZE = 1024
_MEAN = np.array([123.675, 116.28, 103.53], np.float32)
_STD = np.array([58.395, 57.12, 57.375], np.float32)

LABELS = (
    "hair", "headwear", "face", "eyes", "eyewear", "ears", "earwear", "nose", "mouth",
    "neck", "neckwear", "topwear", "handwear", "bottomwear", "legwear", "footwear", "tail", "wings", "objects",
)

_session: Any = None
_lock = threading.Lock()


def available() -> bool:
    return MODEL_PATH.is_file()


def _get_session() -> Any:
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort

            _session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
        return _session


def parse(image: Image.Image) -> dict[str, np.ndarray]:
    """部位ごとの範囲(True の所。大きさは image と同じ)。部位の名前は LABELS。"""
    rgb = np.asarray(image.convert("RGB"), np.float32)
    h, w = rgb.shape[:2]
    scale = _SIZE / max(h, w)
    nh, nw = round(h * scale), round(w * scale)
    top, left = (_SIZE - nh) // 2, (_SIZE - nw) // 2
    canvas = np.zeros((_SIZE, _SIZE, 3), np.float32)
    canvas[top:top + nh, left:left + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = (canvas - _MEAN) / _STD
    (logits,) = _get_session().run(None, {"image": canvas.transpose(2, 0, 1)[None].astype(np.float32)})
    out = {}
    for name, layer in zip(LABELS, logits[0]):
        full = cv2.resize(layer, (_SIZE, _SIZE), interpolation=cv2.INTER_LINEAR)[top:top + nh, left:left + nw]
        out[name] = cv2.resize(full, (w, h), interpolation=cv2.INTER_LINEAR) > 0
    return out
