"""
スカートの裾の揺れ(Blender の中で使う。dress_model.py から読み込む)。

1. 鎖の骨: スカートのまわりに CHAINS 本、ウエストから裾まで SEGMENTS 個の骨をつなげて入れる(根もとは胴の骨の子)。
   骨の関節は、その向きのスカートの面の、高さごとの点。スカートの重みは、この骨に付け直す(向きと高さで、となりの
   鎖・となりの骨と混ぜる)。
2. 揺らす: 動きごとに、動きを 2 回くり返して、関節を点として動かす(重力・遅れ・胴に付いて動いたときの位置へ戻る
   ばね・骨の長さ・腿とすねのカプセルに当たると押し出す)。2 回目の動きを、鎖の骨の回転のキーとして、その動きの
   アクションに焼き込む(GLB でも揺れる。布のコマごとの形を焼くより、ずっと軽い)。
"""

import math

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

CHAINS, SEGMENTS = 16, 4
# 1 コマを分ける回数・重力 m/s²・遅れ(速さを残す割合)・胴に付いて動いたときの位置へ戻る強さ(1 回ごと)
SUBSTEPS, GRAVITY, KEEP, SPRING = 4, 9.8, 0.9, 0.08
FPS = 24
LEGS = ("thigh.L", "thigh.R", "shin.L", "shin.R")


def _bone_name(k: int, j: int) -> str:
    return f"skirt_chain.{k:02d}.{j}"


def _joints(skirt: bpy.types.Object) -> tuple:
    """スカートの面の上の関節(鎖ごと・高さごと、世界の座標)と、真ん中の軸・鎖の角度。"""
    co = np.array([skirt.matrix_world @ v.co for v in skirt.data.vertices])
    top, bottom = co[:, 2].max(), co[:, 2].min()
    ring = co[co[:, 2] > top - 0.02]
    center = ring[:, :2].mean(axis=0)
    ang = np.arctan2(co[:, 1] - center[1], co[:, 0] - center[0])
    angles = np.linspace(-math.pi, math.pi, CHAINS, endpoint=False)
    levels = np.linspace(top, bottom, SEGMENTS + 1)
    band = (top - bottom) / SEGMENTS / 2
    joints = np.zeros((CHAINS, SEGMENTS + 1, 3))
    for k, a in enumerate(angles):
        diff = np.abs((ang - a + math.pi) % (2 * math.pi) - math.pi)
        sector = co[diff < math.pi / CHAINS * 1.5]
        for j, z in enumerate(levels):
            near = sector[np.abs(sector[:, 2] - z) < band * (0.5 if j in (0, SEGMENTS) else 1.0)]
            if not len(near):
                near = sector[np.argsort(np.abs(sector[:, 2] - z))[:5]]
            joints[k, j] = near.mean(axis=0)
    return joints, center, angles, co


def add_chains(arm: bpy.types.Object, skirt: bpy.types.Object) -> dict:
    """鎖の骨を入れ、スカートの重みを付け直す。揺らすときに使う値(関節の休んだ位置など)を返す。"""
    joints, center, angles, co = _joints(skirt)
    to_arm = arm.matrix_world.inverted()
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones
    for k in range(CHAINS):
        for j in range(SEGMENTS):
            bone = eb.new(_bone_name(k, j))
            bone.head = to_arm @ Vector(joints[k, j])
            bone.tail = to_arm @ Vector(joints[k, j + 1])
            bone.parent = eb["torso"] if j == 0 else eb[_bone_name(k, j - 1)]
            bone.use_connect = j > 0
    bpy.ops.object.mode_set(mode="OBJECT")
    for k in range(CHAINS):
        for j in range(SEGMENTS):
            arm.pose.bones[_bone_name(k, j)].rotation_mode = "QUATERNION"

    # 重み: 向きでとなりの 2 本の鎖、高さでその鎖の骨(関節の近くはとなりの骨と半分ずつ)
    for g in list(skirt.vertex_groups):
        skirt.vertex_groups.remove(g)
    groups = {(k, j): skirt.vertex_groups.new(name=_bone_name(k, j)) for k in range(CHAINS) for j in range(SEGMENTS)}
    ang = np.arctan2(co[:, 1] - center[1], co[:, 0] - center[0])
    step = 2 * math.pi / CHAINS
    pos = ((ang - angles[0]) % (2 * math.pi)) / step
    k0 = np.floor(pos).astype(int) % CHAINS
    t = pos - np.floor(pos)
    for i, p in enumerate(co):
        for k, wk in ((k0[i], 1 - t[i]), ((k0[i] + 1) % CHAINS, t[i])):
            zs = joints[k, :, 2]
            s = float(np.interp(-p[2], -zs, np.arange(SEGMENTS + 1)))
            j = min(int(s), SEGMENTS - 1)
            f = s - j
            parts = [(j, 1.0)]
            if f < 0.25 and j > 0:
                parts = [(j, 0.5 + 2 * f), (j - 1, 0.5 - 2 * f)]
            elif f > 0.75 and j < SEGMENTS - 1:
                parts = [(j, 0.5 + 2 * (1 - f)), (j + 1, 0.5 - 2 * (1 - f))]
            for jj, wj in parts:
                if wk * wj > 0.001:
                    groups[(k, jj)].add([i], float(wk * wj), "ADD")
    return {"joints": joints}


