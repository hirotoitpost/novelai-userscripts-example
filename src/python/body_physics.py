"""
素体の2Dモデルの揺れもの(髪・胸)。動き(body_motions)で骨格を動かしたとき、揺れものの骨が、ばねでつながったおもりのように
遅れて付いてきて、行き過ぎて戻る動きを計算し、コマごとのキーにして動きに足す(Blender でも Spine の書き出しでも同じ揺れになる)。

- 髪: 頭から垂れた鎖(2本の骨)。先は「頭にくっついた位置」へばねで引かれ、重力で「休みの姿勢の向き(下)」へも引かれる
  (頭を傾けても髪は下へ垂れる)。風は絵の右向きの力で、強さが揺らぐ
- 胸: 胴に付いた点。胴が動く(跳ぶ・揺れる)と、遅れて揺れる。ずれは肩幅の割合で上限を付ける
動きは2周まわして、2周目を使う(くり返しの継ぎ目で揺れが飛ばないように)。
座標は Spine と同じ(y が上、度は反時計回りが正。body_export と同じ)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .body_export import _world_bones

# 1コマを何回に分けて計算するか
_SUBSTEPS = 6


@dataclass(frozen=True)
class Spring:
    # ばねの強さ(1/秒²)・減衰(1/秒)・重力で下へ向く割合(髪だけ)
    stiffness: float
    damping: float
    gravity: float = 0.0


# 減衰は、跳んだときに髪が顔の上へはね上がらない程度に強め(2026-10 実機: 減衰 5 では毛先が 110 度回った)
HAIR = Spring(stiffness=70.0, damping=9.0, gravity=0.6)
BREAST = Spring(stiffness=260.0, damping=14.0)
# 胸のずれの上限(肩幅に対する割合)。上限に近づくほど、ばねを強くして押し戻す(ぴたりと止まらないように)
_BREAST_LIMIT = 0.06
# 髪の骨ごとの、頭にくっついた向きからの回転の上限(度。根もと・毛先)
_HAIR_LIMIT = (25.0, 35.0)
# 風の力(髪の長さとばねの強さに対する割合)。強さ 1 で毛先が上限近くまでなびく
_WIND_FORCE = 0.5


def _rest(rig: dict) -> dict[str, dict]:
    """骨ごとの休みの姿勢: ワールドでの根もと・向き・長さと、親から見た位置・向き。"""
    world = _world_bones(rig)
    out = {}
    for name, me in world.items():
        parent = me["parent"]
        if parent is None:
            local_pos, local_rot = me["head"], me["angle"]
        else:
            p = world[parent]
            local_pos = _rotate(me["head"] - p["head"], -p["angle"])
            local_rot = me["angle"] - p["angle"]
        out[name] = {**me, "local_pos": local_pos, "local_rot": local_rot}
    return out


def _rotate(v: np.ndarray, degrees: float) -> np.ndarray:
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def _sample(keys: list, frame: float, width: int) -> list[float]:
    """キー(コマ, 値…)の、frame の値。キーの間はなめらかに(Blender のなめらかな補間に近い形)。"""
    if frame <= keys[0][0]:
        return list(keys[0][1:1 + width])
    for a, b in zip(keys, keys[1:]):
        if a[0] <= frame <= b[0]:
            u = (frame - a[0]) / max(b[0] - a[0], 1e-6)
            u = u * u * (3 - 2 * u)
            return [a[i] + (b[i] - a[i]) * u for i in range(1, 1 + width)]
    return list(keys[-1][1:1 + width])


def pose(rest: dict[str, dict], rotate: dict[str, float], translate: dict[str, np.ndarray]) -> dict[str, dict]:
    """
    骨ごとのワールドの根もと・向き(度)。rotate は休みからの回転(度)、translate は休みの向きでの移動(画素。x が右、y が上)。
    """
    out: dict[str, dict] = {}

    def solve(name: str) -> dict:
        if name in out:
            return out[name]
        me = rest[name]
        offset = translate.get(name, np.zeros(2))
        if me["parent"] is None:
            head = me["local_pos"] + offset
            angle = me["local_rot"] + rotate.get(name, 0.0)
        else:
            p = solve(me["parent"])
            parent_rest = rest[me["parent"]]["angle"]
            local = me["local_pos"] + _rotate(offset, -parent_rest)
            head = p["head"] + _rotate(local, p["angle"])
            angle = p["angle"] + me["local_rot"] + rotate.get(name, 0.0)
        out[name] = {"head": head, "angle": angle}
        return out[name]

    for name in rest:
        solve(name)
    return out


def _wind(t: float, strength: float) -> float:
    """風の強さの揺らぎ(0〜1 くらい)。ゆっくりの波と、速い波を重ねる。"""
    if strength <= 0:
        return 0.0
    return strength * (0.65 + 0.25 * math.sin(2 * math.pi * 0.55 * t) + 0.1 * math.sin(2 * math.pi * 1.7 * t + 1.0))


def _angle(v: np.ndarray) -> float:
    return math.degrees(math.atan2(v[1], v[0]))


def _wrap(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0


def bake(rig: dict, motion: dict, *, wind: float = 0.0) -> dict:
    """
    motion(body_motions.Motion.to_json の形)に、揺れもの(rig の骨のうち kind が hair・breast のもの)のコマごとのキーを
    足したもの。揺れものの骨が無ければ、そのまま返す。wind は風の強さ(0〜1)。
    """
    physics = [b for b in rig["bones"] if b.get("kind") in ("hair", "breast")]
    if not physics:
        return motion
    rest = _rest(rig)
    shoulder = float(np.linalg.norm(np.array(rig["joints"]["left_shoulder"]) - np.array(rig["joints"]["right_shoulder"])))
    frames, fps = int(motion["frames"]), int(motion.get("fps", 24))
    dt = 1.0 / fps / _SUBSTEPS
    rot_keys, tr_keys = motion["rotate"], motion.get("translate", {})

    chains: dict[str, list[str]] = {}
    for bone in physics:
        if bone["kind"] == "hair":
            chains.setdefault(bone["chain"], []).append(bone["name"])
    breasts = [b["name"] for b in physics if b["kind"] == "breast"]

    # 状態: 髪の骨ごとの先の位置と速さ、胸ごとの位置と速さ(ワールド)
    tips = {n: rest[n]["head"] + _rotate(np.array([rest[n]["length"], 0.0]), rest[n]["angle"]) for c in chains.values() for n in c}
    tip_v = {n: np.zeros(2) for n in tips}
    points = {n: rest[n]["head"].copy() for n in breasts}
    point_v = {n: np.zeros(2) for n in breasts}

    out_rot: dict[str, list] = {n: [] for n in tips}
    out_tr: dict[str, list] = {n: [] for n in breasts}
    for loop in range(2):
        for frame in range(1, frames + 1):
            for step in range(_SUBSTEPS):
                f = frame - 1 + (step + 1) / _SUBSTEPS
                rotate = {k: _sample(v, f, 1)[0] for k, v in rot_keys.items()}
                translate = {k: np.array(_sample(v, f, 2)) for k, v in tr_keys.items()}
                world = pose(rest, rotate, translate)
                t = (loop * frames + f) / fps
                gust = _wind(t, wind)
                for chain in chains.values():
                    root = None
                    parent_angle = world[rest[chain[0]]["parent"]]["angle"]
                    for name in chain:
                        me = rest[name]
                        if root is None:
                            root = world[me["parent"]]["head"] + _rotate(me["local_pos"], parent_angle)
                        rigid = parent_angle + me["local_rot"]
                        # 重力: 頭にくっついた向きと、休みの姿勢の向き(下)の間を目ざす
                        target_dir = (1 - HAIR.gravity) * np.array([math.cos(math.radians(rigid)), math.sin(math.radians(rigid))])
                        target_dir += HAIR.gravity * np.array([math.cos(math.radians(me["angle"])), math.sin(math.radians(me["angle"]))])
                        target = root + me["length"] * target_dir / np.linalg.norm(target_dir)
                        force = HAIR.stiffness * (target - tips[name]) - HAIR.damping * tip_v[name]
                        force += np.array([gust * _WIND_FORCE * HAIR.stiffness * me["length"], 0.0])
                        tip_v[name] = tip_v[name] + force * dt
                        tip = tips[name] + tip_v[name] * dt
                        d = tip - root
                        # 頭にくっついた向きから、上限より大きくは回さない(毛先が顔へ入らないように)
                        limit = _HAIR_LIMIT[min(chain.index(name), 1)]
                        turn = max(-limit, min(limit, _wrap(_angle(d) - rigid)))
                        if abs(turn) == limit:
                            tip_v[name] = tip_v[name] * 0.5
                        direction = math.radians(rigid + turn)
                        tips[name] = root + me["length"] * np.array([math.cos(direction), math.sin(direction)])
                        parent_angle = _angle(tips[name] - root)
                        root = tips[name]
                for name in breasts:
                    me = rest[name]
                    torso = world[me["parent"]]
                    target = torso["head"] + _rotate(me["local_pos"], torso["angle"])
                    limit = _BREAST_LIMIT * shoulder
                    stretch = np.linalg.norm(points[name] - target) / limit
                    force = BREAST.stiffness * (1 + 3 * stretch * stretch) * (target - points[name]) - BREAST.damping * point_v[name]
                    point_v[name] = point_v[name] + force * dt
                    points[name] = points[name] + point_v[name] * dt
                    offset = points[name] - target
                    if np.linalg.norm(offset) > limit:
                        points[name] = target + offset * limit / np.linalg.norm(offset)
            if loop == 0:
                continue
            # 2周目のコマごとに、揺れものの骨の休みからの回転・移動をキーにする
            rotate = {k: _sample(v, frame, 1)[0] for k, v in rot_keys.items()}
            translate = {k: np.array(_sample(v, frame, 2)) for k, v in tr_keys.items()}
            world = pose(rest, rotate, translate)
            for chain in chains.values():
                parent_angle = world[rest[chain[0]]["parent"]]["angle"]
                root = None
                for name in chain:
                    me = rest[name]
                    if root is None:
                        root = world[me["parent"]]["head"] + _rotate(me["local_pos"], parent_angle)
                    actual = _angle(tips[name] - root)
                    out_rot[name].append([frame, round(_wrap(actual - parent_angle - me["local_rot"]), 3)])
                    parent_angle, root = actual, tips[name]
            for name in breasts:
                me = rest[name]
                torso = world[me["parent"]]
                target = torso["head"] + _rotate(me["local_pos"], torso["angle"])
                # 胴の今の向きから見たずれを、休みの向きの移動に直す
                offset = _rotate(points[name] - target, -(torso["angle"] - rest[me["parent"]]["angle"]))
                out_tr[name].append([frame, round(float(offset[0]), 2), round(float(offset[1]), 2)])

    return {
        **motion,
        "rotate": {**rot_keys, **out_rot},
        "translate": {**tr_keys, **out_tr},
        "wind": wind,
    }


def hair_chains(hair: np.ndarray, joints: dict[str, np.ndarray], *, min_pixels: int = 400) -> list[dict]:
    """
    垂れた髪(目の高さより下の髪)を、顔の左右(と背中)に分け、それぞれ根もと・中ほど・毛先の点を出す。
    [{"chain": "hair.L", "points": [(x, y) × 3]}](絵の画素)。少なすぎる側は作らない。
    """
    eye = joints["nose"][1] - 0.15 * (joints["neck"][1] - joints["nose"][1])
    shoulder = float(np.linalg.norm(joints["left_shoulder"] - joints["right_shoulder"]))
    ys, xs = np.nonzero(hair)
    below = ys > eye
    xs, ys = xs[below], ys[below]
    nose_x = joints["nose"][0]
    out = []
    # キャラの左は絵の右
    for chain, side in (("hair.L", xs > nose_x + 0.05 * shoulder), ("hair.R", xs < nose_x - 0.05 * shoulder)):
        if side.sum() < min_pixels:
            continue
        sx, sy = xs[side], ys[side]
        tip_y = float(np.percentile(sy, 97))
        if tip_y - eye < 0.15 * shoulder:
            continue
        band = max(4.0, 0.05 * (tip_y - eye))

        def x_at(y: float) -> float:
            near = np.abs(sy - y) < band
            return float(np.median(sx[near])) if near.any() else float(np.median(sx))

        mid_y = eye + (tip_y - eye) * 0.5
        out.append({"chain": chain, "points": [(x_at(eye + band), eye), (x_at(mid_y), mid_y), (x_at(tip_y - band), tip_y)]})
    return out


def breast_points(joints: dict[str, np.ndarray]) -> list[dict]:
    """胸の中心と半径(絵の画素)。両肩と両腰から、胸の高さ・左右の位置を見積もる。"""
    shoulder_mid = (joints["left_shoulder"] + joints["right_shoulder"]) / 2
    hip_mid = (joints["left_hip"] + joints["right_hip"]) / 2
    width = float(np.linalg.norm(joints["left_shoulder"] - joints["right_shoulder"]))
    y = float(shoulder_mid[1] + 0.3 * (hip_mid[1] - shoulder_mid[1]))
    out = []
    for name, sign in (("breast.L", 1), ("breast.R", -1)):
        out.append({"name": name, "center": (float(shoulder_mid[0] + sign * 0.27 * width), y), "radius": 0.24 * width})
    return out


def to_spine_translate(rig: dict, motion: dict) -> dict[str, list]:
    """translate(休みの向きの移動)を、Spine の translate(親の骨から見た移動)に直したもの。"""
    world = _world_bones(rig)
    out = {}
    for name, points in motion.get("translate", {}).items():
        parent = world[name]["parent"]
        angle = world[parent]["angle"] if parent else 0.0
        out[name] = [[p[0], *_rotate(np.array(p[1:3], dtype=float), -angle).round(3).tolist()] for p in points]
    return out

