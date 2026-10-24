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

from . import body_motions


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
    MeshGroup("arm.L", ("upper_arm.L", "forearm.L", "hand.L"), ("torso", "upper_arm.L", "forearm.L", "hand.L"), 2),
    MeshGroup("arm.R", ("upper_arm.R", "forearm.R", "hand.R"), ("torso", "upper_arm.R", "forearm.R", "hand.R"), 2),
)
# 服の層のメッシュは、同じグループの体のメッシュのすぐ手前(z + 1)に置く。名前は "outfit." + グループ名
OUTFIT_PREFIX = "outfit."

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
# 服の層: 素体の絵と服を着た絵の色の差(Lab の距離)がこれより大きい画素を服とみなす
_OUTFIT_DELTA = 12.0
# 服を着た絵の関節が、素体の絵からこれ(肩幅に対する割合)より動いていたら、ずれとして知らせる
OUTFIT_DRIFT_WARN = 0.06


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


def _group_meshes(
    mask: np.ndarray, labels: np.ndarray, j: dict[str, np.ndarray], *, prefix: str = "", z_offset: int = 0,
    texture: str = "texture.png", skip_empty: bool = False,
) -> list[dict]:
    """人物(mask)と部位の割り当て(labels)から、MESH_GROUPS ごとのメッシュ。"""
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
            if skip_empty:
                continue
            raise RigError(f"{group.name} の画素がありませんでした(骨格の点がずれているかもしれません)")
        verts, tris = _region_mesh(region, spacing)
        wts = weights(verts[:, 0], verts[:, 1], j, group.bones, anchor)
        groups.append({
            "name": prefix + group.name, "z": group.z + z_offset, "bones": list(group.bones), "texture": texture,
            "vertices": verts.round(1).tolist(), "triangles": tris.tolist(), "weights": wts.round(4).tolist(),
        })
    return groups


def build_meshes(
    mask: np.ndarray, points: dict[str, np.ndarray], outfit_mask: np.ndarray | None = None,
) -> tuple[dict[str, np.ndarray], list[dict]]:
    """
    (骨の端の点, メッシュのグループごとの {name, z, bones, texture, vertices, triangles, weights})。mask は人物(True)。
    outfit_mask(服の層の画素)があれば、服のメッシュも作る(骨・重みの決め方は体と同じ。服の無いグループは作らない)。
    """
    j = joints(points, mask)
    groups = _group_meshes(mask, assign(mask, j), j)
    if outfit_mask is not None and outfit_mask.any():
        groups += _group_meshes(
            outfit_mask, assign(outfit_mask, j), j, prefix=OUTFIT_PREFIX, z_offset=1, texture="outfit.png", skip_empty=True,
        )
    return j, groups


