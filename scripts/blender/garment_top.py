"""
上の服(身頃と袖)を、体の 3D から作って布として着せる(Blender の中で使う。dress_model.py から読み込む)。

設定(dict。省いた値は DEFAULTS):
- length 丈(0 ウエスト・1 腰)、ease ゆとり mm、bridge 胸の下を渡す広さ(None は胸から真っすぐ落ちる = ゆったり。
  60 ほどで体に沿い、25 ほどでぴったり)、bending 布の曲がりにくさ
- sleeve 袖の長さ(0 袖なし・1 肘・2 手首)、sleeve_ease 袖のゆとり mm、sleeve_flare 袖口の広がり(袖口の周り ÷ 付け根)
- neck_width 襟ぐりの、首からの幅 cm・neck_front 前の深さ cm・neck_back 後ろの深さ cm

作り方(docs/model_3d_plan_jp.md のステップ 6b):
1. 着せる姿勢: 上腕を RAISE 度だけ外へ上げる(raise_arms)。腕が胴から離れ、脇の下・背中で布が余らない。
2. 身頃: 胴の中心から光線を飛ばして体の面に当て(下は水平、胸から上は斜め上へ)、格子にする。袖ぐりは腕の骨の重みと
   肩の線、襟ぐりは首のまわりの線(前を深く)で決め、格子の上でなめらかにして、線のとおりに切る(マーチング・スクエア)。
   形は行ごとのへこみの無い輪(凸包)に、ゆったりなら縦にも凸包(胸から真っすぐ落ちる)、沿わせるなら縦にならした包み。
3. 袖: 肩の関節 → 肘 → 手首の軸のまわりの筒(肩の丸みは、肩の関節から斜め上へ光線)。袖ぐりで切り取った所だけを布にする。
4. 縫い合わせ: 身頃と袖の切り口を、たがいに寄せてから、相手の点の所で自分の辺を分けて 1 対 1 で溶かし合わせる
   (縫い代・折り返しを作らない。アニメ・絵のように縫い目の厚みが無い 1 枚の布になる)。
5. 置いた形を休む形にして、肩の上を軽く留め、体に当たり判定を付けて重力でなじませる。
骨に付けて腕を下ろした姿勢に戻すのは dress_model.py(着せた姿勢の骨の重みで、変形を逆にかける)。
"""

import math

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

DEFAULTS = {
    "length": 1.0, "ease": 8, "bridge": None, "bending": 2.0,
    "sleeve": 0.5, "sleeve_ease": 12, "sleeve_flare": 1.15,
    "neck_width": 1.5, "neck_front": 6, "neck_back": 2,
}
# 着せるときに上腕を外へ上げる角度
RAISE = 40
# 服を付ける骨(袖は腕に、襟は頭に付く)
BONES = ("torso", "upper_arm.L", "upper_arm.R", "forearm.L", "forearm.R", "head")
COLS, SLEEVE_COLS = 160, 48
# 袖ぐりの肩の線(肩の関節から内へ m)・袖の下の端(いちばん低い袖ぐりから上へ m)
SHOULDER_IN, ARMPIT_UP = 0.02, 0.04
# 縫う前に、切り口をたがいに寄せる割合と、寄せる動きをまわりへ広げる距離 m(切り口だけ動かすと、縫い目に折れが残り、
# 布がその折れを休む形にして、縫い目がフリルのように波打った)
CLOSE = 0.85
SPREAD = 0.04
# 腕を下ろしてなじませるとき、上腕の軸からこの距離(m)までは自由に動かし、その外へ向かって留める
SETTLE_FREE = (0.08, 0.14)
# つないだ線のまわりをならす回数
SEAM_SMOOTH = 4
# 体との当たりの距離
COLLISION = 0.003
_HOLE_BONES = ("upper_arm", "forearm", "hand", "head")
_ARM_BONES = ("upper_arm", "forearm", "hand")


def raise_arms(arm: bpy.types.Object, joints: dict, degrees: float = RAISE) -> dict:
    """上腕を、肩の関節を中心に前後の軸(Y)のまわりで外へ上げ、腕の関節の位置を上げた骨から取り直して返す。"""
    for side, sign in (("L", -1), ("R", 1)):
        pb = arm.pose.bones[f"upper_arm.{side}"]
        bpy.context.view_layer.update()
        head = pb.head.copy()
        pb.matrix = Matrix.Translation(head) @ Matrix.Rotation(math.radians(sign * degrees), 4, "Y") @ Matrix.Translation(-head) @ pb.matrix
    bpy.context.view_layer.update()
    joints = dict(joints)
    for side, name in (("L", "left"), ("R", "right")):
        up, fore = arm.pose.bones[f"upper_arm.{side}"], arm.pose.bones[f"forearm.{side}"]
        joints[f"{name}_shoulder"] = list(arm.matrix_world @ up.head)
        joints[f"{name}_elbow"] = list(arm.matrix_world @ up.tail)
        joints[f"{name}_wrist"] = list(arm.matrix_world @ fore.tail)
    return joints


def _mesh_arrays(obj: bpy.types.Object) -> tuple:
    """オブジェクトの、モディファイアを当てた形の頂点(世界の座標)と三角形。"""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    mesh = ev.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.transform(obj.matrix_world)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    co = np.array([v.co[:] for v in bm.verts])
    tris = np.array([[v.index for v in f.verts] for f in bm.faces])
    bm.free()
    ev.to_mesh_clear()
    return co, tris


