"""
Blender の中で動かすスクリプト(model_3d.render_pose から、画面なしで呼ぶ)。

    blender --background <model.blend> --python scripts/blender/pose_render.py -- <pose.json> <out_dir> <style>

rig_model.py が保存した model.blend(骨と人形の手の入った形)に、pose.json のポーズ(body_poses.render_json)を当て、
体のいちばん低い所を床に合わせ、pose.json の cameras のカメラごとに漫画のコマの参考の絵を描き出す(<out_dir>/<camera>.png)。
style は shade(灰色の陰影。Workbench)か line(線画風。EEVEE + Freestyle の線だけ)。
"""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

SIZE = (832, 1216)
FINGERS = ("thumb", "index", "middle", "ring", "little")
# 手の形: 指(親指以外・親指)の節ごとの曲げ(度。指の骨の X 軸のまわり、正で手のひらの側へ)
HAND_SHAPES = {
    "open": ((0, 0, 0), (0, 0, 0)),
    "relaxed": ((15, 20, 15), (10, 10, 5)),
    "fist": ((80, 95, 60), (35, 30, 30)),
}


def rest_rotation(bone: bpy.types.Bone, forward: float, side: float, twist: float) -> Quaternion:
    """骨を入れた姿勢の向きで決めた回転(body_poses の forward・side・twist)を、骨の向きの空間の回転にする。"""
    d = (bone.tail_local - bone.head_local).normalized()
    front = Vector((0, -1, 0))
    axis = d.cross(front)
    if axis.length < 0.3:  # 前を向いた骨(足)は、横の軸
        axis = Vector((-1, 0, 0))
    q = (
        Quaternion(axis.normalized(), math.radians(forward))
        @ Quaternion(front, math.radians(side))
        @ Quaternion(d, math.radians(twist))
    )
    m = bone.matrix_local.to_3x3()
    return (m.inverted() @ q.to_matrix() @ m).to_quaternion()


def apply_pose(arm: bpy.types.Object, pose: dict) -> None:
    if arm.animation_data:
        arm.animation_data.action = None
        for track in arm.animation_data.nla_tracks:
            track.mute = True
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector()
    for name, (forward, side, twist) in pose["bones"].items():
        pb = arm.pose.bones.get(name)
        if pb is not None:
            pb.rotation_quaternion = rest_rotation(pb.bone, forward, side, twist)
    for side, shape in pose["hands"].items():
        fingers, thumb = HAND_SHAPES["fist" if shape == "point" else shape]
        for finger in FINGERS:
            if shape == "point" and finger == "index":
                curl = (0, 0, 0)
            else:
                curl = thumb if finger == "thumb" else fingers
            for i, deg in enumerate(curl, 1):
                pb = arm.pose.bones.get(f"{finger}.{i:02d}.{side}")
                if pb is not None:
                    pb.rotation_quaternion = Quaternion(Vector((1, 0, 0)), math.radians(deg))
    bpy.context.view_layer.update()


def bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    """ポーズを付けたあとの形の箱(ワールド座標)。"""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        for v in mesh.vertices:
            p = evaluated.matrix_world @ v.co
            lo = Vector(min(a, b) for a, b in zip(lo, p))
            hi = Vector(max(a, b) for a, b in zip(hi, p))
        evaluated.to_mesh_clear()
    return lo, hi


