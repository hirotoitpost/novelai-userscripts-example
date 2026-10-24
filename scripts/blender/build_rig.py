"""
Blender の中で動かすスクリプト(body_rig.run_blender から、画面なしで呼ぶ)。

    blender --background --factory-startup --python scripts/blender/build_rig.py -- <rig_dir> <motion.json>

rig_dir/rig.json(body_rig.build が書く)のメッシュ(絵の形に合わせた平らな素体)を、絵と同じ位置に作り、絵(texture.png)を
テクスチャとして貼る(UV は絵の画素の位置そのまま)。関節に骨(アーマチュア)を入れ、頂点の重み(頂点グループ)とアーマチュアの
モディファイアで、骨を回すとメッシュが皮膚のように曲がるようにする。絵は正面(XZ 面、カメラは -Y から見る)に置き、骨は絵の面の
中で回る(骨の Z 軸を -Y に向けるので、ポーズの Z 回転が絵の面の中の回転になる)。動き(motion)のキーを打ち、正投影のカメラで
描き出して rig.blend に保存する。Blender 4.2 以降(EEVEE Next)を前提にする。
"""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

# 1 画素 = 0.01(Blender の単位)。絵の高さが 1216 なら 12.16
SCALE = 0.01
# 重ねる順 1 つあたりの奥行きの差(手前ほど -Y)。透過のマテリアルは、オブジェクトの原点の近さで描く順が決まるので、
# 奥行きは頂点ではなくオブジェクトの位置に付ける(原点がみな同じだと、服の層が体の裏に描かれた)
DEPTH = 0.05
FPS = 24
# 描き出す範囲の余白(絵の大きさに対する割合)。腕を上げると絵の外に出るので広く取る
MARGIN = 0.25