class _Body:
    """
    着せる姿勢の体の面(光線を当てる)と、頂点ごとの腕の重み・頭の面かどうか。under(先に着せた服)を渡すと、その面は
    別に持ち、光線がそれより外に出るまでの距離を測れる(上の服をスカートの外に置く)。
    """

    def __init__(self, body_obj: bpy.types.Object, under: tuple = ()):
        self.co, self.tris = _mesh_arrays(body_obj)
        self.bvh = BVHTree.FromPolygons([tuple(map(float, p)) for p in self.co], [tuple(map(int, t)) for t in self.tris])
        self.under = []
        for obj in under:
            co, tris = _mesh_arrays(obj)
            self.under.append(BVHTree.FromPolygons([tuple(map(float, p)) for p in co], [tuple(map(int, t)) for t in tris]))
        names = {g.index: g.name for g in body_obj.vertex_groups}
        dom = [names.get(max(v.groups, key=lambda g: g.weight).group, "") if v.groups else "" for v in body_obj.data.vertices]
        self.hole_v = np.array([d.startswith(_HOLE_BONES) for d in dom])
        self.head_v = np.array([d.startswith("head") for d in dom])
        self.arm_w = np.zeros(len(self.co))
        for v in body_obj.data.vertices:
            total = sum(g.weight for g in v.groups) or 1.0
            self.arm_w[v.index] = sum(g.weight for g in v.groups if names.get(g.group, "").startswith(_ARM_BONES)) / total

    def beyond_under(self, origin: Vector, direction: Vector, reach: float) -> float:
        """光線が、先に着せた服を最後に出る距離(当たらなければ 0)。"""
        far = 0.0
        for bvh in self.under:
            o, travelled = origin, 0.0
            while True:
                loc, _, _, dist = bvh.ray_cast(o, direction, reach - travelled)
                if loc is None:
                    break
                travelled += dist + 0.001
                far = max(far, travelled)
                o = loc + direction * 0.001
        return far

    def arm_weight(self, face: int, p: np.ndarray) -> float:
        """面の上の点 p の腕の重み(三角形の頂点の重みを重心座標で混ぜる)。"""
        tri = self.tris[face]
        a, b, c = self.co[tri]
        v0, v1, v2 = b - a, c - a, p - a
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01 or 1e-12
        bv, bw = (d11 * d20 - d01 * d21) / den, (d00 * d21 - d01 * d20) / den
        return float(np.clip([1 - bv - bw, bv, bw], 0, 1) @ self.arm_w[tri])


def _hull2(pts: np.ndarray) -> np.ndarray:
    order = sorted(range(len(pts)), key=lambda j: (pts[j, 0], pts[j, 1]))

    def half(idx):
        h = []
        for j in idx:
            while len(h) >= 2 and np.cross(pts[h[-1]] - pts[h[-2]], pts[j] - pts[h[-2]]) <= 0:
                h.pop()
            h.append(j)
        return h

    return pts[half(order)[:-1] + half(order[::-1])[:-1]]


def _hit_hull(origin: np.ndarray, direction: np.ndarray, hull: np.ndarray) -> float:
    """origin から direction へ伸ばした半直線が、凸包の輪を出る距離(当たらなければ 0)。"""
    best = 0.0
    for j in range(len(hull)):
        p0, e = hull[j] - origin, hull[(j + 1) % len(hull)] - hull[j]
        den = direction[0] * (-e[1]) - direction[1] * (-e[0])
        if abs(den) < 1e-12:
            continue
        t = (p0[0] * (-e[1]) - p0[1] * (-e[0])) / den
        u = (direction[0] * p0[1] - direction[1] * p0[0]) / den
        if t > 0 and -1e-9 <= u <= 1 + 1e-9:
            best = max(best, t)
    return best


def _ring_hull(r: np.ndarray, ang: np.ndarray) -> np.ndarray:
    """向きごとの半径 r の輪を、へこみの無い形(凸包)にした半径。"""
    h = _hull2(np.stack([np.cos(ang) * r, np.sin(ang) * r], 1))
    return np.array([max(r[k], _hit_hull(np.zeros(2), np.array([math.cos(a), math.sin(a)]), h)) for k, a in enumerate(ang)])


def _smooth_field(field: np.ndarray, rounds: int = 3) -> np.ndarray:
    """格子の上で値をならす(縦は端を残し、横は輪としてつなぐ)。"""
    for _ in range(rounds):
        f2 = field.copy()
        f2[1:-1] = (2 * field[1:-1] + field[:-2] + field[2:]) / 4
        field = (2 * f2 + np.roll(f2, 1, 1) + np.roll(f2, -1, 1)) / 4
    return field


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip(((p - a) @ ab) / (ab @ ab), 0, 1)
    return np.linalg.norm(p - (a + t[..., None] * ab), axis=-1)


