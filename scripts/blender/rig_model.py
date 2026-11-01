"""
Blender の中で動かすスクリプト(model_3d.rig から、画面なしで呼ぶ)。

    blender --background --factory-startup --python scripts/blender/rig_model.py -- <model_dir> <joints.json> <motions.json> <動きの名前>

model_dir/model_clean.glb(clean_model.py が整えた形。Z 上・正面 -Y・足の裏が 0)に、人型の骨を入れる:
- joints.json の関節の点(正面の静止画の骨格推定を、左右 x・高さ z に戻したもの)に、奥行き y を足す。y は、その (x, z) で
  正面から後ろへ光線を通し、形に入る所と出る所の真ん中にする。
- 骨の名前と親子は 2D モデル(body_rig)と同じ(torso・head・upper_arm/forearm/hand・thigh/shin/foot の .L/.R)ので、
  body_motions の動きをそのまま使える。2D の「絵の面の中の回転」は、正面から見た軸(Y)のまわりの回転にする。
- 重みは Blender の自動(heat)。うまく付かなかった頂点は、いちばん近い骨に付ける。
motions.json の動きをすべてアクションにして(GLB には全部入る)、指定の動きで正面と斜めから描き出し(frames/*.png)、
model_rigged.glb と model.blend を保存し、結果を rig.json に書く。
"""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Quaternion, Vector

SIZE = (360, 600)
# 2D の動きの移動(絵の画素)を m にする割合: 素体の絵の高さ 1216 画素を身長とみなす
PIXELS_PER_HEIGHT = 1216


def load(path: Path) -> bpy.types.Object:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in list(bpy.context.scene.objects):
        if o is not obj:
            bpy.data.objects.remove(o)
    obj.name = "body"
    return obj


def depth(obj: bpy.types.Object, x: float, z: float) -> float:
    """(x, z) で正面(-Y)から後ろへ光線を通し、形に入る所と出る所の真ん中の y。当たらなければ 0。"""
    hits = []
    origin = Vector((x, -10.0, z))
    while True:
        ok, loc, _, _ = obj.ray_cast(origin, Vector((0, 1, 0)))
        if not ok:
            break
        hits.append(loc.y)
        origin = loc + Vector((0, 1e-4, 0))
    return (min(hits) + max(hits)) / 2 if hits else 0.0


def joints3d(obj: bpy.types.Object, joints: dict[str, list[float]]) -> dict[str, Vector]:
    return {name: Vector((x, depth(obj, x, z), z)) for name, (x, z) in joints.items()}


def mid(a: Vector, b: Vector) -> Vector:
    return (a + b) / 2


def build_armature(j: dict[str, Vector], height: float) -> bpy.types.Object:
    pelvis = mid(j["left_hip"], j["right_hip"])
    neck = mid(j["left_shoulder"], j["right_shoulder"])
    head_top = Vector((neck.x, neck.y, height))
    bones = {
        "torso": (pelvis, neck, None),
        "head": (neck, head_top, "torso"),
    }
    for side, key in (("L", "left"), ("R", "right")):
        shoulder, elbow, wrist = j[f"{key}_shoulder"], j[f"{key}_elbow"], j[f"{key}_wrist"]
        hip, knee, ankle = j[f"{key}_hip"], j[f"{key}_knee"], j[f"{key}_ankle"]
        toe = Vector((ankle.x, ankle.y - height * 0.08, max(0.01, ankle.z - height * 0.04)))
        bones.update({
            f"upper_arm.{side}": (shoulder, elbow, "torso"),
            f"forearm.{side}": (elbow, wrist, f"upper_arm.{side}"),
            f"hand.{side}": (wrist, wrist + (wrist - elbow) * 0.45, f"forearm.{side}"),
            f"thigh.{side}": (hip, knee, "torso"),
            f"shin.{side}": (knee, ankle, f"thigh.{side}"),
            f"foot.{side}": (ankle, toe, f"shin.{side}"),
        })
    arm = bpy.data.objects.new("rig", bpy.data.armatures.new("rig"))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for name, (head, tail, _) in bones.items():
        bone = arm.data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        # 骨のねじれをそろえる(Z 軸を正面 -Y に)。2D の回転を正面の軸のまわりに当てるとき、骨ごとの向きに頼らない
        bone.align_roll(Vector((0, -1, 0)))
    for name, (_, _, parent) in bones.items():
        if parent:
            arm.data.edit_bones[name].parent = arm.data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def bind(obj: bpy.types.Object, arm: bpy.types.Object) -> dict:
    """自動の重み(heat)で骨に付ける。重みの無い頂点は、いちばん近い骨に付ける。"""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    segments = [(b.name, b.head_local, b.tail_local) for b in arm.data.bones]
    orphans = 0
    for v in obj.data.vertices:
        if any(g.weight > 1e-4 for g in v.groups):
            continue
        orphans += 1

        def dist(seg):  # noqa: ANN001, ANN202
            _, a, b = seg
            ab = b - a
            t = max(0.0, min(1.0, (v.co - a).dot(ab) / max(ab.length_squared, 1e-9)))
            return (v.co - (a + ab * t)).length

        name = min(segments, key=dist)[0]
        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        group.add([v.index], 1.0, "REPLACE")
    return {"vertices": len(obj.data.vertices), "nearest_bone": orphans}


