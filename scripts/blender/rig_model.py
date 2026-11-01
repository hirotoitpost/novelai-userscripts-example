"""
Blender の中で動かすスクリプト(model_3d.rig から、画面なしで呼ぶ)。

    blender --background --factory-startup --python scripts/blender/rig_model.py -- <model_dir> <joints.json> <motions.json> <動きの名前>

model_dir/model_clean.glb(clean_model.py が整えた形。Z 上・正面 -Y・足の裏が 0)に、人型の骨を入れる:
- joints.json の関節の点(正面の静止画の骨格推定を、左右 x・高さ z に戻したもの)に、奥行き y を足す。y は、その (x, z) で
  正面から後ろへ光線を通し、形に入る所と出る所の真ん中にする。
- 骨の名前と親子は 2D モデル(body_rig)と同じ(torso・head・upper_arm/forearm/hand・thigh/shin/foot の .L/.R)ので、
  body_motions の動きをそのまま使える。2D の「絵の面の中の回転」は、正面から見た軸(Y)のまわりの回転にする。
- 重みは Blender の自動(heat)。うまく付かなかった頂点は、いちばん近い骨に付ける。
- 手は付け替える(Hunyuan3D-2mv は指を作れず、指のくっついたひれの形になる): 作った形の手首から先を切り取り、人形の手
  (手のひら+5本の指・3節ずつ。節ごとに骨)を置く。大きさは前腕の長さ、形の割合は joints.json の hand(素体の手の絵から
  hand_shape.measure が測ったもの)。
motions.json の動きをすべてアクションにして(GLB には全部入る)、指定の動きで正面と斜めから描き出し(frames/*.png)、
model_rigged.glb と model.blend を保存し、結果を rig.json に書く。
"""

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

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


# 人形の手: 指(親指から小指)の付け根の位置(手のひらの幅に対する、親指側を正とした左右の位置)と、広がり(度)
FINGER_BASES = {"index": (0.375, 4), "middle": (0.125, 0), "ring": (-0.125, -3), "little": (-0.375, -7)}
FINGER_PARTS = {"thumb", "index", "middle", "ring", "little"}
HAND_PARTS = FINGER_PARTS | {"hand"}
# 節の長さの割合(付け根・中・先)
SEGMENTS = (0.45, 0.30, 0.25)
# 手首から腕へ食い込ませる長さ(手の長さに対する割合)。切り口との間にすき間を出さない
WRIST_OVERLAP = 0.06


def hand_layout(j: dict[str, Vector], key: str, height: float, ratios: dict) -> dict:
    """
    人形の手の形と骨の位置。腕を下ろした姿勢に合わせ、指は前腕の向きに伸ばし、手のひらは体の側へ、親指は前(-Y)へ向ける。
    手の長さは前腕の 0.74 倍(身長の 9〜12% に収める)、形は ratios(hand_shape.measure の割合)。
    """
    elbow, wrist = j[f"{key}_elbow"], j[f"{key}_wrist"]
    a = (wrist - elbow).normalized()
    w = Vector((0, -1, 0))
    w = (w - a * w.dot(a)).normalized()
    n = a.cross(w)
    if n.x * wrist.x > 0:  # 手のひら(n)を体の真ん中へ向ける
        n = -n
    length = min(max((wrist - elbow).length * 0.74, height * 0.09), height * 0.12)
    middle = length * ratios["middle"]
    palm = length - middle
    width = length * ratios["width"]

    def at(u: float, v: float, k: float = 0.0) -> Vector:
        return wrist + a * u + w * v + n * k

    fingers = {}
    for name, (offset, splay) in FINGER_BASES.items():
        direction = (a * math.cos(math.radians(splay)) + w * math.sin(math.radians(splay))).normalized()
        start = at(palm - (0.04 * length if name == "little" else 0.0), offset * width)
        total = middle * (1.0 if name == "middle" else ratios[name])
        fingers[name] = (start, direction, total, width * (0.10 if name == "little" else 0.115))
    # 親指: 手のひらの根もと寄り・親指側の縁から、前へ 15 度・手のひら側へ 30 度開き、手のひらの縁に沿って下へ向ける
    thumb_dir = (a * math.cos(math.radians(15)) + w * math.sin(math.radians(15))).normalized()
    thumb_dir = (thumb_dir * math.cos(math.radians(30)) + n * math.sin(math.radians(30))).normalized()
    fingers["thumb"] = (at(palm * 0.15, width * 0.45, width * 0.1), thumb_dir, middle * ratios["thumb"], width * 0.12)
    return {
        "a": a, "w": w, "n": n, "length": length, "palm": palm, "width": width, "wrist": wrist,
        "palm_center": at((palm - WRIST_OVERLAP * length) / 2, 0.0),
        "palm_size": Vector(((palm + WRIST_OVERLAP * length) / 2, width / 2, width * 0.16)),
        "fingers": fingers,
    }


def finger_bones(side: str, layout: dict) -> dict[str, tuple[Vector, Vector, str]]:
    bones = {}
    for name, (start, direction, total, _) in layout["fingers"].items():
        parent = f"hand.{side}"
        head = start
        for i, part in enumerate(SEGMENTS, 1):
            tail = head + direction * total * part
            bone = f"{name}.{i:02d}.{side}"
            bones[bone] = (head, tail, parent)
            parent, head = bone, tail
    return bones


