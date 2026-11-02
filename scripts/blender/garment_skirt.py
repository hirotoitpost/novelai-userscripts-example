"""
型紙からスカートを作り、体に布として着せる(Blender の中で使う。dress_model.py から読み込む)。

設定(dict。省いた値は DEFAULTS):
- length 丈(0 腰・1 足首)、flare 裾の周り ÷ 腰の周り(1 以下は肌にぴったり)、ease ゆとり mm
- pleats ひだの数・pleat_depth 深さ mm・pleat_stitch ウエストから縫い留める長さ cm(省くと腰まで)・
  knife 車ひだ(3 枚重ねに折りたたみ、その形を型紙にして折り目を硬くする。制服のスカート)
- tucks ウエストのタックの数(前に左右対称)・tuck_depth 1 つで折りたたむ幅 mm・tuck_stitch 縫い留める長さ cm
- gussets マチの数(後ろ中心・左脇・右脇の順)・gusset_width 裾での幅 cm・gusset_height 高さ cm

作り方(docs/model_3d_plan_jp.md のステップ 6b):
1. 体の寸法: 高さごとの胴の中心(頂点の平均は高さごとに 1cm ほど跳ね、スカートの前に段ができたので、高さに沿って
   2 次式でならす)から光線を飛ばし、向きごとの半径を測る。腰より下は、両脚をまとめた外まわり(外から内へ光線)。
   高さごとの輪はへこみの無い形(凸包)にし(へそ・股のくぼみをわたる)、腰より下は急にすぼまらない(お尻の下)。
2. 型紙: 体に沿って置いた輪の、行ごとの横の長さ・列ごとの縦の長さを、平らな帯に広げる(rest)。ひだ・タック・マチは、
   置く形に折り目・ふくらみを入れ、その分だけ帯の幅を足す。車ひだは、折りたたんだ形そのものを型紙にする。
3. 縫い合わせ: 帯の両端(後ろ中心)を縫い代のばね(sewing)で引き寄せ、ウエストの輪を留めて、重力で落とす。
"""

import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

DEFAULTS = {
    "length": 0.25, "flare": 1.0, "ease": 3, "gores": 8,
    "pleats": 0, "pleat_depth": 14, "pleat_stitch": None, "knife": False,
    "tucks": 0, "tuck_depth": 25, "tuck_stitch": 6,
    "gussets": 0, "gusset_width": 12, "gusset_height": 15,
}
COLS, ROWS = 160, 44


class _Body:
    """体の面(光線・近い点を引く)と、高さごとの胴の中心。"""

    def __init__(self, co, tris, joints):
        self.co = co
        self.bvh = BVHTree.FromPolygons([tuple(map(float, p)) for p in co], [tuple(map(int, t)) for t in tris])
        mid = lambda a, b: (joints[a][2] + joints[b][2]) / 2  # noqa: E731
        self.hip_z = mid("left_hip", "right_hip")
        self.sh_z = mid("left_shoulder", "right_shoulder")
        self.ank_z = mid("left_ankle", "right_ankle")
        self.torso = self.sh_z - self.hip_z
        self.ang = np.linspace(0, 2 * math.pi, COLS, endpoint=False)  # 0 = 体の左(+X)、π/2 = 後ろ(+Y)、3π/2 = 前(-Y)
        zs = np.linspace(self.hip_z - 0.2 * self.torso, self.hip_z + 0.6 * self.torso, 25)
        ax = np.array([self._raw_axis(z) for z in zs])
        self._zs = zs
        self._fit = [np.polyfit(zs, ax[:, k], 2) for k in range(2)]

    def _raw_axis(self, z):
        sl = self.co[(np.abs(self.co[:, 2] - z) < 0.01) & (np.abs(self.co[:, 0]) < 0.11)]
        return np.array([sl[:, 0].mean(), sl[:, 1].mean()]) if len(sl) else np.array([0.0, 0.0])

    def axis(self, z):
        z = min(max(z, self._zs[0]), self._zs[-1])
        return np.array([np.polyval(self._fit[0], z), np.polyval(self._fit[1], z)])

    def inner_radii(self, z):
        """胴の中心から外へ光線を飛ばし、最初に当たった胴の面までの距離(腕に当たらない)。"""
        c = self.axis(z)
        r = np.zeros(COLS)
        for i, a in enumerate(self.ang):
            loc, _, _, dist = self.bvh.ray_cast(Vector((c[0], c[1], z)), Vector((math.cos(a), math.sin(a), 0)), 0.5)
            r[i] = dist if loc is not None else 0.0
        return r

    def outer_radii(self, z):
        """両脚をまとめた外まわり。手に当たらない所(横 21cm・前後 26cm)から内へ光線を飛ばす。"""
        c = self.axis(z)
        r = np.zeros(COLS)
        for i, a in enumerate(self.ang):
            start = Vector((c[0] + 0.21 * math.cos(a), c[1] + 0.26 * math.sin(a), z))
            loc, _, _, _ = self.bvh.ray_cast(start, Vector((-math.cos(a), -math.sin(a), 0)), 0.5)
            r[i] = math.hypot(loc.x - c[0], loc.y - c[1]) if loc is not None else 0.0
        return r

    def ring_length(self, r):
        pts = np.stack([np.cos(self.ang) * r, np.sin(self.ang) * r], 1)
        return float(np.linalg.norm(np.roll(pts, -1, 0) - pts, axis=1).sum())

    def hull_radii(self, r):
        """向きごとの半径 r の輪を、へこみの無い形(凸包)にした半径。"""
        pts = np.stack([np.cos(self.ang) * r, np.sin(self.ang) * r], 1)
        order = sorted(range(len(pts)), key=lambda i: (pts[i, 0], pts[i, 1]))

        def half(idx):
            h = []
            for i in idx:
                while len(h) >= 2 and np.cross(pts[h[-1]] - pts[h[-2]], pts[i] - pts[h[-2]]) <= 0:
                    h.pop()
                h.append(i)
            return h

        hull = pts[half(order)[:-1] + half(order[::-1])[:-1]]
        out = r.copy()
        for k, a in enumerate(self.ang):
            d = np.array([math.cos(a), math.sin(a)])
            best = 0.0
            for j in range(len(hull)):
                p0, e = hull[j], hull[(j + 1) % len(hull)] - hull[j]
                den = d[0] * (-e[1]) - d[1] * (-e[0])
                if abs(den) < 1e-12:
                    continue
                t = (p0[0] * (-e[1]) - p0[1] * (-e[0])) / den
                u = (d[0] * p0[1] - d[1] * p0[0]) / den
                if t > 0 and -1e-9 <= u <= 1 + 1e-9:
                    best = max(best, t)
            out[k] = max(r[k], best)
        return out


