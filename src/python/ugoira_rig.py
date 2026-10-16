"""
うごイラの E: 部位ごとに分けて、骨格とばねで動かす(描き直さない)。

D(変形)は、頭の位置から決めた範囲をまとめて動かすので、髪の横の服や背景まで動いた。ここでは:

1. 素材を分ける: 人物と背景(anime_seg)、髪(頭のてっぺんの色につながる所)、頭(首より上)、体
2. 骨格を取る: RTMPose(体の17点。Apache-2.0)で、目・鼻・肩の位置を取る(首を回す中心・頭の向きに使う)
3. 部位ごとに動かす: 頭は首を中心に回す(固い)。垂れた髪は、ばねでつないだ点の列(鎖)にして、風や頭の動きに
   遅れて付いてくるようにする(フォロースルー)。体は、頭と逆に少しだけ傾いて、遅れて戻る(柔らかさ)
4. 重ねる: 動いて見えるようになった所(髪や頭の裏)は、その部位を消して埋めた絵(cv2.inpaint)。動いていない所は元の絵

髪の表現は2通り: アニメ調(束ごとに大きく揺れ、2コマずつ止める)と、リアル寄り(細かい毛筋ごとに波が流れ、毎コマ動き、
少しぶれる)。
"""

from __future__ import annotations

import io
import math
import re
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_POSE_DIR = _PROJECT_ROOT / "data" / "models" / "rtmpose"
_POSE_FILE = "rtmpose-m_body7_256x192.onnx"
_POSE_URL = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
    "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip"
)
_POSE_INPUT = (192, 256)
_POSE_MEAN = np.array([123.675, 116.28, 103.53], np.float32)
_POSE_STD = np.array([58.395, 57.12, 57.375], np.float32)
# COCO の17点の名前(RTMPose の出力の並び)
KEYPOINTS = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)

# 動きの種類 → (表示名, 1周のミリ秒の既定)
RIGS: dict[str, tuple[str, int]] = {
    "hair": ("髪が風になびく", 2000),
    "tilt": ("首をかしげる", 2400),
}
STYLES = {"anime": "アニメ調", "real": "リアル寄り"}

_session: Any = None
_lock = threading.Lock()


# ---- 構図の型(プロンプトのタグから) ----


@dataclass(frozen=True)
class PoseTemplate:
    """構図ごとの骨の当て方。up は体の上向き(画像の座標。骨格が取れないときに使う)。"""

    name: str
    label: str
    up: tuple[float, float]
    # 髪が垂れて、風や頭の動きで揺れる(寝ている構図では、髪は寝具の上に広がって揺れない)
    hair_swing: bool
    # 顔の目印が片目だけになる(横顔)、顔が見えない(後ろ姿)
    one_eye: bool = False
    no_face: bool = False


# 先に当てはまったものを使う(寝ている・後ろ姿・横顔 を、立っている・座っている より先に見る)
POSE_TEMPLATES: tuple[tuple[re.Pattern[str], PoseTemplate], ...] = (
    (re.compile(r"\b(on back|lying on back|lying on bed|supine)\b"), PoseTemplate("on_back", "仰向け", (0.0, -1.0), False)),
    (re.compile(r"\b(on stomach|lying on stomach|prone)\b"), PoseTemplate("on_stomach", "うつ伏せ", (0.0, -1.0), False)),
    (re.compile(r"\b(lying|on side)\b"), PoseTemplate("lying", "寝そべり", (0.0, -1.0), False)),
    (re.compile(r"\b(from behind|back view)\b"), PoseTemplate("from_behind", "後ろ姿", (0.0, -1.0), True, no_face=True)),
    (re.compile(r"\b(from side|profile)\b"), PoseTemplate("profile", "横顔", (0.0, -1.0), True, one_eye=True)),
    (re.compile(r"\b(squatting|crouching)\b"), PoseTemplate("squatting", "しゃがみ", (0.0, -1.0), True)),
    (re.compile(r"\b(sitting|seiza|kneeling)\b"), PoseTemplate("sitting", "座り", (0.0, -1.0), True)),
)
STANDING = PoseTemplate("standing", "立ち・上半身", (0.0, -1.0), True)


def pose_template(prompt: str) -> PoseTemplate:
    """NovelAI のプロンプト(タグ)から構図の型を選ぶ。手がかりが無ければ立ち・上半身。"""
    lower = (prompt or "").lower()
    return next((t for pattern, t in POSE_TEMPLATES if pattern.search(lower)), STANDING)


class RigError(ValueError):
    pass


# ---- 骨格 ----


