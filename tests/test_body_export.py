"""
素体の2Dモデルの書き出し(body_export.py)のテスト。Spine の JSON は、Spine と同じ計算(骨のワールド変換 → 重み付き頂点)で
解き直して、設定姿勢でメッシュの頂点が元の位置に戻ること・動きで骨が回ることを確かめる。PSD は部位ごとのレイヤーを確かめる。

実行方法:
  uv run pytest tests/test_body_export.py -v
"""

from __future__ import annotations

import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import body_export, body_rig  # noqa: E402
from test_body_rig import _doll  # noqa: E402


def _world(skeleton: dict, animation: dict | None = None, time: float = 0.0) -> dict[str, np.ndarray]:
    """Spine の骨のワールド変換(3×3)。動きは、時刻をまたぐキーの間を直線で補う。"""

    def at(keys: list[dict], fields: tuple[str, ...]) -> list[float]:
        for a, b in zip(keys, keys[1:]):
            if a["time"] <= time <= b["time"]:
                u = (time - a["time"]) / (b["time"] - a["time"])
                return [a[f] + (b[f] - a[f]) * u for f in fields]
        return [keys[0 if time < keys[0]["time"] else -1][f] for f in fields]

    out: dict[str, np.ndarray] = {}
    for bone in skeleton["bones"]:
        rot, x, y = bone.get("rotation", 0.0), bone.get("x", 0.0), bone.get("y", 0.0)
        timeline = (animation or {}).get("bones", {}).get(bone["name"], {})
        if "rotate" in timeline:
            rot += at(timeline["rotate"], ("value",))[0]
        if "translate" in timeline:
            dx, dy = at(timeline["translate"], ("x", "y"))
            x, y = x + dx, y + dy
        r = math.radians(rot)
        local = np.array([[math.cos(r), -math.sin(r), x], [math.sin(r), math.cos(r), y], [0, 0, 1]])
        out[bone["name"]] = out[bone["parent"]] @ local if bone.get("parent") else local
    return out


def _skin(skeleton: dict, attachment: dict, world: dict[str, np.ndarray]) -> np.ndarray:
    names = [b["name"] for b in skeleton["bones"]]
    data, i, points = attachment["vertices"], 0, []
    while i < len(data):
        count = int(data[i])
        i += 1
        p = np.zeros(2)
        for _ in range(count):
            bone, bx, by, weight = data[i:i + 4]
            i += 4
            p += weight * (world[names[int(bone)]] @ np.array([bx, by, 1.0]))[:2]
        points.append(p)
    return np.array(points)


@pytest.fixture
def rig() -> dict:
    _, mask, points = _doll()
    j, meshes = body_rig.build_meshes(mask, points)
    return {
        "size": [400, 560], "texture": "texture.png", "meshes": meshes,
        "joints": {k: [float(v[0]), float(v[1])] for k, v in j.items()},
        "bones": [{"name": b.name, "label": b.label, "parent": b.parent,
                   "head": [float(v) for v in j[b.head]], "tail": [float(v) for v in j[b.tail]]} for b in body_rig.BONES],
    }


def test_spine_setup_pose_puts_vertices_back(rig: dict) -> None:
    skeleton = body_export.spine_skeleton(rig)
    assert skeleton["skeleton"]["spine"].startswith("4.1")
    assert [b["name"] for b in skeleton["bones"]][:2] == ["root", "torso"]
    world = _world(skeleton)
    w, h = rig["size"]
    for slot in skeleton["slots"]:
        attachment = skeleton["skins"][0]["attachments"][slot["name"]][slot["attachment"]]
        uv = np.array(attachment["uvs"]).reshape(-1, 2) * [w, h]
        expected = np.stack([uv[:, 0] - w / 2, h - uv[:, 1]], axis=1)
        assert np.abs(_skin(skeleton, attachment, world) - expected).max() < 0.1
        # 輪郭(hull)の頂点は先頭に、三角形は頂点の数の中
        assert 3 <= attachment["hull"] <= len(uv) and max(attachment["triangles"]) < len(uv)
    # 腕は体の後ろのスロット(手前に描く)
    names = [s["name"] for s in skeleton["slots"]]
    assert names.index("arm.L") > names.index("body")


def test_spine_animation_moves_the_arm(rig: dict) -> None:
    skeleton = body_export.spine_skeleton(rig)
    assert set(skeleton["animations"]) >= {"wave", "jump"}
    attachment = skeleton["skins"][0]["attachments"]["arm.R"]["arm.R"]
    rest = _skin(skeleton, attachment, _world(skeleton))
    raised = _skin(skeleton, attachment, _world(skeleton, skeleton["animations"]["wave"], 0.5))
    # 右手(絵の左)を上げる: 腕の先(いちばん下の頂点)がずっと上へ動く
    lowest = rest[:, 1].argmin()
    assert raised[lowest, 1] - rest[lowest, 1] > 100
    # ジャンプは体ごと上へ
    body = skeleton["skins"][0]["attachments"]["body"]["body"]
    up = _skin(skeleton, body, _world(skeleton, skeleton["animations"]["jump"], 9 / 24)) - _skin(skeleton, body, _world(skeleton))
    assert np.allclose(up[:, 1].mean(), 110, atol=15)


def test_export_writes_spine_zip_and_psd(rig: dict, tmp_path: Path) -> None:
    image, mask, _ = _doll()
    rgba = np.dstack([np.asarray(image), (mask * 255).astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").save(tmp_path / "texture.png")
    (tmp_path / "rig.json").write_text(json.dumps(rig), encoding="utf-8")
    out = body_export.export_all(tmp_path)
    with zipfile.ZipFile(out["spine.zip"]) as archive:
        assert set(archive.namelist()) == {"skeleton.json", "skeleton.atlas", "texture.png"}
        assert "bounds:0,0,400,560" in archive.read("skeleton.atlas").decode()
    from psd_tools import PSDImage

    psd = PSDImage.open(out["model.psd"])
    folders = {g.name: [layer.name for layer in g] for g in psd}
    assert list(folders) == ["body"]
    assert {"head", "torso", "upper_arm.L", "hand.R", "foot.L"} <= set(folders["body"])
    # 腕は胴より手前(PSD は下から並ぶ)
    assert folders["body"].index("upper_arm.L") > folders["body"].index("torso")


def test_hull_first_orders_the_outline() -> None:
    # 正方形を2つの三角形に: 4つとも輪郭
    verts = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    order, tris, hull = body_export.hull_first(verts, np.array([[0, 1, 2], [0, 2, 3]]))
    assert hull == 4 and sorted(order.tolist()) == [0, 1, 2, 3] and tris.max() == 3
