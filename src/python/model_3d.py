"""
素体の正面・横・後ろの絵から、3D の形(GLB)を作る(docs/model_3d_plan_jp.md のステップ 3)。形はリモートの GPU の
Hunyuan3D-2mv(model3d_remote)で作り、キャラ別データセットの `model_3d(_r18)/<素体の id>/` に置く。
できた形は手元の Blender で整え(scripts/blender/clean_model.py: 破片を消す・面を減らす・身長と位置をそろえる)、
確かめ用の静止画と1周の GIF を描き出す(ステップ 4)。整えた形の正面・横・後ろの影絵を、元の素体の絵の影絵と
重ねて比べる(ステップ 5。check)。正面の静止画の骨格の点から人型の骨を入れ、2D モデルと同じ動きを付ける(ステップ 6。rig)。骨にポーズを当て、
カメラの向きを選んで漫画のコマの参考の絵を描き出す(ステップ 7。render_pose)。

素体の部位の絵を、Hunyuan3D-2mv の向きの名前に当てて送る。横の絵は左を向いた真横として送るので、右を向いて描かれた
絵は骨格の点(鼻と耳)で見分けて左右を反転する。年齢・裸の判定は呼び出し側(routes/lora_dataset.py)で行う。
"""

from __future__ import annotations

import json
import os
import re
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
    "model_rigged.glb": "骨と動きの入った形(GLB)",
    "model.blend": "骨の入った Blender のファイル(手でポーズを付ける用)",
    "motion.gif": "動きの確かめ",
    "shape_preview.glb": "形の値の試し(GLB。モデル本体とは別)",
    "meta.json": "作ったときの記録",
    "input_front.png": "3D に送った正面(乳首なしの写し)",
    "input_left.png": "3D に送った横(乳首なしの写し)",
    "blender_edits.json": "Blender で直した所(取り込んだもの。骨を入れ直しても入れる)",
    "rig_ref.npz": "骨を入れたときの形(Blender で直した所を比べる元)",
    "model_dressed.glb": "服を着せた形(GLB・骨と動き入り)",
    "model_dressed.blend": "服を着せた Blender のファイル",
    "dressed_front.png": "服を着せた正面",
    "dressed_side.png": "服を着せた横",
    "dressed_back.png": "服を着せた後ろ",
    "dressed_motion.gif": "服を着せた動きの確かめ",
    "bald_front.png": "3D に送った正面(スキンヘッドの写し)",
    "bald_left.png": "3D に送った横(スキンヘッドの写し)",
    "bald_back.png": "3D に送った後ろ(スキンヘッドの写し)",
}
# 3D に送る乳首なしの写し(裸の素体のモデル)。形を作り直しても消さない(もとの絵が変わるまで使い回す)
INPUT_FILES = {"input_front.png": "front", "input_left.png": "left"}
# 3D に送るスキンヘッドの写し(髪を別の部品にするモデル)。乳首なしの写しと同じく使い回す
BALD_INPUTS = {"bald_front.png": "front", "bald_left.png": "left", "bald_back.png": "back"}
# スキンヘッドのモデルを置くフォルダ(形のフォルダの中。中身は FILES と同じ名前)
BALD_DIR = "bald"
BALD_FILES = {
    f"{BALD_DIR}/{name}": f"スキンヘッドの{label}"
    for name, label in (
        ("model_clean.glb", "整えた形(GLB)"), ("model_rigged.glb", "骨と動きの入った形(GLB)"),
        ("front.png", "正面"), ("side.png", "横(左向き)"), ("back.png", "後ろ"), ("motion.gif", "動きの確かめ"),
        ("hair_front.png", "髪の部品(正面)"), ("hair_side.png", "髪の部品(横)"), ("hair_back.png", "髪の部品(後ろ)"),
    )
}
FILES.update(BALD_FILES)
# 形を作り直しても消さないもの
_KEPT = {*INPUT_FILES, *BALD_INPUTS, *BALD_FILES}
_CLEAN_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "blender" / "clean_model.py"
_RIG_SCRIPT = _CLEAN_SCRIPT.with_name("rig_model.py")
# Windows で Blender を呼ぶとき窓を開かせない(body_rig.NO_WINDOW と同じ。窓なしのバックエンドから呼ぶと、Blender が窓を開いた)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_POSE_SCRIPT = _CLEAN_SCRIPT.with_name("pose_render.py")
_IMPORT_SCRIPT = _CLEAN_SCRIPT.with_name("import_blend.py")
_DRESS_SCRIPT = _CLEAN_SCRIPT.with_name("dress_model.py")
_HAIR_SCRIPT = _CLEAN_SCRIPT.with_name("hair_part.py")
# 取り込んだ結果を書くファイル(読んだら消す)と、比べる元
_IMPORT_REPORT = "import.json"
EDITS_FILE = "blender_edits.json"
# ポーズの絵を置くフォルダ(形のフォルダの中)。名前は <ポーズ>_<カメラ>_<絵の種類>.png
POSE_DIR = "poses"
# clean_model.py の静止画の大きさと、正投影のカメラの写す高さ(身長に対する割合)。画素から m に戻すのに使う
_STILL_SIZE = (360, 600)
_STILL_SPAN = 1.1
# 骨を置くのに要る関節の点
_RIG_POINTS = tuple(
    f"{side}_{part}" for side in ("left", "right") for part in ("shoulder", "elbow", "wrist", "hip", "knee", "ankle")
)
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


