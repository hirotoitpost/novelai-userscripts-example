"""
髪を型から作って、スキンヘッドの骨入りモデルに付ける(model_3d.make_hair から呼ぶ)。

  blender --background --factory-startup --python hair_kit.py -- <bald のフォルダ> <config.json> <確かめの動き>

<bald のフォルダ>/model.blend を開き、スキンヘッドの頭に沿って髪の部品を作る:
- 頭を覆う髪(殻): 頭皮から浮かせ、生え際の高さは向きで変える(額・こめかみ・耳の上・うなじ)。まとめ髪では、お団子へ
  集まる筋を付ける。
- お団子(0・1 個(後ろ)・2 個(左右の後ろ)): 巻いた筋のある球と、はねた毛。
- 前髪・顔の横の房・後れ毛(耳の前・うなじ)・下ろした後ろ髪: 道すじに沿った、平たい・先のとがった房。
- ポニーテール・ツインテール(結んで垂らす束): 結び目から重力で垂らした道すじのまわりに、房を束ねる。
下ろした髪(後ろ髪と、あごより長い顔の横の房)は、根もとを頭に留めて重力で垂らし、頭・肩・背中に当てて落ち着いた
形にする(drape)。道すじを体の面に当てて決めると、房が肩で板のように広がった(2026-11 試作)。
部品は 1 つのオブジェクト hair にまとめ、殻は head の骨、房とお団子は、それぞれの鎖の骨(根もとは head の子)に付ける。
動きごとに、鎖の関節を点として揺らし(遅れ・休んだ形へ戻るばね・頭と顔に当たると押し出す)、鎖の骨の回転のキーを
その動きのアクションに焼き込む。骨入りの GLB(model_rigged.glb)を書き出し直し、休んだ姿勢の正面・横・後ろの絵
(hair_<向き>.png)と、確かめの動きのコマ(hair_frames/)を描く。結果は <bald のフォルダ>/hair.json に書く。

頭の目安(試作。矢野栄子 2026-11): 横幅がいちばん細い高さ(neck)は、あごのあたり。頭の真ん中はその上 45%、目は 27%。
頭の真ん中より下は、頭の真ん中からの向きで当てると胸に当たるので、高さごとに横から当てる。
"""

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

# hairs.py の FIELDS と同じ名前・省いたときの値
DEFAULTS = {
    "volume": 14.0,  # 頭のてっぺんの髪の厚み(mm)
    "hairline": 28.0,  # 額の生え際の高さ(頭の真ん中からの仰角 °)
    "bangs": 9,  # 前髪の房の数
    "bangs_length": 1.0,  # 前髪の長さ(0 で生え際・1 で目の上)
    "bangs_spread": 44.0,  # 前髪の広がり(正面から左右へ °)
    "bangs_part": 0.3,  # 分け目(真ん中の房を短くする割合)
    "side_locks": 1,  # 顔の横の房(左右の組の数)
    "side_length": 1.0,  # 顔の横の房の長さ(0 で耳の下・1 であご・2 で肩)
    "wisps": 2,  # 耳の前の後れ毛(左右それぞれ)
    "nape_wisps": 1,  # うなじの後れ毛(左右それぞれ)
    "buns": 1,  # お団子の数(0・1 で後ろ・2 で左右の後ろ)
    "bun_size": 0.5,  # お団子の大きさ(頭の半径に対する割合)
    "bun_height": 16.0,  # お団子の高さ(頭の真ん中からの仰角 °)
    "bun_wisps": 5,  # お団子からはねた毛
    "back_length": 0.0,  # 下ろした後ろ髪の長さ(0 でまとめ髪・1 で肩・2 で肩甲骨・3 で背中の真ん中)
    "back_locks": 24,  # 下ろした後ろ髪の房の数
    "tails": 0,  # 結んで垂らす束の数(0・1 でポニーテール・2 でツインテール)
    "tail_length": 1.5,  # 束の長さ(1 で 22cm。結び目から垂らす長さ)
    "tail_height": 25.0,  # 結び目の高さ(頭の真ん中からの仰角 °)
    "tail_volume": 1.0,  # 束の太さ(1 で標準)
}
HAIR_COLOR = (0.55, 0.45, 0.33, 1)
FRONT = -math.pi / 2  # 正面(-Y)の向き
# 揺れ: 1 コマを分ける回数・遅れ(速さを残す割合)・休んだ形へ戻る強さ(1 回ごと。部品ごと)。重力は休んだ形に入って
# いるので足さない(足すと、横へ出たお団子が 1cm ほど垂れた)
SUBSTEPS, KEEP, FPS = 4, 0.88, 24
SPRING = {"bang": 0.3, "side": 0.1, "wisp": 0.06, "nape": 0.08, "bun": 0.25, "back": 0.08, "tail": 0.03}
# 鎖の関節が体に近づける距離の下限(休んだ形での離れがこれより近ければ、その離れ)
CLEAR = 0.004
# 垂らす: 繰り返す回数・1 回に落ちる距離(m)・速さを残す割合・曲がりをならす強さ・体から離す距離
DRAPE_STEPS, DRAPE_FALL, DRAPE_KEEP, DRAPE_BEND, DRAPE_CLEAR = 300, 0.0006, 0.9, 0.12, 0.006