class _Pieces:
    """格子を切り口の線で切った面を、1 つのメッシュの頂点・面に集める(0 = 身頃、1・2 = 左右の袖)。"""

    def __init__(self):
        self.verts: list[np.ndarray] = []
        self.faces: list[list[int]] = []
        self.part: list[int] = []
        self.seam: set[int] = set()  # 袖ぐりの切り口の頂点(縫い合わせる所)
        self._ids: dict = {}

    def _vert(self, key, pos) -> int:
        if key not in self._ids:
            self._ids[key] = len(self.verts)
            self.verts.append(pos)
            self.part.append(key[0])
        return self._ids[key]

    def cut(self, gid: int, verts: np.ndarray, field: np.ndarray, seam_mask: np.ndarray) -> None:
        """格子の 1 マスごとに、切り口の線(field = 0。角の値から線形に)で切った多角形(マーチング・スクエア)。"""
        nr, nc = field.shape
        flat, fv, sm = verts.reshape(-1, 3), field.reshape(-1), seam_mask.reshape(-1)
        for i in range(nr - 1):
            for k in range(nc):
                k2 = (k + 1) % nc
                quad = [i * nc + k, i * nc + k2, (i + 1) * nc + k2, (i + 1) * nc + k]
                poly = []
                for j in range(4):
                    a, b = quad[j], quad[(j + 1) % 4]
                    if fv[a] <= 0:
                        poly.append(self._vert((gid, a), flat[a]))
                    if (fv[a] <= 0) != (fv[b] <= 0):
                        t = min(max(fv[a] / (fv[a] - fv[b]), 0.05), 0.95)
                        vi = self._vert((gid, tuple(sorted((a, b)))), flat[a] + t * (flat[b] - flat[a]))
                        poly.append(vi)
                        if sm[b if fv[a] <= 0 else a]:
                            self.seam.add(vi)
                if len(poly) >= 3:
                    self.faces.append(poly)