def read_meta(out_dir: Path) -> dict:
    path = out_dir / "meta.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_meta(out_dir: Path, meta: dict) -> None:
    """meta.json を書く。画面が状態を聞くたびに読むので、書きかけ(空のファイル)を読まれないよう、別の名前に書いてから入れ替える。"""
    tmp = out_dir / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out_dir / "meta.json")


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


def generate(folder: Path, base_id: int, out_dir: Path, *, seed: int = 12345, on_status=None,
             inputs: dict[str, Path] | None = None) -> dict:
    """
    素体の正面(必須)・横・後ろの絵から形を作り、out_dir に model.glb と meta.json を置く。meta(使った向き・
    横の絵を反転したか・かかった秒数など)を返す。inputs(向き → 絵)があれば、その向きはその絵を送る(乳首なしの写し)。
    """
    sources = {view: path for view, path in source_paths(folder, base_id).items() if path.is_file()}
    replaced = sorted(v for v, p in (inputs or {}).items() if v in sources and p.is_file())
    sources.update({v: (inputs or {})[v] for v in replaced})
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
        # 前に作った形から整えたもの・静止画は、新しい形と合わなくなるので消す(写し・スキンヘッドのモデルは残す)
        for name in FILES:
            if name not in _KEPT:
                (out_dir / name).unlink(missing_ok=True)
        model3d_remote.generate(views, out_dir / "model.glb", seed=seed, on_status=on_status)
    meta = {
        "model": MODEL, "seed": seed, "views": sorted(sources), "side_flipped": flipped, "featureless": replaced,
        "seconds": round(time.time() - started), "created": time.time(),
    }
    write_meta(out_dir, meta)
    return meta


