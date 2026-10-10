"""
漫画のページから、コマ(frame)・顔(face)・体(body)・文字(text)の位置を見つける。漫画の取り込み
(構成の参考)で、コマの切り出しと、コマごとの人数・構図・セリフの量に使う。

モデルは deepghs/manga109_yolo の v2023.12.07_s_yv11(YOLO11s, ONNX,
https://huggingface.co/deepghs/manga109_yolo)。学習データは Manga109-s
(Aizawa et al., "Building a Manga Dataset 'Manga109' with Annotations for Multimedia Applications",
IEEE MultiMedia, 2020)。Manga109-s は産業利用も許諾された部分集合で、学習結果の利用は認められているが、
データセット(漫画の画像)の再配布は禁止されている。ここではモデルを手元で動かして位置(座標)を出すだけで、
データセットは使わない。モデル自体にはライセンスの表記が無い(2026-10 時点)ため、アプリを配布・商用提供
するときは作者に確認するか、別のモデルに替えること。モデルはリポジトリに含めず、初回に data/models/ へ
ダウンロードする(約38MB)。取得できなければ None を返し、呼び出し側は枠線からコマを探す方法に戻る。

実機の比較(2026-10): 枠線を探す方法では、裁ち切りのコマや、コマにまたがって描かれた人物のある市販の
ページで、ページ全体を1コマと見誤った。このモデルは同じページのコマを全て拾い、1ページ 0.1 秒程度。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_VERSION = "v2023.12.07_s_yv11"
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / f"manga109_yolo_{_VERSION}"
_MODEL_URL = f"https://huggingface.co/deepghs/manga109_yolo/resolve/main/{_VERSION}/{{name}}"
_INPUT = 640
# 配布元の threshold.json の値(F1 が最大になる閾値)
_DEFAULT_THRESHOLD = 0.383
_IOU = 0.5
LABELS = ("body", "face", "frame", "text")

Box = tuple[float, float, float, float]  # x0, y0, x1, y1(元画像のピクセル座標)

_lock = threading.Lock()
_session = None
_threshold = _DEFAULT_THRESHOLD
_unavailable = False


def _ensure_model() -> Path:
    model = _MODEL_DIR / "model.onnx"
    if model.is_file():
        return model
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=300) as client:
        for name in ("threshold.json", "labels.json", "model.onnx"):
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
                labels = json.loads((_MODEL_DIR / "labels.json").read_text())
                if tuple(labels) != LABELS:
                    raise RuntimeError(f"想定と違うラベルです: {labels}")
                threshold_file = _MODEL_DIR / "threshold.json"
                if threshold_file.is_file():
                    _threshold = float(json.loads(threshold_file.read_text())["threshold"])
            except Exception:  # noqa: BLE001 使えなくても、取り込みは枠線から探す方法で続ける
                logger.exception("manga element detector unavailable")
                _unavailable = True
        return _session


def _nms(boxes: np.ndarray, scores: np.ndarray) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(int(i))
        x0 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        y0 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        x1 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        y1 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
        iou = inter / (area[i] + area[order[1:]] - inter + 1e-6)
        order = order[1:][iou < _IOU]
    return keep


def detect_elements(image: Image.Image) -> dict[str, list[Box]] | None:
    """ページの要素の位置(種類ごと)。モデルが使えなければ None。"""
    session = _get_session()
    if session is None:
        return None
    rgb = image.convert("RGB")
    scale = _INPUT / max(rgb.width, rgb.height)
    resized = rgb.resize((max(round(rgb.width * scale), 1), max(round(rgb.height * scale), 1)))
    canvas = np.full((_INPUT, _INPUT, 3), 114, np.uint8)
    canvas[: resized.height, : resized.width] = np.asarray(resized)
    blob = canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255
    output = np.asarray(session.run(None, {session.get_inputs()[0].name: blob})[0])[0].T  # (N, 4 + classes)
    classes = output[:, 4:].argmax(axis=1)
    scores = output[:, 4:].max(axis=1)
    result: dict[str, list[Box]] = {label: [] for label in LABELS}
    for index, label in enumerate(LABELS):
        keep = (classes == index) & (scores > _threshold)
        if not keep.any():
            continue
        cx, cy, w, h = (output[keep, k] / scale for k in range(4))
        boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
        boxes = np.clip(boxes, 0, [rgb.width, rgb.height, rgb.width, rgb.height])
        result[label] = [tuple(float(v) for v in boxes[i]) for i in _nms(boxes, scores[keep])]  # type: ignore[misc]
    return result
