"""
素体の2Dモデル(body_rig.py)のテスト。人物の切り出し・骨格・Blender は使わず、作った人形の形と関節の点で、
メッシュ(形に合わせた三角形)と重み(関節のまわりで混ざる・腕の胴の重みは肩のまわりだけ・頭は顎の線より上)を確かめる。

実行方法:
  uv run pytest tests/test_body_rig.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import body_rig  # noqa: E402

P = {
    "nose": (200, 60), "left_shoulder": (240, 120), "right_shoulder": (160, 120),
    "left_elbow": (270, 200), "right_elbow": (130, 200), "left_wrist": (290, 270), "right_wrist": (110, 270),
    "left_hip": (225, 280), "right_hip": (175, 280), "left_knee": (232, 390), "right_knee": (168, 390),
    "left_ankle": (236, 480), "right_ankle": (164, 480),
}


def _doll() -> tuple[Image.Image, np.ndarray, dict[str, np.ndarray]]:
    """太い線で描いた人形(Aポーズ。脚の間はあける)。"""
    mask = np.zeros((560, 400), np.uint8)
    cv2.circle(mask, (200, 60), 40, 1, -1)
    cv2.rectangle(mask, (160, 100), (240, 290), 1, -1)
    for a, b, width in (("left_shoulder", "left_elbow", 22), ("left_elbow", "left_wrist", 18),
                        ("right_shoulder", "right_elbow", 22), ("right_elbow", "right_wrist", 18),
                        ("left_hip", "left_knee", 30), ("left_knee", "left_ankle", 22),
                        ("right_hip", "right_knee", 30), ("right_knee", "right_ankle", 22)):
        cv2.line(mask, P[a], P[b], 1, width)
    for w in ("left_wrist", "right_wrist"):
        cv2.circle(mask, P[w], 16, 1, -1)
    image = Image.fromarray(np.dstack([mask * 180] * 3).astype(np.uint8))
    return image, mask.astype(bool), {k: np.array(v, np.float32) for k, v in P.items()}


def _mesh(groups: list[dict], name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    group = next(g for g in groups if g["name"] == name)
    return np.array(group["vertices"]), np.array(group["triangles"]), np.array(group["weights"]), group["bones"]


def test_meshes_cover_the_body_and_weights_blend() -> None:
    _, mask, points = _doll()
    j, groups = body_rig.build_meshes(mask, points)
    assert [g["name"] for g in groups] == ["body", "arm.L", "arm.R"]
    for group in groups:
        weights = np.array(group["weights"])
        assert weights.shape == (len(group["vertices"]), len(group["bones"]))
        assert np.allclose(weights.sum(axis=1), 1.0, atol=1e-3)  # rig.json の重みは小数4桁に丸める
    # 腕は体の手前に重ねる
    assert {g["name"]: g["z"] for g in groups}["arm.L"] > {g["name"]: g["z"] for g in groups}["body"]

    verts, tris, weights, bones = _mesh(groups, "body")
    # 脚の間(股より下)をまたぐ三角形は無い
    centroid = verts[tris].mean(axis=1)
    between_legs = (centroid[:, 1] > 330) & (np.abs(centroid[:, 0] - 200) < 8)
    assert not between_legs.any()
    # 膝のあたりでは、大腿と下腿が混ざる(曲げても切れない)
    knee = np.linalg.norm(verts - P["left_knee"], axis=1) < 6
    assert knee.any() and (weights[knee][:, bones.index("thigh.L")] > 0.05).all() and (weights[knee][:, bones.index("shin.L")] > 0.05).all()
    # 顎の線より上は頭だけ
    top = verts[:, 1] < 70
    assert (weights[top][:, bones.index("head")] > 0.99).all()

    verts, _, weights, bones = _mesh(groups, "arm.L")
    # 腕のメッシュの胴の重みは、肩のまわりだけ(肘・手首では 0)
    torso = weights[:, bones.index("torso")]
    far = np.linalg.norm(verts - P["left_shoulder"], axis=1) > 60
    assert far.any() and (torso[far] == 0).all() and torso.max() > 0.3


def test_build_writes_texture_and_rig_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image, mask, points = _doll()
    source = tmp_path / "a_pose.png"
    image.save(source)
    from python import anime_seg, ugoira_rig

    monkeypatch.setattr(anime_seg, "person_mask", lambda _img: mask.astype(np.float32))
    skeleton = ugoira_rig.Skeleton(
        np.array([points.get(n, (0, 0)) for n in ugoira_rig.KEYPOINTS], np.float32),
        np.array([1.0 if n in points else 0.0 for n in ugoira_rig.KEYPOINTS], np.float32),
    )
    monkeypatch.setattr(ugoira_rig, "detect_pose", lambda _img, _box: skeleton)
    body_rig.build(source, tmp_path / "rig")
    saved = json.loads((tmp_path / "rig" / "rig.json").read_text(encoding="utf-8"))
    assert [b["name"] for b in saved["bones"]] == [b.name for b in body_rig.BONES]
    assert saved["size"] == [400, 560] and len(saved["meshes"]) == 3
    texture = Image.open(tmp_path / "rig" / "texture.png")
    assert texture.mode == "RGBA" and texture.getpixel((5, 5))[3] == 0 and texture.getpixel((200, 200))[3] > 200
    assert (tmp_path / "rig" / "overlay.png").is_file()

    # zip には テクスチャ・rig.json・Blender のファイルが入り、描き出したコマは消える
    (tmp_path / "rig" / "rig.blend").write_bytes(b"blend")
    (tmp_path / "rig" / "frames").mkdir()
    (tmp_path / "rig" / "frames" / "f_0001.png").write_bytes(b"png")
    import zipfile

    with zipfile.ZipFile(body_rig.package(tmp_path / "rig")) as archive:
        assert {"texture.png", "rig.json", "rig.blend"} <= set(archive.namelist())
    assert not (tmp_path / "rig" / "frames").exists()


def test_missing_joints_are_reported() -> None:
    _, mask, points = _doll()
    del points["left_knee"]
    with pytest.raises(body_rig.RigError, match="left_knee"):
        body_rig.build_meshes(mask, points)


def test_find_blender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"")
    monkeypatch.delenv("BLENDER_PATH", raising=False)
    assert body_rig.find_blender(str(exe)) == str(exe)
    monkeypatch.setenv("BLENDER_PATH", str(exe))
    assert body_rig.find_blender("") == str(exe)
    assert body_rig.find_blender(str(tmp_path / "none.exe")) == str(exe)


def test_outfit_layer_is_the_changed_part_and_gets_its_own_meshes() -> None:
    image, mask, points = _doll()
    # 服を着た絵: 胴を青く塗り、スカートを素体の形の外まで広げる。頭は同じ
    outfit = np.asarray(image).copy()
    outfit[130:290, 160:241] = (40, 60, 160)
    skirt = np.zeros(mask.shape, np.uint8)
    cv2.fillPoly(skirt, [np.array([(165, 285), (235, 285), (260, 340), (140, 340)], np.int32)], 1)
    outfit[skirt.astype(bool)] = (40, 60, 160)
    person = mask | skirt.astype(bool)
    j = body_rig.joints(points, mask)
    region = body_rig.outfit_region(image, Image.fromarray(outfit), mask, person, j)
    assert region[200, 200] and region[330, 150]  # 胴の服と、素体の形の外のスカート
    assert not region[60, 200]  # 頭は入れない
    assert not region[230, 285]  # 変わっていない腕は入れない

    _, groups = body_rig.build_meshes(mask, points, region)
    names = {g["name"]: g for g in groups}
    assert "outfit.body" in names and names["outfit.body"]["texture"] == "outfit.png"
    assert names["outfit.body"]["z"] == names["body"]["z"] + 1
    # 服の無い腕のメッシュは作らない(肩の付け根に服がかからなければ)
    assert all(g["texture"] == "outfit.png" for g in groups if g["name"].startswith("outfit."))


def test_joint_drift() -> None:
    _, _, points = _doll()
    moved = {k: v + np.array([8.0, 0.0], np.float32) for k, v in points.items()}
    assert body_rig.joint_drift(points, points) == 0
    assert body_rig.joint_drift(points, moved) == pytest.approx(8 / 80)


def test_motions_are_well_formed() -> None:
    from python import body_motions

    names = {b.name for b in body_rig.BONES}
    assert set(body_rig.MOTIONS) == set(body_motions.MOTIONS) >= {"wave", "idle", "walk", "cheer", "jump", "sway", "point"}
    for key, motion in body_motions.MOTIONS.items():
        for bone, points in [*motion.rotate.items(), *motion.translate.items()]:
            assert bone in names, (key, bone)
            frames = [p[0] for p in points]
            assert frames == sorted(frames) and frames[0] == 1 and frames[-1] <= motion.frames, (key, bone)
            # くり返しても途切れないように、最初と最後は休みの姿勢
            assert all(v == 0 for v in points[0][1:]) and all(v == 0 for v in points[-1][1:]), (key, bone)


def test_run_blender_passes_the_motion_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_run(args, **kwargs):  # noqa: ANN001
        seen["args"] = args
        (tmp_path / "rig.blend").write_bytes(b"blend")
        (tmp_path / "frames").mkdir()
        (tmp_path / "frames" / "f_0001.png").write_bytes(b"png")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(body_rig.subprocess, "run", fake_run)
    result = body_rig.run_blender("blender.exe", tmp_path, "jump")
    motion = json.loads((tmp_path / "motion.json").read_text(encoding="utf-8"))
    assert motion["name"] == "jump" and motion["translate"]["torso"][1] == [10, 0, 110]
    assert seen["args"][-1] == str(tmp_path / "motion.json") and len(result["frames"]) == 1
    with pytest.raises(body_rig.RigError):
        body_rig.run_blender("blender.exe", tmp_path, "fly")