def _torso(body: _Body, joints: dict, cfg: dict, pieces: _Pieces) -> dict:
    """身頃の格子を作って切り、寸法(わきの下の高さなど)を返す。"""
    co = body.co
    mid = lambda a, b: (joints[a][2] + joints[b][2]) / 2  # noqa: E731
    hip_z, sh_z = mid("left_hip", "right_hip"), mid("left_shoulder", "right_shoulder")
    torso = sh_z - hip_z
    # 腕と胴が離れる高さ(肘の少し上)を、上へ向けた光線の中心にする
    z0 = mid("left_elbow", "right_elbow") + 0.15 * (sh_z - mid("left_elbow", "right_elbow"))

    def center(z):
        sl = co[(np.abs(co[:, 2] - z) < 0.006) & (np.abs(co[:, 0]) < 0.03) & ~body.hole_v]
        return (sl[:, 1].min() + sl[:, 1].max()) / 2 if len(sl) else None

    zs = np.linspace(hip_z - 0.1 * torso, z0, 20)
    cs = [(z, c) for z, c in ((z, center(z)) for z in zs) if c is not None]
    fit = np.polyfit([z for z, _ in cs], [c for _, c in cs], 2)
    axis_y = lambda z: float(np.polyval(fit, min(max(z, zs[0]), z0)))  # noqa: E731

    waist_z = hip_z + 0.4 * torso
    hem_z = waist_z - cfg["length"] * (waist_z - hip_z)
    ang = np.linspace(0, 2 * math.pi, COLS, endpoint=False)  # 0 = 左(+X)、π/2 = 後ろ(+Y)、3π/2 = 前(-Y)
    top = Vector((0.0, axis_y(z0), z0))
    # 行: 下は水平の光線(高さ z)、上は top から上向きの光線(仰角 e。84° まで)
    rows = [("h", float(z)) for z in np.linspace(hem_z, z0, max(int(round((z0 - hem_z) / 0.01)), 2), endpoint=False)]
    n_low = len(rows)
    e = 0.0
    while e < math.radians(84):
        rows.append(("e", e))
        e += 0.01 / 0.13
    R = len(rows)

    def ray(i, k):
        kind, v = rows[i]
        a = ang[k]
        if kind == "h":
            return Vector((0.0, axis_y(v), v)), Vector((math.cos(a), math.sin(a), 0.0))
        return top, Vector((math.cos(a) * math.cos(v), math.sin(a) * math.cos(v), math.sin(v)))

    # 首の断面: 肩より 4cm 上の輪切りで、首の中心から近い所だけで測る(髪は外にある)
    nsl = co[(np.abs(co[:, 2] - (sh_z + 0.04)) < 0.004) & (np.abs(co[:, 0]) < 0.08)]
    neck_c = np.array([0.0, float(np.median(nsl[:, 1]))])
    for _ in range(3):
        dd = np.linalg.norm(nsl[:, :2] - neck_c, axis=1)
        core = nsl[dd < np.percentile(dd, 40)]
        neck_c = np.array([0.0, (core[:, 1].min() + core[:, 1].max()) / 2])
    neck_r = float(np.percentile(np.linalg.norm(nsl[:, :2] - neck_c, axis=1), 20))

    def smoothstep(x, a, b):
        t = min(max((x - a) / (b - a), 0.0), 1.0)
        return t * t * (3 - 2 * t)

    def neck_field(p):
        """正なら襟ぐりの穴: 首のまわり(首の半径 + 襟の幅。前ほど広い)の中で、襟ぐりの線(前・後ろを深く)より上。"""
        dxy = p[:2] - neck_c
        d = float(np.linalg.norm(dxy))
        front, back = max(0.0, -dxy[1] / (d or 1)), max(0.0, dxy[1] / (d or 1))
        zline = (sh_z + 0.03 - cfg["neck_front"] / 100 * smoothstep(front, 0.15, 0.7)
                 - cfg["neck_back"] / 100 * smoothstep(back, 0.15, 0.7))
        return min((p[2] - zline) / 0.02, (neck_r + cfg["neck_width"] / 100 + 0.03 + 0.08 * front - d) / 0.02)

    def armhole(p, w):
        """正なら袖ぐりの穴: 腕の重みが半分より上で、肩の線(肩の関節から SHOULDER_IN だけ内)より外。"""
        side = "left_shoulder" if p[0] > 0 else "right_shoulder"
        return min(w - 0.5, (abs(p[0]) - (abs(joints[side][0]) - SHOULDER_IN)) / 0.02)

    armpit_z = math.inf
    otherf = np.full((R, COLS), -1.0)  # 首のほかの穴(袖ぐり・髪・当たらない所)
    hit = np.zeros((R, COLS))
    field = np.zeros((R, COLS))
    miss = np.zeros((R, COLS), dtype=bool)
    neckf = np.full((R, COLS), -1.0)
    for i in range(R):
        for k in range(COLS):
            o, d = ray(i, k)
            loc, _, f, dist = body.bvh.ray_cast(o, d, 0.6)
            # 髪を通り抜ける(首の外で頭の面に当たったら、その先をもう一度引く)
            for _ in range(4):
                if loc is None or not body.head_v[body.tris[f]].all():
                    break
                if np.linalg.norm(np.array(loc)[:2] - neck_c) < neck_r + 0.015:
                    break
                loc2, _, f2, d2 = body.bvh.ray_cast(loc + d * 0.002, d, 0.6)
                if loc2 is None:
                    break
                loc, f, dist = loc2, f2, dist + 0.002 + d2
            if loc is None:
                miss[i, k] = True
                field[i, k] = otherf[i, k] = 1.0
                continue
            # 先に着せた服(スカート)があれば、その外まで
            hit[i, k] = max(dist, body.beyond_under(o, d, 0.6)) if body.under else dist
            p = np.array(loc)
            arm_f = armhole(p, body.arm_weight(f, p))
            neckf[i, k] = neck_field(p)
            field[i, k] = max(arm_f, neckf[i, k])
            otherf[i, k] = arm_f
            if arm_f > 0:
                armpit_z = min(armpit_z, p[2])
            # 首より下で頭(髪)に当たったら、その半径は使わない
            if body.head_v[body.tris[f]].all():
                miss[i, k] = True
                if p[2] > sh_z:
                    field[i, k] = max(field[i, k], 0.5)
                    otherf[i, k] = max(otherf[i, k], 0.5)
    # 首の穴は、列の上へ向かって一度穴になったら、その上はずっと穴(あごの下に当たって布が戻る所を作らない)
    for k in range(COLS):
        above = False
        for i in range(n_low, R):
            if neckf[i, k] > 0:
                above = True
            elif above:
                field[i, k] = max(field[i, k], 0.5)
    field = _smooth_field(field)
    hole = field > 0
    # 小さな穴(光線の当たりそこね)はふさぐ
    seen = np.zeros_like(hole)
    for i in range(R):
        for k in range(COLS):
            if not hole[i, k] or seen[i, k]:
                continue
            comp, stack = [], [(i, k)]
            seen[i, k] = True
            while stack:
                a, b = stack.pop()
                comp.append((a, b))
                for di, dk in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ii, kk = a + di, (b + dk) % COLS
                    if 0 <= ii < R and hole[ii, kk] and not seen[ii, kk]:
                        seen[ii, kk] = True
                        stack.append((ii, kk))
            if len(comp) < 40:
                for a, b in comp:
                    hole[a, b] = False

    def to_xyz(i, k, rr):
        o, d = ray(i, k)
        return np.array(o + d * rr)

    # 穴の所の半径は、同じ行の両側からつなぐ(凸包の点には入れない)
    r = hit.copy()
    for i in range(R):
        ok = ~hole[i] & ~miss[i]
        if ok.sum() >= 2:
            idx = np.nonzero(ok)[0]
            r[i] = np.interp(np.arange(COLS), np.concatenate([idx - COLS, idx, idx + COLS]), np.tile(r[i, idx], 3))

    def row_hull(rr):
        return np.array([_ring_hull(rr[i], ang) for i in range(R)])

    def col_hull(rr):
        """列ごとの縦の断面(体の中心を通る、その向きの面)で凸包にする: 胸から真っすぐ下へ落ちる。"""
        out = rr.copy()
        origins = [np.array([0.0, v]) if kind == "h" else np.array([0.0, z0]) for kind, v in rows]
        dirs = [np.array([1.0, 0.0]) if kind == "h" else np.array([math.cos(v), math.sin(v)]) for kind, v in rows]
        for k in range(COLS):
            pts = [origins[i] + dirs[i] * rr[i, k] for i in range(R) if not hole[i, k] and not miss[i, k]]
            h = _hull2(np.array(pts + [np.array([0.0, rows[0][1]]), np.array([0.0, z0])]))
            for i in range(R):
                out[i, k] = max(rr[i, k], _hit_hull(origins[i], dirs[i], h))
        return out

    r1 = row_hull(r)
    if cfg["bridge"] is None:
        r3 = col_hull(r1)
    else:
        # 体の形を縦(列の向き)にならした包み: 体より内へは入れず、ならす回数(bridge)が多いほど、胸の下のくぼみを広く渡る
        r3 = r1.copy()
        for _ in range(int(cfg["bridge"])):
            sm = r3.copy()
            sm[1:-1] = (r3[:-2] + 2 * r3[1:-1] + r3[2:]) / 4
            r3 = np.maximum(r1, sm)
    floor_r = r + cfg["ease"] / 1000 + 0.004
    place_r = row_hull(r3) + cfg["ease"] / 1000 + 0.004

    # 腕(上腕・前腕のカプセル)の中に置いた所は、胴の面の上(ゆとりだけ外)まで引き戻す。腕は布の外に出す
    arm_pts = co[body.arm_w > 0.5]
    caps = []
    for side in ("left", "right"):
        pts = [np.array(joints[f"{side}_{j}"]) for j in ("shoulder", "elbow", "wrist")]
        for a, b in zip(pts[:-1], pts[1:]):
            near = _seg_dist(arm_pts, a, b)
            near = near[near < 0.08]
            caps.append((a, b, float(np.percentile(near, 90)) if len(near) else 0.04))
    for i in range(R):
        for k in range(COLS):
            if hole[i, k]:
                continue
            p = to_xyz(i, k, place_r[i, k])
            if any(_seg_dist(p[None], a, b)[0] < cr + 0.01 for a, b, cr in caps):
                place_r[i, k] = min(place_r[i, k], floor_r[i, k])
    # 引き戻した所と、まわりの段差をならす(体より内へは入れない)
    limit = np.where(~hole & (place_r <= floor_r + 1e-9), place_r, np.inf)
    for _ in range(6):
        sm = place_r.copy()
        sm[1:-1] = (2 * place_r[1:-1] + place_r[:-2] + place_r[2:]) / 4
        sm = (2 * sm + np.roll(sm, 1, 1) + np.roll(sm, -1, 1)) / 4
        place_r = np.maximum(np.minimum(sm, limit), floor_r)
    verts = np.array([[to_xyz(i, k, place_r[i, k]) for k in range(COLS)] for i in range(R)])

    # 首の穴は、布を置いた位置で決め直す(体の当たった点だと、胸の谷間で線がずれて切れ込みになる)
    nfp = np.array([[neck_field(verts[i, k]) for k in range(COLS)] for i in range(R)])
    for k in range(COLS):
        above = False
        for i in range(n_low, R):
            if nfp[i, k] > 0:
                above = True
            elif above:
                nfp[i, k] = max(nfp[i, k], 0.5)
    # 襟ぐりの線を横にならす(1〜2 列だけの切れ込みを消す)
    for _ in range(8):
        nfp = (2 * nfp + np.roll(nfp, 1, 1) + np.roll(nfp, -1, 1)) / 4
    fc = _smooth_field(np.maximum(otherf, nfp))
    fc[~hole & (fc > 0) & (otherf <= 0) & (nfp <= 0)] = -0.05
    pieces.cut(0, verts, fc, otherf >= nfp)
    return {"sh_z": sh_z, "hem_z": round(float(hem_z), 4), "armpit_z": round(float(armpit_z), 4)}