def _world_mesh(obj: bpy.types.Object) -> tuple[np.ndarray, list]:
    """評価した形(休んだ姿勢)の頂点(世界の座標)と面。"""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    mw = np.array(obj.matrix_world)
    co = np.array([v.co for v in me.vertices]).reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    polys = [tuple(p.vertices) for p in me.polygons]
    ev.to_mesh_clear()
    return co, polys


class Head:
    """
    スキンヘッドの頭の目安と、頭・体の面に当てる道具。当てるのはいつも体の面で、clothes(着せた服)があれば、その所の
    服の外までの距離だけ体から離す(need)。服は 1 枚の面で、表裏がそろわない所があり、服の面に直に当てると、近くの
    面の向きで内へ押し込まれて、房が飛び散った(2026-11 ロング+ブラウス)。
    """

    def __init__(self, body: bpy.types.Object, clothes: tuple = ()):
        co, polys = _world_mesh(body)
        self.bvh = BVHTree.FromPolygons([tuple(v) for v in co], polys)
        self.cloth = None
        if clothes:
            verts, faces = [], []
            for obj in clothes:
                c, f = _world_mesh(obj)
                base = len(verts)
                verts += [tuple(v) for v in c]
                faces += [tuple(base + i for i in face) for face in f]
            self.cloth = BVHTree.FromPolygons(verts, faces)
        self.height = float(np.ptp(co[:, 2]))
        zs = np.linspace(0.75 * self.height, 0.95 * self.height, 41)
        widths = []
        for z in zs:
            band = co[np.abs(co[:, 2] - z) < 0.004]
            widths.append(np.ptp(band[:, 0]) if len(band) > 10 else np.inf)
        self.neck = float(zs[int(np.argmin(widths))])
        head = co[co[:, 2] > self.neck + 0.02]
        self.top = float(head[:, 2].max())
        span = self.top - self.neck
        self.center = np.array([(head[:, 0].min() + head[:, 0].max()) / 2, (head[:, 1].min() + head[:, 1].max()) / 2,
                                self.neck + 0.45 * span])
        self.radius = self.top - self.center[2]
        self.eye = self.neck + 0.27 * span
        self.chin = self.neck - 0.015

    def _cast(self, origin: np.ndarray, d: np.ndarray, fallback: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        hit = self.bvh.ray_cast(Vector(origin), Vector(-d))
        if hit[0] is None:
            return fallback, d
        n = np.array(hit[1])
        return np.array(hit[0]), n if n @ d >= 0 else -n

    def surface(self, az: float, el: float) -> tuple[np.ndarray, np.ndarray]:
        """頭の真ん中から(向き, 仰角)の方向の、体の外側の面の点と法線。"""
        d = direction(az, el)
        return self._cast(self.center + d * 0.6, d, self.center + d * self.radius)

    def side(self, az: float, z: float) -> tuple[np.ndarray, np.ndarray]:
        """高さ z で、頭の縦の軸へ横から当てた、体の外側の面の点と法線(頭の真ん中より下に使う)。"""
        d = np.array([math.cos(az), math.sin(az), 0.0])
        origin = np.array([self.center[0], self.center[1], z]) + d * 0.5
        return self._cast(origin, d, np.array([self.center[0], self.center[1], z]) + d * 0.05)

    def hang(self, az0: float, el0: float, az1: float, z1: float, n: int, off0: float, off1: float) -> tuple[list, list]:
        """頭の上(az0, el0)から、頭に沿って下り、高さ z1 まで垂れる道すじ(点・外向き)。"""
        p0, _ = self.surface(az0, el0)
        pts, outs = [], []
        reach = 0.0
        for k in range(n):
            t = k / (n - 1)
            az = az0 + (az1 - az0) * t
            z = p0[2] + (z1 - p0[2]) * t
            off = off0 + (off1 - off0) * t
            if z > self.center[2]:
                p, nrm = self.surface(az, math.asin(min(1.0, (z - self.center[2]) / self.radius)))
            else:
                p, nrm = self.side(az, z)
            q = p + nrm * off
            # あごより下では、頭の縦の軸へ寄らずにまっすぐ垂らす(細い首に沿って内へ回り込んだ)
            radial = q[:2] - self.center[:2]
            dist = float(np.linalg.norm(radial))
            if z < self.chin and dist < reach:
                q[:2] = self.center[:2] + radial / max(dist, 1e-9) * reach
            reach = max(reach, float(np.linalg.norm(q[:2] - self.center[:2]))) if z < self.chin + 0.02 else reach
            pts.append(q)
            outs.append(nrm)
        return pts, outs


    def need(self, at, normal, clear: float) -> float:
        """体の面の点 at から、体の外へ離す距離: clear か、服があれば服の外までの距離 + clear。"""
        if self.cloth is None:
            return clear
        hit = self.cloth.ray_cast(Vector(at) + Vector(normal) * 0.0005, Vector(normal), 0.08)
        return clear if hit[0] is None else hit[3] + 0.0005 + clear

    def drape(self, path: list, fixed: int, clear: float = DRAPE_CLEAR) -> list:
        """
        道すじ(点の並び)の先 fixed 個を留めて、残りを重力で垂らし、体(頭・肩・背中・胸)に当てて落ち着かせる。
        長さは保ち、曲がりは少しならす(房が折れないように)。
        """
        p = np.array(path, dtype=float)
        rest = np.linalg.norm(np.diff(p, axis=0), axis=1)
        prev = p.copy()
        for _ in range(DRAPE_STEPS):
            vel = (p - prev) * DRAPE_KEEP
            prev = p.copy()
            p[fixed:] += vel[fixed:]
            p[fixed:, 2] -= DRAPE_FALL
            for _ in range(3):
                for i in range(max(fixed, 1), len(p)):
                    d = p[i] - p[i - 1]
                    p[i] = p[i - 1] + d / max(np.linalg.norm(d), 1e-9) * rest[i - 1]
            p[fixed:-1] += DRAPE_BEND * ((p[fixed - 1:-2] + p[fixed + 1:]) / 2 - p[fixed:-1])
            for i in range(fixed, len(p)):
                hit = self.bvh.find_nearest(Vector(p[i]))
                if hit[0] is None:
                    continue
                n = np.array(hit[1])
                gap = (p[i] - np.array(hit[0])) @ n
                need = self.need(hit[0], hit[1], clear)
                if gap < need:
                    p[i] += n * (need - gap)
        return list(p)

    def outward(self, p: np.ndarray) -> np.ndarray:
        """点のいちばん近くの体の面の、外向きの法線。"""
        hit = self.bvh.find_nearest(Vector(p))
        return np.array(hit[1]) if hit[0] is not None else np.array([0, 1.0, 0])


def direction(az: float, el: float) -> np.ndarray:
    return np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])