def _layout(body: _Body, cfg: dict) -> dict:
    """置く形(place)・型紙(rest)・折り目の列・寸法。"""
    ease, flare = cfg["ease"] / 1000, cfg["flare"]
    tight = flare <= 1.0
    zs = np.linspace(body.hip_z + 0.15 * body.torso, body.hip_z + 0.5 * body.torso, 15)
    waist_z = min(zs, key=lambda z: body.ring_length(body.inner_radii(z)))
    zs = np.linspace(body.hip_z - 0.15 * body.torso, body.hip_z + 0.05 * body.torso, 9)
    hipline_z = max(zs, key=lambda z: body.ring_length(body.inner_radii(z)))
    hem_z = body.hip_z - cfg["length"] * (body.hip_z - body.ank_z)
    row_z = np.linspace(waist_z, hem_z, ROWS)
    body_r = np.array([body.inner_radii(z) if z >= hipline_z else body.outer_radii(z) for z in row_z])
    body_r = np.array([body.hull_radii(r) for r in body_r])
    for i in range(1, ROWS):
        if row_z[i] < hipline_z:
            body_r[i] = np.maximum(body_r[i], body_r[i - 1] - 0.12 * (row_z[i - 1] - row_z[i]))
    hip_c = body.ring_length(body.inner_radii(hipline_z) + ease)

    base_r = body_r + ease + 0.004
    if not tight:
        hem_c = hip_c * flare
        for i, z in enumerate(row_z):
            if z < hipline_z:
                base_r[i] = np.maximum(base_r[i], np.interp(z, [hem_z, hipline_z], [hem_c, hip_c]) / (2 * math.pi))

    fold = np.zeros((ROWS, COLS))
    step = 2 * math.pi / COLS

    def bump(center, width_cols, extra, i):
        """center を真ん中に、width_cols 列の幅で、横の長さが extra(m)増えるとがったふくらみ。"""
        if extra <= 0:
            return
        k0, half = int(round(center / step)), width_cols / 2
        base = width_cols * step * float(base_r[i].mean())
        height = math.sqrt(max(((extra + base) / 2) ** 2 - (base / 2) ** 2, 0.0))
        for d in range(-int(half), int(half) + 1):
            fold[i, (k0 + d) % COLS] += height * max(0.0, 1 - abs(d) / half)

    for i, z in enumerate(row_z):
        if cfg["pleats"] and not cfg["knife"]:
            stitched = cfg["pleat_stitch"] is not None
            top = waist_z - cfg["pleat_stitch"] / 100 if stitched else hipline_z
            open_ = min(max((top - z) / (0.04 if stitched else 0.08), 0.0), 1.0)
            tri = np.abs((np.arange(COLS) * cfg["pleats"] / COLS) % 1 * 2 - 1) - 0.5
            fold[i] += open_ * cfg["pleat_depth"] / 1000 * tri
        if cfg["tucks"]:
            stitch, below = cfg["tuck_stitch"] / 100, waist_z - z
            extra = cfg["tuck_depth"] / 1000
            if below > stitch:
                extra *= max(0.0, 1 - (below - stitch) / max(waist_z - stitch - hipline_z, 0.03))
            for t in range(cfg["tucks"]):
                bump(1.5 * math.pi + (t - (cfg["tucks"] - 1) / 2) * math.radians(14), 4, extra, i)
        if cfg["gussets"]:
            extra = cfg["gusset_width"] / 100 * max(0.0, 1 - (z - hem_z) / (cfg["gusset_height"] / 100))
            for a in [0.5 * math.pi, 0.0, math.pi][: cfg["gussets"]]:
                bump(a, 10, extra, i)

    centers = np.array([body.axis(z) for z in row_z])
    creases: list[int] = []
    if cfg["knife"] and cfg["pleats"]:
        # 車ひだ: ひだ 1 つ = 表の布(幅 U)→ 折り目 → 奥へ戻る布(深さ D)→ 折り目 → また前へ出る布(D)→ 折り目。
        # 3 枚を少しずつ内側に重ねて置き、この折りたたんだ形をそのまま型紙にする(アイロンで付けた折り目)
        n, depth, t = cfg["pleats"], cfg["pleat_depth"] / 1000, 0.003
        m_o, m_d = 6, 3
        unit = 2 * math.pi / n
        rings = []
        for i, z in enumerate(row_z):
            ring = np.append(base_r[i], base_r[i][0])
            dl = min(depth / (float(base_r[i].mean()) + 2 * t), 0.8 * unit)
            cols, creases = [], []
            for u in range(n):
                a0 = 0.5 * math.pi + u * unit  # 後ろ中心から(縫い目を後ろに)
                a1 = a0 + unit
                cols += [(a0 + unit * j / m_o, 0.0) for j in range(m_o)]
                cols.append((a1, -0.5 * t)); creases.append(len(cols) - 1)  # noqa: E702
                cols += [(a1 - dl * j / m_d, -t) for j in range(1, m_d)]
                cols.append((a1 - dl, -1.5 * t)); creases.append(len(cols) - 1)  # noqa: E702
                cols += [(a1 - dl + dl * j / m_d, -2 * t) for j in range(1, m_d)]
                cols.append((a1, -t)); creases.append(len(cols) - 1)  # noqa: E702
            cols.append(cols[0])
            row = []
            for a, dr in cols:
                rr = float(np.interp(a % (2 * math.pi), np.append(body.ang, 2 * math.pi), ring)) + 2 * t + dr
                row.append((centers[i, 0] + rr * math.cos(a), centers[i, 1] + rr * math.sin(a), z))
            rings.append(row)
        place = np.array(rings)
    else:
        place_r = base_r + fold
        place = np.zeros((ROWS, COLS + 1, 3))
        for i, z in enumerate(row_z):
            for k in range(COLS + 1):
                a = body.ang[k % COLS]
                r = place_r[i, k % COLS]
                place[i, k] = (centers[i, 0] + r * math.cos(a), centers[i, 1] + r * math.sin(a), z)

    seg = np.linalg.norm(place[:, 1:] - place[:, :-1], axis=2)
    vert = np.linalg.norm(place[1:] - place[:-1], axis=2)
    y = np.vstack([np.zeros((1, place.shape[1])), np.cumsum(vert, axis=0)])
    x = np.hstack([np.zeros((ROWS, 1)), np.cumsum(seg, axis=1)])
    x -= x[:, -1:] / 2
    if creases:
        rest = place.copy()
    else:
        rest = np.stack([x, np.zeros_like(x), -y + row_z[0]], axis=2)
    width = x[:, -1] - x[:, 0]
    return {
        "place": place, "rest": rest, "creases": sorted(set(creases)), "tight": tight,
        "measure": {"waist_z": float(waist_z), "hipline_z": float(hipline_z), "hem_z": float(hem_z),
                    "hip_cm": round(hip_c * 100, 1), "pattern_waist_cm": round(float(width[0]) * 100, 1),
                    "pattern_hem_cm": round(float(width[-1]) * 100, 1)},
        "outline": [[float(x[i, 0]), float(-y[i, 0])] for i in range(ROWS)]
        + [[float(x[i, -1]), float(-y[i, -1])] for i in range(ROWS - 1, -1, -1)],
    }