def _sleeve(body: _Body, joints: dict, cfg: dict, pieces: _Pieces, gid: int, side: str, armpit_z: float) -> None:
    """腕の軸(肩の関節 → 肘 → 手首)のまわりの筒を作り、胴の袖ぐりで切り取った所(腕の重みが半分より上で、肩の線より外か、
    わきの下より下)だけを布にする。"""
    sh, el, wr = (np.array(joints[f"{side}_{j}"]) for j in ("shoulder", "elbow", "wrist"))
    up_len, fore_len = float(np.linalg.norm(el - sh)), float(np.linalg.norm(wr - el))
    s_end = cfg["sleeve"] * up_len if cfg["sleeve"] <= 1 else up_len + (cfg["sleeve"] - 1) * fore_len
    # 行: 肩の丸み(肩の関節から、腕に垂直な向きから上へ傾けた光線 β)と、腕に沿った輪(s)
    cap = list(np.radians(np.linspace(80, 0, 8, endpoint=False)))
    ss = [0.0] * len(cap) + list(np.linspace(0, s_end, max(int(s_end / 0.01), 4)))
    betas = cap + [0.0] * (len(ss) - len(cap))
    t_up, t_fore = (el - sh) / up_len, (wr - el) / fore_len
    sang = np.linspace(0, 2 * math.pi, SLEEVE_COLS, endpoint=False)
    lat = np.array([1.0 if side == "left" else -1.0, 0.0, 0.0])
    n = len(ss)
    sfield = np.zeros((n, SLEEVE_COLS))
    shit = np.zeros((n, SLEEVE_COLS))
    smiss = np.zeros((n, SLEEVE_COLS), dtype=bool)
    frames = []
    for i, sv in enumerate(ss):
        c = sh + t_up * sv if sv <= up_len else el + t_fore * (sv - up_len)
        w = min(max((sv - (up_len - 0.03)) / 0.06, 0.0), 1.0)
        t = t_up * (1 - w) + t_fore * w
        t /= np.linalg.norm(t)
        u = lat - t * (t @ lat)
        u /= np.linalg.norm(u)
        v = np.cross(t, u)
        frames.append((c, u, v, t, betas[i]))
        for k, a in enumerate(sang):
            d = math.cos(betas[i]) * (math.cos(a) * u + math.sin(a) * v) - math.sin(betas[i]) * t
            loc, _, f, dist = body.bvh.ray_cast(Vector(c), Vector(d), 0.12)
            if loc is None:
                smiss[i, k] = True
                sfield[i, k] = 1.0
                continue
            shit[i, k] = dist
            p = np.array(loc)
            out = (abs(p[0]) - (abs(sh[0]) - SHOULDER_IN)) / 0.02
            sfield[i, k] = -min(body.arm_weight(f, p) - 0.5, max(out, (armpit_z + ARMPIT_UP - p[2]) / 0.02))
            if body.head_v[body.tris[f]].all():
                sfield[i, k] = 1.0
    sfield = _smooth_field(sfield)
    # 袖の太さ: 行ごとの凸包 + ゆとり、袖口へ広げる
    sverts = np.zeros((n, SLEEVE_COLS, 3))
    for i, sv in enumerate(ss):
        ok = ~smiss[i] & (sfield[i] <= 0)
        if ok.sum() < 3:
            ok = ~smiss[i]
        rr = shit[i].copy()
        if ok.sum() >= 2:
            idx = np.nonzero(ok)[0]
            rr = np.interp(np.arange(SLEEVE_COLS), np.concatenate([idx - SLEEVE_COLS, idx, idx + SLEEVE_COLS]), np.tile(rr[idx], 3))
        grow = 1 + (cfg["sleeve_flare"] - 1) * sv / max(s_end, 0.01)
        rr = (_ring_hull(rr, sang) + cfg["sleeve_ease"] / 1000 + 0.004) * grow
        c, u, v, t, b = frames[i]
        for k, a in enumerate(sang):
            sverts[i, k] = c + rr[k] * (math.cos(b) * (math.cos(a) * u + math.sin(a) * v) - math.sin(b) * t)
    pieces.cut(gid, sverts, sfield, ~smiss)


