"""
Blender の中で動かすスクリプト(model_3d.dress から、画面なしで呼ぶ)。

    blender --background <model_dir>/model.blend --python scripts/blender/dress_model.py -- <model_dir> <garments.json> <動きの名前>

rig_model.py が保存した model.blend(体 body・骨 rig)の、休んだ姿勢の体に、garments.json の服(今はスカートだけ。
garment_skirt.py)を布として着せる。服は骨に付ける: 服の頂点ごとに、いちばん近い体の面の骨の重みを写し、服の上で
なめらかにする(脚の間で布が裂けないように)。骨と動き入りの model_dressed.glb・model_dressed.blend を書き、確かめの
静止画(dressed_front/side/back.png)と、動きのコマ(frames_dressed/*.png)を描き出し、結果を dress.json に書く。
model.blend は書き換えない。
"""

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Euler, Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import garment_skirt  # noqa: E402

SIZE = (360, 600)
# 服の重みをなめらかにする回数と、1 頂点に残す骨の数
SMOOTH_ROUNDS = 30
MAX_BONES = 4
# 服(スカート)を付ける骨。腕・手に付けると、腕を振ったときにスカートが引っ張られる
GARMENT_BONES = ("torso", "thigh.L", "thigh.R", "shin.L", "shin.R")


def rest_pose(arm: bpy.types.Object) -> None:
    """動きを止めて、骨を入れた姿勢にする(服は休んだ姿勢の体に着せる)。"""
    if arm.animation_data:
        arm.animation_data.action = None
        for track in arm.animation_data.nla_tracks:
            track.mute = True
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)


def transfer_weights(garment: bpy.types.Object, body: bpy.types.Object, arm: bpy.types.Object) -> dict:
    """服の頂点ごとに、いちばん近い体の面(三角形)の頂点の骨の重みを混ぜて写し、服の上でなめらかにする。"""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(dg)
    mesh = ev.to_mesh()
    mesh.calc_loop_triangles()
    co = np.array([body.matrix_world @ v.co for v in mesh.vertices])
    tris = np.array([t.vertices[:] for t in mesh.loop_triangles])
    ev.to_mesh_clear()
    bones = [b.name for b in arm.data.bones if b.use_deform and b.name in GARMENT_BONES]
    groups = {g.index: g.name for g in body.vertex_groups if g.name in bones}
    names = sorted(set(groups.values()))
    col = {n: i for i, n in enumerate(names)}
    body_w = np.zeros((len(body.data.vertices), len(names)))
    for v in body.data.vertices:
        for g in v.groups:
            if g.group in groups:
                body_w[v.index, col[groups[g.group]]] = g.weight
    # 近い面は、服を付ける骨にいちばん重く付いた体の面からだけ探す(手・腕の面を拾わないように)
    all_groups = {g.index: g.name for g in body.vertex_groups}
    main = np.zeros(len(body.data.vertices), dtype=bool)
    for v in body.data.vertices:
        if v.groups:
            top = max(v.groups, key=lambda g: g.weight)
            main[v.index] = all_groups.get(top.group) in GARMENT_BONES
    tris = tris[main[tris].all(axis=1)]
    bvh = BVHTree.FromPolygons([tuple(p) for p in co], [tuple(map(int, t)) for t in tris])
    gco = np.array([garment.matrix_world @ v.co for v in garment.data.vertices])
    w = np.zeros((len(gco), len(names)))
    for i, p in enumerate(gco):
        loc, _, tri, _ = bvh.find_nearest(Vector(p))
        if loc is None:
            continue
        a, b, c = (co[k] for k in tris[tri])
        # 三角形の上の点の重心座標
        v0, v1, v2 = b - a, c - a, np.array(loc) - a
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01 or 1e-12
        bv, bw = (d11 * d20 - d01 * d21) / den, (d00 * d21 - d01 * d20) / den
        bary = np.clip([1 - bv - bw, bv, bw], 0, 1)
        w[i] = bary @ body_w[tris[tri]]
    edges = np.array([e.vertices[:] for e in garment.data.edges])
    deg = np.bincount(edges.ravel(), minlength=len(gco)).astype(float)[:, None]
    deg[deg == 0] = 1
    for _ in range(SMOOTH_ROUNDS):
        total = np.zeros_like(w)
        np.add.at(total, edges[:, 0], w[edges[:, 1]])
        np.add.at(total, edges[:, 1], w[edges[:, 0]])
        w = 0.5 * w + 0.5 * total / deg
    # 1 頂点に MAX_BONES 本まで、合わせて 1 に
    order = np.argsort(-w, axis=1)
    keep = np.zeros_like(w, dtype=bool)
    np.put_along_axis(keep, order[:, :MAX_BONES], True, axis=1)
    w = np.where(keep & (w > 0.01), w, 0.0)
    w /= np.maximum(w.sum(axis=1, keepdims=True), 1e-9)
    for n in names:
        garment.vertex_groups.new(name=n)
    for i in range(len(gco)):
        for k in np.nonzero(w[i])[0]:
            garment.vertex_groups[names[k]].add([i], float(w[i, k]), "REPLACE")
    mod = garment.modifiers.new("rig", "ARMATURE")
    mod.object = arm
    garment.parent = arm
    garment.matrix_parent_inverse = arm.matrix_world.inverted()
    used = w.sum(axis=0)
    return {names[k]: round(float(used[k] / len(gco)), 3) for k in np.argsort(-used)[:6] if used[k] > 0}


