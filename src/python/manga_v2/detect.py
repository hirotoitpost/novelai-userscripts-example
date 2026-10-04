"""
コマの絵から登場人物(人・動物)の頭の位置を見つける。吹き出しを顔に被せない配置と、
寄りのコマの切り抜き位置に使う。

モデルは deepghs/anime_head_detection の head_detect_v0.5_s(YOLOv8, ONNX, MIT License,
https://huggingface.co/deepghs/anime_head_detection)。実機の生成画像で比べた結果:
- OpenCV の lbpcascade_animeface: 12コマで検出 0
- ローカルの画像モデル(qwen2.5vl:3b)に座標を答えさせる: GPU に載らず CPU で1枚10分超
- このモデル: 人の頭に加えて狐の頭もほぼ拾え、CPU で1枚 0.15 秒程度
初回に data/models/ へダウンロードする(約45MB)。取得できなければ検出なしとして扱い、
合成自体は止めない。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / "head_detect_v0.5_s"
_MODEL_URL = "https://huggingface.co/deepghs/anime_head_detection/resolve/main/head_detect_v0.5_s/{name}"
_INPUT = 640
# モデル配布元の threshold.json の値(F1 が最大になる閾値)
_DEFAULT_THRESHOLD = 0.415
_IOU = 0.5

Box = tuple[float, float, float, float]

_lock = threading.Lock()
_session = None
_threshold = _DEFAULT_THRESHOLD
_unavailable = False
_cache: dict[tuple[str, float], list[Box]] = {}

logger = logging.getLogger(__name__)


def _ensure_model() -> Path:
    model = _MODEL_DIR / "model.onnx"
    if model.is_file():
        return model
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=300) as client:
        for name in ("threshold.json", "model.onnx"):
            response = client.get(_MODEL_URL.format(name=name))
            response.raise_for_status()
            tmp = _MODEL_DIR / f"{name}.part"
            tmp.write_bytes(response.content)
            tmp.replace(_MODEL_DIR / name)
    return model


def _get_session():
    global _session, _threshold, _unavailable
    with _lock:
        if _session is None and not _unavailable:
            try:
                import onnxruntime as ort

                model = _ensure_model()
                _session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
                threshold_file = _MODEL_DIR / "threshold.json"
                if threshold_file.is_file():
                    _threshold = float(json.loads(threshold_file.read_text())["threshold"])
            except Exception:  # noqa: BLE001 検出できなくても合成は続ける
                logger.exception("head detector unavailable")
                _unavailable = True
        return _session


def _nms(boxes: np.ndarray, scores: np.ndarray) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        x0 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        y0 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        x1 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        y1 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
        area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        iou = inter / (area[i] + area[order[1:]] - inter + 1e-6)
        order = order[1:][iou < _IOU]
    return keep


def detect_heads(path: Path) -> list[Box]:
    """画像の頭の位置(元画像のピクセル座標 x0, y0, x1, y1)。同じファイルは結果を使い回す。"""
    try:
        cache_key = (str(path), path.stat().st_mtime)
    except OSError:
        return []
    if cache_key in _cache:
        return _cache[cache_key]
    session = _get_session()
    if session is None:
        return []
    with Image.open(path) as src:
        image = src.convert("RGB")
    scale = _INPUT / max(image.width, image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)))
    canvas = np.full((_INPUT, _INPUT, 3), 114, np.uint8)
    canvas[: resized.height, : resized.width] = np.asarray(resized)
    blob = canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255
    output = session.run(None, {session.get_inputs()[0].name: blob})[0][0].T  # (N, 4 + classes)
    scores = output[:, 4:].max(axis=1)
    keep = scores > _threshold
    cx, cy, w, h = (output[keep, i] / scale for i in range(4))
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1) if keep.any() else np.zeros((0, 4))
    result = [tuple(float(v) for v in boxes[i]) for i in _nms(boxes, scores[keep])] if len(boxes) else []
    _cache[cache_key] = result  # type: ignore[assignment]
    return result  # type: ignore[return-value]