def to_world(x: float, y: float, height: int, z: int = 0) -> Vector:
    return Vector((x * SCALE, -z * DEPTH, (height - y) * SCALE))


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def make_material(image_path: Path) -> bpy.types.Material:
    """絵の色をそのまま出す(光を当てない)透過のマテリアル。テクスチャ(体・服の層)ごとに1つ。"""
    mat = bpy.data.materials.new(image_path.stem)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(image_path))
    tex.interpolation = "Linear"
    tex.extension = "CLIP"
    emission = nodes.new("ShaderNodeEmission")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(tex.outputs["Color"], emission.inputs["Color"])
    links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(emission.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    else:
        mat.blend_method = "BLEND"
    return mat


def make_mesh(data: dict, material: bpy.types.Material, width: int, height: int,
              collection: bpy.types.Collection) -> bpy.types.Object:
    """絵の形に合わせたメッシュ。UV は頂点の絵の位置、頂点グループは骨ごとの重み。"""
    verts = [tuple(to_world(x, y, height)) for x, y in data["vertices"]]
    mesh = bpy.data.meshes.new(data["name"])
    mesh.from_pydata(verts, [], [tuple(t) for t in data["triangles"]])
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            x, y = data["vertices"][mesh.loops[loop_index].vertex_index]
            uv.data[loop_index].uv = (x / width, 1.0 - y / height)
    mesh.materials.append(material)
    obj = bpy.data.objects.new(data["name"], mesh)
    obj.location = (0.0, -data["z"] * DEPTH, 0.0)
    collection.objects.link(obj)
    groups = [obj.vertex_groups.new(name=bone) for bone in data["bones"]]
    for index, row in enumerate(data["weights"]):
        for group, weight in zip(groups, row):
            if weight > 0.001:
                group.add([index], weight, "REPLACE")
    return obj


def make_armature(bones: list, height: int) -> bpy.types.Object:
    data = bpy.data.armatures.new("rig")
    arm = bpy.data.objects.new("rig", data)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in bones:
        edit = data.edit_bones.new(bone["name"])
        edit.head = to_world(*bone["head"], height)
        edit.tail = to_world(*bone["tail"], height)
        # 骨の Z 軸をカメラ側(-Y)に向ける: ポーズの Z 回転が、絵の面の中の回転になる
        edit.align_roll(Vector((0, -1, 0)))
    for bone in bones:
        if bone["parent"]:
            data.edit_bones[bone["name"]].parent = data.edit_bones[bone["parent"]]
    bpy.ops.object.mode_set(mode="OBJECT")
    for pose_bone in arm.pose.bones:
        pose_bone.rotation_mode = "XYZ"
    return arm


def bind(arm: bpy.types.Object, meshes: list) -> None:
    """メッシュをアーマチュアの子にし、アーマチュアのモディファイア(頂点グループの重み)で曲げる。"""
    for obj in meshes:
        obj.parent = arm
        modifier = obj.modifiers.new("armature", "ARMATURE")
        modifier.object = arm
        modifier.use_vertex_groups = True


def animate(arm: bpy.types.Object, motion: dict) -> int:
    """
    motion.json(body_motions.Motion.to_json)のキーを打つ。rotate は絵の面の中の回転(度。骨の Z 回転)、translate は
    骨の移動(絵の画素。x は絵の右、y は絵の上。胴の骨は上向きなので、骨の X・Y がそのまま絵の右・上になる)。
    """
    for name, points in motion["rotate"].items():
        pose_bone = arm.pose.bones.get(name)
        if pose_bone is None:
            continue
        for frame, degrees in points:
            pose_bone.rotation_euler = (0.0, 0.0, math.radians(degrees))
            pose_bone.keyframe_insert("rotation_euler", frame=frame)
    for name, points in motion.get("translate", {}).items():
        pose_bone = arm.pose.bones.get(name)
        if pose_bone is None:
            continue
        for frame, dx, dy in points:
            pose_bone.location = (dx * SCALE, dy * SCALE, 0.0)
            pose_bone.keyframe_insert("location", frame=frame)
    return int(motion["frames"])


def setup_render(width: int, height: int, frames: int, out_dir: Path) -> None:
    scene = bpy.context.scene
    view_w, view_h = int(width * (1 + 2 * MARGIN)), int(height * (1 + MARGIN))
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.render.resolution_x = view_w
    scene.render.resolution_y = view_h
    scene.render.resolution_percentage = 50
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.render.fps = FPS
    scene.frame_start = 1
    scene.frame_end = frames
    scene.render.filepath = str(out_dir / "frames" / "f_")

    cam_data = bpy.data.cameras.new("camera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(view_w, view_h) * SCALE
    cam = bpy.data.objects.new("camera", cam_data)
    bpy.context.collection.objects.link(cam)
    # 横は左右に、縦は上にだけ余白(足もとは絵の下端のまま)
    cam.location = (width * SCALE / 2, -10.0, (height * (1 + MARGIN)) * SCALE / 2)
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.camera = cam


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1:]
    rig_dir, motion = Path(args[0]), json.loads(Path(args[1]).read_text(encoding="utf-8"))
    rig = json.loads((rig_dir / "rig.json").read_text(encoding="utf-8"))
    width, height = rig["size"]

    clear_scene()
    # 服の層は別のコレクションに入れる(Blender で表示を切り替えれば、素体だけ・服を着た姿を見比べられる)
    collections = {"body": bpy.context.scene.collection}
    if any(data["name"].startswith("outfit.") for data in rig["meshes"]):
        collections["outfit"] = bpy.data.collections.new("outfit")
        bpy.context.scene.collection.children.link(collections["outfit"])
    materials: dict = {}
    meshes = []
    for data in rig["meshes"]:
        texture = data.get("texture", rig["texture"])
        if texture not in materials:
            materials[texture] = make_material(rig_dir / texture)
        layer = "outfit" if data["name"].startswith("outfit.") else "body"
        meshes.append(make_mesh(data, materials[texture], width, height, collections[layer]))
    arm = make_armature(rig["bones"], height)
    bind(arm, meshes)
    frames = animate(arm, motion)
    setup_render(width, height, frames, rig_dir)
    # 動きの確かめ用のコマを描き出してから、休みの姿勢(1コマ目)で保存する
    bpy.ops.render.render(animation=True)
    bpy.context.scene.frame_set(1)
    # 絵を .blend の中に入れる(.blend だけをほかの PC へ持っていっても開けるように)
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(rig_dir / "rig.blend"))
    print(f"RIG_DONE frames={frames}")


main()