class Part:
    """髪の部品 1 つ: 形と、鎖(関節の点。None なら head に付ける)、頂点ごとの鎖の上の位置(0〜1)。"""

    def __init__(self, name: str, kind: str, verts: list, faces: list, chain=None, along=None):
        self.name, self.kind, self.verts, self.faces = name, kind, verts, faces
        self.chain = None if chain is None else np.array(chain)
        self.along = along


def hairline(cfg: dict, az: float) -> float:
    """生え際の仰角: 額(cfg)・こめかみ・耳の上・うなじ。"""
    a = abs(((az - FRONT) + math.pi) % (2 * math.pi) - math.pi)
    front = math.radians(cfg["hairline"])
    temple, side, back = front - math.radians(10), math.radians(5), math.radians(-38)
    if a < math.pi * 0.25:
        return front + (temple - front) * (a / (math.pi * 0.25))
    if a < math.pi * 0.5:
        return temple + (side - temple) * ((a - math.pi * 0.25) / (math.pi * 0.25))
    return side + (back - side) * ((a - math.pi * 0.5) / (math.pi * 0.5)) ** 1.3


def bun_places(cfg: dict) -> list[tuple[float, float]]:
    el = math.radians(cfg["bun_height"])
    if cfg["buns"] == 1:
        return [(math.pi / 2, el)]
    if cfg["buns"] == 2:
        return [(math.pi / 2 - math.radians(55), el), (math.pi / 2 + math.radians(55), el)]
    return []


def tail_places(cfg: dict) -> list[tuple[float, float]]:
    """結び目の向き: 1 本は後ろ、2 本は左右の耳の後ろ。"""
    el = math.radians(cfg["tail_height"])
    if cfg["tails"] == 1:
        return [(math.pi / 2, el)]
    if cfg["tails"] == 2:
        return [(math.pi / 2 - math.radians(75), el), (math.pi / 2 + math.radians(75), el)]
    return []


def cap(head: Head, cfg: dict) -> Part:
    """頭を覆う髪(厚みのある殻)。お団子・結び目があれば、そこへ集まる筋を付ける。"""
    cols, rows = 96, 28
    buns = [direction(*p) for p in bun_places(cfg) + tail_places(cfg)]
    outer, inner = [], []
    for r in range(rows + 1):
        for c in range(cols):
            az = 2 * math.pi * c / cols - math.pi
            el = math.pi / 2 - (math.pi / 2 - hairline(cfg, az)) * (r / rows)
            p, n = head.surface(az, el)
            thick = 0.004 + cfg["volume"] / 1000 * (1 - r / rows) ** 0.6
            if buns:
                axis = min(buns, key=lambda b: np.linalg.norm(direction(az, el) - b))
                rel = p - head.center
                perp = rel - axis * (rel @ axis)
                ref = np.cross(axis, [0, 0, 1.0])
                ref /= np.linalg.norm(ref)
                ang = math.atan2(perp @ np.cross(axis, ref), perp @ ref)
                thick *= 0.8 + 0.35 * (0.5 + 0.5 * math.cos(ang * 14))
            outer.append(p + n * (0.002 + thick))
            inner.append(p + n * 0.001)
    faces = []
    total = (rows + 1) * cols
    for r in range(rows):
        for c in range(cols):
            a, b = r * cols + c, r * cols + (c + 1) % cols
            faces.append((a, b, b + cols, a + cols))
            faces.append((total + a + cols, total + b + cols, total + b, total + a))
    for c in range(cols):
        a, b = rows * cols + c, rows * cols + (c + 1) % cols
        faces.append((a, total + a, total + b, b))
    return Part("cap", "cap", outer + inner, faces)