def clean(blender: str, out_dir: Path, height: float, *, max_faces: int = MAX_FACES, timeout: float = 600) -> dict:
    """
    out_dir/model.glb を Blender で整えて model_clean.glb にし、正面・横・後ろの静止画と preview.gif を置く。
    結果(面の数・消した破片の数・身長)を meta.json の clean に足して返す。
    """
    from .body_rig import frames_to_gif

    # 前に入れた骨とポーズの絵は、整え直した形と合わなくなるので消す
    for name in ("model_rigged.glb", "model.blend", "motion.gif"):
        (out_dir / name).unlink(missing_ok=True)
    shutil.rmtree(out_dir / POSE_DIR, ignore_errors=True)
    result = subprocess.run(
        [blender, "--background", "--factory-startup", "--python", str(_CLEAN_SCRIPT), "--",
         str(out_dir), str(height), str(max_faces)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=NO_WINDOW,
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
    meta = read_meta(out_dir)
    meta["clean"] = report
    write_meta(out_dir, meta)
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


def check(folder: Path, base_id: int, out_dir: Path, *, side_flipped: bool = False,
          references: dict[str, Path] | None = None) -> dict:
    """
    整えた形の静止画(clean が描いた front・side・back。背景は透明)の影絵を、素体の同じ向きの絵の影絵(anime_seg で
    人を切り出す)と比べる。向きごとの重なり(IoU)と、その平均(score)を返す。描いていない向きは比べない。
    references(向き → 絵)があれば、その向きはその絵と比べる(スキンヘッドの写し。元の絵とは頭の影絵が違う)。
    """
    from .anime_seg import person_mask

    sources = {**source_paths(folder, base_id), **(references or {})}
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
    meta = read_meta(out_dir)
    meta["check"] = result
    write_meta(out_dir, meta)


def clean_and_check(blender: str, folder: Path, base_id: int, out_dir: Path, height: float,
                    references: dict[str, Path] | None = None) -> dict:
    """整えて(clean)、影絵を比べ(check)、結果を meta.json に残す。チェックの結果を返す。"""
    clean(blender, out_dir, height)
    meta = read_meta(out_dir)
    result = check(folder, base_id, out_dir, side_flipped=bool(meta.get("side_flipped")), references=references)
    _record_check(out_dir, result)
    return result


def make(folder: Path, base_id: int, out_dir: Path, *, blender: str, height: float, seed: int = 12345,
         attempts: int = 1, on_status=None, inputs: dict[str, Path] | None = None, bald: bool = False) -> dict:
    """
    形を作り(generate)、整え、影絵を比べる。bald(inputs がスキンヘッドの写し)なら、影絵も写しと比べる。合わなければシードを変えて作り直す(最初の1回も含めて attempts 回まで)。
    いちばん重なりのよい回の形を out_dir に残し、meta.json の check に結果(作った回数も)を書く。前の形で選んでいた
    形状パーツ(meta.json の shapes: 強さ・場面ごとの組み合わせ・乳首の位置のずらし)は、新しい形にも引き継ぐ。
    """
    kept = read_meta(out_dir).get("shapes")
    best: tuple[float, Path] | None = None
    tries = out_dir / "_tries"
    tries.mkdir(parents=True, exist_ok=True)
    try:
        for attempt in range(max(1, attempts)):
            work = tries / str(attempt)
            if on_status is not None:
                on_status("queued", 0)
            generate(folder, base_id, work, seed=seed + attempt * _SEED_STEP, on_status=on_status, inputs=inputs)
            if on_status is not None:
                on_status("cleaning", 0)
            result = clean_and_check(blender, folder, base_id, work, height, references=inputs if bald else None)
            if best is None or result["score"] > best[0]:
                best = (result["score"], work)
            if result["passed"]:
                break
        _, chosen = best
        meta = read_meta(chosen)
        meta["check"]["attempts"] = attempt + 1
        if kept:
            meta["shapes"] = kept
        write_meta(chosen, meta)
        for name in FILES:
            if name in _KEPT:
                continue
            (out_dir / name).unlink(missing_ok=True)
            if (chosen / name).is_file():
                shutil.move(chosen / name, out_dir / name)
        return meta
    finally:
        shutil.rmtree(tries, ignore_errors=True)


class Model3DRigError(RuntimeError):
    """骨を入れられなかった。"""


def still_to_world(px: float, py: float, height: float) -> tuple[float, float]:
    """正面の静止画の画素を、形の左右 x・高さ z(m)に戻す(カメラは高さ height/2 の正投影で、画像の高さに height*1.1 を写す)。"""
    width, tall = _STILL_SIZE
    scale = height * _STILL_SPAN / tall
    return (px - width / 2) * scale, height / 2 + (tall / 2 - py) * scale


def find_nipples(image: Image.Image) -> dict | None:
    """
    素体の正面の絵(乳首を描いたもの)から、左右の乳首の位置(絵の画素)を測る。骨格の点(肩・腰)から胸の範囲を決め、
    その中で肌より赤い所(乳輪)を色の差で探し、左右それぞれのいちばん強い所のまわりの重心にする(暗さも足すと、胸の下の
    影を拾った。2026-11 実機)。
    {"L": (x, y), "R": (x, y), "score": 確からしさ}。見つからなければ None。L はキャラの左(絵の右)。
    """
    import cv2

    from .body_rig import RigError, detect

    rgb = image.convert("RGB")
    try:
        mask, points = detect(rgb)
    except RigError:
        return None
    need = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    if any(n not in points for n in need):
        return None
    shoulder_y = (points["left_shoulder"][1] + points["right_shoulder"][1]) / 2
    hip_y = (points["left_hip"][1] + points["right_hip"][1]) / 2
    torso = hip_y - shoulder_y
    x0 = min(points["left_shoulder"][0], points["right_shoulder"][0])
    x1 = max(points["left_shoulder"][0], points["right_shoulder"][0])
    mid = (x0 + x1) / 2
    y0, y1 = int(shoulder_y + 0.12 * torso), int(shoulder_y + 0.6 * torso)
    lab = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2LAB).astype(np.float32)
    box = np.zeros(mask.shape, dtype=bool)
    box[max(y0, 0):max(y1, 0), max(int(x0), 0):max(int(x1), 0)] = True
    skin = box & mask
    if skin.sum() < 200:
        return None
    redness = lab[..., 1]
    # 肌(胸の範囲のふつうの色)からの赤みの差
    score = (redness - np.median(redness[skin])) / (redness[skin].std() + 1e-6)
    score = cv2.GaussianBlur(np.where(skin, score, 0).astype(np.float32), (0, 0), max(2.0, 0.01 * rgb.height))
    result: dict = {}
    strength = []
    xs = np.arange(score.shape[1])[None, :]
    for side, pick in (("L", xs > mid), ("R", xs < mid)):
        half = np.where(pick & skin, score, -np.inf)
        y, x = np.unravel_index(np.argmax(half), half.shape)
        peak = float(half[y, x])
        if not np.isfinite(peak) or peak < 1.2:
            return None
        near = (half > 0.6 * peak) & (np.hypot(*np.mgrid[0:half.shape[0], 0:half.shape[1]][::-1] - np.array([x, y])[:, None, None]) < 0.06 * torso)
        ys, xs_ = np.nonzero(near)
        result[side] = (float(xs_.mean()), float(ys.mean()))
        strength.append(peak)
        # 胸の下のふち(正面から見た胸の輪郭の、乳首の真下の黒い線画の線)までの距離を、肩から腰までの長さに対する割合で
        result[f"gap_{side}"] = _underbust_gap(lab[..., 0], result[side], torso, hip_y)
    result["score"] = round(min(strength), 2)
    result["torso"] = float(torso)
    return result


def _underbust_gap(lightness, nipple: tuple[float, float], torso: float, hip_y: float) -> float | None:
    """乳首の真下をたどり、最初の黒い線(線画の胸の輪郭)の真ん中までの距離(肩から腰までの長さに対する割合)。無ければ None。"""
    x, y = int(round(nipple[0])), int(round(nipple[1]))
    lo, hi = max(x - 3, 0), min(x + 4, lightness.shape[1])
    profile = lightness[:, lo:hi].min(axis=1)
    start, end = int(y + 0.03 * torso), min(int(hip_y), lightness.shape[0])
    run = []
    for yy in range(start, end):
        if profile[yy] < 60:
            run.append(yy)
        elif run:
            break
    if len(run) < 2:
        return None
    return round(float((np.mean(run) - y) / torso), 4)


def nipple_hint(folder: Path, base_id: int) -> dict | None:
    """
    素体の正面の絵から測った乳首の位置を、3D の形の左右 x・高さ z(m の割合。身長を 1 とした値)にする。絵の人物の範囲と、
    3D の形の範囲(clean で、足の裏を 0・左右の真ん中を 0 にしたもの)は、同じ正面の絵から作ったので、高さでそろえて
    対応させる。{"L": [x, z], "R": [x, z], "score", "source"}。測れなければ None。
    """
    from .anime_seg import person_mask

    path = folder / f"{base_id}_front.png"
    if not path.is_file():
        return None
    image = Image.open(path).convert("RGB")
    found = find_nipples(image)
    if found is None:
        return None
    mask = person_mask(image) > 0.5
    ys, xs = np.nonzero(mask)
    top, bottom = float(ys.min()), float(ys.max())
    center = (float(xs.min()) + float(xs.max())) / 2
    tall = max(bottom - top, 1.0)
    hint = {side: [round((found[side][0] - center) / tall, 4), round((bottom - found[side][1]) / tall, 4)] for side in ("L", "R")}
    # 胸の下のふちからの距離(肩から腰までの長さに対する割合)。3D の胸は絵ほど下がらないことがあるので、高さそのままより、
    # 3D の胸の下のふちからこの距離に置くほうが、胸の上の位置が絵に合う(2026-11 矢野栄子)
    gaps = {side: found.get(f"gap_{side}") for side in ("L", "R")}
    return {**hint, "gaps": gaps, "score": found["score"], "source": path.name}


def areola_mask(image: Image.Image, *, outline: bool = False) -> np.ndarray | None:
    """
    素体の絵(正面・横)の乳首・乳輪の所(描き直して乳首なしの写しにする範囲)。骨格の点(肩・腰)から胸の高さの帯を決め、
    人物の中で肌より赤い所(find_nipples と同じ赤み)のうち、丸いかたまり(乳輪)だけを、絵の高さの 1.5% だけ広げる。
    胸の下のピンクの影も赤いが、細長い(丸さ 0.54〜0.72。乳輪は 0.94〜0.98。2026-11 矢野栄子)ので外す。outline(横の絵)
    なら、人物の輪郭に届いた所は外へも同じ幅だけ含める。無ければ None。
    """
    import cv2

    from .body_rig import RigError, detect

    rgb = image.convert("RGB")
    try:
        person, points = detect(rgb)
    except RigError:
        return None
    shoulders = [points[n][1] for n in ("left_shoulder", "right_shoulder") if n in points]
    hips = [points[n][1] for n in ("left_hip", "right_hip") if n in points]
    if not shoulders or not hips:
        return None
    top, bottom = float(np.mean(shoulders)), float(np.mean(hips))
    band = np.zeros(person.shape, dtype=bool)
    band[int(top + 0.12 * (bottom - top)):int(top + 0.65 * (bottom - top)), :] = True
    skin = band & person
    if skin.sum() < 200:
        return None
    redness = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2LAB)[..., 1].astype(np.float32)
    score = (redness - np.median(redness[skin])) / (redness[skin].std() + 1e-6)
    score = cv2.GaussianBlur(np.where(skin, score, 0).astype(np.float32), (0, 0), max(2.0, 0.005 * rgb.height))
    hot = ((score > 2.0) & skin).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(hot)
    keep = np.zeros(hot.shape, dtype=bool)
    min_area = 60 * (rgb.height / 1216) ** 2
    for k in range(1, count):
        area = stats[k, cv2.CC_STAT_AREA]
        if area < min_area:
            continue
        component = labels == k
        contours, _ = cv2.findContours(component.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        perimeter = cv2.arcLength(contours[0], True)
        if perimeter > 0 and 4 * np.pi * area / perimeter ** 2 >= 0.85:
            keep |= component
    hot = keep
    if not hot.any():
        return None
    radius = max(3, int(0.015 * rgb.height))
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    inside = cv2.dilate(hot.astype(np.uint8), disc).astype(bool) & cv2.dilate(person.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    if not outline:
        return inside
    # 横の絵で乳首が出ていた所の輪郭を丸く描き直せるように、外も含める(含めないと胸の先の輪郭に角が残り、3D の胸に
    # しわができた。矢野栄子、2026-11)。正面の絵では胸の下の線・腕まで入るので含めない
    outside = cv2.dilate(inside.astype(np.uint8), disc).astype(bool) & ~person
    return inside | outside


def featureless_prompt(prompt: str) -> str:
    """素体の絵のタグの nipples を featureless breasts にする(乳首なしの写しを描くとき)。"""
    tags = [t.strip() for t in prompt.split(",")]
    return ", ".join("featureless breasts" if t.lower() == "nipples" else t for t in tags if t)


FEATURELESS_NEGATIVE = "nipples, areolae, areola, puffy nipples, inverted nipples"

# スキンヘッドの写しのタグ。「bald」だけでは、正面で顔を囲む形の描き直す所に、髪(白・黒のおかっぱ)を描くことが多かった
# ので、強めて打ち消す(2026-11 矢野栄子。横・後ろは bald だけでも描けた)
BALD_TAG = "2::bald, shaved head, bald head::"
BALD_NEGATIVE = "1.5::hair, bangs, bob cut, white hair, black hair::, hair bun, sidelocks, hat, headwear"
_HAIR_TAG = re.compile(r"hair|bangs|\bbun\b|ponytail|twintail|braid|sidelock|ahoge|hairband|hairclip", re.I)
# 描き直した所に、まだ髪が残っているとみなす割合(髪と見分けた画素 / 描き直した所)。描けた絵は 0〜1%、
# 髪を描いた絵は 4.8%、肩に房の先を灰色で描いた絵は 1.6% だった(2026-11 矢野栄子)
BALD_HAIR_LEFT = 0.01


def bald_prompt(prompt: str) -> str:
    """素体の絵のタグから髪のタグを除き、スキンヘッドのタグを頭に付ける(スキンヘッドの写しを描くとき)。"""
    tags = [t.strip() for t in prompt.split(",") if t.strip() and not _HAIR_TAG.search(t)]
    return ", ".join([BALD_TAG, *tags])


def hair_mask(image: Image.Image) -> np.ndarray | None:
    """
    素体の絵の髪の所(描き直してスキンヘッドの写しにする範囲)。体の部位分け(body_parsing)の髪を人物の中に限り、
    絵の高さの 1.2% だけ広げる(髪の縁の色を残さない)。髪が見つからなければ None。
    """
    import cv2

    from . import body_parsing
    from .anime_seg import person_mask

    rgb = image.convert("RGB")
    hair = body_parsing.parse(rgb)["hair"] & (person_mask(rgb) > 0.5)
    if hair.sum() < 50:
        return None
    radius = max(3, int(0.012 * rgb.height))
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    return cv2.dilate(hair.astype(np.uint8), disc).astype(bool)


def hair_left(image: Image.Image, mask: np.ndarray) -> float:
    """描き直した絵の、描き直した所(mask)のうち、まだ髪と見分けられる割合。"""
    from . import body_parsing

    hair = body_parsing.parse(image.convert("RGB"))["hair"]
    return float((hair & mask).sum() / max(1, int(mask.sum())))


def clear_strands(image: Image.Image, mask: np.ndarray) -> Image.Image:
    """
    スキンヘッドに描き直した絵の、描き直した所(mask)に残った髪の房の細い線を、背景の色で消す。人物の切り出しを
    開いて(絵の高さの 0.6% の円)細いはみ出しを外し、人物の外・細いはみ出しは背景に、人物から離れた所の背景と違う
    色(濃い線は差 40、まわりの薄い線は差 6)も背景にする。横の房があった所に、細い線が残った(2026-11 矢野栄子)。
    描き直したままの絵では、人物の切り出しが線と首のあいだを人物に含めて、線が残ることがある(正面の首の横の線が、3D で
    耳から肩への針金になった): 消したあとの絵で人物を切り出し直し、濃い線だけをもう一度消す(ほかの消し方を繰り返すと、
    顔の横・首まで消えた。背景との色の差で細い所を消すと、白い顔の輪郭まで消えた)。
    """
    import cv2

    from .anime_seg import person_mask

    rgb = image.convert("RGB")
    a = np.asarray(rgb).copy()
    edge = np.zeros(mask.shape, dtype=bool)
    edge[:5] = edge[-5:] = True
    edge[:, :5] = edge[:, -5:] = True
    bg = np.median(a[edge], axis=0)
    person = person_mask(rgb) > 0.5
    radius = max(3, int(0.006 * rgb.height))
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    thick = cv2.morphologyEx(person.astype(np.uint8), cv2.MORPH_OPEN, disc)
    diff = np.linalg.norm(a.astype(np.float32) - bg, axis=2)
    clear = mask & (~person | ~thick.astype(bool))
    clear |= mask & (diff > 40) & ~cv2.dilate(thick, np.ones((5, 5), np.uint8)).astype(bool)
    grown = cv2.dilate(mask.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    clear |= grown & (diff > 6) & ~cv2.dilate(thick, np.ones((7, 7), np.uint8)).astype(bool)
    a[clear] = bg
    person = person_mask(Image.fromarray(a)) > 0.5
    thick = cv2.morphologyEx(person.astype(np.uint8), cv2.MORPH_OPEN, disc)
    diff = np.linalg.norm(a.astype(np.float32) - bg, axis=2)
    line = mask & (diff > 40) & ~cv2.dilate(thick, np.ones((5, 5), np.uint8)).astype(bool)
    # 線の縁のにじみも一緒に
    a[cv2.dilate(line.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & mask & (diff > 6)] = bg
    return Image.fromarray(a)


def hand_ratios(folder: Path, base_id: int) -> dict:
    """
    素体の手の絵(手のひら、無ければ手の甲)から、人形の手の形の割合。測れなければ標準の割合。
    {"ratios": {...}, "source": 測った絵の名前か None, "score": 確からしさ}
    """
    from . import hand_shape

    for part in ("hand_palm", "hand_back"):
        path = folder / f"{base_id}_{part}.png"
        if path.is_file():
            measured = hand_shape.measure(path)
            if measured is not None:
                return measured
    return {"ratios": dict(hand_shape.DEFAULTS), "source": None, "score": None}


def rig(blender: str, out_dir: Path, *, motion: str = "wave", hand: dict | None = None, shapes: dict | None = None,
        shape_weights: dict[str, float] | None = None, nipples: dict | None = None, preview_only: bool = False,
        smooth: list[dict] | None = None, timeout: float = 900) -> dict:
    """
    整えた形(model_clean.glb)に人型の骨を入れ、body_motions の動きを全部付ける(model_rigged.glb・model.blend)。
    関節は、正面の静止画(front.png)の骨格推定の点から求める。手は人形の手に付け替え、形の割合は hand(hand_ratios。
    省くと標準)。shapes(body_shapes.to_json)があれば形状パーツのシェイプキーを作り、shape_weights の強さを入れる
    (meta.json の shapes に残し、ポーズの絵にも使う)。ならす所(smooth。省くと meta.json の shapes.smooth)は、まわりの面に
    寄せて体の形に焼き込む。motion の動きで motion.gif を描き出す。結果を meta.json の rig に足す。
    """
    from . import body_motions, hand_shape
    from .body_rig import RigError, detect, frames_to_gif

    hand = hand or {"ratios": dict(hand_shape.DEFAULTS), "source": None, "score": None}

    if motion not in body_motions.MOTIONS:
        raise Model3DRigError(f"動きの種類が違います: {motion}")
    meta = read_meta(out_dir)
    height = float(meta.get("clean", {}).get("height") or 0)
    if not height or not (out_dir / "model_clean.glb").is_file() or not (out_dir / "front.png").is_file():
        raise Model3DRigError("整えた形がありません。先に形を作ってください。")
    still = Image.open(out_dir / "front.png").convert("RGBA")
    white = Image.new("RGBA", still.size, (255, 255, 255, 255))
    white.alpha_composite(still)
    try:
        _, points = detect(white.convert("RGB"))
    except RigError as exc:
        raise Model3DRigError(f"形の正面の絵から関節が取れませんでした: {exc}") from exc
    missing = [n for n in _RIG_POINTS if n not in points]
    if missing:
        raise Model3DRigError(f"形の正面の絵から関節が取れませんでした({', '.join(missing)})")
    joints = {n: still_to_world(float(points[n][0]), float(points[n][1]), height) for n in _RIG_POINTS}
    # 前のポーズの絵は、入れ直した骨と合わなくなるので消す
    shutil.rmtree(out_dir / POSE_DIR, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix="model3d_rig_") as tmp:
        joints_path = Path(tmp) / "joints.json"
        sent = {"points": joints, "hand": hand["ratios"]}
        if shapes:
            sent.update(shapes=shapes, shape_weights=shape_weights or {})
        if preview_only:
            sent["preview_only"] = True
        spots = meta.get("shapes", {}).get("smooth", []) if smooth is None else smooth
        if spots:
            sent["smooth"] = spots
        if nipples:
            # 乳首の位置: 正面の絵から測った位置(nipple_hint。身長を 1 とした値)と、ずらす量(mm)
            sent["nipples"] = nipples
        joints_path.write_text(json.dumps(sent), encoding="utf-8")
        motions_path = Path(tmp) / "motions.json"
        motions_path.write_text(json.dumps({k: m.to_json() for k, m in body_motions.MOTIONS.items()}), encoding="utf-8")
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--python", str(_RIG_SCRIPT), "--",
             str(out_dir), str(joints_path), str(motions_path), motion],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=NO_WINDOW,
        )
    report_path = out_dir / "rig.json"
    if result.returncode != 0 or not report_path.is_file() or not (out_dir / "model_rigged.glb").is_file():
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-15:])
        raise Model3DRigError(f"Blender で骨を入れられませんでした(終了コード {result.returncode}):\n{tail}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink()
    if preview_only:
        return report
    frames = out_dir / "frames"
    frames_to_gif(sorted(frames.glob("*.png")), out_dir / "motion.gif")
    shutil.rmtree(frames)
    # 着せた服は、入れ直す前の骨に付けてあるので消す(着せ直す)
    for name in DRESSED_FILES:
        (out_dir / name).unlink(missing_ok=True)
    meta = read_meta(out_dir)
    meta.pop("garments", None)
    meta["rig"] = {"motion": motion, "hand": hand, **report, "nipples": nipples}
    if shapes:
        # 作ったシェイプキーと、作った部品(part_<形>_<L|R>)の形の強さだけ残す
        made = set((report.get("shapes") or {}).get("keys", {}))
        made |= {name[len("part_"):-2] for name in (report.get("shapes") or {}).get("parts", {})}
        # 場面ごとの組み合わせ・乳首の位置のずらしは、そのまま残す
        meta["shapes"] = {**meta.get("shapes", {}), "weights": {k: v for k, v in (shape_weights or {}).items() if k in made}}
    else:
        meta.get("shapes", {}).pop("weights", None)
        if not meta.get("shapes"):
            meta.pop("shapes", None)
    write_meta(out_dir, meta)
    return meta["rig"]


PREVIEW_FILE = "shape_preview.glb"


def preview_shapes(blender: str, out_dir: Path, *, hand: dict | None, shapes: dict, shape_weights: dict[str, float],
                   nipples: dict | None = None, smooth: list[dict] | None = None, timeout: float = 600) -> Path:
    """
    形の値の試し: 整えた形を作業用のフォルダに写し、渡した形(形の値を変えたもの)で骨と形状パーツを入れて、GLB だけを
    out_dir/shape_preview.glb に置く。モデル本体(model_rigged.glb・model.blend・meta.json)は書き換えない。動き・確かめの
    コマ・Blender のファイルは作らないので、骨を入れ直すより速い。smooth は、ならす所(省くと保存したもの)。
    """
    # 試しでは、強さの入った形だけを作る(全部の形の部品とシェイプキーを作ると、骨を入れ直すのと変わらない時間がかかった)
    used = {k for k, w in shape_weights.items() if w > 0}
    shapes = {**shapes, "presets": {k: p for k, p in shapes["presets"].items() if k in used}}
    with tempfile.TemporaryDirectory(prefix="model3d_preview_") as tmp:
        work = Path(tmp)
        for name in ("model_clean.glb", "front.png", "meta.json"):
            if not (out_dir / name).is_file():
                raise Model3DRigError("整えた形がありません。先に形を作ってください。")
            shutil.copyfile(out_dir / name, work / name)
        # Blender で直した所も、試しに入れる
        if (out_dir / EDITS_FILE).is_file():
            shutil.copyfile(out_dir / EDITS_FILE, work / EDITS_FILE)
        rig(blender, work, hand=hand, shapes=shapes, shape_weights=shape_weights, nipples=nipples, preview_only=True,
            smooth=smooth, timeout=timeout)
        target = out_dir / PREVIEW_FILE
        shutil.copyfile(work / "model_rigged.glb", target)
    return target


class Model3DImportError(RuntimeError):
    """Blender で直したファイルを取り込めなかった。"""


def open_blender(blender: str, out_dir: Path) -> None:
    """
    骨の入った model.blend を、手元の Blender の画面で開く(手直し用。待たない)。窓を出すのが目的なので NO_WINDOW は
    付けず、コンソールの窓を出さない blender-launcher.exe があればそれで開く。
    """
    path = out_dir / "model.blend"
    if not path.is_file():
        raise Model3DImportError("この素体の 3D の形に、まだ骨を入れていません。")
    launcher = Path(blender).with_name("blender-launcher.exe")
    subprocess.Popen([str(launcher if launcher.is_file() else blender), str(path)], cwd=str(out_dir))


def import_blend(blender: str, out_dir: Path, *, timeout: float = 300) -> dict:
    """
    Blender で直して保存した model.blend を、骨を入れたときの形(rig_ref.npz)と比べ、直した所を blender_edits.json に
    書く(scripts/blender/import_blend.py)。直した所は、このあと骨を入れ直すと形に焼き込まれる(骨を入れ直すたびに入る)。
    結果(体を直した頂点の数・直したシェイプキー・まだ取り込めない足したシェイプキー)を meta.json の blender_import に
    残して返す。
    """
    if not (out_dir / "model.blend").is_file() or not (out_dir / "rig_ref.npz").is_file():
        raise Model3DImportError("比べる元がありません。骨を入れ直してから、Blender で開いて直してください。")
    report_path = out_dir / _IMPORT_REPORT
    report_path.unlink(missing_ok=True)
    result = subprocess.run(
        [blender, "--background", "--factory-startup", str(out_dir / "model.blend"), "--python", str(_IMPORT_SCRIPT),
         "--python-exit-code", "1", "--", str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW,
    )
    if result.returncode != 0 or not report_path.is_file():
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-10:])
        raise Model3DImportError(f"Blender のファイルを読めませんでした(終了コード {result.returncode}):\n{tail}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink()
    if "error" in report:
        raise Model3DImportError(report["error"])
    meta = read_meta(out_dir)
    meta["blender_import"] = {**report, "imported": time.time()}
    write_meta(out_dir, meta)
    return report


class Model3DDressError(RuntimeError):
    """服を着せられなかった。"""


# 服を着せたときのファイル(骨を入れ直すと消す)
DRESSED_FILES = ("model_dressed.glb", "model_dressed.blend", "dressed_front.png", "dressed_side.png", "dressed_back.png",
                 "dressed_motion.gif")
# 作れる服の種類(scripts/blender/garment_*.py)
GARMENT_KINDS = ("skirt", "top")


def dress(blender: str, out_dir: Path, garments: list[dict], *, motion: str = "walk", timeout: float = 1800) -> dict:
    """
    骨を入れた形(model.blend)の休んだ姿勢の体に、型紙から作った服を布として着せ、骨に付ける
    (scripts/blender/dress_model.py。スカート garment_skirt.py・上の服 garment_top.py)。garments は [{"kind", "name", "config"}]。
    骨と動き入りの model_dressed.glb・model_dressed.blend と、確かめの静止画・motion の動きの GIF を描き出す。
    model.blend は書き換えない(骨を入れ直したら、着せ直す)。結果(服ごとの寸法・体への食い込み・付けた骨)を
    meta.json の garments に残して返す。
    """
    from . import body_motions
    from .body_rig import frames_to_gif

    if motion not in body_motions.MOTIONS:
        raise Model3DDressError(f"動きの種類が違います: {motion}")
    unknown = [g.get("kind") for g in garments if g.get("kind") not in GARMENT_KINDS]
    if not garments or unknown:
        raise Model3DDressError(f"作れない服です: {', '.join(map(str, unknown)) or '(服がありません)'}")
    meta = read_meta(out_dir)
    joints = (meta.get("rig") or {}).get("joints")
    if not (out_dir / "model.blend").is_file() or not joints:
        raise Model3DDressError("この素体の 3D の形に、まだ骨を入れていません。")
    shutil.rmtree(out_dir / "frames_dressed", ignore_errors=True)
    report_path = out_dir / "dress.json"
    report_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="model3d_dress_") as tmp:
        spec = Path(tmp) / "garments.json"
        spec.write_text(json.dumps({"joints": joints, "garments": garments,
                                    "frames": body_motions.MOTIONS[motion].frames}), encoding="utf-8")
        result = subprocess.run(
            [blender, "--background", "--factory-startup", str(out_dir / "model.blend"), "--python", str(_DRESS_SCRIPT),
             "--python-exit-code", "1", "--", str(out_dir), str(spec), motion],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW,
        )
    if result.returncode != 0 or not report_path.is_file() or not (out_dir / "model_dressed.glb").is_file():
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-15:])
        raise Model3DDressError(f"Blender で服を着せられませんでした(終了コード {result.returncode}):\n{tail}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink()
    frames = out_dir / "frames_dressed"
    if frames.is_dir():
        frames_to_gif(sorted(frames.glob("*.png")), out_dir / "dressed_motion.gif")
        shutil.rmtree(frames)
    meta = read_meta(out_dir)
    meta["garments"] = {"motion": motion, "items": report["garments"], "dressed": time.time()}
    write_meta(out_dir, meta)
    return meta["garments"]


class Model3DPoseError(RuntimeError):
    """ポーズの絵を描けなかった。"""


def pose_images(out_dir: Path) -> list[dict]:
    """描いたポーズの絵(新しい順)。"""
    from . import body_poses

    folder = out_dir / POSE_DIR
    result = []
    for path in folder.glob("*.png") if folder.is_dir() else []:
        pose, camera, style = (path.stem.rsplit("_", 2) + ["", ""])[:3]
        if pose in body_poses.POSES and camera in body_poses.CAMERAS and style in body_poses.STYLES:
            result.append({
                "name": f"{POSE_DIR}/{path.name}", "pose": pose, "camera": camera, "style": style,
                "updated": path.stat().st_mtime,
            })
    return sorted(result, key=lambda item: -item["updated"])


def render_pose(blender: str, out_dir: Path, pose: str, cameras: list[str], style: str = "shade", *,
                shape_weights: dict[str, float] | None = None, timeout: float = 600) -> list[Path]:
    """
    骨を入れた形(model.blend)にポーズを当て、カメラごとに参考の絵を描き出す(poses/<ポーズ>_<カメラ>_<種類>.png)。
    形状パーツの強さは shape_weights(省くと meta.json の shapes の強さ。場面ごとに選ぶ)。
    描いた絵のパスを返す。
    """
    from . import body_poses

    if pose not in body_poses.POSES:
        raise Model3DPoseError(f"ポーズの種類が違います: {pose}")
    unknown = [c for c in cameras if c not in body_poses.CAMERAS]
    if unknown or not cameras:
        raise Model3DPoseError(f"カメラの向きが違います: {', '.join(unknown) or '(なし)'}")
    if style not in body_poses.STYLES:
        raise Model3DPoseError(f"絵の種類が違います: {style}")
    if not (out_dir / "model.blend").is_file():
        raise Model3DPoseError("骨を入れた形がありません。先に形を作ってください。")
    folder = out_dir / POSE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="model3d_pose_") as tmp:
        pose_path = Path(tmp) / "pose.json"
        if shape_weights is None:
            shape_weights = read_meta(out_dir).get("shapes", {}).get("weights", {})
        sent = {**body_poses.render_json(pose, cameras), "shapes": shape_weights}
        pose_path.write_text(json.dumps(sent), encoding="utf-8")
        renders = Path(tmp) / "renders"
        result = subprocess.run(
            [blender, "--background", str(out_dir / "model.blend"), "--python-exit-code", "1", "--python",
             str(_POSE_SCRIPT), "--", str(pose_path), str(renders), style],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=NO_WINDOW,
        )
        missing = [c for c in cameras if not (renders / f"{c}.png").is_file()]
        if result.returncode != 0 or missing:
            tail = "\n".join((result.stdout + result.stderr).splitlines()[-15:])
            raise Model3DPoseError(f"Blender でポーズの絵を描けませんでした(終了コード {result.returncode}):\n{tail}")
        paths = []
        for camera in cameras:
            target = folder / f"{pose}_{camera}_{style}.png"
            shutil.move(renders / f"{camera}.png", target)
            paths.append(target)
    return paths