def _ensure_pose_model() -> Path:
    model = _POSE_DIR / _POSE_FILE
    if model.is_file():
        return model
    _POSE_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=600) as client:
        data = client.get(_POSE_URL).raise_for_status().content
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = next(n for n in zf.namelist() if n.endswith("end2end.onnx"))
        tmp = _POSE_DIR / f"{_POSE_FILE}.part"
        tmp.write_bytes(zf.read(name))
    tmp.replace(model)
    return model


def _pose_session() -> Any:
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.log_severity_level = 3
            _session = ort.InferenceSession(str(_ensure_pose_model()), options, providers=["CPUExecutionProvider"])
    return _session


@dataclass
class Skeleton:
    """体の17点(元の絵の画素座標)と、それぞれの確からしさ(0〜1 くらい)。"""

    points: np.ndarray  # (17, 2)
    scores: np.ndarray  # (17,)

    def get(self, name: str, threshold: float = 0.3) -> np.ndarray | None:
        index = KEYPOINTS.index(name)
        return self.points[index] if self.scores[index] >= threshold else None


def detect_pose(image: Image.Image, box: tuple[float, float, float, float]) -> Skeleton:
    """box(人物の範囲 x0, y0, x1, y1)の中の人物の骨格。人物の検出は使わず、人物の切り分けの範囲を渡す。"""
    rgb = np.asarray(image.convert("RGB"), np.float32)
    x0, y0, x1, y1 = box
    center = np.array([(x0 + x1) / 2, (y0 + y1) / 2], np.float32)
    w, h = (x1 - x0) * 1.25, (y1 - y0) * 1.25
    aspect = _POSE_INPUT[0] / _POSE_INPUT[1]
    w, h = (w, w / aspect) if w > h * aspect else (h * aspect, h)
    scale = _POSE_INPUT[0] / w
    matrix = np.array([[scale, 0, _POSE_INPUT[0] / 2 - center[0] * scale], [0, scale, _POSE_INPUT[1] / 2 - center[1] * scale]], np.float32)
    crop = cv2.warpAffine(rgb, matrix, _POSE_INPUT, flags=cv2.INTER_LINEAR)
    crop = (crop - _POSE_MEAN) / _POSE_STD
    simcc_x, simcc_y = _pose_session().run(None, {"input": crop.transpose(2, 0, 1)[None].astype(np.float32)})
    xs, ys = simcc_x[0].argmax(axis=1) / 2.0, simcc_y[0].argmax(axis=1) / 2.0
    scores = np.minimum(simcc_x[0].max(axis=1), simcc_y[0].max(axis=1))
    points = np.stack([(xs - matrix[0, 2]) / scale, (ys - matrix[1, 2]) / scale], axis=1)
    return Skeleton(points.astype(np.float32), scores.astype(np.float32))


# ---- 素材を分ける ----


@dataclass
class Parts:
    """部位ごとの重み(0〜1、元の絵と同じ大きさ)と、骨格から決めた位置。"""

    person: np.ndarray
    hair: np.ndarray  # 髪(顔は除く)
    head: np.ndarray  # 首より上(頭・顔・頭の上の髪)
    hanging: np.ndarray  # 肩より下へ垂れた髪
    pivot: tuple[float, float]  # 首を回す中心
    head_box: tuple[float, float, float, float]
    template: PoseTemplate = STANDING