def cap_offset(cfg: dict, az: float, el: float) -> float:
    """頭を覆う髪の、その所の外側の頭皮からの高さ(筋は除く)。上に重ねる房を、殻の上から生やすのに使う。"""
    r = min(1.0, max(0.0, (math.pi / 2 - el) / max(math.pi / 2 - hairline(cfg, az), 1e-6)))
    return 0.006 + cfg["volume"] / 1000 * (1 - r) ** 0.6


def lock(name: str, kind: str, path: list, outs, width: float, thick: float, ring: int = 8, segments: int = 3) -> Part:
    """道すじに沿った、平たい・先のとがった房(根もとは細く始めて、殻との境に段を作らない)。鎖は道すじを segments 個に分けた点。"""
    path = np.array(path)
    n = len(path)
    verts, faces, along = [], [], []
    for i in range(n):
        t = i / (n - 1)
        tan = path[min(i + 1, n - 1)] - path[max(i - 1, 0)]
        tan /= np.linalg.norm(tan)
        out = np.array(outs[i]) if np.ndim(outs) == 2 else np.array(outs)
        out = out - tan * (out @ tan)
        out /= np.linalg.norm(out)
        side = np.cross(tan, out)
        root = 0.4 + 0.6 * min(1.0, t / 0.1)
        w = width * root * (1 - t ** 1.6) + 0.0005
        h = thick * root * (1 - t ** 1.6) + 0.0005
        for k in range(ring):
            a = 2 * math.pi * k / ring
            verts.append(path[i] + side * math.cos(a) * w / 2 + out * math.sin(a) * h / 2)
            along.append(t)
    for i in range(n - 1):
        for k in range(ring):
            a, b = i * ring + k, i * ring + (k + 1) % ring
            faces.append((a, b, b + ring, a + ring))
    faces.append(tuple(range(ring))[::-1])
    lengths = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))])
    ts = lengths / lengths[-1]
    chain = [np.array([np.interp(s, ts, path[:, d]) for d in range(3)]) for s in np.linspace(0, 1, segments + 1)]
    # 頂点の鎖の上の位置は、道すじの長さの割合
    along = [float(np.interp(a * (n - 1), np.arange(n), ts)) for a in along]
    return Part(name, kind, verts, faces, chain, along)