def _leg_capsules(arm: bpy.types.Object, body: bpy.types.Object) -> dict:
    """腿・すねの太さ(骨の軸から体の面までの距離の 7 割の所)。"""
    names = {g.index: g.name for g in body.vertex_groups}
    co = np.array([body.matrix_world @ v.co for v in body.data.vertices])
    radius = {}
    for leg in LEGS:
        idx = [v.index for v in body.data.vertices
               if v.groups and names.get(max(v.groups, key=lambda g: g.weight).group) == leg]
        bone = arm.data.bones[leg]
        a = np.array(arm.matrix_world @ bone.head_local)
        b = np.array(arm.matrix_world @ bone.tail_local)
        radius[leg] = float(np.percentile(_seg_dist(co[idx], a, b), 70)) if idx else 0.06
    return radius


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip(((p - a) @ ab) / (ab @ ab), 0, 1)
    return np.linalg.norm(p - (a + t[..., None] * ab), axis=-1)


def _closest(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip(((p - a) @ ab) / (ab @ ab), 0, 1)
    return a + t[..., None] * ab


def bake(arm: bpy.types.Object, body: bpy.types.Object, chains: dict, actions: list) -> int:
    """動き(アクション)ごとに揺れを計算して、鎖の骨の回転のキーを打つ。キーを打った動きの数を返す。"""
    rest = chains["joints"]  # 世界の座標の、休んだ姿勢の関節
    seg_len = np.linalg.norm(rest[:, 1:] - rest[:, :-1], axis=2)
    leg_r = _leg_capsules(arm, body)
    scene = bpy.context.scene
    torso_rest = arm.matrix_world @ arm.data.bones["torso"].matrix_local
    rest_in_torso = np.array([[(torso_rest.inverted() @ Vector(p))[:] for p in chain] for chain in rest])
    # 休んだ姿勢で、関節から脚の軸までの距離(ぴったりのスカートが、初めから押し出されないように)
    leg_axes = {leg: (np.array(arm.matrix_world @ arm.data.bones[leg].head_local),
                      np.array(arm.matrix_world @ arm.data.bones[leg].tail_local)) for leg in LEGS}
    min_r = {leg: np.minimum(leg_r[leg] + 0.004, _seg_dist(rest.reshape(-1, 3), *leg_axes[leg]).reshape(rest.shape[:2]) - 0.001)
             for leg in LEGS}
    rel = {}
    for k in range(CHAINS):
        for j in range(SEGMENTS):
            bone = arm.data.bones[_bone_name(k, j)]
            rel[(k, j)] = bone.parent.matrix_local.inverted() @ bone.matrix_local
    baked = 0
    dt = 1 / FPS / SUBSTEPS
    to_arm = arm.matrix_world.inverted()
    for action in actions:
        start, end = (int(round(x)) for x in action.frame_range)
        arm.animation_data.action = action
        pos = rest.copy()
        prev = rest.copy()
        record = {}
        for loop in range(2):
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                torso = arm.matrix_world @ arm.pose.bones["torso"].matrix
                target = np.array([[(torso @ Vector(p))[:] for p in chain] for chain in rest_in_torso])
                legs = {leg: (np.array(arm.matrix_world @ arm.pose.bones[leg].head),
                              np.array(arm.matrix_world @ arm.pose.bones[leg].tail)) for leg in LEGS}
                for _ in range(SUBSTEPS):
                    vel = (pos - prev) * KEEP
                    prev = pos.copy()
                    pos = pos + vel + np.array([0, 0, -GRAVITY]) * dt * dt
                    pos += (target - pos) * SPRING
                    pos[:, 0] = target[:, 0]
                    for j in range(1, SEGMENTS + 1):
                        d = pos[:, j] - pos[:, j - 1]
                        n = np.linalg.norm(d, axis=1, keepdims=True)
                        pos[:, j] = pos[:, j - 1] + d / np.maximum(n, 1e-9) * seg_len[:, j - 1:j]
                    for leg in LEGS:
                        a, b = legs[leg]
                        c = _closest(pos.reshape(-1, 3), a, b).reshape(pos.shape)
                        d = pos - c
                        n = np.linalg.norm(d, axis=2)
                        push = (n < min_r[leg]) & (n > 1e-9)
                        pos[push] = c[push] + d[push] / n[push][:, None] * min_r[leg][push][:, None]
                if loop == 1:
                    record[frame] = (pos.copy(), torso.copy())
        # 関節の位置を、鎖の骨の回転にする(親から順に、骨の向きを次の関節へ向ける)
        for frame, (p, torso) in record.items():
            torso_arm = to_arm @ torso
            for k in range(CHAINS):
                parent = torso_arm
                for j in range(SEGMENTS):
                    m0 = parent @ rel[(k, j)]
                    head = m0.to_translation()
                    want = (to_arm @ Vector(p[k, j + 1])) - head
                    if j == 0:
                        head = to_arm @ Vector(p[k, 0])
                    have = m0.to_3x3() @ Vector((0, 1, 0))
                    turn = have.rotation_difference(want)
                    q = m0.to_quaternion()
                    basis = q.inverted() @ turn @ q
                    pb = arm.pose.bones[_bone_name(k, j)]
                    pb.rotation_quaternion = basis
                    pb.keyframe_insert("rotation_quaternion", frame=frame)
                    parent = Matrix.Translation(head) @ (turn @ q).to_matrix().to_4x4()
        baked += 1
    arm.animation_data.action = None
    for k in range(CHAINS):
        for j in range(SEGMENTS):
            arm.pose.bones[_bone_name(k, j)].rotation_quaternion = Quaternion()
    return baked