class Model3DHairError(RuntimeError):
    """髪の部品を付けられなかった。"""


def front_used(out_dir: Path, folder: Path, base_id: int) -> Path:
    """髪ありの形を作ったときに送った正面の絵(乳首なしの写しを送っていればそれ)。"""
    if "front" in read_meta(out_dir).get("featureless", []) and (out_dir / "input_front.png").is_file():
        return out_dir / "input_front.png"
    return source_paths(folder, base_id)["front"]


def attach_hair(blender: str, out_dir: Path, front: Path, *, timeout: float = 900) -> dict:
    """
    髪を別の部品にする(scripts/blender/hair_part.py)。髪ありの整えた形(out_dir/model_clean.glb)を、スキンヘッドの骨入り
    モデル(out_dir/bald/model.blend)に胸〜首の体で合わせ、スキンヘッドの頭から外に出た所を髪の部品として head の骨に
    付けて、bald/model_rigged.glb を書き出し直す。前を向いた面は、髪ありの形を作った正面の絵(front)の髪の所かで決める
    (前髪と顔は形の差では見分けられない)。結果を bald/meta.json の hair に残して返す。
    """
    from . import body_parsing
    from .anime_seg import person_mask

    bald = out_dir / BALD_DIR
    if not (out_dir / "model_clean.glb").is_file():
        raise Model3DHairError("髪ありの形がありません。先に髪ありの形を作ってください。")
    if not (bald / "model.blend").is_file():
        raise Model3DHairError("スキンヘッドの骨入りのモデルがありません。先にスキンヘッドの形を作ってください。")
    image = Image.open(front).convert("RGB")
    person = person_mask(image) > 0.5
    if not person.any():
        raise Model3DHairError("正面の絵から人物を切り出せませんでした。")
    hair = body_parsing.parse(image)["hair"] & person
    ys, xs = np.nonzero(person)
    with tempfile.TemporaryDirectory(prefix="model3d_hair_") as tmp:
        mask = Path(tmp) / "mask.npz"
        np.savez_compressed(mask, front_hair=hair, front_box=np.array([xs.min(), xs.max(), ys.min(), ys.max()], float))
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--python", str(_HAIR_SCRIPT), "--python-exit-code", "1",
             "--", str(bald.resolve()), str((out_dir / "model_clean.glb").resolve()), str(mask)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW,
        )
    report_path = bald / "hair.json"
    if result.returncode != 0 or not report_path.is_file():
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-15:])
        raise Model3DHairError(f"Blender で髪の部品を付けられませんでした(終了コード {result.returncode}):\n{tail}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink()
    meta = read_meta(bald)
    meta["hair"] = report
    write_meta(bald, meta)
    return report
