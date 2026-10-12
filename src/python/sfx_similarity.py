"""
描き文字(手描きの効果音)どうしの、形の似かたを比べる。読みを入れた描き文字から、まだ読みの無い描き文字の
読みの候補を出すのに使う(routes/manga_import.py の sfx-suggestions)。

文字として読むのではなく、絵として比べる。使うのは DINOv2 small(画像の特徴を取り出すモデル。Apache-2.0。
ONNX 版は onnx-community/dinov2-small、約 88MB)。初回に data/models へダウンロードする。

実機で測った結果(docs/trials/2026-10-12_sfx-detection.md):
- 同じ描き手の描き文字どうしなら、いちばん似たものの読みが合うのは 42%、上位5つに正解が入るのは 56%
- 描き手が違うと、ほとんど合わない(1〜9%)
- 合っているときと外れているときで、似かたの値に差が無い(0.93 と 0.92)。値で「分からない」とは言えない

なので、候補は同じ取り込み(同じ作品)の中からだけ出し、自動では決めずに、選んでもらうために見せる。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_NAME = "dinov2-small"
_MODEL_DIR = _PROJECT_ROOT / "data" / "models" / MODEL_NAME
_MODEL_URL = "https://huggingface.co/onnx-community/dinov2-small/resolve/main/onnx/model.onnx"

# モデルの前処理(preprocessor_config.json): 256 に縮めて中央の 224 を切り出し、ImageNet の平均・分散でそろえる
_RESIZE = 256
_CROP = 224
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
# 正方形にするときに足す余白(長い辺に対する割合)
_PAD = 1.15
# 候補として出す読みの数
MAX_SUGGESTIONS = 5

_session: Any = None
_lock = threading.Lock()


def _ensure_model() -> Path:
    model = _MODEL_DIR / "model.onnx"
    if model.is_file():
        return model
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _MODEL_DIR / "model.onnx.part"
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


def _prepare(image: Image.Image) -> np.ndarray:
    """白い正方形の中央に置いて、モデルの入力の形にする。"""
    rgb = image.convert("RGB")
    side = max(round(max(rgb.size) * _PAD), 1)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(rgb, ((side - rgb.width) // 2, (side - rgb.height) // 2))
    offset = (_RESIZE - _CROP) // 2
    canvas = canvas.resize((_RESIZE, _RESIZE), Image.BICUBIC).crop((offset, offset, offset + _CROP, offset + _CROP))
    return ((np.asarray(canvas, np.float32) / 255 - _MEAN) / _STD).transpose(2, 0, 1)


def features(images: list[Image.Image]) -> np.ndarray:
    """絵ごとの特徴(長さ1にそろえたベクトル)。行が絵に対応する。"""
    if not images:
        return np.zeros((0, 0), np.float32)
    session = _get_session()
    name = session.get_inputs()[0].name
    rows = []
    for start in range(0, len(images), 16):
        batch = np.stack([_prepare(image) for image in images[start : start + 16]]).astype(np.float32)
        with _lock:
            hidden = session.run(None, {name: batch})[0]
        # 先頭のトークン(絵全体の要約)を使う
        rows.append(hidden[:, 0])
    out = np.concatenate(rows)
    return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)


def rank_readings(query: np.ndarray, known: np.ndarray, readings: list[str], limit: int = MAX_SUGGESTIONS) -> list[str]:
    """query に形の似ている順に、読みを重ならないように並べる(known の行と readings が対応する)。"""
    if not len(readings):
        return []
    order = np.argsort(-(known @ query))
    return list(dict.fromkeys(readings[int(i)] for i in order))[:limit]