def build_skirt(body_obj: bpy.types.Object, joints: dict, cfg: dict, name: str = "skirt", frames: int = 90) -> tuple:
    """
    体(休んだ姿勢。シェイプキー・モディファイアを当てた形)に、型紙のスカートを布として着せ、落ち着いた形のメッシュの
    オブジェクトと、寸法・確かめの記録を返す。体には当たり判定を付けて、終わったら外す。
    """
    cfg = {**DEFAULTS, **cfg}
    dg = bpy.context.evaluated_depsgraph_get()
    ev = body_obj.evaluated_get(dg)
    mesh = ev.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.transform(body_obj.matrix_world)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    co = np.array([v.co[:] for v in bm.verts])
    tris = np.array([[v.index for v in f.verts] for f in bm.faces])
    bm.free()
    ev.to_mesh_clear()
    body = _Body(co, tris, joints)
    lay = _layout(body, cfg)
    place, rest, creases = lay["place"], lay["rest"], lay["creases"]
    nc = place.shape[1] - 1

    me = bpy.data.meshes.new(name)
    idx = lambda i, k: i * (nc + 1) + k  # noqa: E731
    faces = [(idx(i, k), idx(i, k + 1), idx(i + 1, k + 1), idx(i + 1, k)) for i in range(ROWS - 1) for k in range(nc)]
    seams = [(idx(i, 0), idx(i, nc)) for i in range(ROWS)]  # 後ろ中心の縫い目(面の無い辺)
    me.from_pydata([tuple(p) for p in place.reshape(-1, 3)], seams, faces)
    me.update()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.shape_key_add(name="Basis")
    pattern = obj.shape_key_add(name="pattern")
    pattern.value = 0.0
    pattern.data.foreach_set("co", rest.reshape(-1).astype(np.float32))
    pin = obj.vertex_groups.new(name="_pin")
    pin.add([idx(0, k) for k in range(nc + 1)], 1.0, "REPLACE")
    if creases:
        crease = obj.vertex_groups.new(name="_crease")
        crease.add([idx(i, k) for i in range(ROWS) for k in range(nc + 1)], 0.05, "REPLACE")
        crease.add([idx(i, k) for i in range(ROWS) for k in creases], 1.0, "REPLACE")

    cl = obj.modifiers.new("cloth", "CLOTH")
    s = cl.settings
    s.quality = 10
    s.mass = 0.2
    s.air_damping = 1.0
    s.tension_stiffness = s.compression_stiffness = 80 if lay["tight"] else 20
    s.shear_stiffness = 10
    s.bending_stiffness = 3.0 if cfg["pleats"] else 0.5
    if creases:
        # 制服のスカートのような張りのある布(伸びにくく、曲がりにくい)。折り目の線はさらに硬く
        s.tension_stiffness = s.compression_stiffness = 80
        s.shear_stiffness = 40
        s.bending_stiffness = 4.0
        s.vertex_group_bending = "_crease"
        s.bending_stiffness_max = 100.0
    s.vertex_group_mass = "_pin"
    s.pin_stiffness = 1.0
    s.rest_shape_key = pattern
    s.use_sewing_springs = True
    s.sewing_force_max = 50
    cs = cl.collision_settings
    cs.distance_min = 0.003
    cs.use_self_collision = True
    cs.self_distance_min = 0.001 if creases else 0.002
    cl.point_cache.frame_start = 1
    cl.point_cache.frame_end = frames
    col = body_obj.modifiers.new("_garment_collision", "COLLISION")
    body_obj.collision.thickness_outer = 0.004
    body_obj.collision.cloth_friction = 5
    scene = bpy.context.scene
    keep = (scene.frame_start, scene.frame_end, scene.frame_current)
    scene.frame_start, scene.frame_end = 1, frames
    for f in range(1, frames + 1):
        scene.frame_set(f)
    dg = bpy.context.evaluated_depsgraph_get()
    done = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    scene.frame_start, scene.frame_end = keep[0], keep[1]
    scene.frame_set(keep[2])
    body_obj.modifiers.remove(col)
    obj.modifiers.clear()
    obj.shape_key_clear()
    old = obj.data
    obj.data = done
    bpy.data.meshes.remove(old)
    for p in done.polygons:
        p.use_smooth = True
    for g in list(obj.vertex_groups):
        obj.vertex_groups.remove(g)
    inside = 0
    for v in done.vertices:
        p = obj.matrix_world @ v.co
        loc, n, _, _ = body.bvh.find_nearest(p)
        if loc is not None and (p - loc).dot(n) < 0:
            inside += 1
    gap = max((done.vertices[a].co - done.vertices[b].co).length for a, b in seams)
    info = {"config": cfg, **lay["measure"], "vertices": len(done.vertices), "inside_body": inside,
            "seam_gap_mm": round(gap * 1000, 2), "outline": lay["outline"]}
    return obj, info