def bun(head: Head, cfg: dict, index: int, az: float, el: float, rng) -> list[Part]:
    """お団子(巻いた筋のある、少しつぶれた球)と、はねた毛。鎖は頭皮からお団子の真ん中を通る 1 本。"""
    axis = direction(az, el)
    root, _ = head.surface(az, el)
    size = cfg["bun_size"] * head.radius
    center = root + axis * size * 0.55
    e1 = np.cross(axis, [0, 0, 1.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis, e1)
    u, v = 48, 24
    verts, faces = [], []
    for i in range(v + 1):
        th = math.pi * i / v
        for j in range(u):
            ph = 2 * math.pi * j / u
            r = size * (1 + 0.04 * math.sin(3 * ph + 5 * th))
            d = math.cos(th) * axis * 0.85 + math.sin(th) * (math.cos(ph) * e1 + math.sin(ph) * e2)
            verts.append(center + d * r)
    for i in range(v):
        for j in range(u):
            a, b = i * u + j, i * u + (j + 1) % u
            faces.append((a, b, b + u, a + u))
    chain = [root, center + axis * size]
    parts = [Part(f"bun{index}", "bun", verts, faces, chain, [1.0] * len(verts))]
    for k in range(cfg["bun_wisps"]):
        d0 = rng.normal(size=3)
        d0 -= axis * (d0 @ axis) * 0.5
        d0 /= np.linalg.norm(d0)
        curl = np.cross(d0, axis)
        start = center + d0 * size * 0.9
        pts = [start + d0 * size * 0.6 * t + curl * size * 0.35 * t * t for t in np.linspace(0, 1, 8)]
        wisp = lock(f"bun{index}_wisp{k}", "bun", pts, axis, 0.006, 0.002)
        # はねた毛は、お団子と一緒に動く
        wisp.chain, wisp.along = np.array(chain), [1.0] * len(wisp.verts)
        wisp.name = f"bun{index}"
        parts.append(wisp)
    return parts


def tail_bundle(head: Head, cfg: dict, index: int, az: float, el: float) -> list[Part]:
    """
    結んで垂らす束: 結び目(殻の上)から少し外へ出し、下へ足して重力で垂らした真ん中の道すじのまわりに、房を 7 本束ねる
    (根もとは細く、なかほどで広がり、先でまとまる)。房は全部、真ん中の道すじの鎖に付ける(束で一緒に揺れる)。
    細い房 7 本では、房が離れて糸のように見えたので、太く 9 本にした。
    """
    root, n = head.surface(az, el)
    tie = root + n * (cap_offset(cfg, az, el) + 0.004)
    length = 0.22 * cfg["tail_length"]
    lift = [tie + n * 0.025 * k / 3 for k in range(4)]
    more = max(6, int(length / 0.012))
    pts = lift + [lift[-1] - np.array([0, 0, length * k / more]) for k in range(1, more + 1)]
    center = np.array(head.drape(pts, 2, clear=DRAPE_CLEAR + 0.006))
    core = lock(f"tail{index}", "tail", center, [head.outward(q) for q in center], 0.04, 0.03, segments=4)
    parts = [core]
    spread = 0.02 * cfg["tail_volume"]
    count = len(center)
    for k in range(9):
        a = 2 * math.pi * k / 9
        path = []
        for i, q in enumerate(center):
            t = i / (count - 1)
            tan = center[min(i + 1, count - 1)] - center[max(i - 1, 0)]
            tan /= np.linalg.norm(tan)
            u = np.cross(tan, [0, 0, 1.0]) if abs(tan[2]) < 0.9 else np.cross(tan, [1.0, 0, 0])
            u /= np.linalg.norm(u)
            v = np.cross(tan, u)
            r = spread * (0.3 + 1.2 * math.sin(math.pi * min(1.0, t * 1.2)) ** 0.8)
            path.append(q + (u * math.cos(a) + v * math.sin(a)) * r)
        strand = lock(f"tail{index}", "tail", path, [head.outward(q) for q in path], 0.036 * cfg["tail_volume"], 0.012)
        # 束の房は、真ん中の道すじの鎖で一緒に動かす
        strand.chain, strand.along = core.chain, strand.along
        parts.append(strand)
    return parts


def build(head: Head, cfg: dict) -> list[Part]:
    """設定から、髪の部品を全部作る。"""
    parts = [cap(head, cfg)]
    rng = np.random.default_rng(3)
    for i, (az, el) in enumerate(bun_places(cfg)):
        parts += bun(head, cfg, i, az, el, rng)
    for i, (az, el) in enumerate(tail_places(cfg)):
        parts += tail_bundle(head, cfg, i, az, el)
    # 前髪: 正面のまわりに並べ(少し重ねる)、てっぺんの前から額に沿って下ろす。分け目は真ん中を短く
    count = cfg["bangs"]
    spread = cfg["bangs_spread"]
    for i, da in enumerate(np.linspace(-spread, spread, count) if count > 1 else [0.0] * count):
        az = FRONT + math.radians(da)
        start, _ = head.surface(az, math.radians(60))
        eye = head.eye + 0.008 - abs(da) * 0.0002
        part = cfg["bangs_part"] * 0.3 * max(0.0, 1 - abs(da) / max(spread * 0.4, 1e-6))
        z_end = start[2] + (eye - start[2]) * cfg["bangs_length"] * (1 - part)
        pts, outs = head.hang(az, math.radians(60), az + math.radians(da * 0.2), z_end, 14, 0.012, 0.016)
        parts.append(lock(f"bang{i}", "bang", pts, outs, 0.042 - abs(da) * 0.00012, 0.007, segments=2))
    fwd = np.array([0, -1.0, 0])
    ear = head.eye - 0.02
    for s, side in ((1, "L"), (-1, "R")):
        # 顔の横の房: こめかみから。平たい面を前へ向ける(横の面に沿わせると、正面から細い線にしか見えない)
        length = cfg["side_length"]
        z1 = ear + (head.chin - 0.01 - ear) * min(length, 1.0)
        for m in range(cfg["side_locks"]):
            az = FRONT + s * math.radians(58 + 8 * m)
            pts, outs = head.hang(az, math.radians(30), az - s * math.radians(10), z1, 18, 0.012, 0.020)
            outs = [(o + fwd * 1.5) / np.linalg.norm(o + fwd * 1.5) for o in outs]
            if length > 1:
                # あごより長い房は、あごから先を足して垂らす(肩・胸に沿う)
                extra = 0.10 * (length - 1)
                tail = pts[-1] - pts[-2]
                tail /= np.linalg.norm(tail)
                more = max(4, int(extra / 0.012))
                pts = pts + [pts[-1] + tail * extra * k / more for k in range(1, more + 1)]
                pts = head.drape(pts, len(pts) - more)
                ends = [head.outward(q) + fwd * 1.5 for q in pts[-more:]]
                outs = outs + [o / np.linalg.norm(o) for o in ends]
            parts.append(lock(f"side{side}{m}", "side", pts, outs, 0.028, 0.007, segments=3 if length <= 1 else 4))
        # 耳の前の後れ毛: 細く、あごのあたりまで
        for m in range(cfg["wisps"]):
            az = FRONT + s * math.radians(70 + 8 * m)
            z1 = head.chin + 0.01 - 0.04 * m
            pts, outs = head.hang(az, math.radians(18), az + s * math.radians(4), z1, 14, 0.008, 0.016)
            outs = [(o + fwd) / np.linalg.norm(o + fwd) for o in outs]
            parts.append(lock(f"wisp{side}{m}", "wisp", pts, outs, 0.012, 0.004))
        # うなじの後れ毛
        for m in range(cfg["nape_wisps"]):
            az = math.pi / 2 - s * math.radians(35 + 12 * m)
            pts, outs = head.hang(az, math.radians(-25), az, head.neck - 0.01 - 0.01 * m, 10, 0.004, 0.008)
            parts.append(lock(f"nape{side}{m}", "nape", pts, outs, 0.008, 0.003, segments=2))
    if cfg["back_length"] > 0:
        parts += back_hair(head, cfg)
    return parts


def back_hair(head: Head, cfg: dict) -> list[Part]:
    """
    下ろした後ろ髪: 耳の後ろから後ろを回って房を並べ、頭の高い所(殻の上)から生やして生え際まで頭に沿わせ、そこから
    まっすぐ下へ足して、重力で垂らす(頭の真ん中より上は留める)。房は少し重ね、となりと体からの離れを変えて重なりの
    ちらつきを防ぐ。
    """
    parts = []
    count = cfg["back_locks"]
    z_end = head.neck - 0.10 * cfg["back_length"]
    for i, da in enumerate(np.linspace(-100, 100, count)):
        az = math.pi / 2 + math.radians(da)
        layer = 0.002 * (i % 2)
        top_el, low_el = math.radians(60), hairline(cfg, az) + math.radians(3)
        pts = []
        for k in range(10):
            el = top_el + (low_el - top_el) * k / 9
            p, n = head.surface(az, el)
            pts.append(p + n * (cap_offset(cfg, az, el) + 0.002 + layer))
        drop = max(0.0, pts[-1][2] - z_end)
        more = max(4, int(drop / 0.012))
        pts += [pts[-1] - np.array([0, 0, drop * k / more]) for k in range(1, more + 1)]
        fixed = sum(1 for q in pts if q[2] > head.center[2])
        pts = head.drape(pts, max(fixed, 2), clear=DRAPE_CLEAR + layer)
        outs = [head.outward(q) for q in pts]
        parts.append(lock(f"back{i}", "back", pts, outs, 0.056, 0.009, segments=4))
    return parts


def chain_bones(part: Part, index: int) -> list[str]:
    return [f"hair_{part.name}.{j}" for j in range(len(part.chain) - 1)]


def assemble(parts: list[Part], arm: bpy.types.Object) -> tuple[bpy.types.Object, list[dict]]:
    """部品を 1 つのオブジェクト hair にまとめ、鎖の骨を入れて、重みを付ける。揺らす鎖の一覧を返す。"""
    to_arm = arm.matrix_world.inverted()
    # 同じ鎖を使う部品(お団子とはねた毛)は、鎖を 1 つにする
    chains: dict[str, dict] = {}
    for p in parts:
        if p.chain is not None and p.name not in chains:
            chains[p.name] = {"name": p.name, "kind": p.kind, "rest": p.chain, "bones": chain_bones(p, 0)}
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones
    for c in chains.values():
        for j, name in enumerate(c["bones"]):
            bone = eb.new(name)
            bone.head = to_arm @ Vector(c["rest"][j])
            bone.tail = to_arm @ Vector(c["rest"][j + 1])
            bone.parent = eb["head"] if j == 0 else eb[c["bones"][j - 1]]
            bone.use_connect = j > 0
    bpy.ops.object.mode_set(mode="OBJECT")
    for c in chains.values():
        for name in c["bones"]:
            arm.pose.bones[name].rotation_mode = "QUATERNION"

    verts, faces, weights = [], [], []
    for p in parts:
        base = len(verts)
        verts += [tuple(v) for v in p.verts]
        faces += [tuple(base + i for i in f) for f in p.faces]
        if p.chain is None:
            weights += [{"head": 1.0}] * len(p.verts)
            continue
        bones = chains[p.name]["bones"]
        seg = len(bones)
        for a in p.along:
            s = min(a * seg, seg - 1e-6)
            j = int(s)
            f = s - j
            w = {bones[j]: 1.0}
            if f < 0.25 and j > 0:
                w = {bones[j]: 0.5 + 2 * f, bones[j - 1]: 0.5 - 2 * f}
            elif f > 0.75 and j < seg - 1:
                w = {bones[j]: 0.5 + 2 * (1 - f), bones[j + 1]: 0.5 - 2 * (1 - f)}
            weights.append(w)
    me = bpy.data.meshes.new("hair")
    me.from_pydata(verts, [], faces)
    me.update()
    for poly in me.polygons:
        poly.use_smooth = True
    hair = bpy.data.objects.new("hair", me)
    bpy.context.scene.collection.objects.link(hair)
    groups: dict[str, bpy.types.VertexGroup] = {}
    for i, w in enumerate(weights):
        for name, value in w.items():
            if name not in groups:
                groups[name] = hair.vertex_groups.new(name=name)
            groups[name].add([i], value, "REPLACE")
    mod = hair.modifiers.new("rig", "ARMATURE")
    mod.object = arm
    hair.parent = arm
    hair.matrix_parent_inverse = arm.matrix_world.inverted()
    hair.color = HAIR_COLOR
    return hair, list(chains.values())


def bake(arm: bpy.types.Object, head: Head, chains: list[dict], actions: list) -> dict[str, float]:
    """
    動き(アクション)ごとに鎖を揺らして、鎖の骨の回転のキーを打つ(garment_sway.bake と同じ流れ)。部品の種類ごとの、
    鎖の先が頭に付いたまま動いた位置から離れたいちばん大きな距離(mm)を返す。
    """
    scene = bpy.context.scene
    to_arm = arm.matrix_world.inverted()
    head_rest = arm.matrix_world @ arm.data.bones["head"].matrix_local
    head_rest_inv = head_rest.inverted()
    torso_rest = arm.matrix_world @ arm.data.bones["torso"].matrix_local
    for c in chains:
        c["in_head"] = np.array([(head_rest_inv @ Vector(p))[:] for p in c["rest"]])
        # 首より下の関節は、胴に付いた体の面(肩・背中・胸)に当てる
        c["on_torso"] = [p[2] < head.neck for p in c["rest"]]
        c["len"] = np.linalg.norm(np.diff(c["rest"], axis=0), axis=1)
        clear = []
        for p in c["rest"]:
            hit = head.bvh.find_nearest(Vector(p))
            if hit[0] is None:
                clear.append(0.0)
                continue
            gap = (Vector(p) - hit[0]).dot(hit[1])
            clear.append(min(head.need(hit[0], hit[1], CLEAR), max(0.0, gap - 0.001)))
        c["clear"] = clear
        c["rel"] = [arm.data.bones[b].parent.matrix_local.inverted() @ arm.data.bones[b].matrix_local for b in c["bones"]]
    dt_keep = KEEP
    sway: dict[str, float] = {}
    for action in actions:
        start, end = (int(round(x)) for x in action.frame_range)
        arm.animation_data.action = action
        pos = {c["name"]: c["rest"].copy() for c in chains}
        prev = {c["name"]: c["rest"].copy() for c in chains}
        record = {}
        for loop in range(2):
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                hm = arm.matrix_world @ arm.pose.bones["head"].matrix
                tm = arm.matrix_world @ arm.pose.bones["torso"].matrix
                frames = {False: (head_rest @ hm.inverted(), hm @ head_rest_inv),
                          True: (torso_rest @ tm.inverted(), tm @ torso_rest.inverted())}
                for c in chains:
                    target = np.array([(hm @ Vector(q))[:] for q in c["in_head"]])
                    p, pr = pos[c["name"]], prev[c["name"]]
                    spring = SPRING[c["kind"]]
                    for _ in range(SUBSTEPS):
                        vel = (p - pr) * dt_keep
                        pr = p.copy()
                        p = p + vel + (target - p) * spring
                        p[0] = target[0]
                        for j in range(1, len(p)):
                            d = p[j] - p[j - 1]
                            p[j] = p[j - 1] + d / max(np.linalg.norm(d), 1e-9) * c["len"][j - 1]
                            # 体に当たったら押し出す(休んだ姿勢の体の面で。首より上は頭、下は胴に付けて見る)
                            to_rest, from_rest = frames[c["on_torso"][j]]
                            q = to_rest @ Vector(p[j])
                            hit = head.bvh.find_nearest(q)
                            if hit[0] is not None and (q - hit[0]).dot(hit[1]) < c["clear"][j]:
                                p[j] = np.array(from_rest @ (hit[0] + hit[1] * c["clear"][j]))
                    pos[c["name"]], prev[c["name"]] = p, pr
                if loop == 1:
                    record[frame] = ({k: v.copy() for k, v in pos.items()}, hm.copy())
                    for c in chains:
                        tip = np.array(hm @ Vector(c["in_head"][-1]))
                        off = float(np.linalg.norm(pos[c["name"]][-1] - tip)) * 1000
                        sway[c["kind"]] = max(sway.get(c["kind"], 0.0), off)
        for frame, (positions, hm) in record.items():
            head_arm = to_arm @ hm
            for c in chains:
                p = positions[c["name"]]
                parent = head_arm
                for j, name in enumerate(c["bones"]):
                    m0 = parent @ c["rel"][j]
                    origin = m0.to_translation() if j else to_arm @ Vector(p[0])
                    want = (to_arm @ Vector(p[j + 1])) - (m0.to_translation())
                    have = m0.to_3x3() @ Vector((0, 1, 0))
                    turn = have.rotation_difference(want)
                    q = m0.to_quaternion()
                    pb = arm.pose.bones[name]
                    pb.rotation_quaternion = q.inverted() @ turn @ q
                    pb.keyframe_insert("rotation_quaternion", frame=frame)
                    parent = Matrix.Translation(origin) @ (turn @ q).to_matrix().to_4x4()
    arm.animation_data.action = None
    for c in chains:
        for name in c["bones"]:
            arm.pose.bones[name].rotation_quaternion = Quaternion()
    return {k: round(v, 1) for k, v in sway.items()}


def render_views(folder: Path, height: float, length: float = 0.0) -> None:
    """休んだ姿勢の、頭のまわりの正面・横・後ろ(hair_<向き>.png)。下ろした髪が長いほど、下まで写す。"""
    scene = bpy.context.scene
    cam = _camera(height, 0.34 + 0.06 * length)
    for name, deg in (("front", 0), ("side", 90), ("back", 180)):
        _aim(cam, deg, height, 0.86 - 0.03 * length)
        scene.render.filepath = str(folder / f"hair_{name}.png")
        bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam)