def outfit_region(base: Image.Image, outfit: Image.Image, base_mask: np.ndarray, outfit_mask: np.ndarray,
                  j: dict[str, np.ndarray]) -> np.ndarray:
    """
    服の層の画素: 服を着た絵の人物のうち、素体の絵と色が違う所と、素体の形の外にはみ出した所(スカートの裾など)。
    頭(顎の線より上)は入れない。細かい点は消し、服の中の小さな穴は埋める。
    """
    lab_a = cv2.cvtColor(np.asarray(base.convert("RGB")), cv2.COLOR_RGB2LAB).astype(np.float32)
    lab_b = cv2.cvtColor(np.asarray(outfit.convert("RGB")), cv2.COLOR_RGB2LAB).astype(np.float32)
    delta = np.linalg.norm(cv2.GaussianBlur(lab_a - lab_b, (5, 5), 0), axis=2)
    region = outfit_mask & ((delta > _OUTFIT_DELTA) | ~base_mask)
    h, w = region.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    region &= _head_share(xs, ys, j) < 0.5
    region = cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    region = cv2.morphologyEx(region, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    # 小さなかけら(色の揺れ)を消し、服の中の小さな穴(柄が素体と同じ色の所)を埋める
    area = int(outfit_mask.sum())
    count, comp, stats, _ = cv2.connectedComponentsWithStats(region, connectivity=8)
    keep = np.zeros(count, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= area * 0.002
    region = keep[comp]
    holes = (~region).astype(np.uint8)
    count, comp, stats, _ = cv2.connectedComponentsWithStats(holes, connectivity=4)
    small = np.zeros(count, bool)
    small[1:] = stats[1:, cv2.CC_STAT_AREA] < area * 0.01
    region |= small[comp] & outfit_mask
    return region


def detect(image: Image.Image) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """(人物(True), 確からしい骨格の点)。"""
    from .anime_seg import person_mask
    from .ugoira_rig import KEYPOINTS, detect_pose

    mask = person_mask(image) > 0.5
    if not mask.any():
        raise RigError("人物が見つかりませんでした。")
    ys, xs = np.nonzero(mask)
    skeleton = detect_pose(image, (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())))
    return mask, {n: p for n, p, s in zip(KEYPOINTS, skeleton.points, skeleton.scores) if s >= _MIN_SCORE}


def joint_drift(base: dict[str, np.ndarray], other: dict[str, np.ndarray]) -> float:
    """2枚の絵の関節(両方で取れた点)のずれの平均。肩幅に対する割合。"""
    shoulder = float(np.linalg.norm(base["left_shoulder"] - base["right_shoulder"]))
    common = [n for n in base if n in other]
    if not common:
        return float("inf")
    return float(np.mean([np.linalg.norm(base[n] - other[n]) for n in common]) / shoulder)


HAIR_MESH = "hair"
# 体の太さ(骨からの距離/骨の太さ)がこれより外の人物の画素は、髪につながっていれば髪とみなす。胴の縁はおよそ 0.8、
# 腕の縁はおよそ 0.8 なので、それより少し外(胴と腕の間に垂れた影の髪を拾うため、1.15 では取りこぼした)
_HAIR_OUTSIDE = 0.95
# 髪の層の重ねる順: 体(0)・服(1)より手前、腕(2)より奥
_HAIR_Z = 1.5


def _hair_bones(chains: list[dict]) -> list[dict]:
    """垂れた髪の左右ごとに、頭から2本の鎖の骨。"""
    bones = []
    for chain in chains:
        p = chain["points"]
        bones.append({"name": f"{chain['chain']}.1", "label": "髪", "parent": "head", "kind": "hair",
                      "chain": chain["chain"], "head": [float(v) for v in p[0]], "tail": [float(v) for v in p[1]]})
        bones.append({"name": f"{chain['chain']}.2", "label": "髪(毛先)", "parent": f"{chain['chain']}.1", "kind": "hair",
                      "chain": chain["chain"], "head": [float(v) for v in p[1]], "tail": [float(v) for v in p[2]]})
    return bones


def _hanging(hair_pixels: np.ndarray, person: np.ndarray, chains: list[dict], j: dict[str, np.ndarray]) -> np.ndarray:
    """
    髪の層にする画素: 目の高さより下の髪のうち、鎖を作った側のもの。髪の色で取った画素に、体の太さ(骨からの距離)より外へ
    はみ出した人物の画素で、髪につながっているものを足す(影で黒くなった長い髪の毛先は、色では取りこぼした)。
    """
    h, w = hair_pixels.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    eye = j["nose"][1] - 0.15 * (j["neck"][1] - j["nose"][1])
    outside = person & (_scores(xs, ys, j).min(axis=0) > _HAIR_OUTSIDE) & (ys > eye)
    union = (hair_pixels | outside).astype(np.uint8)
    count, labels = cv2.connectedComponents(union, connectivity=8)
    keep = set(np.unique(labels[hair_pixels]).tolist()) - {0}
    grown = np.isin(labels, list(keep))
    out = np.zeros_like(hair_pixels)
    for chain in chains:
        side = xs > j["nose"][0] if chain["chain"] == "hair.L" else xs < j["nose"][0]
        out |= grown & side & (ys > eye)
    return out


def _hair_mesh(hanging: np.ndarray, bones: list[dict], j: dict[str, np.ndarray]) -> dict:
    """髪の層のメッシュ。根もとは頭の重み、下へ行くほど鎖の骨へ、鎖の中では毛先ほど2本目へ。"""
    shoulder = float(np.linalg.norm(j["left_shoulder"] - j["right_shoulder"]))
    region = cv2.dilate(hanging.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))).astype(bool)
    verts, tris = _region_mesh(region, _GRID * shoulder)
    names = ["head"]
    columns = [np.ones(len(verts))]
    for chain in sorted({b["chain"] for b in bones}):
        seg1 = next(b for b in bones if b["name"] == f"{chain}.1")
        seg2 = next(b for b in bones if b["name"] == f"{chain}.2")
        root, mid, tip = np.array(seg1["head"]), np.array(seg1["tail"]), np.array(seg2["tail"])
        side = (verts[:, 0] > j["nose"][0]) if chain == "hair.L" else (verts[:, 0] < j["nose"][0])
        length = max(tip[1] - root[1], 1.0)
        share = np.clip((verts[:, 1] - root[1]) / (0.3 * length), 0.0, 1.0) * side
        along = np.clip((verts[:, 1] - mid[1]) / (0.4 * length) + 0.5, 0.0, 1.0)
        columns = [c * (1 - share) for c in columns] + [share * (1 - along), share * along]
        names += [seg1["name"], seg2["name"]]
    table = np.column_stack(columns)
    table /= table.sum(axis=1, keepdims=True)
    return {
        "name": HAIR_MESH, "z": _HAIR_Z, "bones": names, "texture": "hair.png",
        "vertices": verts.round(1).tolist(), "triangles": tris.tolist(), "weights": table.round(4).tolist(),
    }


