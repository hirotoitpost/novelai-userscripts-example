"""
素体から 2D アニメ用のモデルを作る。素体の Aポーズの絵の形に合わせた平らなメッシュ(2D の素体)を作り、絵をテクスチャとして
貼って、Blender で骨(アーマチュア)の重みで曲げる(Live2D・Spine のメッシュ変形と同じ考え)。

1. 人物を切り出す(anime_seg)、骨格の17点を取る(ugoira_rig.detect_pose の RTMPose)
2. メッシュを作る: 人物の形の中に格子の点と輪郭の点を置き、三角形でつなぐ(Delaunay。形の外にかかる三角形・長すぎる辺は捨てる)。
   体(頭・胴・脚)で1枚、左右の腕(手前に重ねる)で1枚ずつ。腕のメッシュは肩の付け根の胴も少し含み、そこは胴の骨に寄せた
   重みにして、腕を上げたとき肩がなめらかにつながるようにする
3. 重み: 頂点ごとに、骨(関節と関節を結ぶ線)までの距離を骨の太さで割ったものを比べ、近い骨ほど重くする。関節のまわりでは
   となりの骨と混ざるので、曲げても切れ目ができず、皮膚のように伸び縮みする。頭は顎の線のあたりで首から頭へ移す
4. 書き出す: texture.png(背景を透明にした元の絵)と rig.json(骨・メッシュの頂点(絵の画素)・三角形・重み)
5. Blender(scripts/blender/build_rig.py)で、メッシュに絵を貼り(UV は絵の位置そのまま)、骨と重みを入れて、動きを付けて描き出す

Aポーズ(腕を体から離した立ち絵)を前提にする。腕が体に重なる絵では、腕の裏の胴の絵が無い。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Bone:
    name: str
    label: str
    # 骨の始まり(回す軸)と終わりの関節。"neck"(両肩の中点)・"pelvis"(両腰の中点)・"head_top"・"hand_end"・"foot_end" は計算で出す
    head: str
    tail: str
    parent: str | None
    # 太さ(肩幅に対する半径の割合)。重みと割り当てで、距離をこれで割る
    radius: float


# 正面の絵の左右は、キャラから見た左右(絵の右側がキャラの左)。RTMPose の left_* もキャラの左
BONES: tuple[Bone, ...] = (
    Bone("torso", "胴", "pelvis", "neck", None, 0.55),
    Bone("head", "頭", "neck", "head_top", "torso", 0.5),
    Bone("upper_arm.L", "上腕(左)", "left_shoulder", "left_elbow", "torso", 0.13),
    Bone("forearm.L", "前腕(左)", "left_elbow", "left_wrist", "upper_arm.L", 0.11),
    Bone("hand.L", "手(左)", "left_wrist", "left_hand_end", "forearm.L", 0.12),
    Bone("upper_arm.R", "上腕(右)", "right_shoulder", "right_elbow", "torso", 0.13),
    Bone("forearm.R", "前腕(右)", "right_elbow", "right_wrist", "upper_arm.R", 0.11),
    Bone("hand.R", "手(右)", "right_wrist", "right_hand_end", "forearm.R", 0.12),
    Bone("thigh.L", "大腿(左)", "left_hip", "left_knee", "torso", 0.2),
    Bone("shin.L", "下腿(左)", "left_knee", "left_ankle", "thigh.L", 0.14),
    Bone("foot.L", "足(左)", "left_ankle", "left_foot_end", "shin.L", 0.12),
    Bone("thigh.R", "大腿(右)", "right_hip", "right_knee", "torso", 0.2),
    Bone("shin.R", "下腿(右)", "right_knee", "right_ankle", "thigh.R", 0.14),
    Bone("foot.R", "足(右)", "right_ankle", "right_foot_end", "shin.R", 0.12),
)
_NAMES = [b.name for b in BONES]


@dataclass(frozen=True)
class MeshGroup:
    name: str
    # このメッシュに入る部位(割り当ての骨)と、重みを付ける骨(肩の付け根の胴など、部位の外の骨も入る)
    parts: tuple[str, ...]
    bones: tuple[str, ...]
    # 重ねる順(大きいほど手前)
    z: int


MESH_GROUPS: tuple[MeshGroup, ...] = (
    MeshGroup("body", ("torso", "head", "thigh.L", "shin.L", "foot.L", "thigh.R", "shin.R", "foot.R"),
              ("torso", "head", "thigh.L", "shin.L", "foot.L", "thigh.R", "shin.R", "foot.R"), 0),
    MeshGroup("arm.L", ("upper_arm.L", "forearm.L", "hand.L"), ("torso", "upper_arm.L", "forearm.L", "hand.L"), 1),
    MeshGroup("arm.R", ("upper_arm.R", "forearm.R", "hand.R"), ("torso", "upper_arm.R", "forearm.R", "hand.R"), 1),
)

# 格子の間隔(肩幅に対する割合)。細かいほど曲げがなめらかだが、頂点が増える
_GRID = 0.045
# 重みの混ざり具合: 骨ごとの(距離/太さ)の差がこれくらいなら、ほぼ同じ重さで混ざる
_BLEND = 0.12
# 一番近い骨より、(距離/太さ)がこれ以上離れた骨には重みを付けない
_CUTOFF = 0.6
# 腕のメッシュに入れる、肩の付け根の胴の範囲(上腕の太さに対する倍率)
_SHOULDER_CAP = 1.6
# 頭への移り: 顎の線の上下のこの幅(肩幅に対する割合)で、首(胴)から頭へ重みを移す
_NECK_BAND = 0.08
_MIN_SCORE = 0.3


class RigError(ValueError):
    """リグを作れない(人物・骨格が見つからない など)。"""


def joints(points: dict[str, np.ndarray], mask: np.ndarray) -> dict[str, np.ndarray]:
    """骨格の17点から、骨の端の点をすべて出す(首・骨盤・頭のてっぺん・手先・つま先)。"""
    need = ("left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist",
            "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle", "nose")
    missing = [n for n in need if n not in points]
    if missing:
        raise RigError(f"骨格の点が見つかりませんでした: {', '.join(missing)}(腕と脚が見える Aポーズの全身の絵を使ってください)")
    j = dict(points)
    j["neck"] = (points["left_shoulder"] + points["right_shoulder"]) / 2
    j["pelvis"] = (points["left_hip"] + points["right_hip"]) / 2
    ys = np.nonzero(mask)[0]
    j["head_top"] = np.array([points["nose"][0], float(ys.min())], np.float32)
    for side in ("left", "right"):
        # 手先: 手首から前腕の向きに、前腕の 0.6 倍。つま先: 足首から真下に、下腿の 0.3 倍(正面の絵では足は下に伸びる)
        forearm = points[f"{side}_wrist"] - points[f"{side}_elbow"]
        j[f"{side}_hand_end"] = points[f"{side}_wrist"] + forearm * 0.6
        shin = np.linalg.norm(points[f"{side}_ankle"] - points[f"{side}_knee"])
        j[f"{side}_foot_end"] = points[f"{side}_ankle"] + np.array([0.0, shin * 0.3], np.float32)
    return j


def _segment_distance(xs: np.ndarray, ys: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """点 (xs, ys) から線分 a-b までの距離。"""
    ab = b - a
    t = ((xs - a[0]) * ab[0] + (ys - a[1]) * ab[1]) / max(float(ab @ ab), 1e-6)
    t = np.clip(t, 0.0, 1.0)
    return np.hypot(xs - (a[0] + t * ab[0]), ys - (a[1] + t * ab[1]))


def _scores(xs: np.ndarray, ys: np.ndarray, j: dict[str, np.ndarray]) -> np.ndarray:
    """点ごと・骨ごとの(骨までの距離/骨の太さ)。並びは BONES。"""
    shoulder = float(np.linalg.norm(j["left_shoulder"] - j["right_shoulder"]))
    return np.stack([_segment_distance(xs, ys, j[b.head], j[b.tail]) / (b.radius * shoulder) for b in BONES])


def _head_share(xs: np.ndarray, ys: np.ndarray, j: dict[str, np.ndarray]) -> np.ndarray:
    """頭の骨に寄せる割合(0〜1)。顎の線より上で頭の幅の中は 1、顎の線の下へ _NECK_BAND の幅で 0 へ。"""
    shoulder = float(np.linalg.norm(j["left_shoulder"] - j["right_shoulder"]))
    chin = j["nose"][1] + 0.6 * (j["neck"][1] - j["nose"][1])
    band = _NECK_BAND * shoulder
    share = np.clip((chin + band - ys) / (2 * band), 0.0, 1.0)
    return np.where(np.abs(xs - j["nose"][0]) < shoulder * 0.75, share, 0.0)


def assign(mask: np.ndarray, j: dict[str, np.ndarray]) -> np.ndarray:
    """人物の画素ごとの部位の番号(BONES の並び。人物でない所は -1)。メッシュをどのグループに入れるかに使う。"""
    h, w = mask.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    scores = _scores(xs, ys, j)
    labels = scores.argmin(axis=0)
    # 胴の細い出っ張り(脇の下に残った腕の内側の縁など)は、胴から外して次に近い骨へ(腕を上げると、胴の脇に筋が残った)
    shoulder = float(np.linalg.norm(j["left_shoulder"] - j["right_shoulder"]))
    torso_index = _NAMES.index("torso")
    torso = ((labels == torso_index) & mask).astype(np.uint8)
    size = max(3, int(shoulder * 0.08)) | 1
    opened = cv2.morphologyEx(torso, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    sliver = (torso > 0) & (opened == 0)
    if sliver.any():
        scores[torso_index][sliver] = np.inf
        labels[sliver] = scores[:, sliver].argmin(axis=0)
    labels[_head_share(xs, ys, j) >= 0.5] = _NAMES.index("head")
    labels[~mask] = -1
    return labels


def weights(
    xs: np.ndarray, ys: np.ndarray, j: dict[str, np.ndarray], bones: tuple[str, ...],
    anchor: tuple[str, str, float] | None = None,
) -> np.ndarray:
    """
    頂点ごとの重み(頂点 × bones。行の和は 1)。近い骨ほど重く、関節のまわりではとなりの骨と混ざる。
    anchor(骨, 関節, 半径)は、その骨の重みを関節のまわりの半径の中だけに絞る(腕のメッシュの胴の重みは肩のまわりだけ。
    脇の下の腕の縁まで胴に引かれると、腕を上げたとき脇から膜のように伸びた)。
    """
    scores = _scores(xs, ys, j)[[_NAMES.index(b) for b in bones]]
    # 頭は距離ではなく顎の線で決める(_head_share)。ほかの骨の重みは、頭を除いて出してから、頭の割合の残りに縮める
    head = bones.index("head") if "head" in bones else None
    if head is not None:
        scores[head] = np.inf
    nearest = scores.min(axis=0)
    w = np.exp(-(scores - nearest) / _BLEND)
    w[scores - nearest > _CUTOFF] = 0.0
    if anchor is not None:
        bone, joint, radius = anchor
        falloff = np.clip(1.0 - np.hypot(xs - j[joint][0], ys - j[joint][1]) / radius, 0.0, 1.0)
        w[bones.index(bone)] *= falloff
        # 絞った骨しか重みが残らない頂点は、いちばん近いほかの骨へ
        empty = w.sum(axis=0) <= 1e-6
        if empty.any():
            others = np.where(np.arange(len(bones))[:, None] == bones.index(bone), np.inf, scores)
            w[others[:, empty].argmin(axis=0), np.nonzero(empty)[0]] = 1.0
    w = w / w.sum(axis=0)
    if head is not None:
        share = _head_share(xs, ys, j)
        w = w * (1 - share)
        w[head] = share
    return w.T


def _region_mesh(region: np.ndarray, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """region(True の所)を覆う三角形のメッシュ。(頂点(x, y) × N, 三角形の頂点番号 × M)。"""
    from scipy.spatial import Delaunay

    h, w = region.shape
    step = max(4, int(spacing))
    gy, gx = np.mgrid[step // 2:h:step, step // 2:w:step]
    inside = region[gy, gx]
    points = [np.stack([gx[inside], gy[inside]], axis=1).astype(np.float32)]
    contours, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    for contour in contours:
        ring = contour[:, 0, :].astype(np.float32)
        points.append(ring[:: max(1, step // 2)])
    pts = np.unique(np.concatenate(points), axis=0)
    if len(pts) < 3:
        raise RigError("メッシュを作れませんでした(部位が小さすぎます)。")
    tri = Delaunay(pts).simplices
    # 形の外にかかる三角形(脚の間・腕と胴の間をまたぐもの)と、長すぎる辺の三角形を捨てる
    centroid = pts[tri].mean(axis=1)
    cx = np.clip(centroid[:, 0].round().astype(int), 0, w - 1)
    cy = np.clip(centroid[:, 1].round().astype(int), 0, h - 1)
    edges = np.linalg.norm(pts[tri] - pts[np.roll(tri, 1, axis=1)], axis=2).max(axis=1)
    keep = region[cy, cx] & (edges < step * 2.2)
    tri = tri[keep]
    used = np.unique(tri)
    remap = np.full(len(pts), -1)
    remap[used] = np.arange(len(used))
    return pts[used], remap[tri]


def build_meshes(mask: np.ndarray, points: dict[str, np.ndarray]) -> tuple[dict[str, np.ndarray], list[dict]]:
    """(骨の端の点, メッシュのグループごとの {name, z, bones, vertices, triangles, weights})。mask は人物(True)。"""
    j = joints(points, mask)
    labels = assign(mask, j)
    shoulder = float(np.linalg.norm(j["left_shoulder"] - j["right_shoulder"]))
    spacing = _GRID * shoulder
    # メッシュは人物の形より少し外まで覆う(輪郭は絵の透明で切れる。曲げたとき縁が欠けないように)
    pad = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    groups = []
    for group in MESH_GROUPS:
        region = np.isin(labels, [_NAMES.index(p) for p in group.parts])
        anchor = None
        if group.name != "body":
            # 腕: 肩の付け根の胴も含める(重みは胴へ寄るので、腕を上げると肩がなめらかにつながる)
            side = "left" if group.name.endswith(".L") else "right"
            cap = np.zeros(mask.shape, np.uint8)
            radius = int(BONES[_NAMES.index(f"upper_arm.{group.name[-1]}")].radius * _SHOULDER_CAP * shoulder)
            cv2.circle(cap, tuple(int(v) for v in j[f"{side}_shoulder"]), radius, 1, -1)
            region |= cap.astype(bool) & mask
            anchor = ("torso", f"{side}_shoulder", float(radius))
        # 広げるのは背景の側だけ(ほかのメッシュの人物の画素まで広げると、腕を動かしたとき腕の縁が胴に残った)
        region = cv2.dilate(region.astype(np.uint8), pad).astype(bool) & (region | ~mask)
        if not region.any():
            raise RigError(f"{group.name} の画素がありませんでした(骨格の点がずれているかもしれません)")
        verts, tris = _region_mesh(region, spacing)
        wts = weights(verts[:, 0], verts[:, 1], j, group.bones, anchor)
        groups.append({
            "name": group.name, "z": group.z, "bones": list(group.bones),
            "vertices": verts.round(1).tolist(), "triangles": tris.tolist(), "weights": wts.round(4).tolist(),
        })
    return j, groups


def build(source: Path, out_dir: Path) -> dict:
    """source(Aポーズの絵)から、out_dir に texture.png・rig.json・overlay.png(確かめ用)を書く。"""
    from .anime_seg import person_mask
    from .ugoira_rig import KEYPOINTS, detect_pose

    image = Image.open(source).convert("RGB")
    mask = person_mask(image) > 0.5
    if not mask.any():
        raise RigError("人物が見つかりませんでした。")
    ys, xs = np.nonzero(mask)
    skeleton = detect_pose(image, (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())))
    points = {n: p for n, p, s in zip(KEYPOINTS, skeleton.points, skeleton.scores) if s >= _MIN_SCORE}
    j, meshes = build_meshes(mask, points)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    # テクスチャ: 元の絵の背景を透明に(人物の縁は anime_seg の確からしさでなめらかに)
    soft = cv2.GaussianBlur(mask.astype(np.float32), (3, 3), 0)
    Image.fromarray(np.dstack([np.asarray(image), (soft * 255).astype(np.uint8)]), "RGBA").save(out_dir / "texture.png")
    rig = {
        "source": source.name,
        "size": list(image.size),
        "texture": "texture.png",
        "bones": [
            {"name": b.name, "label": b.label, "parent": b.parent,
             "head": [float(v) for v in j[b.head]], "tail": [float(v) for v in j[b.tail]]}
            for b in BONES
        ],
        "meshes": meshes,
    }
    (out_dir / "rig.json").write_text(json.dumps(rig, ensure_ascii=False), encoding="utf-8")
    _overlay(image, rig).save(out_dir / "overlay.png")
    return rig


def _overlay(image: Image.Image, rig: dict) -> Image.Image:
    """確かめ用: メッシュの三角形を、いちばん重い骨の色で描き、骨を白い線で描いた絵。"""
    out = (np.asarray(image.convert("RGB")).astype(np.float32) * 0.45).astype(np.uint8)
    palette = [(255, 99, 99), (255, 214, 102), (102, 204, 255), (153, 102, 255), (102, 255, 153), (255, 153, 204),
               (255, 170, 80), (80, 220, 220), (200, 120, 255), (160, 230, 90), (255, 120, 180), (120, 160, 255),
               (230, 200, 140), (140, 255, 220)]
    colors = {b.name: palette[i % len(palette)] for i, b in enumerate(BONES)}
    for mesh in rig["meshes"]:
        verts = np.array(mesh["vertices"], np.float32)
        wts = np.array(mesh["weights"])
        for tri in mesh["triangles"]:
            bone = mesh["bones"][int(wts[tri].sum(axis=0).argmax())]
            cv2.polylines(out, [verts[tri].round().astype(np.int32)], True, colors[bone], 1, cv2.LINE_AA)
    for bone in rig["bones"]:
        a = tuple(int(v) for v in bone["head"])
        b = tuple(int(v) for v in bone["tail"])
        cv2.line(out, a, b, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.circle(out, a, 5, (255, 40, 40), -1)
    return Image.fromarray(out)


# ---- Blender ----

_BLENDER_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "blender" / "build_rig.py"
MOTIONS = {"wave": "手を振る", "idle": "待機(呼吸・揺れ)", "walk": "その場で歩く"}


def find_blender(configured: str | None = None) -> str | None:
    """Blender の実行ファイル。設定(blender.path)→ 環境変数 BLENDER_PATH → PATH の順に探す。"""
    for candidate in (configured, os.environ.get("BLENDER_PATH"), shutil.which("blender")):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def run_blender(blender: str, rig_dir: Path, motion: str, *, timeout: float = 600) -> dict:
    """
    rig_dir の rig.json から Blender でリグを組み、動きを付けて描き出す(画面は出さない)。
    rig.blend(Blender のファイル)と frames/*.png(描き出したコマ)ができる。
    """
    if motion not in MOTIONS:
        raise RigError(f"動きの種類が違います: {motion}")
    result = subprocess.run(
        [blender, "--background", "--factory-startup", "--python", str(_BLENDER_SCRIPT), "--",
         str(rig_dir), motion],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode != 0 or not (rig_dir / "rig.blend").is_file():
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-15:])
        raise RigError(f"Blender で組めませんでした(終了コード {result.returncode}):\n{tail}")
    frames = sorted((rig_dir / "frames").glob("*.png"))
    return {"blend": rig_dir / "rig.blend", "frames": frames}


def frames_to_gif(frames: list[Path], out: Path, fps: int = 12, width: int = 360) -> Path:
    """描き出したコマを、確かめ用の GIF に(透過は白で埋める)。"""
    images = []
    for path in frames:
        frame = Image.open(path).convert("RGBA")
        white = Image.new("RGBA", frame.size, (255, 255, 255, 255))
        white.alpha_composite(frame)
        frame = white.convert("RGB")
        frame.thumbnail((width, width * 4))
        images.append(frame)
    if not images:
        raise RigError("描き出したコマがありません。")
    images[0].save(out, save_all=True, append_images=images[1:], duration=int(1000 / fps), loop=0)
    return out


def package(rig_dir: Path) -> Path:
    """部位の絵・rig.json・rig.blend を rig.zip にまとめる(ほかのツールへ持っていく用)。描き出したコマは消す。"""
    import zipfile

    frames = rig_dir / "frames"
    if frames.is_dir():
        shutil.rmtree(frames)
    out = rig_dir / "rig.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(rig_dir.rglob("*")):
            if path.is_file() and path.suffix != ".zip" and path.name not in ("preview.gif", "meta.json"):
                archive.write(path, path.relative_to(rig_dir).as_posix())
    return out
