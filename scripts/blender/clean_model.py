"""
Blender の中で動かすスクリプト(model_3d.clean から、画面なしで呼ぶ)。

    blender --background --factory-startup --python scripts/blender/clean_model.py -- <model_dir> <身長(m)> <面の数の上限>

model_dir/model.glb(Hunyuan3D-2mv の形だけの GLB)を整えて、model_clean.glb にする:
- 浮いた破片(ほかとつながらない小さなかたまり)を消す
- 面の数を上限まで減らす(Decimate の Collapse)
- 法線を外向きにそろえ、なめらかに表示する
- 足の裏を原点の高さに置き、身長を指定の高さにそろえ、左右・前後の真ん中を原点にする
向きは、取り込んだまま(Blender で Z 上・正面が -Y。GLB に書き出すと Y 上になる)。
確かめ用に、正面・横(左向き)・後ろの静止画(front.png・side.png・back.png)と、1周のコマ(turntable/*.png)を描き出し、
結果の数(面の数・消した破片の数など)を clean.json に書く。
"""

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

# 破片とみなす大きさ: いちばん大きなかたまりの頂点の数に対する割合
FRAGMENT = 0.01
TURNTABLE_FRAMES = 36
SIZE = (360, 600)


def load(path: Path) -> bpy.types.Object:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    # 親(glTF のノード)の移動・回転を頂点に焼き込み、親を外す
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in list(bpy.context.scene.objects):
        if o is not obj:
            bpy.data.objects.remove(o)
    obj.name = "body"
    return obj


def remove_fragments(obj: bpy.types.Object) -> int:
    """ほかとつながらない小さなかたまりを消し、消した数を返す。"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    seen: set[int] = set()
    islands: list[list[bmesh.types.BMVert]] = []
    for start in bm.verts:
        if start.index in seen:
            continue
        stack, island = [start], []
        seen.add(start.index)
        while stack:
            v = stack.pop()
            island.append(v)
            for e in v.link_edges:
                w = e.other_vert(v)
                if w.index not in seen:
                    seen.add(w.index)
                    stack.append(w)
        islands.append(island)
    largest = max(len(i) for i in islands)
    small = [i for i in islands if len(i) < largest * FRAGMENT]
    bmesh.ops.delete(bm, geom=[v for i in small for v in i], context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    return len(small)


def decimate(obj: bpy.types.Object, max_faces: int) -> None:
    faces = len(obj.data.polygons)
    if faces <= max_faces:
        return
    mod = obj.modifiers.new("decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = max_faces / faces
    bpy.ops.object.modifier_apply(modifier=mod.name)


def normalize(obj: bpy.types.Object, height: float) -> None:
    """足の裏を高さ 0、身長を height(m)、左右・前後の真ん中を原点に。"""
    pts = [v.co for v in obj.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    scale = height / max(hi.z - lo.z, 1e-6)
    center = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    for v in obj.data.vertices:
        v.co = (v.co - center) * scale


def render_setup(height: float) -> tuple[bpy.types.Object, bpy.types.Scene]:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.single_color = (0.8, 0.8, 0.8)
    scene.render.resolution_x, scene.render.resolution_y = SIZE
    scene.render.film_transparent = True
    cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = height * 1.1
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam, scene


def shoot(cam: bpy.types.Object, scene: bpy.types.Scene, deg: float, height: float, path: Path) -> None:
    """原点のまわりを回るカメラ。0 度は正面(-Y 側から見る)、90 度は右から見る(キャラは左を向いて写る)。"""
    a = math.radians(deg)
    cam.location = (math.sin(a) * height * 3, -math.cos(a) * height * 3, height / 2)
    cam.rotation_euler = (math.radians(90), 0, a)
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    folder, height, max_faces = Path(argv[0]), float(argv[1]), int(argv[2])
    obj = load(folder / "model.glb")
    faces_in = len(obj.data.polygons)
    fragments = remove_fragments(obj)
    decimate(obj, max_faces)
    normalize(obj, height)
    obj.data.materials.clear()
    for poly in obj.data.polygons:
        poly.use_smooth = True
    bpy.ops.export_scene.gltf(filepath=str(folder / "model_clean.glb"), export_format="GLB", use_selection=False)

    cam, scene = render_setup(height)
    for name, deg in (("front", 0), ("side", 90), ("back", 180)):
        shoot(cam, scene, deg, height, folder / f"{name}.png")
    turntable = folder / "turntable"
    turntable.mkdir(exist_ok=True)
    for i in range(TURNTABLE_FRAMES):
        shoot(cam, scene, 360 * i / TURNTABLE_FRAMES, height, turntable / f"{i:03d}.png")

    report = {"faces_in": faces_in, "faces": len(obj.data.polygons), "fragments": fragments, "height": height}
    (folder / "clean.json").write_text(json.dumps(report), encoding="utf-8")


main()
