"""
素体の正面・横・後ろの絵から、3D の形(GLB)を作る(docs/model_3d_plan_jp.md のステップ 3)。形はリモートの GPU の
Hunyuan3D-2mv(model3d_remote)で作り、キャラ別データセットの `model_3d(_r18)/<素体の id>/` に置く。

素体の部位の絵を、Hunyuan3D-2mv の向きの名前に当てて送る。横の絵は左を向いた真横として送るので、右を向いて描かれた
絵は骨格の点(鼻と耳)で見分けて左右を反転する。年齢・裸の判定は呼び出し側(routes/lora_dataset.py)で行う。
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import model3d_remote

# Hunyuan3D-2mv の向き → 素体の部位(body_base.PARTS のキー)
VIEW_PARTS = {"front": "front", "left": "side", "back": "back"}
MODEL = "tencent/Hunyuan3D-2mv (shape only)"
FILES = {"model.glb": "形(GLB・テクスチャなし)", "meta.json": "作ったときの記録"}


class Model3DInputError(ValueError):
    """入力の絵が足りない・使えない。"""


def source_paths(folder: Path, base_id: int) -> dict[str, Path]:
    """素体の絵のフォルダから、向きごとの絵のパス(描いていなくても返す)。"""
    return {view: folder / f"{base_id}_{part}.png" for view, part in VIEW_PARTS.items()}


def facing(points: dict[str, np.ndarray]) -> str | None:
    """横向きの絵の顔の向き("left"・"right")。鼻が耳(取れなければ肩)より左にあれば左向き。分からなければ None。"""
    if "nose" not in points:
        return None
    for names in (("left_ear", "right_ear"), ("left_shoulder", "right_shoulder")):
        found = [points[n][0] for n in names if n in points]
        if found:
            dx = float(points["nose"][0]) - float(np.mean(found))
            if abs(dx) < 2:
                return None
            return "left" if dx < 0 else "right"
    return None


def _side_facing(image: Image.Image) -> str | None:
    from .body_rig import RigError, detect

    try:
        _, points = detect(image.convert("RGB"))
    except RigError:
        return None
    return facing(points)


def generate(folder: Path, base_id: int, out_dir: Path, *, seed: int = 12345, on_status=None) -> dict:
    """
    素体の正面(必須)・横・後ろの絵から形を作り、out_dir に model.glb と meta.json を置く。meta(使った向き・
    横の絵を反転したか・かかった秒数など)を返す。
    """
    sources = {view: path for view, path in source_paths(folder, base_id).items() if path.is_file()}
    if "front" not in sources:
        raise Model3DInputError("素体の「正面」の絵をまだ描いていません。先に描いてください。")
    started = time.time()
    flipped = False
    with tempfile.TemporaryDirectory(prefix="model3d_") as tmp:
        views = dict(sources)
        if "left" in views:
            side = Image.open(views["left"])
            if _side_facing(side) == "right":
                views["left"] = Path(tmp) / "left.png"
                side.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(views["left"], "PNG")
                flipped = True
        out_dir.mkdir(parents=True, exist_ok=True)
        model3d_remote.generate(views, out_dir / "model.glb", seed=seed, on_status=on_status)
    meta = {
        "model": MODEL, "seed": seed, "views": sorted(sources), "side_flipped": flipped,
        "seconds": round(time.time() - started), "created": time.time(),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta
