"""
素体の 3D の形(model_3d.py と /api/lora-dataset/character/{id}/model-3d)のテスト。リモートの GPU と Blender は呼ばず、
model3d_remote.generate と Blender の実行を差し替える。DB と出力先は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_model_3d.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import body_poses, body_rig, db, hand_shape, model3d_remote, model_3d  # noqa: E402
from python.client import get_client  # noqa: E402
from python.routes import lora_dataset as lora_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


def _points(**xy: tuple[float, float]) -> dict[str, np.ndarray]:
    return {k: np.array(v, dtype=float) for k, v in xy.items()}


def test_facing_compares_the_nose_with_the_ears() -> None:
    assert model_3d.facing(_points(nose=(100, 50), left_ear=(130, 48))) == "left"
    assert model_3d.facing(_points(nose=(160, 50), right_ear=(130, 48))) == "right"
    # 耳が取れなければ肩で見る
    assert model_3d.facing(_points(nose=(100, 50), left_shoulder=(120, 120), right_shoulder=(125, 120))) == "left"
    assert model_3d.facing(_points(left_ear=(130, 48))) is None
    assert model_3d.facing(_points(nose=(130, 50), left_ear=(130, 48))) is None


def _draw(folder: Path, base_id: int, parts: tuple[str, ...]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for part in parts:
        image = Image.new("RGB", (8, 8), (200, 200, 200))
        image.putpixel((0, 0), (255, 0, 0))  # 左右の反転が分かるよう、左上に印を付ける
        image.save(folder / f"{base_id}_{part}.png")


def test_generate_flips_a_right_facing_side_and_records_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _draw(tmp_path / "body_base", 3, ("front", "side", "back"))
    sent: dict[str, Image.Image] = {}

    def fake_generate(views: dict[str, Path], out: Path, **options) -> Path:  # noqa: ANN003
        sent.update({k: Image.open(p).copy() for k, p in views.items()})
        out.write_bytes(b"glTF")
        return out

    monkeypatch.setattr(model3d_remote, "generate", fake_generate)
    monkeypatch.setattr(model_3d, "_side_facing", lambda _image: "right")
    meta = model_3d.generate(tmp_path / "body_base", 3, tmp_path / "out", seed=7)
    assert sorted(sent) == ["back", "front", "left"]
    # 横の絵だけ反転する(印が右上へ移る)
    assert sent["left"].getpixel((7, 0)) == (255, 0, 0) and sent["front"].getpixel((0, 0)) == (255, 0, 0)
    assert meta["side_flipped"] and meta["seed"] == 7 and meta["views"] == ["back", "front", "left"]
    assert json.loads((tmp_path / "out" / "meta.json").read_text(encoding="utf-8"))["side_flipped"]
    assert (tmp_path / "out" / "model.glb").read_bytes() == b"glTF"

    monkeypatch.setattr(model_3d, "_side_facing", lambda _image: "left")
    assert not model_3d.generate(tmp_path / "body_base", 3, tmp_path / "out")["side_flipped"]


def test_generate_needs_the_front(tmp_path: Path) -> None:
    _draw(tmp_path, 3, ("side", "back"))
    with pytest.raises(model_3d.Model3DInputError, match="正面"):
        model_3d.generate(tmp_path, 3, tmp_path / "out")


def test_silhouette_iou_finds_the_best_shift() -> None:
    a = np.zeros((100, 60), bool)
    a[:, 20:40] = True
    b = np.zeros((200, 200), bool)
    b[:, 150:190] = True  # 高さも位置も違うが、形は同じ
    assert model_3d.silhouette_iou(a, b) == pytest.approx(1.0, abs=0.02)
    wide = np.zeros((100, 60), bool)
    wide[:, 0:60] = True
    assert model_3d.silhouette_iou(a, wide) < 0.5
    assert model_3d.silhouette_iou(a, np.zeros((10, 10), bool)) == 0.0


def test_check_compares_each_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import anime_seg

    _draw(tmp_path, 3, ("front", "side"))
    person = np.zeros((8, 8), np.float32)
    person[:, 2:6] = 1
    monkeypatch.setattr(anime_seg, "person_mask", lambda _image: person)
    still = np.zeros((8, 8, 4), np.uint8)
    still[:, 2:6, 3] = 255
    Image.fromarray(still).save(tmp_path / "front.png")
    still[:, :, 3] = 255  # 横は形が太すぎる
    Image.fromarray(still).save(tmp_path / "side.png")
    result = model_3d.check(tmp_path, 3, tmp_path)
    assert result["views"]["front"] > 0.95 and result["views"]["left"] < model_3d.MIN_VIEW_IOU
    # 後ろは描いていないので比べない
    assert "back" not in result["views"] and not result["passed"] and "横" in result["reasons"][0]


def test_make_retries_with_another_seed_and_keeps_the_best(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seeds: list[int] = []
    scores = iter([0.5, 0.6, 0.55])

    def fake_generate(folder: Path, base_id: int, out: Path, *, seed: int, on_status=None) -> dict:  # noqa: ANN001
        seeds.append(seed)
        out.mkdir(parents=True)
        (out / "model.glb").write_bytes(str(seed).encode())
        (out / "meta.json").write_text(json.dumps({"seed": seed}), encoding="utf-8")
        return {}

    def fake_clean_and_check(blender: str, folder: Path, base_id: int, out: Path, height: float) -> dict:
        score = next(scores)
        result = {"views": {"left": score}, "score": score, "passed": False, "reasons": ["横"]}
        model_3d._record_check(out, result)
        return result

    monkeypatch.setattr(model_3d, "generate", fake_generate)
    monkeypatch.setattr(model_3d, "clean_and_check", fake_clean_and_check)
    out = tmp_path / "out"
    (out).mkdir()
    (out / "model_clean.glb").write_bytes(b"old")  # 前に作った形の残り
    meta = model_3d.make(tmp_path, 3, out, blender="blender", height=1.6, seed=10, attempts=3)
    assert seeds == [10, 10 + model_3d._SEED_STEP, 10 + 2 * model_3d._SEED_STEP]
    # いちばん重なりのよい2回目を残す
    assert (out / "model.glb").read_bytes() == str(seeds[1]).encode() and meta["check"]["attempts"] == 3
    assert meta["check"]["score"] == 0.6 and not (out / "model_clean.glb").exists() and not (out / "_tries").exists()


def test_still_to_world_inverts_the_front_camera() -> None:
    # 静止画の真ん中は、左右 0・身長の半分。上の端は身長の 1.05 倍(写す高さが身長の 1.1 倍)
    assert model_3d.still_to_world(180, 300, 1.6) == pytest.approx((0.0, 0.8))
    assert model_3d.still_to_world(180, 0, 1.6) == pytest.approx((0.0, 1.68))
    x, _ = model_3d.still_to_world(240, 300, 1.6)
    assert x == pytest.approx(60 * 1.6 * 1.1 / 600)


def test_rig_finds_joints_and_runs_blender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "model_clean.glb").write_bytes(b"glTF")
    Image.new("RGBA", (360, 600)).save(tmp_path / "front.png")
    model_3d.write_meta(tmp_path, {"clean": {"height": 1.6}})
    points = {n: np.array([180.0, 300.0]) for n in model_3d._RIG_POINTS}
    monkeypatch.setattr(body_rig, "detect", lambda _image: (None, points))
    sent: dict = {}

    def fake_run(command: list[str], **_kw) -> subprocess.CompletedProcess:  # noqa: ANN003
        sent["joints"] = json.loads(Path(command[-3]).read_text(encoding="utf-8"))
        sent["motions"] = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
        sent["motion"] = command[-1]
        (tmp_path / "model_rigged.glb").write_bytes(b"glTF")
        (tmp_path / "frames").mkdir()
        Image.new("RGBA", (20, 30)).save(tmp_path / "frames" / "001.png")
        (tmp_path / "rig.json").write_text(json.dumps({"weights": {"nearest_bone": 0}}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(model_3d.subprocess, "run", fake_run)
    report = model_3d.rig("blender", tmp_path, motion="walk")
    assert sent["joints"]["points"]["left_knee"] == pytest.approx([0.0, 0.8])
    assert "wave" in sent["motions"] and sent["motion"] == "walk"
    # 手の割合を省くと標準の割合で、Blender にも渡す
    assert sent["joints"]["hand"] == hand_shape.DEFAULTS
    assert report == {
        "motion": "walk", "weights": {"nearest_bone": 0},
        "hand": {"ratios": hand_shape.DEFAULTS, "source": None, "score": None},
    }
    assert (tmp_path / "motion.gif").is_file() and not (tmp_path / "frames").exists()
    assert model_3d.read_meta(tmp_path)["rig"] == report

    # 関節が足りなければ、Blender を呼ばずに止める
    monkeypatch.setattr(body_rig, "detect", lambda _image: (None, {"nose": np.array([1.0, 1.0])}))
    with pytest.raises(model_3d.Model3DRigError, match="left_shoulder"):
        model_3d.rig("blender", tmp_path)
    with pytest.raises(model_3d.Model3DRigError, match="動き"):
        model_3d.rig("blender", tmp_path, motion="dance")


def _hand_points(scale: float = 1.0, bent: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """手の21点(手首が下、指が上へまっすぐ伸びた手。中指の長さは手の長さの 0.43)。"""
    points = np.zeros((21, 2), np.float32)
    points[0] = (0, 0)
    bases = {"thumb": -45, "index": -30, "middle": -10, "ring": 10, "little": 30}
    lengths = {"thumb": 30, "index": 39.5, "middle": 43, "ring": 41, "little": 32.5}
    for name, chain in hand_shape.FINGERS.items():
        x = bases[name]
        y0 = -40 if name == "thumb" else -57
        for k, i in enumerate(chain):
            points[i] = (x, y0 - lengths[name] * k / 3)
        if name == bent:
            points[chain[-1]] = points[chain[1]]  # 先が付け根の近くへ曲がった指
    points *= scale
    return points, np.ones(21, np.float32)


def test_hand_ratios_from_points() -> None:
    ratios = hand_shape._ratios(*_hand_points())
    assert ratios["middle"] == pytest.approx(0.43, abs=0.01)
    assert ratios["index"] == pytest.approx(39.5 / 43, abs=0.01)
    # 付け根の間 60 → 手のひらの幅 75 → 手の長さ 100 に対して 0.75 は範囲の外なので上限
    assert ratios["width"] == hand_shape.RANGES["width"][1]
    # 曲がった指は標準の値。大きさが違っても割合は同じ
    bent = hand_shape._ratios(*_hand_points(scale=3.0, bent="little"))
    assert bent["little"] == hand_shape.DEFAULTS["little"] and bent["index"] == ratios["index"]
    # 中指が取れなければ、全部標準
    points, scores = _hand_points()
    scores[12] = 0.1
    assert hand_shape._ratios(points, scores) == hand_shape.DEFAULTS


def test_hand_ratios_fall_back_to_the_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert model_3d.hand_ratios(tmp_path, 3) == {"ratios": hand_shape.DEFAULTS, "source": None, "score": None}
    _draw(tmp_path, 3, ("hand_back",))
    measured = {"ratios": {**hand_shape.DEFAULTS, "width": 0.5}, "score": 0.8, "source": "3_hand_back.png"}
    monkeypatch.setattr(hand_shape, "measure", lambda path: measured if path.name == "3_hand_back.png" else None)
    assert model_3d.hand_ratios(tmp_path, 3) == measured


def test_poses_use_known_bones_and_shapes() -> None:
    bones = {"torso", "head"} | {
        f"{part}.{side}" for part in ("upper_arm", "forearm", "hand", "thigh", "shin", "foot") for side in ("L", "R")
    }
    for pose in body_poses.POSES.values():
        assert set(pose.bones) <= bones, pose.label
        assert set(pose.hands) == {"L", "R"} and set(pose.hands.values()) <= set(body_poses.HAND_SHAPES)
    sent = body_poses.render_json("sit_chair", ["front", "low"])
    assert sent["prop"] == "chair" and sent["cameras"] == {"front": [0, 0.55, 1.0], "low": [20, 0.02, 0.92]}


def test_render_pose_runs_blender_and_names_the_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "model.blend").write_bytes(b"blend")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kw) -> subprocess.CompletedProcess:  # noqa: ANN003
        commands.append(command)
        sent = json.loads(Path(command[-3]).read_text(encoding="utf-8"))
        renders = Path(command[-2])
        renders.mkdir()
        for camera in sent["cameras"]:
            Image.new("RGB", (8, 8)).save(renders / f"{camera}.png")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(model_3d.subprocess, "run", fake_run)
    paths = model_3d.render_pose("blender", tmp_path, "sit_chair", ["front", "low"], "line")
    assert [p.name for p in paths] == ["sit_chair_front_line.png", "sit_chair_low_line.png"]
    assert commands[0][2] == str(tmp_path / "model.blend") and commands[0][-1] == "line"
    assert {i["name"] for i in model_3d.pose_images(tmp_path)} == {"poses/sit_chair_front_line.png", "poses/sit_chair_low_line.png"}
    # 名前の分からない絵は一覧に出さない
    Image.new("RGB", (8, 8)).save(tmp_path / "poses" / "x_y_z.png")
    assert len(model_3d.pose_images(tmp_path)) == 2

    with pytest.raises(model_3d.Model3DPoseError, match="ポーズ"):
        model_3d.render_pose("blender", tmp_path, "dance", ["front"])
    with pytest.raises(model_3d.Model3DPoseError, match="カメラ"):
        model_3d.render_pose("blender", tmp_path, "stand", ["top"])
    monkeypatch.setattr(model_3d.subprocess, "run", lambda command, **_kw: subprocess.CompletedProcess(command, 1, "", "boom"))
    with pytest.raises(model_3d.Model3DPoseError, match="boom"):
        model_3d.render_pose("blender", tmp_path, "stand", ["front"])


def test_height_follows_the_base_sex_and_height() -> None:
    assert model_3d.height_of({"sex": "female", "height": ""}) == 1.60
    assert model_3d.height_of({"sex": "male", "height": "tall"}) == 1.83
    assert model_3d.height_of({"sex": None, "height": "unknown"}) == 1.60


def test_clean_runs_blender_and_makes_the_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "model.glb").write_bytes(b"glTF")
    (tmp_path / "meta.json").write_text(json.dumps({"seed": 1}), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kw) -> subprocess.CompletedProcess:  # noqa: ANN003
        commands.append(command)
        (tmp_path / "model_clean.glb").write_bytes(b"glTF")
        (tmp_path / "turntable").mkdir()
        for i in range(3):
            Image.new("RGBA", (20, 30), (200, 200, 200, 255)).save(tmp_path / "turntable" / f"{i:03d}.png")
        (tmp_path / "clean.json").write_text(json.dumps({"faces_in": 9, "faces": 5, "fragments": 1, "height": 1.6}))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(model_3d.subprocess, "run", fake_run)
    report = model_3d.clean("blender", tmp_path, 1.6)
    assert report["fragments"] == 1
    assert commands[0][-3:] == [str(tmp_path), "1.6", str(model_3d.MAX_FACES)]
    # 1周のコマは GIF にまとめて消す。結果は meta.json に足す
    assert (tmp_path / "preview.gif").is_file() and not (tmp_path / "turntable").exists()
    assert not (tmp_path / "clean.json").exists()
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"seed": 1, "clean": report}

    monkeypatch.setattr(model_3d.subprocess, "run", lambda command, **_kw: subprocess.CompletedProcess(command, 1, "", "boom"))
    with pytest.raises(model_3d.Model3DCleanError, match="boom"):
        model_3d.clean("blender", tmp_path / "missing", 1.6)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(lora_routes, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(model3d_remote, "configured", lambda: True)
    calls: list[tuple[Path, int, Path, int]] = []
    rig_shapes: list[tuple[dict, dict]] = []

    def fake_generate(folder: Path, base_id: int, out: Path, *, seed: int, on_status=None) -> dict:  # noqa: ANN001
        calls.append((folder, base_id, out, seed))
        on_status("running", 1)
        out.mkdir(parents=True, exist_ok=True)
        (out / "model.glb").write_bytes(b"glTF")
        (out / "meta.json").write_text(json.dumps({"seed": seed}), encoding="utf-8")
        return {"seed": seed}

    def fake_clean(blender: str, out: Path, height: float) -> dict:
        calls.append(("clean", blender, out, height))
        (out / "model_clean.glb").write_bytes(b"clean")
        return {"height": height}

    def fake_rig(blender: str, out: Path, *, motion: str, hand: dict, shapes: dict, shape_weights: dict) -> dict:
        calls.append(("rig", out, motion))
        assert set(hand["ratios"]) == set(hand_shape.DEFAULTS)
        rig_shapes.append((shapes, shape_weights))
        (out / "model_rigged.glb").write_bytes(b"rigged")
        return {"motion": motion}

    monkeypatch.setattr(model_3d, "generate", fake_generate)
    monkeypatch.setattr(model_3d, "clean", fake_clean)
    monkeypatch.setattr(model_3d, "rig", fake_rig)
    # 影絵の比べ(anime_seg)は呼ばず、いつも合格にする
    monkeypatch.setattr(model_3d, "check", lambda *_a, **_k: {"views": {}, "score": 1.0, "passed": True, "reasons": []})
    monkeypatch.setattr(body_rig, "find_blender", lambda _configured: "blender")
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(lora_routes.router)
    app.dependency_overrides[get_client] = lambda: object()
    with TestClient(app) as test_client:
        test_client.calls = calls  # type: ignore[attr-defined]
        test_client.rig_shapes = rig_shapes  # type: ignore[attr-defined]
        yield test_client


def _character(client: TestClient, name: str, **sheet) -> dict:
    character = client.post(
        "/api/story/characters/new", json={"name": name, "appearance_tags": "1girl, brown hair", "trigger_word": name}
    ).json()
    if sheet:
        conn = db.get_connection()
        try:
            db.update_character_sheet(conn, character["id"], sheet)
        finally:
            conn.close()
    return character


def _base(client: TestClient, name: str, **fields) -> dict:
    res = client.post("/api/lora-dataset/body-bases", json={"name": name, **fields})
    assert res.status_code == 200, res.text
    return res.json()


def _wait(client: TestClient, character_id: int, base_id: int) -> dict:
    for _ in range(200):
        info = client.get(f"/api/lora-dataset/character/{character_id}/model-3d").json()
        entry = next(e for e in info["bases"] if e["base_id"] == base_id)
        if entry["job"] and entry["job"]["status"] != "running":
            return entry
        time.sleep(0.01)
    raise AssertionError(f"仕事が終わらない: {entry}")


def test_model_3d_job_for_the_assigned_suit_base(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    character = _character(client, "eiko")
    base = _base(client, "水着")
    client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
    url = f"/api/lora-dataset/character/{character['id']}/model-3d"
    # 正面の絵が無ければ作らない
    assert client.post(url, json={}).status_code == 422
    folder = tmp_path / "outputs/training_data/eiko/body_base"
    _draw(folder, base["id"], ("front", "side"))

    info = client.get(url).json()
    assert info["configured"] and info["bases"][0]["views"] == {"front": True, "left": True, "back": False}
    assert client.post(url, json={"seed": 9}).status_code == 200
    entry = _wait(client, character["id"], base["id"])
    assert entry["job"]["status"] == "done" and entry["job"]["remote"] == "rigging"
    assert set(entry["files"]) == {"model.glb", "model_clean.glb", "model_rigged.glb", "meta.json"}
    assert entry["meta"]["seed"] == 9 and entry["meta"]["check"]["attempts"] == 1
    out = tmp_path / f"outputs/training_data/eiko/model_3d/{base['id']}"
    # 作ったあと、素体の身長(女性・ふつう)にそろえて整える。作業用のフォルダは残さない
    work = out / "_tries" / "0"
    # 整えたあと、残した形に骨を入れる
    assert client.calls == [  # type: ignore[attr-defined]
        (folder, base["id"], work, 9), ("clean", "blender", work, 1.60), ("rig", out, "wave"),
    ]
    assert not (out / "_tries").exists()

    # 整え直すだけ(GPU は使わない)
    assert client.post(f"{url}/clean", json={}).status_code == 200
    assert _wait(client, character["id"], base["id"])["job"]["status"] == "done"
    assert client.calls[-2:] == [("clean", "blender", out, 1.60), ("rig", out, "wave")]  # type: ignore[attr-defined]

    # 骨だけ入れ直す(確かめの動きを変える)。知らない動きは断る
    assert client.post(f"{url}/rig", json={"motion": "walk"}).status_code == 200
    assert _wait(client, character["id"], base["id"])["job"]["status"] == "done"
    assert client.calls[-1] == ("rig", out, "walk")  # type: ignore[attr-defined]
    assert client.post(f"{url}/rig", json={"motion": "dance"}).status_code == 422
    assert client.post(url, json={"motion": "dance"}).status_code == 422
    assert "wave" in client.get(url).json()["motions"]

    # ポーズの絵: 骨を入れた形(model.blend)が要る
    assert client.post(f"{url}/pose", json={"pose": "stand"}).status_code == 422
    (out / "model.blend").write_bytes(b"blend")

    def fake_render(blender: str, folder_: Path, pose: str, cameras: list[str], style: str, shape_weights=None) -> list[Path]:  # noqa: ANN001
        client.calls.append(("pose", pose, cameras, style))  # type: ignore[attr-defined]
        (folder_ / "poses").mkdir(exist_ok=True)
        for camera in cameras:
            (folder_ / "poses" / f"{pose}_{camera}_{style}.png").write_bytes(b"png")
        return []

    monkeypatch.setattr(model_3d, "render_pose", fake_render)
    res = client.post(f"{url}/pose", json={"pose": "sit_chair", "cameras": ["low", "low", "front"], "style": "line"})
    assert res.status_code == 200
    entry = _wait(client, character["id"], base["id"])
    assert client.calls[-1] == ("pose", "sit_chair", ["low", "front"], "line")  # type: ignore[attr-defined]
    assert {i["name"] for i in entry["poses"]} == {"poses/sit_chair_low_line.png", "poses/sit_chair_front_line.png"}
    image = client.get(f"{url}/file", params={"base_id": base["id"], "name": "poses/sit_chair_low_line.png"})
    assert image.status_code == 200 and image.content == b"png"
    assert client.get(f"{url}/file", params={"base_id": base["id"], "name": "poses/../meta.json"}).status_code == 404
    assert client.post(f"{url}/pose", json={"pose": "dance"}).status_code == 422
    assert client.post(f"{url}/pose", json={"pose": "stand", "cameras": ["top"]}).status_code == 422
    assert "sit_chair" in client.get(url).json()["pose_options"]["poses"]

    res = client.get(f"{url}/file", params={"base_id": base["id"], "name": "model.glb"})
    assert res.status_code == 200 and res.content == b"glTF"
    assert client.get(f"{url}/file", params={"base_id": base["id"], "name": "../app.db"}).status_code == 404


def test_model_3d_nude_base_only_for_adults(client: TestClient, tmp_path: Path) -> None:
    adult = _character(client, "rumi", is_adult=1, age=28)
    minor = _character(client, "yura", is_minor=1, age=17)
    nude = _base(client, "裸", kind="nude")
    for name in ("rumi", "yura"):
        _draw(tmp_path / f"outputs/training_data/{name}/body_base_r18", nude["id"], ("front",))

    # 未成年のキャラには、裸の素体を候補に出さず、作らない
    minor_url = f"/api/lora-dataset/character/{minor['id']}/model-3d"
    assert client.get(minor_url).json()["bases"] == []
    res = client.post(minor_url, json={"base_id": nude["id"]})
    assert res.status_code == 403
    assert client.get(f"{minor_url}/file", params={"base_id": nude["id"], "name": "model.glb"}).status_code == 404
    assert client.calls == []  # type: ignore[attr-defined]

    adult_url = f"/api/lora-dataset/character/{adult['id']}/model-3d"
    assert client.post(adult_url, json={"base_id": nude["id"]}).status_code == 200
    assert _wait(client, adult["id"], nude["id"])["job"]["status"] == "done"
    assert (tmp_path / f"outputs/training_data/rumi/model_3d_r18/{nude['id']}/model.glb").is_file()


def test_model_3d_needs_a_base_and_the_server(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    character = _character(client, "test")
    url = f"/api/lora-dataset/character/{character['id']}/model-3d"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"base_id": 999}).status_code == 404
    base = _base(client, "スーツ")
    monkeypatch.setattr(model3d_remote, "configured", lambda: False)
    res = client.post(url, json={"base_id": base["id"]})
    assert res.status_code == 422 and "MODEL3D_BASE_URL" in res.text
    # 整え直すのは、形を作ったあとだけ
    assert client.post(f"{url}/clean", json={"base_id": base["id"]}).status_code == 404  # 正面の絵が無い素体は候補に出ない


def test_model_3d_needs_blender(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    character = _character(client, "test")
    base = _base(client, "スーツ")
    _draw(tmp_path / "outputs/training_data/test/body_base", base["id"], ("front",))
    url = f"/api/lora-dataset/character/{character['id']}/model-3d"
    assert client.post(f"{url}/clean", json={"base_id": base["id"]}).status_code == 422
    monkeypatch.setattr(body_rig, "find_blender", lambda _configured: None)
    res = client.post(url, json={"base_id": base["id"]})
    assert res.status_code == 422 and "Blender" in res.text
    assert client.calls == []  # type: ignore[attr-defined]


def test_shape_presets_follow_tags_and_age_rules() -> None:
    from python import body_shapes

    chosen = body_shapes.choose("1girl, 1.2::large nipples::, Plump_Areola, huge breasts", nude=True, modest=False)
    assert chosen == {
        "repair": "repair", "nipple": "nipple_large", "areola": "areola_plump", "breast": "breast_larger",
        "navel": "navel_normal", "belly": "belly_flat",
    }
    # 乳首・乳輪は裸の素体のモデルだけ。胸の大きさは、控えめに描くキャラには当てない
    suit = body_shapes.choose("large nipples, huge breasts", nude=False, modest=False)
    assert "nipple" not in suit and "areola" not in suit and suit["breast"] == "breast_larger"
    modest = body_shapes.choose("huge breasts", nude=False, modest=True)
    assert set(modest) == {"repair", "navel", "belly"}
    assert set(body_shapes.to_json(nude=False, modest=True)["parts"]) == {"repair", "navel", "belly"}
    # 乳首・乳輪は着脱する部品。陥没は体をへこませる量(socket)も持つ
    presets = body_shapes.to_json(nude=True, modest=False)["presets"]
    assert {p["op"] for k, p in presets.items() if p["part"] in ("nipple", "areola")} == {"part"}
    assert presets["nipple_inverted"]["socket_depth"] > 0 and presets["nipple_inverted"]["socket_aspect"] > 1
    # 陥没乳首には、乳輪のタグが無ければ隆起した乳輪を合わせる(タグがあればそちら)
    assert body_shapes.choose("inverted nipples", nude=True, modest=False)["areola"] == "areola_raised"
    assert body_shapes.choose("inverted nipples, plump areola", nude=True, modest=False)["areola"] == "areola_plump"
    assert "nipple" in body_shapes.labels(nude=True, modest=False)


def test_rig_sends_shapes_and_keeps_the_made_weights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import body_shapes

    (tmp_path / "model_clean.glb").write_bytes(b"glTF")
    Image.new("RGBA", (360, 600)).save(tmp_path / "front.png")
    model_3d.write_meta(tmp_path, {"clean": {"height": 1.6}})
    points = {n: np.array([180.0, 300.0]) for n in model_3d._RIG_POINTS}
    monkeypatch.setattr(body_rig, "detect", lambda _image: (None, points))
    sent: dict = {}

    def fake_run(command: list[str], **_kw) -> subprocess.CompletedProcess:  # noqa: ANN003
        sent.update(json.loads(Path(command[-3]).read_text(encoding="utf-8")))
        (tmp_path / "model_rigged.glb").write_bytes(b"glTF")
        (tmp_path / "frames").mkdir(exist_ok=True)
        Image.new("RGBA", (20, 30)).save(tmp_path / "frames" / "001.png")
        report = {"shapes": {"keys": {"repair": {}, "navel_normal": {}}, "anchors": {}, "parts": {"part_nipple_large_L": 433}}}
        (tmp_path / "rig.json").write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(model_3d.subprocess, "run", fake_run)
    shapes = body_shapes.to_json(nude=False, modest=True)
    weights = {"repair": 1.0, "navel_normal": 1.0, "belly_flat": 1.0, "nipple_large": 1.0}
    model_3d.rig("blender", tmp_path, shapes=shapes, shape_weights=weights)
    assert sent["shapes"] == shapes and sent["shape_weights"]["belly_flat"] == 1.0
    # 作れなかった形(お腹の点が見つからなかったなど)の強さは残さない。部品(乳首)は、作った部品の形として残す
    assert model_3d.read_meta(tmp_path)["shapes"] == {"weights": {"repair": 1.0, "navel_normal": 1.0, "nipple_large": 1.0}}


def test_model_3d_shapes_are_saved_and_used_for_poses(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adult = _character(client, "rumi", is_adult=1, age=28, appearance_tags="1girl, puffy nipples")
    nude = _base(client, "裸", kind="nude")
    suit = _base(client, "スーツ")
    for base in (nude, suit):
        folder = "body_base_r18" if base["kind"] == "nude" else "body_base"
        _draw(tmp_path / f"outputs/training_data/rumi/{folder}", base["id"], ("front",))
    url = f"/api/lora-dataset/character/{adult['id']}/model-3d"
    assert client.post(url, json={"base_id": nude["id"]}).status_code == 200
    _wait(client, adult["id"], nude["id"])
    shapes, weights = client.rig_shapes[-1]  # type: ignore[attr-defined]
    assert "nipple_puffy" in shapes["presets"] and weights["nipple_puffy"] == 1.0

    out = tmp_path / f"outputs/training_data/rumi/model_3d_r18/{nude['id']}"
    (out / "model.blend").write_bytes(b"blend")
    res = client.put(f"{url}/shapes", json={"base_id": nude["id"], "weights": {"repair": 1, "nipple_large": 0.7}})
    entry = next(b for b in res.json()["bases"] if b["base_id"] == nude["id"])
    assert entry["shapes"]["weights"] == {"repair": 1.0, "nipple_large": 0.7}
    assert "nipple" in entry["shapes"]["options"]
    assert client.put(f"{url}/shapes", json={"base_id": nude["id"], "weights": {"repair": 3}}).status_code == 422

    # 全年齢の素体のモデルには、乳首の形は当てられない
    assert client.post(url, json={"base_id": suit["id"]}).status_code == 200
    _wait(client, adult["id"], suit["id"])
    suit_out = tmp_path / f"outputs/training_data/rumi/model_3d/{suit['id']}"
    (suit_out / "model.blend").write_bytes(b"blend")
    res = client.put(f"{url}/shapes", json={"base_id": suit["id"], "weights": {"nipple_large": 1}})
    assert res.status_code == 422 and "nipple_large" in res.text
    suit_entry = next(b for b in client.get(url).json()["bases"] if b["base_id"] == suit["id"])
    assert "nipple" not in suit_entry["shapes"]["options"]

    # ポーズの絵: 場面ごとの強さを渡す(省くと保存した強さ)
    rendered: list[dict | None] = []

    def fake_render(blender: str, folder_: Path, pose: str, cameras: list[str], style: str, shape_weights=None) -> list[Path]:  # noqa: ANN001
        rendered.append(shape_weights)
        return []

    monkeypatch.setattr(model_3d, "render_pose", fake_render)
    client.post(f"{url}/pose", json={"base_id": nude["id"], "pose": "stand", "shapes": {"nipple_inverted": 1}})
    _wait(client, adult["id"], nude["id"])
    client.post(f"{url}/pose", json={"base_id": nude["id"], "pose": "stand"})
    _wait(client, adult["id"], nude["id"])
    assert rendered == [{"nipple_inverted": 1.0}, None]
    assert client.post(f"{url}/pose", json={"base_id": suit["id"], "pose": "stand", "shapes": {"nipple_large": 1}}).status_code == 422


def test_custom_shape_presets_are_shared_checked_and_used(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "/api/lora-dataset/body-shape-presets"
    catalog = client.get(url).json()
    assert catalog["params"]["socket_lip_width"]["max"] == 1.0 and "part" in catalog["parts"]["areola"]["ops"]
    # 範囲の外・部位に合わない変形のしかたは断る
    assert client.post(url, json={"part": "areola", "label": "x", "params": {"op": "part", "radius": 9, "height": 0.1}}).status_code == 422
    assert client.post(url, json={"part": "areola", "label": "x", "params": {"op": "lift", "radius": 1, "height": 0.1}}).status_code == 422
    params = {"op": "part", "radius": 1.6, "height": 0.35, "edge": 0.1, "unused": 1}
    res = client.post(url, json={"part": "areola", "label": "大きな丘", "params": params, "tags": "huge areolae"})
    assert res.status_code == 200
    key = res.json()["key"]
    custom = next(p for p in res.json()["presets"] if p["key"] == key)
    assert custom["params"] == {"op": "part", "radius": 1.6, "height": 0.35, "edge": 0.1} and not custom["builtin"]
    # 直すときは丸ごと置き換える(タグも送る)
    edited = client.put(f"{url}/{custom['id']}", json={
        "part": "areola", "label": "大きな丘2", "params": {**params, "height": 0.4}, "tags": "huge areolae",
    })
    assert next(p for p in edited.json()["presets"] if p["key"] == key)["params"]["height"] == 0.4

    # タグに合う自分の形が、骨を入れるときの既定になる(裸の素体のモデル)。全年齢のモデルには当てない
    adult = _character(client, "rumi", is_adult=1, age=28, appearance_tags="1girl, huge areolae")
    nude = _base(client, "裸", kind="nude")
    suit = _base(client, "スーツ")
    for base in (nude, suit):
        folder = "body_base_r18" if base["kind"] == "nude" else "body_base"
        _draw(tmp_path / f"outputs/training_data/rumi/{folder}", base["id"], ("front",))
    model = f"/api/lora-dataset/character/{adult['id']}/model-3d"
    client.post(model, json={"base_id": nude["id"]})
    _wait(client, adult["id"], nude["id"])
    shapes, weights = client.rig_shapes[-1]  # type: ignore[attr-defined]
    assert shapes["presets"][key]["height"] == 0.4 and weights[key] == 1.0
    client.post(model, json={"base_id": suit["id"]})
    _wait(client, adult["id"], suit["id"])
    assert key not in client.rig_shapes[-1][0]["presets"]  # type: ignore[attr-defined]

    # 場面ごとの組み合わせ: いまの強さは変えずに名前を付けて残し、ポーズの絵で選ぶ
    out = tmp_path / f"outputs/training_data/rumi/model_3d_r18/{nude['id']}"
    (out / "model.blend").write_bytes(b"blend")
    shapes_url = f"{model}/shapes"
    client.put(shapes_url, json={"base_id": nude["id"], "weights": {"repair": 1, key: 1}})
    res = client.put(shapes_url, json={"base_id": nude["id"], "weights": {"repair": 1, "nipple_inverted": 1}, "set_name": "陥没"})
    entry = next(b for b in res.json()["bases"] if b["base_id"] == nude["id"])
    assert entry["shapes"]["weights"] == {"repair": 1.0, key: 1.0} and entry["shapes"]["sets"]["陥没"]["nipple_inverted"] == 1.0
    rendered: list[dict | None] = []

    def fake_render(blender: str, folder_: Path, pose: str, cameras: list[str], style: str, shape_weights=None) -> list[Path]:  # noqa: ANN001
        rendered.append(shape_weights)
        return []

    monkeypatch.setattr(model_3d, "render_pose", fake_render)
    client.post(f"{model}/pose", json={"base_id": nude["id"], "pose": "stand", "shape_set": "陥没"})
    _wait(client, adult["id"], nude["id"])
    assert rendered == [{"repair": 1.0, "nipple_inverted": 1.0}]
    assert client.post(f"{model}/pose", json={"base_id": nude["id"], "pose": "stand", "shape_set": "無い"}).status_code == 422
    res = client.delete(f"{shapes_url}/sets/陥没", params={"base_id": nude["id"]})
    assert next(b for b in res.json()["bases"] if b["base_id"] == nude["id"])["shapes"]["sets"] == {}

    # 消すと一覧から外れる
    assert client.delete(f"{url}/{custom['id']}").status_code == 200
    assert all(p["key"] != key for p in client.get(url).json()["presets"])
    assert client.delete(f"{url}/{custom['id']}").status_code == 404
