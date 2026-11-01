"""
Blender の中で動かすスクリプト(model_3d.rig から、画面なしで呼ぶ)。

    blender --background --factory-startup --python scripts/blender/rig_model.py -- <model_dir> <joints.json> <motions.json> <動きの名前>

model_dir/model_clean.glb(clean_model.py が整えた形。Z 上・正面 -Y・足の裏が 0)に、人型の骨を入れる:
- joints.json の関節の点(正面の静止画の骨格推定を、左右 x・高さ z に戻したもの)に、奥行き y を足す。y は、その (x, z) で
  正面から後ろへ光線を通し、形に入る所と出る所の真ん中にする。
- 骨の名前と親子は 2D モデル(body_rig)と同じ(torso・head・upper_arm/forearm/hand・thigh/shin/foot の .L/.R)ので、
  body_motions の動きをそのまま使える。2D の「絵の面の中の回転」は、正面から見た軸(Y)のまわりの回転にする。
- 重みは Blender の自動(heat)。うまく付かなかった頂点は、いちばん近い骨に付ける。そのあと、左右でくっついた膝から下を
  切り離し、反対側の脚の重みを外す(足をそろえて立つ絵から作ると、脚を開いたとき間が膜のように伸びる。切り離してから
  自動の重みを付けると、切り口の重なった頂点で全部失敗した)。
- 手は付け替える(Hunyuan3D-2mv は指を作れず、指のくっついたひれの形になる): 作った形の手首から先を切り取り、人形の手
  (手のひら+5本の指・3節ずつ。節ごとに骨)を置く。大きさは前腕の長さ、形の割合は joints.json の hand(素体の手の絵から
  hand_shape.measure が測ったもの)。
joints.json に shapes(body_shapes.to_json)があれば、形状パーツのシェイプキーを作り、shape_weights の強さを入れる
(GLB にはモーフとして入る)。motions.json の動きをすべてアクションにして(GLB には全部入る)、指定の動きで正面と斜めから描き出し(frames/*.png)、
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


def separate_legs(obj: bpy.types.Object, j: dict[str, Vector]) -> int:
    """
    左右の脚の、股より下のくっついた所を切り離す。素体の正面の絵は足をそろえて立つので、作った形は足首・足が左右で
    つながっていて、脚を開くとその間が膜のように伸びた(2026-11 実機)。太ももの付いた体型(矢野栄子)では太ももの間も
    つながっていたので、膝より下でなく股の下(腰の関節より腰幅の 0.55 倍下)まで切る。左右の足首と腰の真ん中の面
    (YZ 面)で、その高さより下の面だけを切り、切り口で分ける。重みを付けたあとに呼ぶ。切った辺の数を返す。
    """
    pelvis = mid(j["left_hip"], j["right_hip"])
    crotch = pelvis.z - 0.55 * (j["left_hip"] - j["right_hip"]).length
    x = (j["left_ankle"].x + j["right_ankle"].x + 2 * pelvis.x) / 4
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    faces = [f for f in bm.faces if all(v.co.z < crotch for v in f.verts)]
    edges = list({e for f in faces for e in f.edges})
    verts = list({v for f in faces for v in f.verts})
    result = bmesh.ops.bisect_plane(bm, geom=faces + edges + verts, plane_co=Vector((x, 0, 0)), plane_no=Vector((1, 0, 0)))
    cut = [e for e in result["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
    # 切った辺のうち、両側に面がある(左右の脚がつながっていた)所だけを分ける
    inner = [e for e in cut if len(e.link_faces) == 2]
    if inner:
        # 切り口はふさがない(股から足首までの長い輪を 1 枚の面でふさぐと、膝を曲げたとき面が引き伸ばされて板になった)。
        # 脚の内側に細い切れ目が残るだけ
        bmesh.ops.split_edges(bm, edges=inner)
    # 膝より下の頂点から、反対側の脚の骨の重みを外す(くっついた形で付けた重みは、左右が混ざっている)
    names = {g.index: g.name for g in obj.vertex_groups}
    deform = bm.verts.layers.deform.verify()
    for v in bm.verts:
        if v.co.z >= crotch:
            continue
        # 切り口の頂点は面の上にちょうど乗るので、つながる面(ふさいだ多角形を除く)の真ん中で左右を決める
        centers = [f.calc_center_median().x for f in v.link_faces if len(f.verts) <= 4]
        side_x = sum(centers) / len(centers) if centers else v.co.x
        other = ".R" if side_x > x else ".L"
        weights = v[deform]
        for i in [i for i in weights.keys() if names[i].endswith(other) and names[i].split(".")[0] in LEG_PARTS]:
            del weights[i]
        total = sum(weights.values())
        if total > 0:
            for i in list(weights.keys()):
                weights[i] /= total
    bm.to_mesh(obj.data)
    bm.free()
    return len(inner)


LEG_PARTS = {"thigh", "shin", "foot"}


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
    # 手首の切り口だけふさぐ(脚を切り離した切れ目はふさがない)
    boundary = [
        e for e in bm.edges if e.is_boundary
        and any((e.verts[0].co - layout["wrist"]).length < 0.08 for layout in hands.values())
    ]
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


# ---- 形状パーツ(シェイプキー。body_shapes.py) ----


def mesh_arrays(obj: bpy.types.Object) -> tuple:
    """頂点の位置・法線(n, 3)と、辺(m, 2)。"""
    import numpy as np

    mesh = obj.data
    n = len(mesh.vertices)
    co = np.empty(n * 3)
    mesh.vertices.foreach_get("co", co)
    normals = np.empty(n * 3)
    mesh.vertex_normals.foreach_get("vector", normals)
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    return co.reshape(n, 3), normals.reshape(n, 3), edges.reshape(-1, 2)


def laplacian(co, edges, iterations: int = 40, rate: float = 0.5):
    """となりの頂点の真ん中へ少しずつ寄せた、なめらかな形。"""
    import numpy as np

    smooth = co.copy()
    degree = np.bincount(edges.ravel(), minlength=len(co)).astype(float)[:, None]
    degree[degree == 0] = 1
    for _ in range(iterations):
        total = np.zeros_like(smooth)
        np.add.at(total, edges[:, 0], smooth[edges[:, 1]])
        np.add.at(total, edges[:, 1], smooth[edges[:, 0]])
        smooth += rate * (total / degree - smooth)
    return smooth


def components(mask, edges) -> list:
    """mask の頂点のうち、辺でつながったかたまり(頂点の番号の並び)。"""
    import numpy as np

    keep = edges[mask[edges[:, 0]] & mask[edges[:, 1]]]
    parent = np.arange(len(mask))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in keep:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[int, list[int]] = {}
    for i in np.nonzero(mask)[0]:
        groups.setdefault(find(int(i)), []).append(int(i))
    return list(groups.values())


def shape_anchors(co, normals, edges, j: dict[str, Vector]) -> dict:
    """
    部位の点・向き・大きさ(body_shapes の割合の基準)。胸の先は、胸の高さの左右でいちばん前の頂点。へそは、胴の真ん中で
    いちばん深くへこんだ所(まわり 8mm の平均。見つからなければ、腰と肩の間の 3 割の高さの前の面)。お腹は、へその少し下。
    """
    import numpy as np

    pelvis = mid(j["left_hip"], j["right_hip"])
    neck = mid(j["left_shoulder"], j["right_shoulder"])
    torso = neck.z - pelvis.z
    hip_width = (j["left_hip"] - j["right_hip"]).length

    def normal_at(index: int):
        near = np.linalg.norm(co - co[index], axis=1) < 0.01
        n = normals[near].mean(axis=0)
        return n / max(np.linalg.norm(n), 1e-9)

    anchors: dict[str, dict] = {}
    chest = (co[:, 2] > pelvis.z + 0.5 * torso) & (co[:, 2] < neck.z - 0.08 * torso) & (normals[:, 1] < -0.3)
    apexes = {}
    for side, sign in (("L", 1), ("R", -1)):
        pick = chest & ((co[:, 0] - pelvis.x) * sign > 0.02)
        if pick.any():
            apexes[side] = int(np.nonzero(pick)[0][np.argmin(co[pick, 1])])
    if len(apexes) == 2:
        half = abs(co[apexes["L"], 0] - co[apexes["R"], 0]) / 2
        for side, index in apexes.items():
            point = {"center": co[index].copy(), "normal": normal_at(index)}
            anchors[f"breast.{side}"] = {**point, "size": half}
            anchors[f"nipple.{side}"] = {**point, "size": half * 0.18}
            anchors[f"areola.{side}"] = {**point, "size": half * 0.18}
    smooth = laplacian(co, edges, 20)
    depth = ((smooth - co) * normals).sum(axis=1)  # 正 = なめらかな面より内へへこむ
    belly = (
        (np.abs(co[:, 0] - pelvis.x) < 0.02) & (co[:, 2] > pelvis.z + 0.1 * torso) & (co[:, 2] < pelvis.z + 0.5 * torso)
        & (normals[:, 1] < -0.5)
    )
    navel = None
    if belly.any():
        # 頂点 1 つの深さはばらつくので、まわり 8mm の深さの平均がいちばん深い頂点にする
        picks = np.nonzero(belly)[0]
        near = np.linalg.norm(co[picks][:, None, :] - co[picks][None, :, :], axis=2) < 0.008
        mean_depth = (near * depth[picks][None, :]).sum(axis=1) / near.sum(axis=1)
        index = int(picks[np.argmax(mean_depth)])
        if mean_depth.max() > 0.002:
            navel = index
    if navel is None:
        front = belly & (np.abs(co[:, 2] - (pelvis.z + 0.3 * torso)) < 0.01)
        if front.any():
            navel = int(np.nonzero(front)[0][np.argmin(co[front, 1])])
    if navel is not None:
        anchors["navel"] = {"center": co[navel].copy(), "normal": normal_at(navel), "size": hip_width}
        below = co[navel] + np.array([0.0, 0.0, -0.04 * torso])
        anchors["belly"] = {"center": below, "normal": normal_at(navel), "size": hip_width}
    return anchors


def repair_delta(co, normals, edges, j: dict[str, Vector], params: dict, keep_out: list) -> tuple:
    """
    小さなへこみ(深さ depth m 以上、かたまりの大きさ size m 以下)を、なめらかな面へ寄せる動き。顔・股と keep_out
    (点と半径)のまわりは除く。大きなくぼみ(脇・胸の下・お尻のすじ)は形のうちなので残す。直したかたまりの数も返す。
    """
    import numpy as np

    pelvis = mid(j["left_hip"], j["right_hip"])
    neck = mid(j["left_shoulder"], j["right_shoulder"])
    knee = min(j["left_knee"].z, j["right_knee"].z)
    hip_width = (j["left_hip"] - j["right_hip"]).length
    smooth = laplacian(co, edges, 40)
    inward = ((smooth - co) * normals).sum(axis=1)
    candidate = inward > params["depth"]
    candidate &= co[:, 2] < neck.z  # 顔(目・口・耳)
    crotch = (co[:, 2] < pelvis.z) & (co[:, 2] > knee) & (np.abs(co[:, 0] - pelvis.x) < hip_width * 0.35)
    candidate &= ~crotch
    for point, radius in keep_out:
        candidate &= np.linalg.norm(co - point, axis=1) > radius
    delta = np.zeros_like(co)
    fixed = 0
    for group in components(candidate, edges):
        pts = co[group]
        if np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)) > params["size"]:
            continue
        # かたまりと、その 1 つ外の輪まで寄せる(輪は半分だけ。段がつかないように)
        members = set(group)
        touching = edges[np.isin(edges[:, 0], group) | np.isin(edges[:, 1], group)]
        ring = sorted({int(x) for x in touching.ravel()} - members)
        delta[group] = smooth[group] - co[group]
        delta[ring] = 0.5 * (smooth[ring] - co[ring])
        fixed += 1
    return delta, fixed


# 胸の先を作り直す範囲(胸の幅の半分に対する割合)と、当てはめに使う外の輪の外側
CAP_RADIUS = 0.3
CAP_RING = 1.5


def cap_delta(co, normals, edges, anchor: dict) -> tuple:
    """
    胸の先のまわり(半径 CAP_RADIUS × 胸の幅の半分)を、その外の輪の面から当てはめたなめらかな面(2 次の面)に置き換える動き。
    AI の形は、素体の絵の乳首・乳輪の陰影を、くしゃっとした穴にした(矢野栄子の裸の素体、2026-11 実機)。となりの頂点に
    寄せるなめらかさは面の細かさで効き方が変わる(細かくした所では見つからなかった)ので、距離で決まる当てはめにする。
    いちばん深くへこんでいた所(まわり 4mm の平均で 2mm 以上。絵の乳首があった所)の頂点の番号も返す(無ければ None)。
    """
    import numpy as np

    n = anchor["normal"]
    radius = CAP_RADIUS * anchor["size"]
    u = np.cross(n, [0.0, 0.0, 1.0])
    u = u / max(np.linalg.norm(u), 1e-9)
    v = np.cross(n, u)
    offset = co - anchor["center"]
    a, b, w = offset @ u, offset @ v, offset @ n
    r = np.hypot(a, b)
    ring = ((normals @ n) > 0.2) & (r > radius) & (r < CAP_RING * radius) & (np.abs(w) < 2 * radius)
    delta = np.zeros_like(co)
    if ring.sum() < 12:
        return delta, None

    def basis(m):
        return np.stack([np.ones(m.sum()), a[m], b[m], a[m] ** 2, b[m] ** 2, a[m] * b[m]], axis=1)

    coef, *_ = np.linalg.lstsq(basis(ring), w[ring], rcond=None)
    # 穴の三角形は横・内を向いているので、頂点の向きでなく、当てはめた面からの距離で選ぶ(胸の先の点からの奥行きで選ぶと、
    # 丸い胸の下のほうが外れて、境目に弧の筋が出た)
    near = r <= radius
    fit_all = np.zeros(len(co))
    fit_all[near] = basis(near) @ coef
    # 穴は深いので奥へは半径まで、手前へは浅く。そのうち、胸の先の点から辺でつながった所だけ(正面から見て重なる、
    # 胸の裏側などの離れた面を拾わないように)
    candidate = near & (w - fit_all > -radius) & (w - fit_all < 0.3 * radius)
    if not candidate.any():
        return delta, None
    start = int(np.nonzero(candidate)[0][np.argmin(np.linalg.norm(co[candidate] - anchor["center"], axis=1))])
    inner = np.zeros(len(co), dtype=bool)
    inner[start] = True
    local = edges[candidate[edges[:, 0]] & candidate[edges[:, 1]]]
    while True:
        grow = (inner[local[:, 0]] & ~inner[local[:, 1]]) | (inner[local[:, 1]] & ~inner[local[:, 0]])
        if not grow.any():
            break
        inner[local[grow].ravel()] = True
    target = fit_all[inner]
    # 半径の 6 割から外へ向けて、なめらかに元の面へ戻す(戻す幅が狭いと、ふちに輪の段が見えた。広いと、穴のまわりの
    # 折れが半分残った)
    t = np.clip((radius - r[inner]) / (0.4 * radius), 0, 1)
    blend = t * t * (3 - 2 * t)
    delta[inner] = ((target - w[inner]) * blend)[:, None] * n[None, :]
    # 穴の三角形は横にも折り重なっているので、真ん中(半径の 6 割の内側。穴はここに入る)では面に沿う向きにもならし、
    # そのたびに当てはめた面の上へ戻す(戻さないと、ならした頂点が浮き沈みして新しいしわになった)
    picks = np.nonzero(inner)[0]
    core = picks[r[inner] < 0.6 * radius]
    if len(core):
        inside = np.zeros(len(co), dtype=bool)
        inside[core] = True
        local = edges[inside[edges[:, 0]] | inside[edges[:, 1]]]
        degree = np.bincount(local.ravel(), minlength=len(co)).astype(float)
        degree[degree == 0] = 1
        plane = np.stack([a, b], axis=1)
        for _ in range(60):
            total = np.zeros_like(plane)
            np.add.at(total, local[:, 0], plane[local[:, 1]])
            np.add.at(total, local[:, 1], plane[local[:, 0]])
            plane[core] += 0.5 * (total[core] / degree[core, None] - plane[core])
        pa, pb = plane[core, 0], plane[core, 1]
        fit = np.stack([np.ones(len(core)), pa, pb, pa ** 2, pb ** 2, pa * pb], axis=1) @ coef
        moved = anchor["center"] + pa[:, None] * u[None, :] + pb[:, None] * v[None, :] + fit[:, None] * n[None, :]
        delta[core] = moved - co[core]
    sunk = target - w[inner]
    near = np.linalg.norm(co[picks][:, None, :] - co[picks][None, :, :], axis=2) < 0.004
    mean_sunk = (near * sunk[None, :]).sum(axis=1) / near.sum(axis=1)
    crater = int(picks[np.argmax(mean_sunk)]) if mean_sunk.max() > 0.002 else None
    return delta, crater


def bump_delta(co, normals, anchor: dict, params: dict):
    """部位の点のまわりを、面の向きへ盛り上げる・へこませる(op=bump)か、上へ動かす(op=lift)動き。"""
    import numpy as np

    radius = params["radius"] * anchor["size"]
    height = params["height"] * anchor["size"]
    offset = co - anchor["center"]
    t = np.linalg.norm(offset, axis=1) / max(radius, 1e-6)
    # 反対側の面(背中など)は動かさない。小さな部位では頂点の向きでなく、点の前か後ろかで決める(細かくした所の頂点の
    # 向きがばらつき、動かなかった頂点が細いひびになった)
    if radius < 0.03:
        facing = (offset @ anchor["normal"]) > -radius
    else:
        facing = (normals @ anchor["normal"]) > 0.2
    edge = float(params.get("edge", 0.0))
    f = np.clip((1 - t) / max(1 - 0.9 * edge, 1e-3), 0, 1)
    f = f * f * (3 - 2 * f)
    f[~facing | (t >= 1)] = 0
    direction = np.array([0.0, 0.0, 1.0]) if params["op"] == "lift" else anchor["normal"]
    return f[:, None] * height * direction[None, :]


# 形状パーツで細かく形を作る所(部位の点のまわり、半径 m)。8 万面のままでは辺が 6〜8mm あり、半径 5mm ほどの乳首が
# 頂点 10 個ほどでしか形にならなかった(2026-11 実機)
REFINE = {"nipple": 0.035, "navel": 0.025}


def refine_regions(obj: bpy.types.Object, anchors: dict) -> int:
    """形状パーツで細かく形を作る所(anchors のまわり)の面を、辺を 3 つに分けて細かくする。重みを付けたあとに呼ぶ。分けた辺の数を返す。"""
    import numpy as np

    points = [(a["center"], REFINE[name.split(".")[0]]) for name, a in anchors.items() if name.split(".")[0] in REFINE]
    if not points:
        return 0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    picked = []
    for e in bm.edges:
        a, b = (np.array(v.co) for v in e.verts)
        if any(np.linalg.norm(a - c) < r and np.linalg.norm(b - c) < r for c, r in points):
            picked.append(e)
    if picked:
        bmesh.ops.subdivide_edges(bm, edges=picked, cuts=3, use_grid_fill=True)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
        # 面の内側に足された頂点は重みが無いので、辺をたどっていちばん近い、重みのある頂点から写す
        deform = bm.verts.layers.deform.verify()
        for v in [v for v in bm.verts if not v[deform]]:
            seen, frontier = {v}, [v]
            while frontier:
                found = next((w for w in frontier if w[deform]), None)
                if found is not None:
                    for group, weight in found[deform].items():
                        v[deform][group] = weight
                    break
                frontier = [w for u in frontier for e in u.link_edges for w in [e.other_vert(u)] if w not in seen]
                seen.update(frontier)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(picked)


def add_shape_keys(obj: bpy.types.Object, j: dict[str, Vector], shapes: dict, weights: dict[str, float], anchors: dict) -> dict:
    """
    体の形状パーツのシェイプキーを作る(形ごとに 1 つ。左右のある部位は左右とも)。weights の強さを入れる。部位の点は
    anchors(面を細かくする前に決め、胸の先は bake_caps で作り直した面の上に移したもの)。乳首・乳輪は部品(make_parts)
    なので作らないが、陥没乳首のように体をへこませる形(socket)は、その形の名前の体のシェイプキーにする。
    """
    import numpy as np

    co, normals, edges = mesh_arrays(obj)
    obj.shape_key_add(name="Basis", from_mix=False)
    made: dict[str, dict] = {}
    for key, preset in shapes["presets"].items():
        part = preset["part"]
        names = [f"{part}.{s}" for s in ("L", "R")] if shapes["parts"][part]["sides"] else [part]
        found = [anchors[n] for n in names if n in anchors]
        if preset["op"] == "repair":
            # 細かくした所(胸の先・へそ)は、となりに寄せるなめらかさの効き方が違うので除く(胸の先は bake_caps で直してある)
            keep_out = [(a["center"], REFINE[n.split(".")[0]]) for n, a in anchors.items() if n.split(".")[0] in REFINE]
            delta, fixed = repair_delta(co, normals, edges, j, preset, keep_out)
            made[key] = {"fixed": fixed}
        elif preset["op"] == "part":
            if "socket_depth" not in preset or not found:
                continue
            delta = sum(socket_delta(co, a, preset) for a in found)
            made[key] = {"socket": int((np.linalg.norm(delta, axis=1) > 1e-5).sum())}
        else:
            if not found:
                continue
            delta = sum(bump_delta(co, normals, a, preset) for a in found)
            made[key] = {"moved": int((np.linalg.norm(delta, axis=1) > 1e-5).sum())}
        block = obj.shape_key_add(name=key, from_mix=False)
        block.data.foreach_set("co", (co + delta).ravel())
    for block in obj.data.shape_keys.key_blocks[1:]:
        block.slider_max = 2.0
        block.value = float(weights.get(block.name, 0.0))
    return {
        "keys": made,
        "anchors": {k: [round(float(c), 4) for c in a["center"]] for k, a in anchors.items()},
    }


def underbust_z(obj: bpy.types.Object, x: float, z_from: float, z_to: float) -> float | None:
    """
    左右 x の、正面から見た胸の下のふちの高さ。上から下へ正面の面をたどり、面がいちばん大きく奥へ引っ込む所
    (胸の下で、胴へ戻る所)。引っ込みが 5mm に満たなければ None。
    """
    step = 0.002
    prev = None
    best = (0.0, None)
    z = z_from
    while z > z_to:
        ok, loc, _, _ = obj.ray_cast(Vector((x, -10.0, z)), Vector((0, 1, 0)))
        if ok:
            if prev is not None and loc.y - prev > best[0]:
                best = (loc.y - prev, z + step / 2)
            prev = loc.y
        z -= step
    return best[1] if best[0] > 0.005 else None


def place_nipples(obj: bpy.types.Object, anchors: dict, nipples: dict, height: float, torso: float = 0.0) -> dict:
    """
    乳首・乳輪の点を、正面の絵から測った位置(nipples の hint。身長を 1 とした左右 x・高さ z。胸の下のふちからの距離 gaps が
    あれば、高さは 3D の胸の下のふちからその距離)に、ずらす量(offsets。mm)を
    足した所へ置く。その (x, z) に正面から光線を当て、体の前の面に当たった所にする。面を細かくする前に呼ぶ(細かくする所も、
    この位置に合わせる)。置いた点には placed を付け、bake_caps で上書きしない。
    """
    import numpy as np

    anchors = dict(anchors)
    hint = nipples.get("hint") or {}
    offsets = nipples.get("offsets") or {}
    for side in ("L", "R"):
        dx, dz = (offsets.get(side) or [0.0, 0.0])
        base = hint.get(side)
        if base is not None:
            x, z = base[0] * height, base[1] * height
            # 絵の胸の下のふちからの距離があれば、3D の胸の下のふちからその距離に置く(3D の胸が絵ほど下がっていなくても、
            # 胸の上の位置が絵に合う)
            gap = (nipples.get("hint") or {}).get("gaps", {}).get(side)
            if gap is not None and torso > 0:
                bottom = underbust_z(obj, x, z + 0.06, z - 0.12)
                if bottom is not None:
                    z = bottom + gap * torso
        elif (dx or dz) and f"nipple.{side}" in anchors:
            # 絵から測れなかったときは、胸の形から決めた点をずらす
            center = anchors[f"nipple.{side}"]["center"]
            x, z = float(center[0]), float(center[2])
        else:
            continue
        x, z = x + dx / 1000, z + dz / 1000
        ok, loc, normal, _ = obj.ray_cast(Vector((x, -10.0, z)), Vector((0, 1, 0)))
        if not ok:
            continue
        for part in ("nipple", "areola"):
            name = f"{part}.{side}"
            if name in anchors:
                anchors[name] = {**anchors[name], "center": np.array(loc), "normal": np.array(normal), "placed": True}
    return anchors


def bake_caps(obj: bpy.types.Object, anchors: dict) -> dict:
    """
    胸の先を、なめらかな面に作り直して体の形(シェイプキーの前の形)に焼き込む(乳首・乳輪は、この上に部品として付ける)。
    乳首・乳輪の点は、穴があった所(絵の乳首の所。無ければ胸のいちばん前の点)の、作り直した面の上にする。
    向きは、作り直した面のまわりの点のならびから求める。直した anchors を返す。
    """
    import numpy as np

    co, normals, edges = mesh_arrays(obj)
    anchors = dict(anchors)
    delta = np.zeros_like(co)
    craters: dict[str, int] = {}
    for side in ("L", "R"):
        if f"breast.{side}" in anchors:
            cap, crater = cap_delta(co, normals, edges, anchors[f"breast.{side}"])
            delta += cap
            if crater is not None:
                craters[side] = crater
    baked = co + delta
    obj.data.vertices.foreach_set("co", baked.ravel())
    obj.data.update()
    for side in ("L", "R"):
        for part in ("nipple", "areola"):
            name = f"{part}.{side}"
            if name not in anchors:
                continue
            index = craters.get(side)
            if index is None or anchors[name].get("placed"):
                # 絵から測って置いた点(placed)は、その場所のまま作り直した面の上へ移す
                index = int(np.argmin(np.linalg.norm(co - anchors[name]["center"], axis=1)))
            center = baked[index].copy()
            near = np.linalg.norm(baked - center, axis=1) < 0.012
            normal = anchors[name]["normal"]
            if near.sum() >= 6:
                pts = baked[near] - baked[near].mean(axis=0)
                fitted = np.linalg.svd(pts, full_matrices=False)[2][-1]
                normal = fitted if fitted @ normal > 0 else -fitted
            anchors[name] = {**anchors[name], "center": center, "normal": normal}
    return anchors


# 部品の円盤の細かさ(部位 → 輪の数・まわりの分け数)。乳輪は陥没の細い切れ目に沿わせるので細かく(粗いと切れ目の縁が
# 放射状の筋になった)
PART_GRID = {"nipple": (12, 36), "areola": (24, 72)}
# 部品の、体の面からの浮き(真ん中)と、ふちの沈み(m)。ふちを体の面の少し内へ入れて、境目を体に溶け込ませる
PART_LIFT = 0.0003


def profile(t, edge: float):
    """部品の高さの形(0 が真ん中、1 がふち)。edge が大きいほど筒のように切り立つ。ふちで高さも傾きも 0。"""
    import numpy as np

    f = np.clip((1 - t) / max(1 - 0.9 * edge, 1e-3), 0, 1)
    return f * f * (3 - 2 * f)


def body_surface(obj: bpy.types.Object) -> tuple:
    """体の面(シェイプキーの前の形)の三角形の木と、三角形ごとの頂点の番号。"""
    import numpy as np
    from mathutils.bvhtree import BVHTree

    mesh = obj.data
    mesh.calc_loop_triangles()
    co = np.array([v.co for v in mesh.vertices])
    tris = np.array([t.vertices[:] for t in mesh.loop_triangles])
    tree = BVHTree.FromPolygons([Vector(p) for p in co], tris.tolist())
    return tree, tris, co


def bind_points(points, tree, tris, co) -> tuple:
    """部品の点ごとに、いちばん近い体の三角形と、その中の位置(重心座標)。体の面への「つなぎ止め」。"""
    import numpy as np

    rows, bary = [], []
    for p in points:
        loc, _, index, _ = tree.find_nearest(Vector(p))
        a, b, c = co[tris[index]]
        v0, v1, v2 = b - a, c - a, np.array(loc) - a
        d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
        d20, d21 = v2 @ v0, v2 @ v1
        denom = max(d00 * d11 - d01 * d01, 1e-18)
        w1 = (d11 * d20 - d01 * d21) / denom
        w2 = (d00 * d21 - d01 * d20) / denom
        rows.append(tris[index])
        bary.append(np.clip([1 - w1 - w2, w1, w2], 0, 1))
    bary = np.array(bary)
    return np.array(rows), bary / bary.sum(axis=1, keepdims=True)


def part_mesh(anchor: dict, params: dict, tree) -> tuple:
    """
    部品の円盤(真ん中の点+輪)の頂点・面・ふちからの割合 t。頂点は、体の面へ正面から当てた所から、面の向きへ形の高さだけ
    浮かせる。ふちは体の面の少し内へ沈める。
    """
    import numpy as np

    n = np.array(anchor["normal"])
    u, v = frame(n)
    radius = params["radius"] * anchor["size"]
    height = params["height"] * anchor["size"]
    # aspect があれば横長の楕円にする(面積は同じ。陥没の切れ目の奥の乳首)
    stretch = math.sqrt(float(params.get("aspect", 1.0)))
    rings, segments = PART_GRID.get(params.get("part", ""), (12, 36))
    local = [(0.0, 0.0)]
    for i in range(1, rings + 1):
        r = radius * i / rings
        for k in range(segments):
            a = 2 * math.pi * k / segments
            local.append((r * math.cos(a) * stretch, r * math.sin(a) / stretch))
    verts, ts = [], []
    for a, b in local:
        start = np.array(anchor["center"]) + a * u + b * v + n * 0.03
        hit = tree.ray_cast(Vector(start), Vector(-n), 0.08)
        if hit[0] is None:
            hit = tree.find_nearest(Vector(start - n * 0.03))
        # 浮かせる向きは部位の点の面の向き(当たった三角形の向きは、穴の名残の裏返った小さな三角形で乱れ、こぶになった)
        loc, normal = np.array(hit[0]), n
        t = math.hypot(a / stretch, b * stretch) / max(radius, 1e-9)
        lift = height * float(profile(np.array([t]), float(params.get("edge", 0.0)))[0])
        lift += PART_LIFT * (1 - 1.5 * t ** 6)
        verts.append(loc + normal * lift)
        ts.append(t)
    faces = [(0, 1 + k, 1 + (k + 1) % segments) for k in range(segments)]
    for i in range(1, rings):
        inner, outer = 1 + (i - 1) * segments, 1 + i * segments
        for k in range(segments):
            k2 = (k + 1) % segments
            faces.append((inner + k, outer + k, outer + k2, inner + k2))
    return np.array(verts), faces, np.array(ts)


def frame(normal) -> tuple:
    """部位の点の、横(u。体の左右)と縦(v)の向き。normal は面の向き。"""
    import numpy as np

    n = np.array(normal)
    u = np.cross(n, [0.0, 0.0, 1.0])
    u = u / max(np.linalg.norm(u), 1e-9)
    return u, np.cross(n, u)


def socket_delta(co, anchor: dict, params: dict):
    """
    陥没乳首の体のへこみ。乳輪の真ん中に、横に長い切れ目(横:縦 = socket_aspect)のくぼみを、socket_depth の深さで作り、
    切れ目のふちに唇のような盛り上がり(高さ socket_lip、幅 socket_lip_width、折り返し socket_lip_roll)を付ける(利用者の参考の絵: 丸い穴でなく横長の切れ目で、上下が少し
    盛り上がって内へ巻き込む)。
    """
    import numpy as np

    n = np.array(anchor["normal"])
    u, v = frame(n)
    radius = params["socket_radius"] * anchor["size"]
    aspect = float(params.get("socket_aspect", 1.0))
    offset = co - anchor["center"]
    a, b, w = offset @ u, offset @ v, offset @ n
    t = np.hypot(a / (radius * np.sqrt(aspect)), b * np.sqrt(aspect) / radius)
    near = np.abs(w) < 2 * radius
    dent = -params["socket_depth"] * anchor["size"] * profile(t, 0.5)
    # 唇の幅(socket_lip_width。切れ目の大きさに対する割合)。広いほど、なだらかに盛り上がる
    width = float(params.get("socket_lip_width", 0.45))
    lip = float(params.get("socket_lip", 0.0)) * anchor["size"] * np.exp(-(((t - 1.05) / width) ** 2))
    lift = np.where(near & (t < 2.0), dent + lip, 0.0)
    delta = lift[:, None] * n[None, :]
    # 唇の折り返し(socket_lip_roll): 唇のあたりを、面に沿って切れ目の中心線(横の線)へ寄せる。上の唇は下へ、下の唇は上へ
    # 寄って切れ目に覆いかぶさり、巻き込んだ厚みになる(利用者の要望)
    roll = float(params.get("socket_lip_roll", 0.0)) * anchor["size"]
    if roll > 0:
        ring = np.exp(-(((t - 0.95) / (0.8 * width)) ** 2))
        ring = np.where(near & (t < 2.0) & (np.abs(b) > 1e-6), ring, 0.0)
        delta += (-np.sign(b) * roll * ring)[:, None] * v[None, :]
    return delta


def add_part_bones(arm: bpy.types.Object, anchors: dict) -> list[str]:
    """乳首の点に、小さな骨(nipple.L / nipple.R。胴の子)を足す。乳首の部品の真ん中はこの骨に付く。"""
    names = []
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for side in ("L", "R"):
        anchor = anchors.get(f"nipple.{side}")
        if anchor is None:
            continue
        bone = arm.data.edit_bones.new(f"nipple.{side}")
        center = Vector(anchor["center"])
        bone.head, bone.tail = center, center + Vector(anchor["normal"]) * 0.01
        bone.align_roll(Vector((0, 0, 1)))
        bone.parent = arm.data.edit_bones["torso"]
        names.append(bone.name)
    bpy.ops.object.mode_set(mode="OBJECT")
    return names


def make_parts(body: bpy.types.Object, arm: bpy.types.Object, shapes: dict, weights: dict, anchors: dict) -> dict:
    """
    乳首・乳輪の部品(形ごと・左右ごとの円盤のオブジェクト part_<形>_<L|R>)を作り、体の面につなぎ止める:
    - 骨の重み: つなぎ止めた体の三角形の頂点の重みを写す。乳首は真ん中へ向かって乳首の骨の割合を増やす(ふちは体に従い、
      真ん中は乳首の形を保つ。組織のつながりのイメージ)
    - 体の形(シェイプキー): 体のシェイプキーの動きを同じ名前で写し、Blender では体のキーの強さに連動させる(ドライバー)
    - 乳首は乳輪の上に乗るよう、乳輪の形ごとに、その形の高さだけ持ち上げるシェイプキー(乳輪の形と同じ名前)を持つ
    選んでいない形(weights が 0)の部品は隠す。作った部品の数などを返す。
    """
    import numpy as np

    tree, tris, co = body_surface(body)
    body_normals = np.array([v.normal for v in body.data.vertices])
    keys = body.data.shape_keys.key_blocks if body.data.shape_keys else []
    basis = np.array([v.co for v in keys[0].data]) if keys else co
    deltas = {k.name: np.array([v.co for v in k.data]) - basis for k in list(keys)[1:]}
    names = {g.index: g.name for g in body.vertex_groups}
    groups = [{names[g.group]: g.weight for g in v.groups} for v in body.data.vertices]
    areolas = {k: p for k, p in shapes["presets"].items() if p["part"] == "areola" and p["op"] == "part"}
    nipple_bones = add_part_bones(arm, anchors)
    made = {}
    for key, preset in shapes["presets"].items():
        if preset["op"] != "part":
            continue
        for side in ("L", "R"):
            anchor = anchors.get(f"{preset['part']}.{side}")
            if anchor is None:
                continue
            verts, faces, ts = part_mesh(anchor, preset, tree)
            rows, bary = bind_points(verts, tree, tris, co)
            mesh = bpy.data.meshes.new(f"part_{key}_{side}")
            mesh.from_pydata([Vector(p) for p in verts], [], faces)
            for poly in mesh.polygons:
                poly.use_smooth = True
            # ふちの近くでは、面の向き(法線)を、つなぎ止めた体の面の向きに寄せる(部品の向きのままだと、ふちが陰影の線に
            # なって見えた)。ふちの外側 4 割で、なだらかに体の向きへ
            own = np.array([v.normal for v in mesh.vertices])
            under = (body_normals[rows] * bary[:, :, None]).sum(axis=1)
            w = np.clip((ts - 0.6) / 0.4, 0, 1)
            w = (w * w * (3 - 2 * w))[:, None]
            blended = (1 - w) * own + w * under
            blended /= np.maximum(np.linalg.norm(blended, axis=1, keepdims=True), 1e-9)
            mesh.normals_split_custom_set_from_vertices([Vector(n) for n in blended])
            obj = bpy.data.objects.new(mesh.name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            # 骨の重み: 体の三角形の頂点の重みを、重心座標で混ぜる(4 本まで)。乳首は真ん中ほど乳首の骨
            bone = f"nipple.{side}" if preset["part"] == "nipple" and f"nipple.{side}" in nipple_bones else None
            for i in range(len(verts)):
                mixed: dict[str, float] = {}
                for vi, b in zip(rows[i], bary[i]):
                    for g, w in groups[vi].items():
                        mixed[g] = mixed.get(g, 0.0) + b * w
                if bone is not None:
                    s = 0.8 * (1 - ts[i])
                    mixed = {g: w * (1 - s) for g, w in mixed.items()}
                    mixed[bone] = mixed.get(bone, 0.0) + s
                top = sorted(mixed.items(), key=lambda x: -x[1])[:4]
                total = sum(w for _, w in top) or 1.0
                for g, w in top:
                    group = obj.vertex_groups.get(g) or obj.vertex_groups.new(name=g)
                    group.add([i], w / total, "REPLACE")
            obj.parent = arm
            obj.modifiers.new("armature", "ARMATURE").object = arm
            # 体のシェイプキーの動きを写し、体のキーに連動させる
            obj.shape_key_add(name="Basis", from_mix=False)
            for name, delta in deltas.items():
                moved = (delta[rows] * bary[:, :, None]).sum(axis=1)
                if np.abs(moved).max() < 1e-6:
                    continue
                block = obj.shape_key_add(name=name, from_mix=False)
                block.data.foreach_set("co", (verts + moved).ravel())
                block.slider_max = 2.0
                driver = block.driver_add("value").driver
                driver.type = "AVERAGE"
                var = driver.variables.new()
                var.type = "SINGLE_PROP"
                var.targets[0].id_type = "KEY"
                var.targets[0].id = body.data.shape_keys
                var.targets[0].data_path = f'key_blocks["{name}"].value'
            # 乳首: 乳輪の形ごとに、その高さだけ持ち上げる(乳輪を選ぶと、乳首がその上に乗る)
            if preset["part"] == "nipple":
                areola_anchor = anchors.get(f"areola.{side}")
                for akey, aparams in areolas.items():
                    if areola_anchor is None:
                        break
                    r = np.linalg.norm(verts - np.array(areola_anchor["center"]), axis=1)
                    t = r / max(aparams["radius"] * areola_anchor["size"], 1e-9)
                    lift = aparams["height"] * areola_anchor["size"] * profile(t, float(aparams.get("edge", 0.0)))
                    if lift.max() < 1e-6:
                        continue
                    block = obj.shape_key_add(name=akey, from_mix=False)
                    block.data.foreach_set("co", (verts + lift[:, None] * np.array(areola_anchor["normal"])[None, :]).ravel())
                    block.value = float(weights.get(akey, 0.0))
            visible = float(weights.get(key, 0.0)) > 0
            obj.hide_render = not visible
            obj.hide_viewport = not visible
            made[obj.name] = len(verts)
    return {"parts": made, "bones": nipple_bones}


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
    weights["legs_split"] = separate_legs(obj, j)
    if joints.get("shapes"):
        # 部位の点は、細かくする前の形で決める。乳首は、正面の絵から測った位置があればそこ(ずらす量も足す)
        anchors = shape_anchors(*mesh_arrays(obj), j)
        if joints.get("nipples"):
            torso = mid(j["left_shoulder"], j["right_shoulder"]).z - mid(j["left_hip"], j["right_hip"]).z
            anchors = place_nipples(obj, anchors, joints["nipples"], height, torso)
        # 細かくするのは重みを付けたあと(先に細かくすると、自動の重みが全部失敗した)。新しい頂点の重みはとなりから写す
        weights["refined_edges"] = refine_regions(obj, anchors)
        weights["refined_unweighted"] = sum(1 for v in obj.data.vertices if not any(g.weight > 1e-4 for g in v.groups))
    weights["hand_cut"] = cut_hands(obj, hands)
    make_hands(arm, hands)
    shapes = joints.get("shapes")
    shape_report = None
    if shapes:
        shape_weights = joints.get("shape_weights", {})
        # 乳首・乳輪を部品で付けるモデルでは、胸の先をなめらかな面に作り直して体の形に焼き込む
        if "nipple" in shapes["parts"]:
            anchors = bake_caps(obj, anchors)
        shape_report = add_shape_keys(obj, j, shapes, shape_weights, anchors)
        shape_report.update(make_parts(obj, arm, shapes, shape_weights, anchors))
    # 形の値の試し(joints.json の preview_only): 形だけ見るので、動き・確かめのコマ・Blender のファイルは作らない
    preview_only = bool(joints.get("preview_only"))
    actions = {} if preview_only else {name: add_motion(arm, name, motion, height) for name, motion in motions.items()}
    for pb in arm.pose.bones:
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector()
    bpy.context.scene.render.fps = 24
    bpy.ops.export_scene.gltf(
        filepath=str(folder / "model_rigged.glb"), export_format="GLB", export_animations=not preview_only,
        # モーフの法線も入れる(入れないと、ブラウザ(three.js)は元の形の法線で陰影を付け、ならした胸の先の穴がそのまま
        # 見えた。Blender は形を変えたあと法線を計算し直すので、Blender の絵では出なかった)
        export_animation_mode="NLA_TRACKS", export_morph_normal=True,
    )
    if not preview_only:
        render_motion(arm, actions[preview], motions[preview]["frames"], height, folder / "frames")
        bpy.ops.wm.save_as_mainfile(filepath=str(folder / "model.blend"))
    report = {
        "joints": {k: [round(c, 4) for c in v] for k, v in j.items()},
        "weights": weights, "motions": sorted(motions), "preview": preview, "bones": len(arm.data.bones),
        "shapes": shape_report,
    }
    (folder / "rig.json").write_text(json.dumps(report), encoding="utf-8")


main()