def render_motion(folder: Path, arm: bpy.types.Object, action, height: float) -> None:
    """確かめの動きを、斜め後ろから、頭に付いて(hair_frames/)。"""
    scene = bpy.context.scene
    cam = _camera(height, 0.4)
    frames = folder / "hair_frames"
    frames.mkdir(exist_ok=True)
    arm.animation_data.action = action
    start, end = (int(round(x)) for x in action.frame_range)
    a = math.radians(140)
    for i, f in enumerate(range(start, end + 1, 2)):
        scene.frame_set(f)
        at = arm.matrix_world @ arm.pose.bones["head"].head
        cam.location = (at.x + math.sin(a) * height * 2, at.y - math.cos(a) * height * 2, at.z + 0.06 * height)
        cam.rotation_euler = (math.radians(90), 0, a)
        scene.render.filepath = str(frames / f"{i:03d}.png")
        bpy.ops.render.render(write_still=True)
    arm.animation_data.action = None
    bpy.data.objects.remove(cam)


def _camera(height: float, span: float) -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = scene.render.resolution_y = 400
    scene.render.film_transparent = True
    cam = bpy.data.objects.new("_hair_cam", bpy.data.cameras.new("_hair_cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = span * height
    return cam


def _aim(cam: bpy.types.Object, deg: float, height: float, at: float) -> None:
    a = math.radians(deg)
    cam.location = (math.sin(a) * height * 2, -math.cos(a) * height * 2, at * height)
    cam.rotation_euler = (math.radians(90), 0, a)


def remove_old(arm: bpy.types.Object) -> None:
    """前に付けた髪と、その鎖の骨・キーを消す。"""
    old = bpy.data.objects.get("hair")
    if old is not None:
        bpy.data.objects.remove(old)
    names = [b.name for b in arm.data.bones if b.name.startswith("hair_")]
    if not names:
        return
    for action in bpy.data.actions:
        for fc in [fc for fc in action.fcurves if any(f'"{n}"' in fc.data_path for n in names)]:
            action.fcurves.remove(fc)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for n in names:
        arm.data.edit_bones.remove(arm.data.edit_bones[n])
    bpy.ops.object.mode_set(mode="OBJECT")


def attach(arm: bpy.types.Object, body: bpy.types.Object, cfg: dict, clothes: tuple = ()) -> tuple[Head, dict]:
    """
    前に付けた髪を消し、cfg の髪を作って骨に付け、全部の動きに揺れを焼き込む(main と dress_model.py から使う)。
    clothes(着せた服)があれば、下ろした髪はその上に垂らし、揺れでも服の外に保つ。頭の目安と結果を返す。
    """
    cfg = {**DEFAULTS, **cfg}
    for key in ("bangs", "side_locks", "wisps", "nape_wisps", "buns", "bun_wisps", "back_locks", "tails"):
        cfg[key] = int(cfg[key])
    ad = arm.animation_data
    ad.action = None
    remove_old(arm)
    arm.data.pose_position = "REST"
    bpy.context.view_layer.update()
    head = Head(body, clothes)
    parts = build(head, cfg)
    hair, chains = assemble(parts, arm)
    arm.data.pose_position = "POSE"
    for pb in arm.pose.bones:
        pb.matrix_basis.identity()
    actions = [s.action for t in ad.nla_tracks for s in t.strips if s.action]
    # 動きの帯(NLA)を止めて、1 つずつの動きで揺らす(止めないと、全部の帯の動きが重なって頭が大きく動いた)
    muted = [t.mute for t in ad.nla_tracks]
    for t in ad.nla_tracks:
        t.mute = True
    sway = bake(arm, head, chains, actions)
    for t, m in zip(ad.nla_tracks, muted):
        t.mute = m
    return head, {
        "parts": len(parts), "chains": len(chains), "bones": sum(len(c["bones"]) for c in chains),
        "faces": len(hair.data.polygons), "motions": len(actions), "sway_mm": sway,
        "head": {"neck_z": round(head.neck, 3), "eye_z": round(head.eye, 3), "radius": round(head.radius, 3)},
    }


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    folder, cfg, motion = Path(argv[0]), json.loads(Path(argv[1]).read_text(encoding="utf-8")), argv[2]
    bpy.ops.wm.open_mainfile(filepath=str(folder / "model.blend"))
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    body = bpy.data.objects["body"]
    head, report = attach(arm, body, cfg)
    hidden = [o for o in bpy.data.objects if o.name.startswith("part_") and not o.hide_render]
    for o in hidden:
        o.hide_render = True
    body.color = (0.82, 0.8, 0.78, 1)
    bpy.context.view_layer.update()
    render_views(folder, head.height, float(cfg.get("back_length", DEFAULTS["back_length"])))
    preview = bpy.data.actions.get(motion)
    if preview is not None:
        tracks = arm.animation_data.nla_tracks
        muted = [t.mute for t in tracks]
        for t in tracks:
            t.mute = True
        render_motion(folder, arm, preview, head.height)
        for t, m in zip(tracks, muted):
            t.mute = m
    for o in hidden:
        o.hide_render = False
    bpy.context.scene.frame_set(1)
    bpy.ops.export_scene.gltf(
        filepath=str(folder / "model_rigged.glb"), export_format="GLB", export_animations=True,
        export_animation_mode="NLA_TRACKS", export_morph_normal=True,
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "model.blend"))
    (folder / "hair.json").write_text(json.dumps(report), encoding="utf-8")
    print("hair", report)


if __name__ == "__main__":
    main()
