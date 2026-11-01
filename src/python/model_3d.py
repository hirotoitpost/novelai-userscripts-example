"""
素体の正面・横・後ろの絵から、3D の形(GLB)を作る(docs/model_3d_plan_jp.md のステップ 3)。形はリモートの GPU の
Hunyuan3D-2mv(model3d_remote)で作り、キャラ別データセットの `model_3d(_r18)/<素体の id>/` に置く。
できた形は手元の Blender で整え(scripts/blender/clean_model.py: 破片を消す・面を減らす・身長と位置をそろえる)、
確かめ用の静止画と1周の GIF を描き出す(ステップ 4)。整えた形の正面・横・後ろの影絵を、元の素体の絵の影絵と
重ねて比べる(ステップ 5。check)。

素体の部位の絵を、Hunyuan3D-2mv の向きの名前に当てて送る。横の絵は左を向いた真横として送るので、右を向いて描かれた
絵は骨格の点(鼻と耳)で見分けて左右を反転する。年齢・裸の判定は呼び出し側(routes/lora_dataset.py)で行う。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import model3d_remote

# Hunyuan3D-2mv の向き → 素体の部位(body_base.PARTS のキー)
VIEW_PARTS = {"front": "front", "left": "side", "back": "back"}
MODEL = "tencent/Hunyuan3D-2mv (shape only)"
FILES = {
    "model.glb": "形(GLB・テクスチャなし・生のもの)",
    "model_clean.glb": "整えた形(GLB・破片を消し面を減らし、身長をそろえたもの)",
    "preview.gif": "1周の確かめ",
    "front.png": "正面",
    "side.png": "横(左向き)",
    "back.png": "後ろ",
    "meta.json": "作ったときの記録",
}
_CLEAN_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "blender" / "clean_model.py"
# 整えた形の面の数の上限(計画の 5〜10万面)
MAX_FACES = 80_000
# 身長(m): 素体の性別 → 素体の身長の設定("" ふつう・short 低め・tall 高め)
HEIGHTS = {
    "female": {"": 1.60, "short": 1.50, "tall": 1.72},
    "male": {"": 1.72, "short": 1.62, "tall": 1.83},
}


class Model3DInputError(ValueError):
    """入力の絵が足りない・使えない。"""


class Model3DCleanError(RuntimeError):
    """Blender で形を整えられなかった。"""


def height_of(base: dict) -> float:
    """素体の性別と身長の設定から、身長(m)。"""
    heights = HEIGHTS.get(base.get("sex") or "female", HEIGHTS["female"])
    return heights.get(base.get("height") or "", heights[""])


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
        # 前に作った形から整えたもの・静止画は、新しい形と合わなくなるので消す
        for name in FILES:
            (out_dir / name).unlink(missing_ok=True)
        model3d_remote.generate(views, out_dir / "model.glb", seed=seed, on_status=on_status)
    meta = {
        "model": MODEL, "seed": seed, "views": sorted(sources), "side_flipped": flipped,
        "seconds": round(time.time() - started), "created": time.time(),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def clean(blender: str, out_dir: Path, height: float, *, max_faces: int = MAX_FACES, timeout: float = 600) -> dict:
    """
    out_dir/model.glb を Blender で整えて model_clean.glb にし、正面・横・後ろの静止画と preview.gif を置く。
    結果(面の数・消した破片の数・身長)を meta.json の clean に足して返す。
    """
    from .body_rig import frames_to_gif

    result = subprocess.run(
        [blender, "--background", "--factory-startup", "--python", str(_CLEAN_SCRIPT), "--",
         str(out_dir), str(height), str(max_faces)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    report_path = out_dir / "clean.json"
    if result.returncode != 0 or not report_path.is_file() or not (out_dir / "model_clean.glb").is_file():
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-15:])
        raise Model3DCleanError(f"Blender で形を整えられませんでした(終了コード {result.returncode}):\n{tail}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink()
    turntable = out_dir / "turntable"
    frames_to_gif(sorted(turntable.glob("*.png")), out_dir / "preview.gif")
    shutil.rmtree(turntable)
    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    meta["clean"] = report
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return report


# 影絵を比べるときの高さ(画素)と、左右にずらして探す幅(高さに対する割合)
_CHECK_HEIGHT = 512
_CHECK_SHIFT = 0.12
# 静止画の名前 → 素体の向き
_STILLS = {"front.png": "front", "side.png": "left", "back.png": "back"}
# 向きごとの重なりがこれより低ければ、合わないとみなす。2026-11 の実機: 正しい素体で 0.76〜0.79、別の素体の絵とでも
# 0.6〜0.8(立った人の影絵は似る)、崩れた絵(2人並び・正面向きの横の絵)で 0.29〜0.41。別人までは見分けられず、
# 崩れた形・合わない入力を拾うためのもの
MIN_VIEW_IOU = 0.65
VIEW_LABELS = {"front": "正面", "left": "横", "back": "後ろ"}
# 作り直すときの、シードのずらし幅
_SEED_STEP = 7919


def _fit(mask: np.ndarray) -> np.ndarray:
    """影絵を上下の端で切り、高さを _CHECK_HEIGHT にそろえる。"""
    ys, xs = np.nonzero(mask)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    width = max(1, round(crop.shape[1] * _CHECK_HEIGHT / crop.shape[0]))
    return np.asarray(Image.fromarray(crop.astype(np.uint8) * 255).resize((width, _CHECK_HEIGHT))) > 127


def silhouette_iou(a: np.ndarray, b: np.ndarray) -> float:
    """2つの影絵(True が人)の重なり(IoU)。高さをそろえ、左右にずらしていちばん重なる所で測る。"""
    if not a.any() or not b.any():
        return 0.0
    a, b = _fit(a), _fit(b)
    shift = round(_CHECK_HEIGHT * _CHECK_SHIFT)
    width = max(a.shape[1], b.shape[1]) + 2 * shift
    base = np.zeros((_CHECK_HEIGHT, width), bool)
    x0 = (width - a.shape[1]) // 2
    base[:, x0:x0 + a.shape[1]] = a
    best = 0.0
    for dx in range(-shift, shift + 1, 2):
        other = np.zeros_like(base)
        x1 = (width - b.shape[1]) // 2 + dx
        other[:, x1:x1 + b.shape[1]] = b
        best = max(best, float((base & other).sum() / (base | other).sum()))
    return best


def check(folder: Path, base_id: int, out_dir: Path, *, side_flipped: bool = False) -> dict:
    """
    整えた形の静止画(clean が描いた front・side・back。背景は透明)の影絵を、素体の同じ向きの絵の影絵(anime_seg で
    人を切り出す)と比べる。向きごとの重なり(IoU)と、その平均(score)を返す。描いていない向きは比べない。
    """
    from .anime_seg import person_mask

    sources = source_paths(folder, base_id)
    views: dict[str, float] = {}
    for still, view in _STILLS.items():
        render = out_dir / still
        if not sources[view].is_file() or not render.is_file():
            continue
        image = Image.open(sources[view]).convert("RGB")
        if view == "left" and side_flipped:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        source_mask = person_mask(image) > 0.5
        render_mask = np.asarray(Image.open(render).convert("RGBA"))[..., 3] > 127
        views[view] = round(silhouette_iou(source_mask, render_mask), 3)
    score = round(float(np.mean(list(views.values()))), 3) if views else 0.0
    reasons = [
        f"{VIEW_LABELS[view]}の絵と形の影絵が合わない(重なり {iou:.2f})" for view, iou in views.items() if iou < MIN_VIEW_IOU
    ]
    return {"views": views, "score": score, "passed": not reasons, "reasons": reasons}


def _record_check(out_dir: Path, result: dict) -> None:
    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    meta["check"] = result
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def clean_and_check(blender: str, folder: Path, base_id: int, out_dir: Path, height: float) -> dict:
    """整えて(clean)、影絵を比べ(check)、結果を meta.json に残す。チェックの結果を返す。"""
    clean(blender, out_dir, height)
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    result = check(folder, base_id, out_dir, side_flipped=bool(meta.get("side_flipped")))
    _record_check(out_dir, result)
    return result


def make(folder: Path, base_id: int, out_dir: Path, *, blender: str, height: float, seed: int = 12345,
         attempts: int = 1, on_status=None) -> dict:
    """
    形を作り(generate)、整え、影絵を比べる。合わなければシードを変えて作り直す(最初の1回も含めて attempts 回まで)。
    いちばん重なりのよい回の形を out_dir に残し、meta.json の check に結果(作った回数も)を書く。
    """
    best: tuple[float, Path] | None = None
    tries = out_dir / "_tries"
    tries.mkdir(parents=True, exist_ok=True)
    try:
        for attempt in range(max(1, attempts)):
            work = tries / str(attempt)
            if on_status is not None:
                on_status("queued", 0)
            generate(folder, base_id, work, seed=seed + attempt * _SEED_STEP, on_status=on_status)
            if on_status is not None:
                on_status("cleaning", 0)
            result = clean_and_check(blender, folder, base_id, work, height)
            if best is None or result["score"] > best[0]:
                best = (result["score"], work)
            if result["passed"]:
                break
        _, chosen = best
        meta = json.loads((chosen / "meta.json").read_text(encoding="utf-8"))
        meta["check"]["attempts"] = attempt + 1
        (chosen / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        for name in FILES:
            (out_dir / name).unlink(missing_ok=True)
            if (chosen / name).is_file():
                shutil.move(chosen / name, out_dir / name)
        return meta
    finally:
        shutil.rmtree(tries, ignore_errors=True)
