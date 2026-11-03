"""
髪を別の部品にする(model_3d.attach_hair から呼ぶ)。スキンヘッドの骨入りモデル(bald/model.blend)に、髪ありの整えた形
(model_clean.glb)を胸〜首の体で合わせ、スキンヘッドの頭から外に出た所を髪の部品(オブジェクト hair)として切り出し、
head の骨に付ける。骨入りの GLB(model_rigged.glb)を書き出し直し、正面・横・後ろの絵(hair_<向き>.png)を描く。

  blender --background --factory-startup --python hair_part.py -- <bald のフォルダ> <髪ありの model_clean.glb> <mask.npz>

mask.npz: 髪ありの形を作った正面の絵の髪の所(front_hair。画素ごと)と、人物の左右・上下の範囲(front_box)。
結果は <bald のフォルダ>/hair.json に書く。
"""

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

# 合わせに使う体: 身長に対するこの割合より上、首まで(脚・腰は姿勢の違いで合いにくい)
ALIGN_LOW = 0.55
ALIGN_ITERS = 50
# 合わせで外す、離れた点の割合(髪の房・腕の違い)
ALIGN_DROP = 85
# スキンヘッドの面からこれより外を髪に
OUTSIDE = 0.004
# 首より下で、これより外は下がった房(後ろ・横を向いた面)
HANG = 0.015
# 頭皮に沈んだ・近すぎる髪の点を出す、頭皮からの距離
LIFT = 0.003
# 髪にする高さの下限(首の高さからの差)
BELOW_NECK = 0.12
# これより面の少ないかたまりは破片として捨てる
MIN_ISLAND = 200
HAIR_COLOR = (0.55, 0.4, 0.25, 1)


def _world_verts(obj: bpy.types.Object) -> np.ndarray:
    """評価した(休んだ姿勢・シェイプキーを当てた)形の頂点(世界の座標)。"""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    co = co.reshape(-1, 3)
    m = np.array(obj.matrix_world)
    return co @ m[:3, :3].T + m[:3, 3]


def _import(path: Path) -> bpy.types.Object:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in new:
        if o.name in bpy.data.objects and o != obj:
            bpy.data.objects.remove(o)
    return obj


def neck_height(verts: np.ndarray, height: float) -> float:
    """首の高さ: 身長の 75〜95% のうち、横幅がいちばん細い高さ。"""
    zs = np.linspace(0.75 * height, 0.95 * height, 41)
    widths = []
    for z in zs:
        band = verts[np.abs(verts[:, 2] - z) < 0.004]
        widths.append(np.ptp(band[:, 0]) if len(band) > 10 else np.inf)
    return float(zs[int(np.argmin(widths))])