def _boundary_loops(pieces: _Pieces, edge_count: dict, part: int) -> list[list[int]]:
    """その部品の、面が 1 つだけの辺をたどった輪(切り口)。"""
    adj: dict[int, list[int]] = {}
    for (x, y), n in edge_count.items():
        if n == 1 and pieces.part[x] == part and pieces.part[y] == part:
            adj.setdefault(x, []).append(y)
            adj.setdefault(y, []).append(x)
    seen, loops = set(), []
    for v0 in adj:
        if v0 in seen:
            continue
        loop, prev, cur = [v0], None, v0
        seen.add(v0)
        while True:
            nxt = [w for w in adj[cur] if w != prev and w not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            loop.append(cur)
            seen.add(cur)
        loops.append(loop)
    return loops


def _seam_run(pieces: _Pieces, edge_count: dict, vs: list[int]) -> list[int]:
    """縫う頂点 vs をいちばん多く含む切り口の輪の上で、縫う頂点が続く所(3 つまでの切れ目は許す)のうち、いちばん長い所。"""
    want = set(vs)
    loop = max(_boundary_loops(pieces, edge_count, pieces.part[vs[0]]), key=lambda lp: len(want.intersection(lp)))
    m = [v in want for v in loop]
    if all(m):
        return loop
    n = len(loop)
    start = m.index(False)
    runs, cur, gap = [], [], 0
    for q in range(1, n + 1):
        idx = (start + q) % n
        if m[idx]:
            if cur:
                cur += [loop[(idx - g) % n] for g in range(gap, 0, -1)]
            cur.append(loop[idx])
            gap = 0
        else:
            gap += 1
            if gap > 3 and cur:
                runs.append(cur)
                cur = []
    if cur:
        runs.append(cur)
    return max(runs, key=len)


def _spread(ov: np.ndarray, part: np.ndarray, moves: dict) -> None:
    """切り口の頂点の動き moves を、同じ部品のまわりの頂点へ、離れるほど小さく(SPREAD で 0)広げて動かす。"""
    for pid in sorted({int(part[v]) for v in moves}):
        seam = [v for v in moves if part[v] == pid]
        src, disp = ov[seam].copy(), np.array([moves[v] for v in seam])
        idx = np.nonzero(part == pid)[0]
        d = np.linalg.norm(ov[idx][:, None] - src[None], axis=2)
        near = d.min(axis=1)
        keep = near < SPREAD
        idx, d, near = idx[keep], d[keep], near[keep]
        g = np.exp(-((d - near[:, None]) / 0.01) ** 2)
        fall = (1 - near / SPREAD) ** 2
        ov[idx] += (g @ disp) / g.sum(axis=1, keepdims=True) * fall[:, None]
        ov[seam] = src + disp


def _zip_plans(pieces: _Pieces, joints: dict) -> list[tuple]:
    """左右の袖ぐりごとに、身頃と袖の切り口を同じ向き・始まりに並べ、たがいに寄せて、ファスナーのように交互に進む順
    (どちらの頂点が、相手のどの辺の上に来るか)を決める。"""
    ov = np.array(pieces.verts)
    part = np.array(pieces.part)
    edge_count: dict = {}
    for f in pieces.faces:
        for j in range(len(f)):
            e = tuple(sorted((f[j], f[(j + 1) % len(f)])))
            edge_count[e] = edge_count.get(e, 0) + 1
    border = sorted({v for e, n in edge_count.items() if n == 1 for v in e})
    plans = []
    for gid, side in ((1, "left"), (2, "right")):
        sx = joints[f"{side}_shoulder"][0]
        torso_b = [v for v in border if pieces.part[v] == 0 and v in pieces.seam and ov[v][0] * sx > 0]
        sleeve_b = [v for v in border if pieces.part[v] == gid and v in pieces.seam]
        if not torso_b or not sleeve_b:
            continue
        T, S = _seam_run(pieces, edge_count, torso_b), _seam_run(pieces, edge_count, sleeve_b)
        if len(T) < 3 or len(S) < 3:
            continue
        tp = ov[T]
        j0 = int(np.linalg.norm(ov[S] - tp[0], axis=1).argmin())
        S = S[j0:] + S[:j0]
        k = max(1, len(T) // 4)
        kk = min(k, len(S) - 1)
        if np.linalg.norm(ov[S[kk]] - tp[k]) > np.linalg.norm(ov[S[-kk]] - tp[k]):
            S = [S[0]] + S[1:][::-1]

        # 切り口どうしを、たがいの線(折れ線)の近い点へ CLOSE だけ寄せる(まわりもなめらかに一緒に動かす)
        def nearest_on(poly, q):
            a, b = poly[:-1], poly[1:]
            ab = b - a
            t = np.clip(((q - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
            c = a + t[:, None] * ab
            return c[np.argmin(np.linalg.norm(c - q, axis=1))]

        tpl, spl = ov[T + T[:1]].copy(), ov[S + S[:1]].copy()
        moves = {v: CLOSE / 2 * (nearest_on(spl, ov[v]) - ov[v]) for v in T}
        moves.update({v: CLOSE / 2 * (nearest_on(tpl, ov[v]) - ov[v]) for v in S})
        _spread(ov, part, moves)
        nT, nS = len(T), len(S)
        i = j = 0
        events = []
        while i < nT or j < nS:
            ti, tn = T[i % nT], T[(i + 1) % nT]
            sj, sn = S[j % nS], S[(j + 1) % nS]
            if j >= nS or (i < nT and np.linalg.norm(ov[tn] - ov[sj]) <= np.linalg.norm(ov[sn] - ov[ti])):
                events.append(("T", (i + 1) % nT, j % nS))  # 身頃の頂点 tn は、袖の辺 (sj, sn) の上に来る
                i += 1
            else:
                events.append(("S", (j + 1) % nS, i % nT))
                j += 1
        plans.append((T, S, events))
    pieces.verts = list(ov)
    return plans


def _join(bm: bmesh.types.BMesh, plans: list[tuple]) -> int:
    """端と端をつなぐ: 相手の頂点の所で自分の辺を分け、1 対 1 で溶かし合わせる(縫い代・折り返しを作らない)。"""
    bm.verts.ensure_lookup_table()
    weld = {}
    for tv, sv, events in [([bm.verts[v] for v in T], [bm.verts[v] for v in S], ev) for T, S, ev in plans]:
        last_t = dict(enumerate(tv))  # 辺 i(T[i] → T[i+1])の上で、いま分けた所
        last_s = dict(enumerate(sv))

        def split_toward(last, verts, e_idx, q):
            a, b = last[e_idx], verts[(e_idx + 1) % len(verts)]
            ed = bm.edges.get((a, b))
            if ed is None:
                return None
            ab = b.co - a.co
            t = max(0.05, min(0.95, (q - a.co).dot(ab) / max(ab.length_squared, 1e-12)))
            if (a.co + ab * t - q).length > 0.05:
                return None
            _, nv = bmesh.utils.edge_split(ed, a, t)
            last[e_idx] = nv
            return nv

        if (tv[0].co - sv[0].co).length < 0.05:
            weld[sv[0]] = tv[0]
        for kind, a_i, e_i in events:
            if kind == "T":
                v = tv[a_i]
                if v in weld or v in weld.values():
                    continue
                nv = split_toward(last_s, sv, e_i, v.co)
                if nv is not None:
                    weld[nv] = v
            else:
                v = sv[a_i]
                if v in weld:
                    continue
                nv = split_toward(last_t, tv, e_i, v.co)
                if nv is not None:
                    weld[v] = nv
    for a, b in weld.items():
        a.co = b.co = (a.co + b.co) / 2
    seam = set(weld.values())
    bmesh.ops.weld_verts(bm, targetmap=weld)
    # つないだ線のまわり(2 つ隣まで)をならす(切り口のぎざぎざを消す)
    ring = {v for v in seam if v.is_valid}
    for _ in range(2):
        ring |= {e.other_vert(v) for v in ring for e in v.link_edges}
    for _ in range(SEAM_SMOOTH):
        bmesh.ops.smooth_vert(bm, verts=list(ring), factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)
    return len(weld)


def _tidy(bm: bmesh.types.BMesh) -> None:
    """縫い目に残った小さなすき間(辺 12 本まで)をふさぎ、面の無い頂点・小さな島(袖ぐりの近くの切れ端)を消す。"""
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-5)
    bmesh.ops.triangulate(bm, faces=bmesh.ops.holes_fill(bm, edges=bm.edges, sides=12)["faces"])
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_edges], context="VERTS")
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
    seen, islands = set(), []
    for v in bm.verts:
        if v in seen:
            continue
        stack, island = [v], []
        seen.add(v)
        while stack:
            u = stack.pop()
            island.append(u)
            for e in u.link_edges:
                w = e.other_vert(u)
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        islands.append(island)
    for island in islands:
        if len(island) < 50:
            bmesh.ops.delete(bm, geom=island, context="VERTS")


def _inside(body: _Body, obj: bpy.types.Object) -> int:
    """体の面より内に入った服の頂点の数。"""
    inside = 0
    for v in obj.data.vertices:
        loc, n, _, _ = body.bvh.find_nearest(obj.matrix_world @ v.co)
        if loc is not None and (obj.matrix_world @ v.co - loc).dot(n) < 0:
            inside += 1
    return inside


def _simulate(obj: bpy.types.Object, body_obj: bpy.types.Object, bending: float, frames: int, pin_w: np.ndarray,
              rest: np.ndarray | None = None, under: tuple = ()) -> None:
    """
    服を布として、体(と先に着せた服 under)に当たり判定を付けて frames コマ動かし、落ち着いた形を服の頂点に書く
    (ほかのモディファイアは残す)。
    pin_w は頂点ごとの留める強さ(0〜1)。rest(頂点ごとの位置)を渡すと、その形の長さを布の休む長さにする。
    """
    pin = obj.vertex_groups.new(name="_pin")
    for i in np.nonzero(pin_w > 0)[0]:
        pin.add([int(i)], float(pin_w[i]), "REPLACE")
    if rest is not None:
        obj.shape_key_add(name="Basis")
        key = obj.shape_key_add(name="_rest")
        key.data.foreach_set("co", rest.reshape(-1).astype(np.float32))
    cl = obj.modifiers.new("_cloth", "CLOTH")
    s = cl.settings
    s.quality = 8
    s.mass = 0.15
    s.air_damping = 1.0
    s.tension_stiffness = s.compression_stiffness = 80
    s.shear_stiffness = 10
    s.bending_stiffness = bending
    s.vertex_group_mass = "_pin"
    s.pin_stiffness = 0.5
    if rest is not None:
        s.rest_shape_key = key
    cs = cl.collision_settings
    cs.distance_min = COLLISION
    cs.use_self_collision = True
    cs.self_distance_min = 0.002
    cl.point_cache.frame_start = 1
    cl.point_cache.frame_end = frames
    colliders = []
    for other in (body_obj, *under):
        colliders.append((other, other.modifiers.new("_garment_collision", "COLLISION")))
        other.collision.thickness_outer = COLLISION
        other.collision.cloth_friction = 5
        # 先に着せた服は薄い 1 枚なので、両面で当てる(片面だと、面の向きの側へ押し出される)
        other.collision.use_culling = other is body_obj
    scene = bpy.context.scene
    keep = (scene.frame_start, scene.frame_end, scene.frame_current)
    scene.frame_start, scene.frame_end = 1, frames
    for f in range(1, frames + 1):
        scene.frame_set(f)
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    co = np.zeros(len(ev.data.vertices) * 3)
    ev.data.vertices.foreach_get("co", co)
    scene.frame_start, scene.frame_end = keep[0], keep[1]
    for other, col in colliders:
        other.modifiers.remove(col)
    obj.modifiers.remove(cl)
    obj.vertex_groups.remove(pin)
    if rest is not None:
        obj.shape_key_clear()
    obj.data.vertices.foreach_set("co", co)
    obj.data.update()
    scene.frame_set(keep[2])


def settle(obj: bpy.types.Object, body_obj: bpy.types.Object, joints: dict, dressed: np.ndarray, cfg: dict,
           frames: int = 20, under: tuple = ()) -> int:
    """
    腕を下ろした姿勢に戻した服(dress_model.py の unpose のあと)を、休んだ姿勢の体の上でもう一度布として動かし、
    腕と胴の間に挟まった布を押し出してなじませる(布の長さは、着せたときの形 dressed のまま)。体に食い込んだ点の数を返す。
    """
    cfg = {**DEFAULTS, **cfg}
    # 動かすのは、腕のまわり(肩 → 肘 → 手首の軸から SETTLE_FREE m の内で、肩の関節より 4cm 以上下)だけ。わきの下に
    # 挟まった布と、腿に当たった袖口を押し出す。ほかは戻した位置に留める(全体を動かすと胴の脇・背中に縦じわが、
    # 肩まで動かすと肩の縫い目にさざ波が出た)
    co = np.array([obj.matrix_world @ v.co for v in obj.data.vertices])
    near = np.full(len(co), np.inf)
    for side in ("left", "right"):
        pts = [np.array(joints[f"{side}_{j}"]) for j in ("shoulder", "elbow", "wrist")]
        for a, b in zip(pts[:-1], pts[1:]):
            near = np.minimum(near, _seg_dist(co, a, b))
    sh_z = (joints["left_shoulder"][2] + joints["right_shoulder"][2]) / 2
    free = (1 - np.clip((near - SETTLE_FREE[0]) / (SETTLE_FREE[1] - SETTLE_FREE[0]), 0, 1)) * np.clip((sh_z - 0.04 - co[:, 2]) / 0.04, 0, 1)
    pin_w = 1 - free
    _simulate(obj, body_obj, cfg["bending"], frames, pin_w, rest=dressed, under=under)
    return _inside(_Body(body_obj, under), obj)


def build_top(body_obj: bpy.types.Object, joints: dict, cfg: dict, name: str = "top", frames: int = 40,
              under: tuple = ()) -> tuple:
    """
    体(着せる姿勢。raise_arms で腕を上げ、関節も取り直したもの)に、上の服を布として着せ、落ち着いた形のメッシュの
    オブジェクトと、寸法・確かめの記録を返す。体(と先に着せた服 under。上の服はその外に置く)には当たり判定を付けて、
    終わったら外す。
    """
    cfg = {**DEFAULTS, **cfg}
    body = _Body(body_obj, under)
    pieces = _Pieces()
    measure = _torso(body, joints, cfg, pieces)
    if cfg["sleeve"] > 0:
        for gid, side in ((1, "left"), (2, "right")):
            _sleeve(body, joints, cfg, pieces, gid, side, measure["armpit_z"])
    plans = _zip_plans(pieces, joints) if cfg["sleeve"] > 0 else []

    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(map(float, p)) for p in pieces.verts], [], pieces.faces)
    me.update()
    bm = bmesh.new()
    bm.from_mesh(me)
    joined = _join(bm, plans)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
    _tidy(bm)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)

    # 布: 置いた形を休む形にする。肩の上を軽く留める
    for poly in me.polygons:
        poly.use_smooth = True
    _simulate(obj, body_obj, cfg["bending"], frames,
              np.array([1.0 if v.co.z > measure["sh_z"] - 0.03 else 0.0 for v in me.vertices]), under=under)
    inside = _inside(body, obj)
    info = {"config": cfg, "hem_z": measure["hem_z"], "armpit_z": measure["armpit_z"], "vertices": len(me.vertices), "inside_body": inside, "seam_joined": joined}
    return obj, info
