"""
素体の2Dモデルの揺れもの(body_physics.py)のテスト。作った骨格(体の骨+髪の鎖+胸)で、揺れのキーの付き方を確かめる。

実行方法:
  uv run pytest tests/test_body_physics.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import body_motions, body_physics, body_rig  # noqa: E402
from test_body_rig import _doll  # noqa: E402


@pytest.fixture
def rig() -> dict:
    _, mask, points = _doll()
    j, meshes = body_rig.build_meshes(mask, points)
    bones = [{"name": b.name, "label": b.label, "parent": b.parent,
              "head": [float(v) for v in j[b.head]], "tail": [float(v) for v in j[b.tail]]} for b in body_rig.BONES]
    # 顔の右(絵の右)に垂れた髪の鎖と、胸
    bones += [
        {"name": "hair.L.1", "parent": "head", "kind": "hair", "chain": "hair.L", "head": [225, 60], "tail": [228, 100]},
        {"name": "hair.L.2", "parent": "hair.L.1", "kind": "hair", "chain": "hair.L", "head": [228, 100], "tail": [226, 140]},
        {"name": "breast.L", "parent": "torso", "kind": "breast", "head": [222, 170], "tail": [222, 158]},
    ]
    return {"size": [400, 560], "bones": bones, "meshes": meshes,
            "joints": {k: [float(v[0]), float(v[1])] for k, v in j.items()}}


def _still(frames: int = 24) -> dict:
    return body_motions.Motion("still", frames, {"torso": [(1, 0), (frames, 0)]}).to_json()


def test_no_motion_no_wind_stays_at_rest(rig: dict) -> None:
    baked = body_physics.bake(rig, _still())
    assert max(abs(v) for _, v in baked["rotate"]["hair.L.2"]) < 0.5
    assert max(abs(v) for _, *xy in baked["translate"]["breast.L"] for v in xy) < 0.1
    assert len(baked["rotate"]["hair.L.1"]) == 24  # コマごとのキー


def test_wind_blows_the_hair_within_limits(rig: dict) -> None:
    baked = body_physics.bake(rig, _still(48), wind=1.0)
    turns = [v for _, v in baked["rotate"]["hair.L.1"]]
    # 風は絵の右向き: 下へ垂れた髪の先は右へ(反時計回り=正)。上限(根もと 25 度)は超えない
    assert max(turns) > 5 and max(abs(t) for t in turns) <= 25.0001


def test_jump_bounces_the_breasts_and_limits_the_hair(rig: dict) -> None:
    baked = body_physics.bake(rig, body_motions.MOTIONS["jump"].to_json())
    ys = [y for _, _, y in baked["translate"]["breast.L"]]
    shoulder = 80.0
    assert max(ys) - min(ys) > 1.0 and max(abs(y) for y in ys) <= shoulder * 0.06 + 0.01
    assert max(abs(v) for _, v in baked["rotate"]["hair.L.2"]) <= 35.0001
    # もとの動きのキーはそのまま
    assert baked["translate"]["torso"] == body_motions.MOTIONS["jump"].to_json()["translate"]["torso"]


def test_rigs_without_physics_bones_are_untouched(rig: dict) -> None:
    plain = {**rig, "bones": [b for b in rig["bones"] if not b.get("kind")]}
    motion = body_motions.MOTIONS["wave"].to_json()
    assert body_physics.bake(plain, motion) is motion


def test_pose_rotates_children_with_the_parent(rig: dict) -> None:
    rest = body_physics._rest(rig)
    world = body_physics.pose(rest, {"torso": 90.0}, {})
    # 胴を 90 度回すと、首(胴の先)は骨盤の横へ
    pelvis = world["torso"]["head"]
    head = world["head"]["head"]
    assert head[0] < pelvis[0] - 100 and abs(head[1] - pelvis[1]) < 5


def test_spine_translate_is_in_parent_space(rig: dict) -> None:
    motion = {"translate": {"breast.L": [[1, 0.0, 10.0]], "torso": [[1, 5.0, 0.0]]}}
    out = body_physics.to_spine_translate(rig, motion)
    # 胴(根もとの骨の子)はそのまま。胸は胴(上向き)から見ると、上は骨の向き(x)
    assert out["torso"][0] == [1, 5.0, 0.0]
    assert out["breast.L"][0][1] == pytest.approx(10.0, abs=0.5) and abs(out["breast.L"][0][2]) < 0.5


def test_hair_chains_and_breast_points() -> None:
    joints = {"nose": np.array([200.0, 60.0]), "neck": np.array([200.0, 120.0]),
              "left_shoulder": np.array([240.0, 120.0]), "right_shoulder": np.array([160.0, 120.0]),
              "left_hip": np.array([225.0, 280.0]), "right_hip": np.array([175.0, 280.0])}
    hair = np.zeros((300, 400), bool)
    hair[60:200, 225:240] = True  # 顔の右(キャラの左)に垂れた髪
    chains = body_physics.hair_chains(hair, joints, min_pixels=100)
    assert [c["chain"] for c in chains] == ["hair.L"]
    (root, mid, tip) = chains[0]["points"]
    assert root[1] < mid[1] < tip[1] and 225 <= tip[0] <= 240
    points = body_physics.breast_points(joints)
    assert [p["name"] for p in points] == ["breast.L", "breast.R"]
    assert points[0]["center"][0] > 200 > points[1]["center"][0]