def _add_breasts(meshes: list[dict], j: dict[str, np.ndarray]) -> list[dict]:
    """胸の骨(左右1本ずつ。上向きの骨で、移動で揺らす)を作り、体と服の層の体のメッシュに、中心から半径の中で重みを足す。"""
    from . import body_physics

    bones = []
    for b in body_physics.breast_points(j):
        cx, cy = b["center"]
        bones.append({"name": b["name"], "label": "胸", "parent": "torso", "kind": "breast",
                      "head": [float(cx), float(cy)], "tail": [float(cx), float(cy - b["radius"] * 0.6)],
                      "radius": float(b["radius"])})
        for mesh in meshes:
            if mesh["name"] not in ("body", OUTFIT_PREFIX + "body"):
                continue
            verts = np.array(mesh["vertices"], dtype=float)
            d = np.hypot(verts[:, 0] - cx, verts[:, 1] - cy) / b["radius"]
            share = np.clip(1 - d * d, 0.0, 1.0) ** 2 * 0.85
            table = np.column_stack([np.array(mesh["weights"], dtype=float) * (1 - share)[:, None], share])
            mesh["bones"] = mesh["bones"] + [b["name"]]
            mesh["weights"] = (table / table.sum(axis=1, keepdims=True)).round(4).tolist()
    return bones