def add_motion(arm: bpy.types.Object, name: str, motion: dict, height: float) -> bpy.types.Action:
    """2D の動き(絵の面の中の回転・胴の移動)を、正面の軸(Y)のまわりの回転と、X・Z の移動のアクションにする。"""
    action = bpy.data.actions.new(name)
    arm.animation_data_create()
    arm.animation_data.action = action
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector()
    for bone_name, keys in motion["rotate"].items():
        pb = arm.pose.bones.get(bone_name)
        if pb is None:
            continue
        # 絵の上で反時計回りが正 = 正面(-Y 側)から見て反時計回り = -Y 軸のまわりの正の回転。骨の向きの空間に直す
        axis = (pb.bone.matrix_local.to_3x3().inverted() @ Vector((0, -1, 0))).normalized()
        for frame, deg in keys:
            pb.rotation_quaternion = Quaternion(axis, math.radians(deg))
            pb.keyframe_insert("rotation_quaternion", frame=frame)
    for bone_name, keys in motion.get("translate", {}).items():
        pb = arm.pose.bones.get(bone_name)
        if pb is None:
            continue
        to_local = pb.bone.matrix_local.to_3x3().inverted()
        for frame, dx, dy in keys:
            pb.location = to_local @ (Vector((dx, 0, dy)) * height / PIXELS_PER_HEIGHT)
            pb.keyframe_insert("location", frame=frame)
    # GLB に全部の動きが入るよう、NLA のトラックに積む
    track = arm.animation_data.nla_tracks.new()
    track.name = name
    track.strips.new(name, 1, action)
    arm.animation_data.action = None
    return action


def render_motion(arm: bpy.types.Object, action: bpy.types.Action, frames: int, height: float, out: Path) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.single_color = (0.8, 0.8, 0.8)
    scene.render.resolution_x, scene.render.resolution_y = SIZE
    scene.render.film_transparent = True
    cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = height * 1.6
    scene.collection.objects.link(cam)
    scene.camera = cam
    # 斜め前(右前 30 度)から。腕を前に出す動きも分かるように
    a = math.radians(30)
    cam.location = (math.sin(a) * height * 3, -math.cos(a) * height * 3, height * 0.55)
    cam.rotation_euler = Euler((math.radians(90), 0, a))
    for track in arm.animation_data.nla_tracks:
        track.mute = True
    arm.animation_data.action = action
    out.mkdir(exist_ok=True)
    for frame in range(1, frames + 1, 2):
        scene.frame_set(frame)
        scene.render.filepath = str(out / f"{frame:03d}.png")
        bpy.ops.render.render(write_still=True)
    arm.animation_data.action = None
    for track in arm.animation_data.nla_tracks:
        track.mute = False


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    folder, joints_path, motions_path, preview = Path(argv[0]), Path(argv[1]), Path(argv[2]), argv[3]
    joints = json.loads(joints_path.read_text(encoding="utf-8"))
    motions = json.loads(motions_path.read_text(encoding="utf-8"))
    obj = load(folder / "model_clean.glb")
    height = max(v.co.z for v in obj.data.vertices)
    j = joints3d(obj, joints["points"])
    arm = build_armature(j, height)
    weights = bind(obj, arm)
    actions = {name: add_motion(arm, name, motion, height) for name, motion in motions.items()}
    for pb in arm.pose.bones:
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector()
    bpy.context.scene.render.fps = 24
    bpy.ops.export_scene.gltf(
        filepath=str(folder / "model_rigged.glb"), export_format="GLB", export_animations=True,
        export_animation_mode="NLA_TRACKS",
    )
    render_motion(arm, actions[preview], motions[preview]["frames"], height, folder / "frames")
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "model.blend"))
    report = {
        "joints": {k: [round(c, 4) for c in v] for k, v in j.items()},
        "weights": weights, "motions": sorted(motions), "preview": preview,
    }
    (folder / "rig.json").write_text(json.dumps(report), encoding="utf-8")


main()