def build_armature(j: dict[str, Vector], height: float, hands: dict[str, dict]) -> bpy.types.Object:
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
            f"hand.{side}": (wrist, wrist + hands[side]["a"] * hands[side]["palm"], f"forearm.{side}"),
            f"thigh.{side}": (hip, knee, "torso"),
            f"shin.{side}": (knee, ankle, f"thigh.{side}"),
            f"foot.{side}": (ankle, toe, f"shin.{side}"),
        })
        bones.update(finger_bones(side, hands[side]))
    arm = bpy.data.objects.new("rig", bpy.data.armatures.new("rig"))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for name, (head, tail, _) in bones.items():
        bone = arm.data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        pieces = name.split(".")
        if pieces[0] in FINGER_PARTS:
            # 指は Z 軸を手のひらの向きにそろえる。X 軸のまわりの回転で、指が手のひらの側へ曲がる(握る)
            bone.align_roll(hands[pieces[-1]]["n"])
        else:
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


def cut_hands(obj: bpy.types.Object, hands: dict[str, dict]) -> int:
    """
    作った形の手(指のくっついた形)を、手首の面で平らに切り取り、切り口をふさぐ。切るのは、自動の重みで手と指の骨に
    付いた所(ほかの所、たとえば手のそばの太ももは切らない)。消した頂点の数を返す。
    """
    names = {g.index: g.name for g in obj.vertex_groups}
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    before = len(bm.verts)
    deform = bm.verts.layers.deform.verify()

    def hand_weight(v: bmesh.types.BMVert, side: str) -> float:
        return sum(
            w for i, w in v[deform].items() if names[i].endswith(f".{side}") and names[i].split(".")[0] in HAND_PARTS
        )

    for side, layout in hands.items():
        wrist, a = layout["wrist"], layout["a"]
        # 手首の少し手前から先の、手の重みのある面だけを切る
        region = [f for f in bm.faces if any(hand_weight(v, side) > 0.2 for v in f.verts)]
        cut = bmesh.ops.bisect_plane(bm, geom=region + list({e for f in region for e in f.edges}) + list(
            {v for f in region for v in f.verts}), plane_co=wrist, plane_no=a, clear_outer=True)
        del cut
        # 切った面より先に残った、手の重みの大きい所(切る範囲の外にはみ出したかけら)も消す
        stray = [v for v in bm.verts if (v.co - wrist).dot(a) > 1e-4 and hand_weight(v, side) > 0.5]
        bmesh.ops.delete(bm, geom=stray, context="VERTS")
    boundary = [e for e in bm.edges if e.is_boundary]
    bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
    removed = before - len(bm.verts)
    bm.to_mesh(obj.data)
    bm.free()
    return removed




def make_hands(arm: bpy.types.Object, hands: dict[str, dict]) -> bpy.types.Object:
    """
    人形の手(手のひらは角の丸い箱、指の節は両端の丸い筒)を作り、節ごとの骨にそのまま付ける。どちらも球を
    超楕円体(各軸の座標を |x|^p にする。p が小さいほど箱に近い)にゆがめて作る。
    """
    bm = bmesh.new()
    parts: list[tuple[str, list]] = []

    def add(matrix: Matrix, bone: str, power: tuple[float, float, float]) -> None:
        new = bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=16, radius=1.0)["verts"]
        for v in new:
            v.co = Vector(tuple(math.copysign(abs(c) ** e, c) for c, e in zip(v.co, power)))
        bmesh.ops.transform(bm, matrix=matrix, verts=new)
        parts.append((bone, new))

    for side, layout in hands.items():
        a, w, n = layout["a"], layout["w"], layout["n"]
        size = layout["palm_size"]
        basis = Matrix((a * size.x, w * size.y, n * size.z)).transposed().to_4x4()
        add(Matrix.Translation(layout["palm_center"]) @ basis, f"hand.{side}", (0.35, 0.35, 0.7))
        for name, (start, direction, total, radius) in layout["fingers"].items():
            head = start
            p1 = n.cross(direction).normalized()
            p2 = direction.cross(p1).normalized()
            for i, part in enumerate(SEGMENTS, 1):
                seg = total * part
                r = radius * (1.0 - 0.07 * (i - 1))
                # 節どうしが少し重なるよう、長さの半分より長めにする
                basis = Matrix((direction * (seg / 2 * 1.12), p1 * r, p2 * r * 0.85)).transposed().to_4x4()
                add(Matrix.Translation(head + direction * seg / 2) @ basis, f"{name}.{i:02d}.{side}", (0.45, 1.0, 1.0))
                head = head + direction * seg
    bm.verts.index_update()
    groups: dict[str, list[int]] = {}
    for bone, verts in parts:
        groups.setdefault(bone, []).extend(v.index for v in verts)
    mesh = bpy.data.meshes.new("hands")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("hands", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for poly in mesh.polygons:
        poly.use_smooth = True
    for bone, indices in groups.items():
        obj.vertex_groups.new(name=bone).add(indices, 1.0, "REPLACE")
    obj.parent = arm
    obj.modifiers.new("armature", "ARMATURE").object = arm
    return obj


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
    hands = {side: hand_layout(j, key, height, joints["hand"]) for side, key in (("L", "left"), ("R", "right"))}
    arm = build_armature(j, height, hands)
    weights = bind(obj, arm)
    weights["hand_cut"] = cut_hands(obj, hands)
    make_hands(arm, hands)
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
        "weights": weights, "motions": sorted(motions), "preview": preview, "bones": len(arm.data.bones),
    }
    (folder / "rig.json").write_text(json.dumps(report), encoding="utf-8")


main()