def _fill_behind_hair(image: Image.Image, person: np.ndarray, hanging: np.ndarray, j: dict[str, np.ndarray]
                      ) -> tuple[Image.Image, np.ndarray]:
    """
    髪の層を外したあとの絵と人物の範囲。髪の下にあった所は、まわりから描いて埋める(LaMa)。体の内側(骨からの距離が体の
    太さの中・顔の幅の中)は人物のまま、外側(体の横に垂れていた髪の所)は背景にする(髪が動くと、体の横は背景が見える)。
    """
    shoulder = float(np.linalg.norm(j["left_shoulder"] - j["right_shoulder"]))
    from .inpaint_local import inpaint

    hole = cv2.dilate(hanging.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & person
    rest = person & ~hole
    h, w = person.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    body = (_scores(xs, ys, j).min(axis=0) <= 1.0) | ((_head_share(xs, ys, j) >= 0.5) & (np.abs(xs - j["nose"][0]) < 0.4 * shoulder))
    k = max(3, int(shoulder * 0.12)) | 1
    closed = cv2.morphologyEx(rest.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    inside = hole & (closed.astype(bool) | body)
    # 体の外になる所は先に背景の色で塗り(髪の残りを、埋める絵の手がかりにしないように)、体の内側の穴だけを、
    # 穴ごとに切り出して埋める(全部まとめて埋めると、背景に引っぱられて白く埋まった)
    pixels = np.asarray(image.convert("RGB")).copy()
    background = ~person
    if background.any():
        pixels[hole & ~inside] = np.median(pixels[background], axis=0).astype(np.uint8)
    count, labels = cv2.connectedComponents(inside.astype(np.uint8), connectivity=8)
    for index in range(1, count):
        pixels = inpaint(pixels, labels == index)
    return Image.fromarray(pixels), rest | inside


def build(source: Path, out_dir: Path, outfit: Path | None = None, *, hair: bool = False, breasts: bool = False,
          wind: float = 0.0) -> dict:
    """
    source(Aポーズの絵)から、out_dir に texture.png・rig.json・overlay.png(確かめ用)を書く。outfit(同じ Aポーズで服を
    着た絵)があれば、服の層(outfit.png と服のメッシュ)も作る。骨は素体の絵の関節で決める(服の層も同じ骨で動く)。
    hair は垂れた髪を髪の層(hair.png と髪のメッシュ・鎖の骨)に分け、breasts は胸の骨を足す(揺れものの骨。胸は、成人キャラ
    だけと呼び出し側で決める)。wind は風の強さ(0〜1)。揺れは、動きを描き出すときに body_physics.bake で足す。
    """
    from . import body_physics

    image = Image.open(source).convert("RGB")
    original = image
    mask, points = detect(image)
    j = joints(points, mask)
    outfit_image = None
    if outfit is not None:
        outfit_image = Image.open(outfit).convert("RGB")
        if outfit_image.size != image.size:
            raise RigError("服を着た絵と素体の絵の大きさが違います。")

    # 髪の層: 垂れた髪を外し、下の体を埋める(服を着た絵も同じ所を埋める)。髪の骨は素体の絵の髪から
    hair_bones: list[dict] = []
    hanging = np.zeros(mask.shape, bool)
    if hair:
        hair_pixels = hair_mask(image, mask, points)
        chains = body_physics.hair_chains(hair_pixels, j)
        hanging = _hanging(hair_pixels, mask, chains, j)
        if hanging.any():
            hair_bones = _hair_bones(chains)
            image, mask = _fill_behind_hair(original, mask, hanging, j)

    outfit_mask = None
    visible = mask
    drift = None
    if outfit_image is not None:
        person, outfit_points = detect(outfit_image)
        drift = joint_drift(points, outfit_points)
        if hanging.any():
            outfit_image, person = _fill_behind_hair(outfit_image, person, hanging, j)
        outfit_mask = outfit_region(image, outfit_image, mask, person, j)
        # 服を着た絵で背景の所は、体の層も見せない(服が素体より細い所で、素体の縁が服の横にはみ出して見えた)
        visible = mask & person
    j, meshes = build_meshes(mask, points, outfit_mask)
    extra = list(hair_bones)
    if hair_bones:
        meshes.append(_hair_mesh(hanging, hair_bones, j))
    if breasts:
        extra += _add_breasts(meshes, j)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    # テクスチャ: 元の絵の背景を透明に(人物の縁は anime_seg の確からしさでなめらかに)
    soft = cv2.GaussianBlur(visible.astype(np.float32), (3, 3), 0)
    Image.fromarray(np.dstack([np.asarray(image), (soft * 255).astype(np.uint8)]), "RGBA").save(out_dir / "texture.png")
    if outfit_image is not None and outfit_mask is not None:
        soft = cv2.GaussianBlur(outfit_mask.astype(np.float32), (3, 3), 0)
        Image.fromarray(np.dstack([np.asarray(outfit_image), (soft * 255).astype(np.uint8)]), "RGBA").save(out_dir / "outfit.png")
    if hair_bones:
        soft = cv2.GaussianBlur(hanging.astype(np.float32), (3, 3), 0)
        Image.fromarray(np.dstack([np.asarray(original), (soft * 255).astype(np.uint8)]), "RGBA").save(out_dir / "hair.png")
    rig = {
        "source": source.name,
        "size": list(image.size),
        "texture": "texture.png",
        "outfit": outfit.name if outfit is not None else None,
        "outfit_drift": round(drift, 4) if drift is not None else None,
        # 関節の点(絵の画素)。書き出し(body_export の PSD)で、メッシュと同じ部位の割り当てをするのに使う
        "joints": {k: [round(float(v[0]), 2), round(float(v[1]), 2)] for k, v in j.items()},
        "bones": [
            {"name": b.name, "label": b.label, "parent": b.parent,
             "head": [float(v) for v in j[b.head]], "tail": [float(v) for v in j[b.tail]]}
            for b in BONES
        ] + extra,
        "meshes": meshes,
        # 揺れもの: 作った骨(髪の鎖・胸)と風の強さ。動きを描き出すときに body_physics.bake で揺れを足す
        "physics": {
            "hair": sorted({b["chain"] for b in extra if b.get("kind") == "hair"}),
            "breasts": any(b.get("kind") == "breast" for b in extra),
            "wind": wind,
        },
    }
    (out_dir / "rig.json").write_text(json.dumps(rig, ensure_ascii=False), encoding="utf-8")
    _overlay(outfit_image or original, rig).save(out_dir / "overlay.png")
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
            # 揺れもの(髪・胸)の骨は白
            cv2.polylines(out, [verts[tri].round().astype(np.int32)], True, colors.get(bone, (255, 255, 255)), 1, cv2.LINE_AA)
    for bone in rig["bones"]:
        a = tuple(int(v) for v in bone["head"])
        b = tuple(int(v) for v in bone["tail"])
        cv2.line(out, a, b, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.circle(out, a, 5, (255, 40, 40), -1)
    return Image.fromarray(out)


# ---- Blender ----

_BLENDER_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "blender" / "build_rig.py"
# 動きの名前(body_motions.py に中身。Blender へは motion.json で渡す)
MOTIONS = body_motions.labels()


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
    if motion not in body_motions.MOTIONS:
        raise RigError(f"動きの種類が違います: {motion}")
    from . import body_physics

    rig = json.loads((rig_dir / "rig.json").read_text(encoding="utf-8"))
    # 揺れもの(髪・胸)の骨があれば、動きに揺れのキーを足して渡す
    wind = float(rig.get("physics", {}).get("wind", 0.0))
    baked = body_physics.bake(rig, body_motions.MOTIONS[motion].to_json(), wind=wind)
    motion_file = rig_dir / "motion.json"
    motion_file.write_text(json.dumps({"name": motion, **baked}, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(
        [blender, "--background", "--factory-startup", "--python", str(_BLENDER_SCRIPT), "--",
         str(rig_dir), str(motion_file)],
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


def outfit_inpaint_mask(image: Image.Image) -> tuple[Image.Image, dict[str, np.ndarray]]:
    """
    服を着せるときに描き直す範囲(白)と、その絵の骨格の点。人物の顎の線より下を、肩幅の 0.15 倍だけ外へ広げた範囲
    (スカートや袖が体の形の外へ出られるように)。頭・髪は描き直さない(顔が変わらないように)。
    """
    mask, points = detect(image)
    j = joints(points, mask)
    shoulder = float(np.linalg.norm(j["left_shoulder"] - j["right_shoulder"]))
    grow = max(3, int(shoulder * 0.15)) | 1
    region = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow))).astype(bool)
    h, w = mask.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    region &= _head_share(xs, ys, j) < 0.5
    return Image.fromarray((region * 255).astype(np.uint8)).convert("RGB"), points


# 描き出したコマの品質: 休みの姿勢(1コマ目)より増えた穴・かけらの大きさが、人物の面積のこの割合を超えたら問題とみなす
_GAP_RATIO = 0.002
_FRAGMENT_PIXELS = 24
# 関節のすき間とみなす穴の太さ(中心から縁までの画素)の上限
_SLIT_WIDTH = 4.0


def _silhouette_issues(alpha: np.ndarray) -> tuple[int, int]:
    """(人物の形の中の穴の画素数, 本体から離れたかけらの画素数)。alpha は 0〜255。"""
    solid = (alpha > 128).astype(np.uint8)
    count, comp, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    if count <= 1:
        return 0, 0
    main = 1 + int(stats[1:, cv2.CC_STAT_AREA].argmax())
    fragments = int(sum(a for i, a in enumerate(stats[:, cv2.CC_STAT_AREA]) if i not in (0, main) and a >= _FRAGMENT_PIXELS))
    # 穴: 背景(外側)につながっていない透明の画素のうち、細い筋(いちばん太い所が _SLIT_WIDTH 以下)だけ。腕が腰に触れたり
    # 脚が寄ったりして囲まれた、ふつうのすき間(太い)は数えない
    background = (1 - solid).astype(np.uint8)
    count_bg, comp_bg, stats_bg, _ = cv2.connectedComponentsWithStats(background, connectivity=4)
    border = set(np.unique(np.concatenate([comp_bg[0], comp_bg[-1], comp_bg[:, 0], comp_bg[:, -1]])).tolist())
    distance = cv2.distanceTransform(background, cv2.DIST_L2, 3)
    holes = 0
    for i in range(1, count_bg):
        if i in border or stats_bg[i, cv2.CC_STAT_AREA] < _FRAGMENT_PIXELS:
            continue
        if float(distance[comp_bg == i].max()) <= _SLIT_WIDTH:
            holes += int(stats_bg[i, cv2.CC_STAT_AREA])
    return holes, fragments


def frame_quality(frames: list[Path]) -> dict:
    """
    描き出したコマの品質。休みの姿勢(1コマ目)と比べて、動かしたときに増えた、本体から離れたかけら(はみ出した筋・取り残された
    縁)と、人物の形の中の細い穴(関節のすき間)を数える。かけらは問題(ok が False)。細い穴は、腕と胴の間が囲まれただけの
    こともあるので、確かめる候補として返すだけ(gap_frames)。
    {"ok", "worst_gap", "worst_fragment", "frames": かけらの出たコマ, "gap_frames": 細い穴の出たコマ}(コマは 1 から)。
    """
    if not frames:
        return {"ok": True, "worst_gap": 0.0, "worst_fragment": 0, "frames": [], "gap_frames": []}
    alphas = [np.asarray(Image.open(f).convert("RGBA"))[..., 3] for f in frames]
    area = max(int((alphas[0] > 128).sum()), 1)
    rest_holes, rest_fragments = _silhouette_issues(alphas[0])
    worst_gap, worst_fragment, bad, gaps = 0.0, 0, [], []
    for index, alpha in enumerate(alphas, start=1):
        holes, fragments = _silhouette_issues(alpha)
        gap = max(0, holes - rest_holes) / area
        fragment = max(0, fragments - rest_fragments)
        worst_gap, worst_fragment = max(worst_gap, gap), max(worst_fragment, fragment)
        if fragment > 0:
            bad.append(index)
        if gap > _GAP_RATIO:
            gaps.append(index)
    return {
        "ok": not bad, "worst_gap": round(worst_gap, 5), "worst_fragment": worst_fragment, "frames": bad, "gap_frames": gaps,
    }


# ---- 揺れもの(髪・胸)の骨 ----

# 髪の色とみなす色の差(Lab の距離)。頭のてっぺんの髪の色からこれ以内
_HAIR_DELTA = 28.0


def hair_mask(image: Image.Image, mask: np.ndarray, points: dict[str, np.ndarray]) -> np.ndarray:
    """
    髪の範囲。うごイラと同じ点の指定で切り出し(ugoira_rig._hair_by_points)、頭のてっぺんの髪の色に近い画素だけにして、
    てっぺんにつながる所だけ残す(首・胸の服や、目などへのはみ出しを外す)。見つからなければ空。
    """
    from .manga_v2.detect import detect_heads_in_image
    from .ugoira_rig import KEYPOINTS, Skeleton, _hair_by_points
    from .ugoira_rig import RigError as UgoiraRigError

    heads = detect_heads_in_image(image)
    if not heads:
        return np.zeros(mask.shape, bool)
    head = max(heads, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    skeleton = Skeleton(
        np.array([points.get(n, (0.0, 0.0)) for n in KEYPOINTS], np.float32),
        np.array([1.0 if n in points else 0.0 for n in KEYPOINTS], np.float32),
    )
    try:
        hair = _hair_by_points(image, head, mask, skeleton)
    except UgoiraRigError:
        return np.zeros(mask.shape, bool)
    lab = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2LAB).astype(np.float32)
    x0, y0, x1, y1 = (int(v) for v in head)
    top = np.zeros_like(hair)
    top[y0:y0 + max(1, (y1 - y0) // 4), x0:x1] = True
    sample = lab[hair & top]
    if len(sample) < 20:
        return np.zeros(mask.shape, bool)
    color = np.median(sample, axis=0)
    # 色のある髪(茶・赤など)は、影で明るさが大きく変わるので明るさの差を軽く見る。黒・白・灰の髪は色味が服(灰色の素体
    # スーツ)と同じなので、明るさで分ける
    chroma = float(np.hypot(color[1] - 128, color[2] - 128))
    weight = np.array([0.35 if chroma > 12 else 1.0, 1.0, 1.0], np.float32)
    to_hair = np.linalg.norm((lab - color) * weight, axis=2)
    hair &= to_hair < _HAIR_DELTA
    # 体(お腹のあたり)の色のほうが近い画素は外す(髪と同じ系統の色の服へのはみ出し)
    need = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    if all(n in points for n in need):
        shoulder_mid = (points["left_shoulder"] + points["right_shoulder"]) / 2
        hip_mid = (points["left_hip"] + points["right_hip"]) / 2
        cx, cy = (shoulder_mid + (hip_mid - shoulder_mid) * 0.65).astype(int)
        r = max(4, int(np.linalg.norm(points["left_shoulder"] - points["right_shoulder"]) * 0.08))
        body = np.median(lab[max(cy - r, 0):cy + r, max(cx - r, 0):cx + r].reshape(-1, 3), axis=0)
        hair &= to_hair < np.linalg.norm((lab - body) * weight, axis=2)
    hair = cv2.morphologyEx(hair.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, labels = cv2.connectedComponents(hair)
    keep = set(np.unique(labels[top & (hair > 0)]).tolist()) - {0}
    return np.isin(labels, list(keep))