def material(name: str, color: tuple[float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    return mat


def add_box(name: str, center: Vector, size: Vector, mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    obj.data.materials.append(mat)
    return obj


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    pose = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    out_dir, style = Path(argv[1]), argv[2]
    arm = bpy.data.objects["rig"]
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    height = arm.data.bones["head"].tail_local.z
    for obj in [o for o in bpy.data.objects if o.type == "CAMERA"]:
        bpy.data.objects.remove(obj)
    apply_pose(arm, pose)
    lo, _ = bounds(meshes)
    arm.location.z -= lo.z
    bpy.context.view_layer.update()

    body_mat = material("body", (0.8, 0.8, 0.8))
    for obj in meshes:
        obj.data.materials.clear()
        obj.data.materials.append(body_mat)
    props = []
    if pose.get("prop") == "chair":
        pelvis = arm.matrix_world @ arm.pose.bones["torso"].head
        seat = max(pelvis.z - height * 0.05, 0.05)
        props.append(add_box(
            "chair", Vector((pelvis.x, pelvis.y + height * 0.03, seat / 2)),
            Vector((height * 0.28, height * 0.26, seat)), material("chair", (0.55, 0.55, 0.6)),
        ))
    lo, hi = bounds(meshes + props)
    center = (lo + hi) / 2
    radius = (hi - lo).length / 2

    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = SIZE
    # model.blend は確かめのコマを透明の背景で描いたまま保存してあるので、白い背景に戻す
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("white")
    world.color = (1, 1, 1)
    scene.world = world
    # 背景の白を白のまま出す(既定の AgX だと灰色になる)
    scene.view_settings.view_transform = "Standard"
    if style == "line":
        scene.render.engine = "BLENDER_EEVEE_NEXT"
        scene.render.use_freestyle = True
        scene.render.line_thickness_mode = "ABSOLUTE"
        scene.render.line_thickness = 1.4
        # 描く場面(ビューレイヤー)の側でも線を有効にし、線の種類(輪郭・境目・折れ目)の設定が無ければ作る
        layer = scene.view_layers[0]
        layer.use_freestyle = True
        settings = layer.freestyle_settings
        lineset = settings.linesets[0] if settings.linesets else settings.linesets.new("lines")
        lineset.select_by_visibility = True
        lineset.select_silhouette = True
        lineset.select_border = True
        lineset.select_crease = True
        settings.crease_angle = math.radians(120)
        if lineset.linestyle is None:
            lineset.linestyle = bpy.data.linestyles.new("lines")
        lineset.linestyle.color = (0, 0, 0)
        lineset.linestyle.thickness = 1.6
        white = bpy.data.materials.new("white")
        white.use_nodes = True
        nodes = white.node_tree.nodes
        nodes.clear()
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = (1, 1, 1, 1)
        out = nodes.new("ShaderNodeOutputMaterial")
        white.node_tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
        world.use_nodes = True
        nodes = world.node_tree.nodes
        nodes.clear()
        background = nodes.new("ShaderNodeBackground")
        background.inputs["Color"].default_value = (1, 1, 1, 1)
        world.node_tree.links.new(background.outputs["Background"], nodes.new("ShaderNodeOutputWorld").inputs["Surface"])
        for obj in meshes + props:
            obj.data.materials.clear()
            obj.data.materials.append(white)
    else:
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.light = "STUDIO"
        # 上から照らす照明にする(既定の照明では、床が暗い灰色になった)
        scene.display.shading.studio_light = "outdoor.sl"
        scene.display.shading.color_type = "MATERIAL"
        scene.display.shading.show_cavity = True
        floor = add_box("floor", Vector((center.x, center.y, -0.005)), Vector((radius * 8, radius * 8, 0.01)),
                        material("floor", (0.97, 0.97, 0.97)))

    cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    cam.data.lens = 50
    cam.data.sensor_fit = "VERTICAL"
    scene.collection.objects.link(cam)
    scene.camera = cam
    fov = 2 * math.atan(cam.data.sensor_height / 2 / cam.data.lens)
    distance = radius / math.sin(fov / 2) * 1.05
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (angle, rise, near) in pose["cameras"].items():
        a = math.radians(angle)
        z = height * rise
        reach = distance * near
        horizontal = math.sqrt(max(reach ** 2 - (z - center.z) ** 2, (reach * 0.3) ** 2))
        cam.location = Vector((center.x + math.sin(a) * horizontal, center.y - math.cos(a) * horizontal, z))
        cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
        if style != "line":
            # 床より低いカメラ(あおり)では、床の板が手前をふさぐので消す
            floor.hide_render = z < height * 0.05
        scene.render.filepath = str(out_dir / f"{name}.png")
        bpy.ops.render.render(write_still=True)


main()