def render(arm: bpy.types.Object, folder: Path, motion: str, frames: int, height: float) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    scene.render.resolution_x, scene.render.resolution_y = SIZE
    scene.render.film_transparent = True
    cam = bpy.data.objects.new("dress_camera", bpy.data.cameras.new("dress_camera"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = height * 1.1
    scene.collection.objects.link(cam)
    scene.camera = cam
    for name, deg in (("front", 0), ("side", 90), ("back", 180)):
        a = math.radians(deg)
        cam.location = (math.sin(a) * height * 3, -math.cos(a) * height * 3, height / 2)
        cam.rotation_euler = Euler((math.radians(90), 0, a))
        scene.render.filepath = str(folder / f"dressed_{name}.png")
        bpy.ops.render.render(write_still=True)
    action = bpy.data.actions.get(motion)
    if action is None:
        return
    cam.data.ortho_scale = height * 1.6
    a = math.radians(30)
    cam.location = (math.sin(a) * height * 3, -math.cos(a) * height * 3, height * 0.55)
    cam.rotation_euler = Euler((math.radians(90), 0, a))
    arm.animation_data.action = action
    out = folder / "frames_dressed"
    out.mkdir(exist_ok=True)
    for frame in range(1, frames + 1, 2):
        scene.frame_set(frame)
        scene.render.filepath = str(out / f"{frame:03d}.png")
        bpy.ops.render.render(write_still=True)
    arm.animation_data.action = None
    bpy.data.objects.remove(cam)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    folder, spec_path, motion = Path(argv[0]), Path(argv[1]), argv[2]
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    body = bpy.data.objects["body"]
    arm = bpy.data.objects["rig"]
    rest_pose(arm)
    bpy.context.scene.frame_set(1)
    joints = {k: v for k, v in spec["joints"].items()}
    height = max((body.matrix_world @ v.co).z for v in body.data.vertices)
    body.color = (0.82, 0.82, 0.82, 1)
    report = {"garments": []}
    colors = [(0.25, 0.32, 0.6, 1), (0.6, 0.3, 0.3, 1), (0.3, 0.55, 0.35, 1)]
    for n, item in enumerate(spec["garments"]):
        if item["kind"] != "skirt":
            raise SystemExit(f"まだ作れない服です: {item['kind']}")
        obj, info = garment_skirt.build_skirt(body, joints, item.get("config", {}), name=f"garment_{item['name']}")
        info["bones"] = transfer_weights(obj, body, arm)
        info["name"] = item["name"]
        info["kind"] = item["kind"]
        obj.color = colors[n % len(colors)]
        report["garments"].append(info)
    # 動きを戻して書き出す(GLB には全部の動き)
    for track in arm.animation_data.nla_tracks:
        track.mute = False
    bpy.ops.export_scene.gltf(
        filepath=str(folder / "model_dressed.glb"), export_format="GLB", export_animations=True,
        export_animation_mode="NLA_TRACKS", export_morph_normal=True,
    )
    for track in arm.animation_data.nla_tracks:
        track.mute = True
    frames = int(spec.get("frames", 48))
    render(arm, folder, motion, frames, height)
    for track in arm.animation_data.nla_tracks:
        track.mute = False
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "model_dressed.blend"))
    (folder / "dress.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


main()
