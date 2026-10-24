"""
素体の2Dモデル(body_rig.py の rig.json)を、ほかの 2D アニメのツールへ持っていく形に書き出す。

- Spine(4.1 の JSON): 骨・スロット・メッシュ(重み付き。体・腕・服の層)・アトラス・動き(body_motions の全部)。
  座標は Spine に合わせて y を上にし、原点を絵の下端の中央に置く。メッシュの頂点は、重みを持つ骨ごとの「骨から見た位置」で書く
  (Spine の重み付きメッシュの形式)。輪郭(hull)の頂点を先頭に並べ直す
- Live2D(Cubism Editor に読み込む PSD): 部位(頭・胴・上腕・前腕・手・大腿・下腿・足)ごとのレイヤー。体と服の層はフォルダを分ける。
  Live2D のモデル(.moc3)は Cubism Editor でしか作れないので、Cubism が読み込む素材(部位ごとの PSD)までを書き出す
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from . import body_motions

SPINE_VERSION = "4.1.00"
_ROOT = "root"


def _to_spine(point: list[float] | np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """絵の画素(y が下)を、Spine の座標(y が上、原点は絵の下端の中央)に。"""
    w, h = size
    return np.array([float(point[0]) - w / 2, h - float(point[1])])


def _rotate(v: np.ndarray, degrees: float) -> np.ndarray:
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def _world_bones(rig: dict) -> dict[str, dict]:
    """骨ごとの Spine 座標での根もと・向き(度)・長さ。"""
    size = tuple(rig["size"])
    out = {}
    for bone in rig["bones"]:
        head = _to_spine(bone["head"], size)
        tail = _to_spine(bone["tail"], size)
        d = tail - head
        out[bone["name"]] = {
            "head": head, "angle": math.degrees(math.atan2(d[1], d[0])), "length": float(np.hypot(*d)),
            "parent": bone["parent"],
        }
    return out


def hull_first(vertices: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """
    輪郭(三角形1つにしか使われない辺がつくる輪)のうち、いちばん長い輪の頂点を先頭に並べ直す(Spine のメッシュは
    先頭 hull 個が輪郭)。(並べ直した頂点の番号の順, 付け直した三角形, hull の数)。
    """
    edges: dict[tuple[int, int], int] = {}
    for tri in triangles:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            key = (min(a, b), max(a, b))
            edges[key] = edges.get(key, 0) + 1
    neighbors: dict[int, list[int]] = {}
    for (a, b), count in edges.items():
        if count == 1:
            neighbors.setdefault(int(a), []).append(int(b))
            neighbors.setdefault(int(b), []).append(int(a))
    loops: list[list[int]] = []
    seen: set[int] = set()
    for start in neighbors:
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        prev, cur = None, start
        while True:
            nxt = next((n for n in neighbors[cur] if n != prev and n not in seen), None)
            if nxt is None:
                break
            loop.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt
        loops.append(loop)
    hull = max(loops, key=len) if loops else []
    rest = [i for i in range(len(vertices)) if i not in set(hull)]
    order = np.array(hull + rest, dtype=int)
    remap = np.empty(len(vertices), dtype=int)
    remap[order] = np.arange(len(order))
    return order, remap[triangles], len(hull)


def spine_skeleton(rig: dict) -> dict:
    """rig.json を Spine 4.1 の JSON(骨・スロット・メッシュ・動き)に。"""
    w, h = rig["size"]
    world = _world_bones(rig)
    names = [_ROOT] + [b["name"] for b in rig["bones"]]
    bones = [{"name": _ROOT}]
    for bone in rig["bones"]:
        me = world[bone["name"]]
        parent = bone["parent"] or _ROOT
        if parent == _ROOT:
            parent_head, parent_angle = np.zeros(2), 0.0
        else:
            parent_head, parent_angle = world[parent]["head"], world[parent]["angle"]
        local = _rotate(me["head"] - parent_head, -parent_angle)
        bones.append({
            "name": bone["name"], "parent": parent, "length": round(me["length"], 2),
            "rotation": round(me["angle"] - parent_angle, 3), "x": round(float(local[0]), 2), "y": round(float(local[1]), 2),
        })

    slots, attachments = [], {}
    for mesh in sorted(rig["meshes"], key=lambda m: m["z"]):
        verts = np.array(mesh["vertices"], dtype=float)
        tris = np.array(mesh["triangles"], dtype=int)
        wts = np.array(mesh["weights"], dtype=float)
        order, tris, hull = hull_first(verts, tris)
        verts, wts = verts[order], wts[order]
        data: list[float] = []
        for v, row in zip(verts, wts):
            used = [(b, float(wt)) for b, wt in zip(mesh["bones"], row) if wt > 0.001]
            total = sum(wt for _, wt in used)
            data.append(len(used))
            point = _to_spine(v, (w, h))
            for bone_name, wt in used:
                local = _rotate(point - world[bone_name]["head"], -world[bone_name]["angle"])
                data += [names.index(bone_name), round(float(local[0]), 2), round(float(local[1]), 2), round(wt / total, 4)]
        region = Path(mesh.get("texture", rig["texture"])).stem
        slots.append({"name": mesh["name"], "bone": _ROOT, "attachment": mesh["name"]})
        attachments[mesh["name"]] = {mesh["name"]: {
            "type": "mesh", "path": region, "width": w, "height": h, "hull": hull,
            "uvs": [round(float(c), 5) for v in verts for c in (v[0] / w, v[1] / h)],
            "triangles": [int(i) for i in tris.reshape(-1)],
            "vertices": data,
        }}

    from . import body_physics

    animations = {}
    wind = float(rig.get("physics", {}).get("wind", 0.0))
    for key, motion in body_motions.MOTIONS.items():
        # 揺れもの(髪・胸)の揺れを足し、移動は Spine の形(親の骨から見た移動)に直す
        baked = body_physics.bake(rig, motion.to_json(), wind=wind)
        timelines: dict[str, dict] = {}
        for bone, points in baked["rotate"].items():
            timelines.setdefault(bone, {})["rotate"] = [
                {"time": round((f - 1) / body_motions.FPS, 4), "value": v} for f, v in points
            ]
        for bone, points in body_physics.to_spine_translate(rig, baked).items():
            timelines.setdefault(bone, {})["translate"] = [
                {"time": round((f - 1) / body_motions.FPS, 4), "x": dx, "y": dy} for f, dx, dy in points
            ]
        animations[key] = {"bones": timelines}

    return {
        "skeleton": {"spine": SPINE_VERSION, "x": -w / 2, "y": 0, "width": w, "height": h, "images": "./"},
        "bones": bones,
        "slots": slots,
        "skins": [{"name": "default", "attachments": attachments}],
        "animations": animations,
    }


def spine_atlas(rig: dict) -> str:
    """テクスチャ1枚を1つの領域にしたアトラス(体と服の層で1ページずつ)。"""
    w, h = rig["size"]
    textures = sorted({m.get("texture", rig["texture"]) for m in rig["meshes"]})
    pages = [f"{t}\nsize:{w},{h}\nfilter:Linear,Linear\n{Path(t).stem}\nbounds:0,0,{w},{h}\n" for t in textures]
    return "\n".join(pages)


def export_spine(rig_dir: Path, rig: dict) -> Path:
    """rig_dir/spine.zip(skeleton.json・skeleton.atlas・テクスチャ)を書く。"""
    out = rig_dir / "spine.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("skeleton.json", json.dumps(spine_skeleton(rig), ensure_ascii=False))
        archive.writestr("skeleton.atlas", spine_atlas(rig))
        for texture in sorted({m.get("texture", rig["texture"]) for m in rig["meshes"]}):
            archive.write(rig_dir / texture, texture)
    return out


# Live2D 用の PSD のレイヤーの並び(上が手前)。フォルダ(体・服の層)ごとに、この順で部位を並べる
_PSD_ORDER = (
    ("arm.L", ("hand.L", "forearm.L", "upper_arm.L")),
    ("arm.R", ("hand.R", "forearm.R", "upper_arm.R")),
    ("body", ("head", "torso", "thigh.L", "thigh.R", "shin.L", "shin.R", "foot.L", "foot.R")),
)


def export_psd(rig_dir: Path, rig: dict) -> Path:
    """
    rig_dir/model.psd(Cubism Editor に読み込む、部位ごとのレイヤー)を書く。画素の部位は、メッシュを作ったときと同じ
    割り当て(body_rig.assign)で決める。服の層があれば、体のフォルダの手前に服のフォルダを置く。
    """
    from psd_tools import PSDImage
    from psd_tools.api.layers import Group, PixelLayer

    from .body_rig import BONES, assign

    joints = {k: np.array(v, dtype=np.float32) for k, v in rig["joints"].items()}
    labels_by_bone = {b.name: i for i, b in enumerate(BONES)}
    w, h = rig["size"]
    psd = PSDImage.new("RGBA", (w, h))
    layers = [("body", rig["texture"])]
    if any(m["name"].startswith("outfit.") for m in rig["meshes"]):
        layers.append(("outfit", "outfit.png"))
    # 先に入れたものが下になる(体 → 服)。フォルダの中も、下(奥)の部位から入れる
    for folder_name, texture in layers:
        image = np.asarray(Image.open(rig_dir / texture).convert("RGBA"))
        labels = assign(image[..., 3] > 8, joints)
        folder = Group.new(psd, folder_name)
        for _, parts in reversed(_PSD_ORDER):
            for part in reversed(parts):
                own = labels == labels_by_bone[part]
                if not own.any():
                    continue
                ys, xs = np.nonzero(own)
                y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
                crop = image[y0:y1, x0:x1].copy()
                crop[..., 3] = np.where(own[y0:y1, x0:x1], crop[..., 3], 0)
                # レイヤー名は英字だけ(PSD の古い形式の名前は日本語を入れられない)
                folder.append(PixelLayer.frompil(Image.fromarray(crop, "RGBA"), psd, part, int(y0), int(x0)))
    # 髪の層(垂れた髪。揺れものの骨で動かす)は、体・服のフォルダの手前に1枚で置く
    if (rig_dir / "hair.png").is_file() and any(m["name"] == "hair" for m in rig["meshes"]):
        hair = np.asarray(Image.open(rig_dir / "hair.png").convert("RGBA"))
        ys, xs = np.nonzero(hair[..., 3] > 8)
        if len(ys):
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            folder = Group.new(psd, "hair")
            folder.append(PixelLayer.frompil(Image.fromarray(hair[y0:y1, x0:x1], "RGBA"), psd, "hair", int(y0), int(x0)))
    out = rig_dir / "model.psd"
    psd.save(out)
    return out


def export_all(rig_dir: Path) -> dict[str, Path]:
    """Spine と Live2D(PSD)の書き出し。rig.json に関節(joints)の無い古いリグは、PSD を作らない。"""
    rig = json.loads((rig_dir / "rig.json").read_text(encoding="utf-8"))
    out = {"spine.zip": export_spine(rig_dir, rig)}
    if rig.get("joints"):
        out["model.psd"] = export_psd(rig_dir, rig)
    return out
