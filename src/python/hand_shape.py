"""
素体の手の絵(hand_palm・hand_back)から、手の形の割合を測る。3D モデルの手を付け替えるとき(model_3d.rig)に使う。
Hunyuan3D-2mv は指を作れず、手が指のくっついたひれの形になるので(2026-11 実機)、手首から先を人形の手に付け替え、
その形をキャラの絵に合わせる。

手の21点(RTMPose の hand5。OpenMMLab、Apache 2.0)を取り、手の長さ(手首から中指の先)に対する手のひらの幅・中指の長さと、
中指に対するほかの指の長さを出す。手が斜めに縮んで写った絵では割合が崩れるので、人の手としてありえる範囲に収め、
曲がっている・確からしさの低い指は標準の値にする。
"""

from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / "rtmpose"
_MODEL_FILE = "rtmpose-m_hand5_256x256.onnx"
_MODEL_URL = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
    "rtmpose-m_simcc-hand5_pt-aic-coco_210e-256x256-74fb594_20230320.zip"
)
_INPUT = (256, 256)
_MEAN = np.array([123.675, 116.28, 103.53], np.float32)
_STD = np.array([58.395, 57.12, 57.375], np.float32)
# 21点の並び: 0 手首、親指 1〜4、人差し指 5〜8、中指 9〜12、薬指 13〜16、小指 17〜20(付け根から先へ)
FINGERS = {"thumb": (1, 2, 3, 4), "index": (5, 6, 7, 8), "middle": (9, 10, 11, 12), "ring": (13, 14, 15, 16), "little": (17, 18, 19, 20)}
# 割合の標準と、ありえる範囲(人の手の計測のおおよそ)。width・middle は手の長さに対する割合、ほかは中指に対する割合
DEFAULTS = {"width": 0.44, "middle": 0.43, "index": 0.92, "ring": 0.95, "little": 0.76, "thumb": 0.75}
RANGES = {
    "width": (0.38, 0.52), "middle": (0.38, 0.50), "index": (0.85, 1.0), "ring": (0.88, 1.0),
    "little": (0.65, 0.85), "thumb": (0.6, 0.9),
}
_MIN_SCORE = 0.5
# 指がまっすぐ伸びているとみなす割合(付け根から先までの距離 / 節の長さの和)
_STRAIGHT = 0.92

_session: Any = None
_lock = threading.Lock()


def _ensure_model() -> Path:
    model = _MODEL_DIR / _MODEL_FILE
    if model.is_file():
        return model
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=600) as client:
        data = client.get(_MODEL_URL).raise_for_status().content
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = next(n for n in zf.namelist() if n.endswith("end2end.onnx"))
        tmp = _MODEL_DIR / f"{_MODEL_FILE}.part"
        tmp.write_bytes(zf.read(name))
    tmp.replace(model)
    return model


def _hand_session() -> Any:
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.log_severity_level = 3
            _session = ort.InferenceSession(str(_ensure_model()), options, providers=["CPUExecutionProvider"])
    return _session


def detect_hand(image: Image.Image, box: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    """box(手の範囲)の中の手の21点(元の絵の画素)と、それぞれの確からしさ。"""
    rgb = np.asarray(image.convert("RGB"), np.float32)
    x0, y0, x1, y1 = box
    center = np.array([(x0 + x1) / 2, (y0 + y1) / 2], np.float32)
    size = max(x1 - x0, y1 - y0) * 1.25
    scale = _INPUT[0] / size
    matrix = np.array([[scale, 0, _INPUT[0] / 2 - center[0] * scale], [0, scale, _INPUT[1] / 2 - center[1] * scale]], np.float32)
    crop = (cv2.warpAffine(rgb, matrix, _INPUT, flags=cv2.INTER_LINEAR) - _MEAN) / _STD
    simcc_x, simcc_y = _hand_session().run(None, {"input": crop.transpose(2, 0, 1)[None].astype(np.float32)})
    xs, ys = simcc_x[0].argmax(axis=1) / 2.0, simcc_y[0].argmax(axis=1) / 2.0
    scores = np.minimum(simcc_x[0].max(axis=1), simcc_y[0].max(axis=1))
    points = np.stack([(xs - matrix[0, 2]) / scale, (ys - matrix[1, 2]) / scale], axis=1)
    return points.astype(np.float32), scores.astype(np.float32)


def _skin_box(image: Image.Image) -> tuple[float, float, float, float] | None:
    """肌の色のいちばん大きなかたまりの範囲(手の寄りの絵では、ほぼ手)。"""
    ycrcb = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2YCrCb)
    skin = cv2.inRange(ycrcb, (60, 135, 85), (255, 180, 135))
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(skin)
    if count <= 1:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[i, :4]
    return float(x), float(y), float(x + w), float(y + h)


def _ratios(points: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    """21点から割合(ありえる範囲に収める)。取れない・曲がった指は標準の値。"""
    def length(chain: tuple[int, ...]) -> float:
        return float(sum(np.linalg.norm(points[a] - points[b]) for a, b in zip(chain, chain[1:])))

    def straight(chain: tuple[int, ...]) -> bool:
        return all(scores[i] >= _MIN_SCORE for i in chain) and (
            float(np.linalg.norm(points[chain[-1]] - points[chain[0]])) >= _STRAIGHT * length(chain)
        )

    result = dict(DEFAULTS)
    middle = FINGERS["middle"]
    if scores[0] < _MIN_SCORE or not straight(middle):
        return result
    hand = float(np.linalg.norm(points[middle[-1]] - points[0]))
    middle_len = length(middle)
    result["middle"] = middle_len / hand
    if scores[5] >= _MIN_SCORE and scores[17] >= _MIN_SCORE:
        # 付け根の点(人差し指〜小指)の間は、手のひらの幅のおよそ 8 割
        result["width"] = float(np.linalg.norm(points[5] - points[17])) / 0.8 / hand
    for name, chain in FINGERS.items():
        if name != "middle" and straight(chain):
            result[name] = length(chain) / middle_len
    return {k: round(min(max(v, RANGES[k][0]), RANGES[k][1]), 3) for k, v in result.items()}


def measure(path: Path) -> dict[str, Any] | None:
    """手の寄りの絵から割合を測る。手が見つからなければ None。"""
    image = Image.open(path).convert("RGB")
    boxes = [box for box in (_skin_box(image), (0.0, 0.0, float(image.width), float(image.height))) if box is not None]
    best: tuple[float, np.ndarray, np.ndarray] | None = None
    for box in boxes:
        points, scores = detect_hand(image, box)
        mean = float(scores.mean())
        if best is None or mean > best[0]:
            best = (mean, points, scores)
    if best is None or best[0] < _MIN_SCORE:
        return None
    return {"ratios": _ratios(best[1], best[2]), "score": round(best[0], 3), "source": path.name}
