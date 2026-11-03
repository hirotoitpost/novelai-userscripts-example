"""
Blender の中で動かすスクリプト(model_3d.dress から、画面なしで呼ぶ)。

    blender --background <model_dir>/model.blend --python scripts/blender/dress_model.py -- <model_dir> <garments.json> <動きの名前>

rig_model.py が保存した model.blend(体 body・骨 rig)の体に、garments.json の服(スカート garment_skirt.py・
上の服 garment_top.py。両方なら、スカートの上に上の服を重ねる)を布として着せる。スカートは休んだ姿勢の体に、上の服は腕を上げた体に着せる(脇の下で布が
余らない)。服は骨に付ける: 服の頂点ごとに、いちばん近い体の面の骨の重みを写し、服の上でなめらかにする(脚の間で
布が裂けないように)。腕を上げて着せた服は、その重みで骨の変形を逆にかけて休んだ姿勢に戻し、休んだ姿勢の体の上で
もう一度布として動かしてなじませる(腕と胴の間に挟まった布を押し出す)。骨と動き入りの model_dressed.glb・model_dressed.blend を書き、確かめの
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
import garment_top  # noqa: E402

SIZE = (360, 600)
# 服の重みをなめらかにする回数と、1 頂点に残す骨の数
SMOOTH_ROUNDS = 30
MAX_BONES = 4
# スカートを付ける骨。腕・手に付けると、腕を振ったときにスカートが引っ張られる
SKIRT_BONES = ("torso", "thigh.L", "thigh.R", "shin.L", "shin.R")
# えりの色(服と見分ける。絵の白いえり)
COLLAR_COLOR = (0.97, 0.95, 0.82, 1)
# 重ねる順(小さいほど内)
LAYERS = {"skirt": 0, "top": 1}


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


def transfer_weights(garment: bpy.types.Object, body: bpy.types.Object, arm: bpy.types.Object, garment_bones: tuple) -> dict:
    """服の頂点ごとに、いちばん近い体の面(三角形)の頂点の骨(garment_bones だけ)の重みを混ぜて写し、服の上でなめらかにする。"""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(dg)
    mesh = ev.to_mesh()
    mesh.calc_loop_triangles()
    co = np.array([body.matrix_world @ v.co for v in mesh.vertices])
    tris = np.array([t.vertices[:] for t in mesh.loop_triangles])
    ev.to_mesh_clear()
    bones = [b.name for b in arm.data.bones if b.use_deform and b.name in garment_bones]
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
            main[v.index] = all_groups.get(top.group) in garment_bones
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


def face_outward(garment: bpy.types.Object) -> bool:
    """服の面の向きを外向き(体の中心の縦の軸から離れる向き)にそろえる。裏返したら True。"""
    me = garment.data
    center = np.mean([v.co[:] for v in me.vertices], axis=0)
    out = 0.0
    for poly in me.polygons:
        radial = np.array(poly.center[:2]) - center[:2]
        out += float(np.dot(np.array(poly.normal[:2]), radial)) * poly.area
    if out >= 0:
        return False
    me.flip_normals()
    me.update()
    return True


def unpose(garment: bpy.types.Object, arm: bpy.types.Object) -> None:
    """
    いまの姿勢の骨で着せた服を、休んだ姿勢の形に戻す: 頂点ごとに、付けた骨の変形(重みで混ぜた行列)の逆をかける。
    骨を休んだ姿勢に戻すと、骨の変形(Armature)で、着せたときの形に戻る。
    """
    bpy.context.view_layer.update()
    # 骨の名前の頂点グループだけ(えりの硬さ "_stiff" のような、骨でないグループは除く)
    bones = {g.index: j for j, g in enumerate(g for g in garment.vertex_groups if g.name in arm.pose.bones)}
    names = [g.name for g in garment.vertex_groups if g.name in arm.pose.bones]
    mats = np.array([
        np.array(arm.matrix_world @ arm.pose.bones[n].matrix @ arm.pose.bones[n].bone.matrix_local.inverted()
                 @ arm.matrix_world.inverted())
        for n in names
    ])
    to_world = np.array(garment.matrix_world)
    to_local = np.linalg.inv(to_world)
    for v in garment.data.vertices:
        w = np.zeros(len(names))
        for g in v.groups:
            if g.group in bones:
                w[bones[g.group]] = g.weight
        if w.sum() <= 0:
            continue
        blend = np.einsum("b,bij->ij", w / w.sum(), mats)
        p = to_world @ np.append(np.array(v.co), 1.0)
        v.co = (to_local @ np.linalg.solve(blend, p))[:3]


def render(arm: bpy.types.Object, folder: Path, motion: str, frames: int, height: float) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    # 服・えりと体の境に線を引く(えりの白と体の灰色が近い)
    scene.display.shading.show_object_outline = True
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
    worn = []
    # 内に着る服から順に着せる(上の服は、先に着せたスカートの外に置き、当たり判定も付ける)
    for n, item in enumerate(sorted(spec["garments"], key=lambda g: LAYERS.get(g["kind"], 0))):
        name = f"garment_{item['name']}"
        if item["kind"] == "skirt":
            obj, info = garment_skirt.build_skirt(body, joints, item.get("config", {}), name=name)
            info["bones"] = transfer_weights(obj, body, arm, SKIRT_BONES)
        elif item["kind"] == "top":
            posed = garment_top.raise_arms(arm, joints)
            obj, info = garment_top.build_top(body, posed, item.get("config", {}), name=name, under=tuple(worn))
            dressed = np.array([v.co[:] for v in obj.data.vertices])
            info["bones"] = transfer_weights(obj, body, arm, garment_top.BONES)
            unpose(obj, arm)
            rest_pose(arm)
            bpy.context.view_layer.update()
            # 腕を下ろすと、腕と胴の間に布が挟まる: 休んだ姿勢でもう一度なじませる
            info["inside_body"] = garment_top.settle(obj, body, joints, dressed, item.get("config", {}), under=tuple(worn))
            collar = garment_top.add_collar(obj, item.get("config", {}), arm)
            if collar is not None:
                collar.color = COLLAR_COLOR
                info["collar_vertices"] = len(collar.data.vertices)
        else:
            raise SystemExit(f"まだ作れない服です: {item['kind']}")
        # 面が内を向いていると、GLB で裏から見え、上に重ねる服が当たり判定で内へ押される
        face_outward(obj)
        info["name"] = item["name"]
        info["kind"] = item["kind"]
        info["preset"] = item.get("preset")
        obj.color = colors[n % len(colors)]
        worn.append(obj)
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