def align(src: np.ndarray, target: np.ndarray) -> tuple[float, np.ndarray, np.ndarray, float]:
    """src を target に、大きさ・回転・位置で合わせる(近い点どうしを繰り返し合わせる)。(大きさ, 回転, 位置, 平均の差)。"""
    kd = KDTree(len(target))
    for i, v in enumerate(target):
        kd.insert(v, i)
    kd.balance()
    src = src[:: max(1, len(src) // 8000)]
    s, rot, t, err = 1.0, np.eye(3), np.zeros(3), 0.0
    for _ in range(ALIGN_ITERS):
        moved = s * src @ rot.T + t
        near = np.array([kd.find(Vector(p))[0] for p in moved])
        d = np.linalg.norm(near - moved, axis=1)
        keep = d < np.percentile(d, ALIGN_DROP)
        a, b = src[keep], near[keep]
        am, bm = a.mean(0), b.mean(0)
        ac, bc = a - am, b - bm
        u, sv, vt = np.linalg.svd(bc.T @ ac / len(a))
        fix = np.eye(3)
        fix[2, 2] = np.sign(np.linalg.det(u @ vt))
        rot = u @ fix @ vt
        s = float(np.trace(np.diag(sv) @ fix) / (ac ** 2).sum() * len(a))
        t = bm - s * am @ rot.T
        err = float(d[keep].mean())
    return s, rot, t, err


class FrontMask:
    """髪ありの形の点(合わせる前)を、正面の絵(人物の上下の範囲を身長に当てる)に映して、髪の所かを見る。"""

    def __init__(self, path: Path, height: float):
        data = np.load(path)
        self.hair = data["front_hair"]
        self.box = data["front_box"]
        self.height = height

    def __call__(self, p: np.ndarray) -> bool:
        x0, x1, y0, y1 = self.box
        k = (y1 - y0) / self.height
        j, i = int(round((x0 + x1) / 2 + p[0] * k)), int(round(y1 - p[2] * k))
        if not (0 <= i < self.hair.shape[0] and 0 <= j < self.hair.shape[1]):
            return False
        return bool(self.hair[max(0, i - 2):i + 3, max(0, j - 2):j + 3].any())


def extract(hair_src: bpy.types.Object, body: bpy.types.Object, mask: FrontMask, height: float) -> dict:
    """hair_src(髪ありの形)を体に合わせ、髪の所だけを残す(hair_src を書き換える)。結果を返す。"""
    va = np.array([v.co for v in hair_src.data.vertices])
    vb = _world_verts(body)
    neck = neck_height(vb, height)
    s, rot, t, err = align(va[(va[:, 2] > ALIGN_LOW * height) & (va[:, 2] < neck + 0.02)], vb)
    hair_src.data.vertices.foreach_set("co", (s * va @ rot.T + t).ravel())
    hair_src.data.update()
    inv = np.linalg.inv(rot)
    head = vb[vb[:, 2] > neck + 0.02]
    hc = head.mean(0)

    bvh = BVHTree.FromPolygons([tuple(v) for v in vb], [tuple(p.vertices) for p in body.data.polygons])

    bm = bmesh.new()
    bm.from_mesh(hair_src.data)
    bm.normal_update()
    drop = []
    for f in bm.faces:
        c = f.calc_center_median()
        hit = bvh.find_nearest(c)
        if hit[0] is None:
            drop.append(f)
            continue
        outside = (c - hit[0]).dot(hit[1]) > 0 and hit[3] > OUTSIDE
        # 頭皮(頭の真ん中より上と、頭の後ろ半分の首より上)は、近くても(沈んでいても)髪になりうる
        scalp = hit[0].z > hc[2] or (hit[0].y > hc[1] and hit[0].z > neck + 0.03)
        if not ((outside or scalp) and c.z > neck - BELOW_NECK):
            drop.append(f)
            continue
        if f.normal.y < -0.3:
            # 前を向いた面は、正面の絵の髪の所か(前髪と顔・肩は、形の差では見分けられない)
            if not mask(inv @ ((np.array(c) - t) / s)):
                drop.append(f)
        elif not ((c.z > neck and (scalp or outside)) or hit[3] > HANG):
            # 後ろ・横を向いた面は、首より上か、首より下ならはっきり外(下がった房)なら髪。後ろの絵は振り返った姿勢の
            # ことがあり、まっすぐ後ろからの絵として映すとずれた(2026-11 矢野栄子)
            drop.append(f)
    bmesh.ops.delete(bm, geom=drop, context="FACES")
    bm.faces.ensure_lookup_table()
    seen: set[int] = set()
    small = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, island = [f], [f]
        seen.add(f.index)
        while stack:
            for e in stack.pop().edges:
                for g in e.link_faces:
                    if g.index not in seen:
                        seen.add(g.index)
                        stack.append(g)
                        island.append(g)
        if len(island) < MIN_ISLAND:
            small.extend(island)
    bmesh.ops.delete(bm, geom=small, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    pushed = 0
    for v in bm.verts:
        hit = bvh.find_nearest(v.co)
        if hit[0] is not None and (v.co - hit[0]).dot(hit[1]) < LIFT:
            v.co = hit[0] + hit[1] * LIFT
            pushed += 1
    bm.to_mesh(hair_src.data)
    bm.free()
    return {
        "scale": round(s, 4), "rotation_deg": round(math.degrees(math.acos(min(1.0, (np.trace(rot) - 1) / 2))), 2),
        "align_error_mm": round(err * 1000, 1), "neck_z": round(neck, 3), "faces": len(hair_src.data.polygons),
        "pushed": pushed,
    }


def attach(hair: bpy.types.Object, arm: bpy.types.Object) -> None:
    """髪を head の骨に付ける(頭と一緒に動く)。"""
    hair.name = hair.data.name = "hair"
    hair.vertex_groups.clear()
    group = hair.vertex_groups.new(name="head")
    group.add(range(len(hair.data.vertices)), 1.0, "REPLACE")
    mod = hair.modifiers.new("rig", "ARMATURE")
    mod.object = arm
    hair.parent = arm
    hair.matrix_parent_inverse = arm.matrix_world.inverted()
    hair.color = HAIR_COLOR
    for poly in hair.data.polygons:
        poly.use_smooth = True


def render(folder: Path, height: float) -> None:
    """正面・横・後ろの、頭のまわりの絵(hair_<向き>.png)。"""
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_backface_culling = False
    scene.render.resolution_x = scene.render.resolution_y = 400
    scene.render.film_transparent = True
    cam = bpy.data.objects.new("_hair_cam", bpy.data.cameras.new("_hair_cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 0.34 * height
    hidden = [o for o in bpy.data.objects if o.name.startswith("part_") and not o.hide_render]
    for o in hidden:
        o.hide_render = True
    for name, deg in (("front", 0), ("side", 90), ("back", 180)):
        a = math.radians(deg)
        cam.location = (math.sin(a) * height * 2, -math.cos(a) * height * 2, 0.86 * height)
        cam.rotation_euler = (math.radians(90), 0, a)
        scene.render.filepath = str(folder / f"hair_{name}.png")
        bpy.ops.render.render(write_still=True)
    for o in hidden:
        o.hide_render = False
    bpy.data.objects.remove(cam)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    folder, hair_glb, mask_path = Path(argv[0]), Path(argv[1]), Path(argv[2])
    bpy.ops.wm.open_mainfile(filepath=str(folder / "model.blend"))
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    body = bpy.data.objects["body"]
    old = bpy.data.objects.get("hair")
    if old is not None:
        bpy.data.objects.remove(old)
    ad = arm.animation_data
    action = ad.action if ad else None
    if ad:
        ad.action = None
    arm.data.pose_position = "REST"
    bpy.context.view_layer.update()
    height = float(np.ptp(_world_verts(body)[:, 2]))
    hair = _import(hair_glb)
    report = extract(hair, body, FrontMask(mask_path, height), height)
    attach(hair, arm)
    # 絵は休んだ姿勢で(動きを当てると腕が上がった)
    bpy.context.view_layer.update()
    render(folder, height)
    arm.data.pose_position = "POSE"
    for pb in arm.pose.bones:
        pb.matrix_basis.identity()
    bpy.ops.export_scene.gltf(
        filepath=str(folder / "model_rigged.glb"), export_format="GLB", export_animations=True,
        export_animation_mode="NLA_TRACKS", export_morph_normal=True,
    )
    if ad:
        ad.action = action
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "model.blend"))
    (folder / "hair.json").write_text(json.dumps(report), encoding="utf-8")
    print("hair", report)


if __name__ == "__main__":
    main()