def _smooth(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _lab(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2LAB).astype(np.float32)


def _feather(mask: np.ndarray, radius: float) -> np.ndarray:
    return cv2.GaussianBlur(mask.astype(np.float32), (0, 0), radius)


def _hair_by_points(
    image: Image.Image,
    head: tuple[float, float, float, float],
    inside: np.ndarray,
    skeleton: Skeleton | None,
    template: PoseTemplate = STANDING,
) -> np.ndarray:
    """
    髪の範囲を、点で指して切り出す(point_segment)。入れたい点は頭のてっぺんと左右、外したい点は顔と胸。
    大きさの違う候補のうち、外したい点を含まず、人物の6割より小さい、いちばん大きいもの。
    色で分けるやり方は、毛先の影の濃い髪や、線画とトーンが同じ灰色のモノクロで外れた(実機で確認)。
    """
    from . import point_segment

    x0, y0, x1, y1 = head
    w, h = x1 - x0, y1 - y0
    positives = [(x0 + 0.5 * w, y0 + 0.1 * h), (x0 + 0.22 * w, y0 + 0.3 * h), (x1 - 0.22 * w, y0 + 0.3 * h)]
    # 後ろ姿では頭はみな髪なので、顔の点は外さない
    negatives = [] if template.no_face else [((x0 + x1) / 2, y0 + 0.62 * h)]
    if skeleton is not None:
        nose = skeleton.get("nose")
        if nose is not None and negatives:
            negatives[0] = (float(nose[0]), float(nose[1]))
        shoulders = [p for p in (skeleton.get("left_shoulder"), skeleton.get("right_shoulder")) if p is not None]
        if len(shoulders) == 2:
            mid = (shoulders[0] + shoulders[1]) / 2
            span = shoulders[0] - shoulders[1]
            for k in (-0.2, 0.0, 0.2):
                point = mid + k * span + np.array([0, 0.45 * h])
                negatives.append((float(point[0]), float(point[1])))
    height, width = inside.shape
    negatives = [(min(max(x, 0), width - 1), min(max(y, 0), height - 1)) for x, y in negatives]
    candidates = point_segment.segment(point_segment.encode(image), positives, negatives)
    limit = 0.6 * inside.sum()
    valid = [m for m, _ in candidates if m.sum() < limit and not any(m[int(y), int(x)] for x, y in negatives)]
    if not valid:
        raise RigError("髪の範囲が分けられませんでした。")
    hair = max(valid, key=lambda m: int(m.sum())) & cv2.dilate(inside.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    # 点々と散った小さい所(服の上のまだら)は外す
    hair = cv2.morphologyEx(hair.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(hair)
    big = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 0.01 * hair.sum()]
    return np.isin(labels, big)


def split_parts(
    image: Image.Image,
    head: tuple[float, float, float, float],
    person: np.ndarray,
    skeleton: Skeleton | None,
    template: PoseTemplate = STANDING,
    hair: np.ndarray | None = None,
) -> Parts:
    """
    hair(髪の重み 0〜1)を渡すと、髪は切り出さずにそれを使う(半自動で直した範囲)。
    髪: 頭のてっぺんの色(Lab の中央値)に近く、そこからつながっている所。顔(目・鼻のまわりの楕円)は除く。
    頭: 首の付け根より上で、人物の所。首の付け根は、肩の中点から鼻へ 35% の所(骨格が無ければ頭の枠の下)。
    """
    x0, y0, x1, y1 = head
    w, h = x1 - x0, y1 - y0
    height, width = person.shape
    inside = person > 0.5

    hair = (hair > 0.5) if hair is not None else _hair_by_points(image, head, inside, skeleton, template)

    # 顔の楕円(目と鼻から)。骨格が無ければ頭の枠から
    eyes = [p for p in (skeleton.get("left_eye"), skeleton.get("right_eye")) if p is not None] if skeleton else []
    nose = skeleton.get("nose") if skeleton else None
    xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    if template.no_face:
        face = np.zeros_like(inside)
    elif template.one_eye and eyes and nose is not None:
        # 横顔: 片目と鼻から
        span = 1.6 * float(np.linalg.norm(eyes[0] - nose))
        fc = (eyes[0] + nose) / 2
        face = (((xs - fc[0]) / (span * 0.9)) ** 2 + ((ys - fc[1] - 0.3 * span) / (span * 1.3)) ** 2) < 1.0
    elif len(eyes) == 2 and nose is not None:
        span = float(np.linalg.norm(eyes[0] - eyes[1]))
        fc = (eyes[0] + eyes[1]) / 2 * 0.4 + nose * 0.6
        face = (((xs - fc[0]) / (span * 1.15)) ** 2 + ((ys - fc[1] - 0.25 * span) / (span * 1.45)) ** 2) < 1.0
    else:
        face = (((xs - (x0 + x1) / 2) / (0.36 * w)) ** 2 + ((ys - (y0 + 0.62 * h)) / (0.38 * h)) ** 2) < 1.0
    hair &= ~face
    hair_soft = _feather(cv2.morphologyEx(hair.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), 1.5)

    # 首の付け根と、首より上(頭)
    shoulders = [p for p in (skeleton.get("left_shoulder"), skeleton.get("right_shoulder")) if p is not None] if skeleton else []
    if template.no_face:
        # 後ろ姿: 鼻・目は当てにならないので、肩と頭の中心から
        nose = np.array([(x0 + x1) / 2, (y0 + y1) / 2], np.float32) if len(shoulders) == 2 else None
    if len(shoulders) == 2 and nose is not None:
        mid = (shoulders[0] + shoulders[1]) / 2
        pivot = mid + 0.35 * (nose - mid)
        axis = (nose - mid) / max(float(np.linalg.norm(nose - mid)), 1.0)
    else:
        axis = np.array(template.up, np.float32)
        pivot = np.array([(x0 + x1) / 2, (y0 + y1) / 2], np.float32) - axis * 0.6 * h
    # 首の付け根からの、頭の向きへの距離(上が正)。少し上から頭になる
    along = (xs - pivot[0]) * axis[0] + (ys - pivot[1]) * axis[1]
    across = np.abs((xs - pivot[0]) * -axis[1] + (ys - pivot[1]) * axis[0])
    head_zone = _smooth((along + 0.05 * h) / (0.18 * h)) * _smooth((1.15 * w - across) / (0.25 * w))
    # 頭のまわりだけ(体が傾いている絵で、首より上の向きの先に体の横の物まで入らないように)
    hcx, hcy = (x0 + x1) / 2, (y0 + y1) / 2
    near_head = np.sqrt(((xs - hcx) / (1.0 * w)) ** 2 + ((ys - hcy) / (0.95 * h)) ** 2)
    head_zone = head_zone * _smooth((1.25 - near_head) / 0.25)
    # 頭は、頭の中心につながった一続きの所だけ(薄い重みが体の横の物にかかると、その物が頭と一緒に回って筋が残った)
    solid = ((head_zone > 0.5) & inside).astype(np.uint8)
    count, labels = cv2.connectedComponents(solid)
    center = labels[int(min(max((y0 + y1) / 2, 0), height - 1)), int(min(max((x0 + x1) / 2, 0), width - 1))]
    head_mask = _feather(labels == center, 2.0) if center else np.zeros_like(head_zone)
    # 垂れた髪: 髪のうち、頭でない所
    hanging = hair_soft * (1.0 - head_zone)
    return Parts(
        person=person.astype(np.float32),
        hair=hair_soft.astype(np.float32),
        head=head_mask.astype(np.float32),
        hanging=hanging.astype(np.float32),
        pivot=(float(pivot[0]), float(pivot[1])),
        head_box=head,
        template=template,
    )


# ---- 半自動: タップした所のまわりを拡大して切り出す ----

# 拡大して切り出す区画の大きさ(元の絵の画素)と倍率
ZOOM_TILE = 256
ZOOM_SCALE = 4
_ZOOM_GRID = 64
# 元の絵がこれより小さいときは、AI 拡大(高精細)を自動で使う(線がにじんで、ふつうの拡大では切り出しが外れやすい)
DETAIL_AUTO_PIXELS = 512 * 768
_zoom_cache: dict[tuple[Any, ...], Any] = {}
_ZOOM_CACHE_MAX = 32


def zoom_segment(
    image: Image.Image, point: tuple[float, float], detail: bool = False, cache_key: Any = None
) -> np.ndarray:
    """
    point のまわりの区画を4倍に拡大して切り出し、元の大きさに縮めて元の絵の座標に戻した重み(0〜1)。
    縮めるので、ふちは「半分だけ」の中間の値になる(重ねたときに輪郭がギザギザにならない)。
    detail なら AI 拡大(upscaler。この PC で、絵の中身は変えない)、そうでなければふつうの拡大。
    同じ区画の特徴は覚えておく(同じ辺りを続けてタップしても待たない)。
    """
    from . import point_segment, upscaler

    width, height = image.size
    half = ZOOM_TILE // 2
    # 区画の位置は 64 画素ごとにそろえる(近い所を続けてタップしたとき、同じ区画の特徴を使い回せる)
    left = int(min(max(round((point[0] - half) / _ZOOM_GRID) * _ZOOM_GRID, 0), max(width - ZOOM_TILE, 0)))
    top = int(min(max(round((point[1] - half) / _ZOOM_GRID) * _ZOOM_GRID, 0), max(height - ZOOM_TILE, 0)))
    tile = image.crop((left, top, min(left + ZOOM_TILE, width), min(top + ZOOM_TILE, height)))
    key = (cache_key, left, top, detail) if cache_key is not None else None
    features = _zoom_cache.get(key) if key is not None else None
    if features is None:
        big = (
            upscaler.upscale(tile)
            if detail
            else tile.resize((tile.width * ZOOM_SCALE, tile.height * ZOOM_SCALE), Image.Resampling.BICUBIC)
        )
        features = point_segment.encode(big)
        if key is not None:
            if len(_zoom_cache) >= _ZOOM_CACHE_MAX:
                _zoom_cache.pop(next(iter(_zoom_cache)))
            _zoom_cache[key] = features
    scale = features.size[0] / tile.width
    local = ((point[0] - left) * scale, (point[1] - top) * scale)
    candidates = [m for m, _ in point_segment.segment(features, [local]) if m[min(int(local[1]), m.shape[0] - 1), min(int(local[0]), m.shape[1] - 1)]]
    if not candidates:
        return np.zeros((height, width), np.float32)
    # いちばん小さい候補(タップした1本・1つ)
    chosen = min(candidates, key=lambda m: int(m.sum()))
    soft = cv2.resize(chosen.astype(np.float32), (tile.width, tile.height), interpolation=cv2.INTER_AREA)
    out = np.zeros((height, width), np.float32)
    out[top : top + tile.height, left : left + tile.width] = soft
    return out


def edit_hair(hair: np.ndarray, region: np.ndarray, mode: str) -> np.ndarray:
    """髪の重みに、切り出した所を足す(add)か、引く(remove)。"""
    if mode == "add":
        return np.maximum(hair, region)
    if mode == "remove":
        return np.clip(hair - region, 0.0, 1.0)
    raise RigError("直し方は add か remove です。")


# ---- 髪の裏を NovelAI で描く(胴体だけを切り出して) ----

# 胴体を描くときに外すタグ(頭・顔・髪・表情・視線・風)。頭が絵に入っていると、髪の続きを描いてしまった(実機で確認)
_TORSO_DROP = re.compile(
    r"(wind|hair|bangs|\beyes?\b|face|smile|blush|mouth|looking|head|expression|\bears?\b|lips|eyelashes|tilt|portrait)", re.I
)
_TORSO_DROP_EXACT = {"1girl", "1boy", "solo", "upper body", "cowboy shot"}
_MONO_TAGS = {"monochrome", "greyscale", "grayscale", "screentone", "manga style"}
TORSO_TAGS = "head out of frame, close-up, torso"
TORSO_NEGATIVE = "hair, long hair, strands of hair, head, face, neck"
# 描いた胴体のうち、元が髪だった所に、髪の色がこれより多く残っていたら描き直す
HAIR_LEFT_LIMIT = 0.15


@dataclass
class TorsoRequest:
    """胴体だけを切り出して、髪の所を描かせる頼み方。"""

    top: int  # 切り出した上の端(元の絵の画素)
    hole: np.ndarray  # 切り出した所の中の、描かせる所(True)
    # 描いた絵を重ねる所(髪と頭を少し広げた所)。描かせる所は広く取る(手がかりの髪を残さない)が、重ねるのは髪の裏だけ
    # (広いまま重ねると、描き直された肩の輪郭が元の輪郭とずれて筋に見えた。実機で確認)
    merge: np.ndarray
    image: Image.Image  # 描かせる所を、まわりのならした色で塗った胴体(拡大済み)
    mask: Image.Image
    prompt: str
    negative: str


def torso_request(source: Image.Image, parts: Parts, prompt: str, negative: str) -> TorsoRequest | None:
    """
    首の付け根の少し下から下(頭が入らない所)を切り出し、髪と頭の所(広げたもの)を描かせる所にする。描かせる所は、
    まわりのならした色で塗っておく(LaMa のにじみは髪の手がかりになった)。描かせる所が無ければ None。
    """
    from .ugoira import fit_size

    image = np.asarray(source.convert("RGB"))
    height, width = image.shape[:2]
    x0, y0, x1, y1 = parts.head_box
    # 首の付け根の少し下から下。あごから下(首を入れる)にすると、髪を描きやすくなり、肩に筋が出た(実機で確認)
    top = int(min(height - 64, parts.pivot[1] + 0.05 * (y1 - y0)))
    hole_full = cv2.dilate((np.maximum(parts.hair, parts.head) > 0.08).astype(np.uint8), np.ones((45, 45), np.uint8)) > 0
    hole = hole_full[top:]
    if not hole.any():
        return None
    merge = (cv2.dilate((np.maximum(parts.hair, parts.head) > 0.08).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0)[top:]
    crop = image[top:]
    filled = crop.copy()
    blurred = cv2.blur(np.where(hole[..., None], 0, crop).astype(np.float32), (61, 61))
    weight = cv2.blur((~hole).astype(np.float32), (61, 61))[..., None]
    filled[hole] = (blurred / np.maximum(weight, 1e-3)).astype(np.uint8)[hole]
    ch, cw = crop.shape[:2]
    gw, gh = fit_size((cw * 2, ch * 2))
    big = cv2.resize(filled, (gw, gh), interpolation=cv2.INTER_CUBIC)
    big_hole = cv2.resize(hole.astype(np.uint8) * 255, (gw, gh), interpolation=cv2.INTER_NEAREST)
    tags = [t.strip() for t in (prompt or "").split(",") if t.strip()]
    kept = [t for t in tags if not _TORSO_DROP.search(t) and t.lower() not in _TORSO_DROP_EXACT]
    # 絵に色があるのにプロンプトがモノクロのとき(V5 はモノクロにならないことがある)は、モノクロのタグを外す
    if float(cv2.cvtColor(image, cv2.COLOR_RGB2HSV)[..., 1].mean()) > 40:
        kept = [t for t in kept if t.lower() not in _MONO_TAGS]
    return TorsoRequest(
        top=top,
        hole=hole,
        merge=merge,
        image=Image.fromarray(big),
        mask=Image.fromarray(big_hole).convert("RGB"),
        prompt=", ".join([TORSO_TAGS, *kept]),
        negative=", ".join(p for p in (negative, TORSO_NEGATIVE) if p),
    )


def hair_left(source: Image.Image, parts: Parts, req: TorsoRequest, drawn: Image.Image) -> float:
    """描いた胴体のうち、元が髪だった所に、髪の色が残っている割合(0〜1)。"""
    lab = _lab(source)
    hair = parts.hair > 0.5
    color = np.median(lab[hair], axis=0)
    spread = float(np.percentile(np.linalg.norm(lab[hair] - color, axis=1), 80))
    ch, cw = req.hole.shape
    small = drawn.convert("RGB").resize((cw, ch), Image.Resampling.LANCZOS)
    region = hair[req.top :] & req.hole
    if not region.any():
        return 0.0
    close = np.linalg.norm(_lab(small) - color, axis=2) < max(18.0, 1.5 * spread)
    return float(close[region].mean())


def merge_torso(plate: np.ndarray, req: TorsoRequest, drawn: Image.Image) -> np.ndarray:
    """描いた胴体を、裏を埋めた絵の、描かせた所にだけ重ねる(明るさ・色は元に合わせる)。"""
    from .ugoira import composite

    ch, cw = req.hole.shape
    small = drawn.convert("RGB").resize((cw, ch), Image.Resampling.LANCZOS)
    soft = Image.fromarray(cv2.GaussianBlur(req.merge.astype(np.uint8) * 255, (0, 0), 3))
    out = plate.copy()
    out[req.top :] = np.asarray(composite(Image.fromarray(plate[req.top :]), small, soft))
    return out


def base_plate(source: Image.Image, parts: Parts, kind: str) -> np.ndarray:
    """動く部位をまるごと消して、LaMa で埋めた絵(render の既定の裏)。"""
    moving = np.maximum(parts.hanging, parts.head if kind == "tilt" else 0)
    return _fill(np.asarray(source.convert("RGB")), moving)


# ---- ばね(髪の鎖・頭の動き) ----


def _follow(target: np.ndarray, dt: float, frequency: float, damping: float) -> np.ndarray:
    """target に、ばね(固有振動数 frequency Hz、減衰比 damping)で付いていく値の並び。"""
    omega = 2 * math.pi * frequency
    x, v = float(target[0]), 0.0
    out = np.empty_like(target)
    for i, goal in enumerate(target):
        a = omega * omega * (goal - x) - 2 * damping * omega * v
        v += a * dt
        x += v * dt
        out[i] = x
    return out


def _chain(root: np.ndarray, force: np.ndarray, dt: float, nodes: int, stiffness: float, damping: float) -> np.ndarray:
    """
    根元が root(時刻ごとの 2D のずれ)で動く、ばねでつないだ点の列。force(時刻×点×2)は風など。
    返り値は時刻×点×2 のずれ(根元は 0 番目)。
    """
    steps = len(root)
    pos = np.zeros((nodes, 2), np.float64)
    vel = np.zeros((nodes, 2), np.float64)
    out = np.empty((steps, nodes, 2), np.float64)
    for t in range(steps):
        pos[0] = root[t]
        acc = np.zeros_like(pos)
        # となりの点へのばね(根元側と毛先側)と、元の形へ戻る弱いばね
        acc[1:] += stiffness * (pos[:-1] - pos[1:])
        acc[1:-1] += stiffness * (pos[2:] - pos[1:-1])
        acc[1:] += 0.15 * stiffness * (root[t] - pos[1:])
        acc[1:] += force[t, 1:] - damping * vel[1:]
        vel[1:] += acc[1:] * dt
        pos[1:] += vel[1:] * dt
        out[t] = pos
    return out


# ---- 書き出し(重ねる) ----


def _remap(src: np.ndarray, map_x: np.ndarray, map_y: np.ndarray) -> np.ndarray:
    return cv2.remap(src, map_x.astype(np.float32), map_y.astype(np.float32), cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def _fill(image: np.ndarray, region: np.ndarray) -> np.ndarray:
    """region(重み)の所を消して、まわりから埋めた絵(LaMa。cv2.inpaint では服のしわがまだらになった)。"""
    from .inpaint_local import inpaint

    # 輪郭の線(人物の切り分けの外側に少し残る)まで消さないと、元の輪郭が背景に薄く残った
    hole = cv2.dilate((region > 0.08).astype(np.uint8), np.ones((25, 25), np.uint8)) > 0
    return inpaint(image, hole)


def _hair_field(parts: Parts, offsets: np.ndarray, chain_x: np.ndarray, levels: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray]:
    """鎖ごとの点のずれ(鎖×点×2)を、髪の画素ごとのずれ(dx, dy)にする。横は鎖の位置からの近さ、縦は点のあいだを補う。"""
    height, width = parts.hair.shape
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    weights = np.exp(-((xs[None, :] - chain_x[:, None]) ** 2) / (2 * sigma * sigma))  # 鎖×幅
    weights /= weights.sum(axis=0, keepdims=True) + 1e-6
    dx = np.zeros((height, width), np.float32)
    dy = np.zeros((height, width), np.float32)
    for c in range(len(chain_x)):
        col_x = np.interp(ys, levels, offsets[c, :, 0])
        col_y = np.interp(ys, levels, offsets[c, :, 1])
        dx += col_x[:, None] * weights[c][None, :]
        dy += col_y[:, None] * weights[c][None, :]
    return dx, dy


def render(
    source: Image.Image,
    parts: Parts,
    kind: str,
    style: str = "anime",
    period_ms: int | None = None,
    amount: float = 1.0,
    plate: np.ndarray | None = None,
) -> list[tuple[Image.Image, int]]:
    """1周分のコマと表示時間(ミリ秒)。最初のコマは元の絵と同じ。plate は動いて見える所の裏(無ければ LaMa で埋める)。"""
    if kind not in RIGS:
        raise RigError(f"動きは {', '.join(RIGS)} のどれかです。")
    if style not in STYLES:
        raise RigError(f"表現は {', '.join(STYLES)} のどれかです。")
    if kind == "hair" and not parts.template.hair_swing:
        raise RigError(f"{parts.template.label}の構図では、髪は垂れていないので揺らせません。")
    anime = style == "anime"
    period = (period_ms or RIGS[kind][1]) / 1000.0
    # アニメ調は 12 枚(2コマずつ止めて見える)、リアル寄りは 24 枚で毎コマ動かす
    frames = 12 if anime else 24
    image = np.asarray(source.convert("RGB"))
    height, width = parts.person.shape
    x0, y0, x1, y1 = parts.head_box
    hw = x1 - x0
    xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))

    # 時間: 何周か回してから最後の1周を使う(ばねの動きが周期に落ち着く)
    dt = 1 / 120
    laps = 5
    steps = int(round(period / dt))
    t = np.arange(steps * laps) * dt
    phase = 2 * math.pi * t / period

    # 頭の傾き(度)
    if kind == "tilt":
        if anime:
            # かしげて止め、戻して止める(ばねで少し行き過ぎてから止まる)
            target = np.where((phase % (2 * math.pi)) < math.pi, 1.0, 0.0) * 8.0 * amount
            angle = _follow(target, dt, 2.2, 0.45)
        else:
            angle = _follow((1 - np.cos(phase)) / 2 * 7.0 * amount, dt, 1.6, 0.9)
    else:
        angle = np.zeros_like(t)

    # 髪の鎖: 垂れた髪の横の広がりに鎖を並べる(アニメ調は束ごとに3本、リアル寄りは細かく7本)
    hanging = parts.hanging
    cols = np.nonzero(hanging.max(axis=0) > 0.3)[0]
    rows = np.nonzero(hanging.max(axis=1) > 0.3)[0]
    has_hair = len(cols) > 0 and len(rows) > 0
    chain_count = 3 if anime else 7
    nodes = 8
    if has_hair:
        chain_x = np.linspace(cols.min(), cols.max(), chain_count + 2)[1:-1].astype(np.float32)
        top = float(min(rows.min(), parts.pivot[1]))
        levels = np.linspace(top, float(rows.max()), nodes).astype(np.float32)
        offsets = np.zeros((chain_count, steps, nodes, 2))
        cx, cy = parts.pivot
        for c in range(chain_count):
            # 根元は頭と一緒に動く(首を中心に回した位置のずれ)
            rad = np.radians(angle)
            rx, ry = chain_x[c] - cx, levels[0] - cy
            root = np.stack([cx + np.cos(rad) * rx - np.sin(rad) * ry - chain_x[c], cy + np.sin(rad) * rx + np.cos(rad) * ry - levels[0]], axis=1)
            force = np.zeros((len(t), nodes, 2))
            if kind == "hair":
                gust = 0.55 + 0.45 * np.sin(phase + c * 0.9) if anime else 0.6 + 0.25 * np.sin(phase + c * 1.7) + 0.15 * np.sin(3 * phase + c * 2.3)
                exposure = np.linspace(0, 1, nodes) ** 1.3
                force[:, :, 0] = gust[:, None] * exposure[None, :]
                # 吹かれた毛先は少し持ち上がる
                force[:, :, 1] = -0.35 * gust[:, None] * exposure[None, :]
            stiffness, damping = (55.0, 5.0) if anime else (30.0, 2.5)
            sim = _chain(root, force, dt, nodes, stiffness, damping)[-steps:]
            if kind == "tilt":
                # 首をかしげるとき、垂れた髪は根元ほど頭と一緒に動き、毛先はあまり動かない(下の物を引きずらない)
                offsets[c] = sim * np.linspace(1.0, 0.35, nodes)[None, :, None]
            else:
                offsets[c] = sim
        if kind == "hair":
            # 毛先の揺れの幅を、頭の幅に対する割合でそろえる(アニメ調は大きめ)
            tip = np.abs(offsets[:, :, -1, 0])
            swing = float(tip.max() - tip.min()) or 1.0
            offsets *= (0.10 if anime else 0.07) * hw * amount / swing
            offsets -= offsets[:, :1]  # 最初のコマを元の形に
    angle = angle[-steps:] - angle[-steps]

    moving = np.maximum(parts.hanging, parts.head if kind == "tilt" else 0)
    head_w = parts.head[..., None]
    # 動かない体: 動く所を少し広げて除く(輪郭の線が体として残ると、元の輪郭が薄く残った)
    grown = _feather(cv2.dilate((moving > 0.08).astype(np.uint8), np.ones((15, 15), np.uint8)), 3.0)
    # 広げて除くのは、人物のふち(背景との境)だけ。体の中(髪の下の服など)は元の絵を残す
    edge = _smooth((0.95 - parts.person) / 0.25)
    # 動く所のふち(ぼかした所)も体に残さない(輪郭の線が半分残った)
    solid_moving = _feather(cv2.dilate((moving > 0.08).astype(np.uint8), np.ones((5, 5), np.uint8)), 1.0)
    body_weight = np.clip(parts.person - np.maximum(solid_moving, grown * edge), 0, 1)[..., None]
    blur = 1 if anime else 3
    hair_sigma = (0.45 if anime else 0.22) * hw

    def layers(k: int) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """時刻 k の、動く部位ごとの (元の絵のどこから取るか x, y, 部位の重み)。下から順(垂れた髪 → 頭)。"""
        out = []
        if has_hair:
            dx, dy = _hair_field(parts, offsets[:, k], chain_x, levels, sigma=hair_sigma)
            out.append((xs - dx, ys - dy, parts.hanging))
        if kind == "tilt":
            rad = math.radians(float(angle[k]))
            cx, cy = parts.pivot
            c, s = math.cos(rad), math.sin(rad)
            out.append((cx + c * (xs - cx) + s * (ys - cy), cy - s * (xs - cx) + c * (ys - cy), parts.head))
        return out

    samples_at = [
        [(int(round(f * steps / frames)) + round((b - blur // 2) * steps / frames / 3)) % steps for b in range(blur)]
        for f in range(frames)
    ]
    # 埋めた絵は、動く部位をまるごと消して作る(見えるようになる細い所だけを消すと、まわりに残った髪から
    # また髪が描かれた。NovelAI のインペイントでも、髪の続きを描いてしまった。実機で確認)
    plate = _fill(image, moving) if plate is None else plate
    result = []
    for ks in samples_at:
        samples = []
        for k in ks:
            canvas = image * body_weight + plate.astype(np.float32) * (1 - body_weight)
            for mx, my, weight in layers(k):
                alpha = _remap(weight, mx, my)[..., None]
                canvas = _remap(image, mx, my).astype(np.float32) * alpha + canvas * (1 - alpha)
            if kind != "tilt":
                canvas = image * head_w + canvas * (1 - head_w)
            samples.append(canvas)
        result.append(np.mean(samples, axis=0))
    # 動いていない所は元の絵に寄せる(最初のコマを元の絵と同じに)
    out = []
    base = result[0]
    for frame in result:
        changed = np.abs(frame - base).max(axis=2)
        alpha = _feather((changed > 1.5).astype(np.float32), 2.0)[..., None]
        alpha = np.clip(alpha * 3, 0, 1)
        out.append(np.clip(image * (1 - alpha) + frame * alpha, 0, 255).astype(np.uint8))
    delay = max(20, round(period * 1000 / frames))
    return [(Image.fromarray(frame), delay) for frame in out]
