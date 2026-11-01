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

from python import body_rig, db, model3d_remote, model_3d  # noqa: E402
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

    monkeypatch.setattr(model_3d, "generate", fake_generate)
    monkeypatch.setattr(model_3d, "clean", fake_clean)
    monkeypatch.setattr(body_rig, "find_blender", lambda _configured: "blender")
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(lora_routes.router)
    app.dependency_overrides[get_client] = lambda: object()
    with TestClient(app) as test_client:
        test_client.calls = calls  # type: ignore[attr-defined]
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
    raise AssertionError("仕事が終わらない")


def test_model_3d_job_for_the_assigned_suit_base(client: TestClient, tmp_path: Path) -> None:
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
    assert entry["job"]["status"] == "done" and entry["job"]["remote"] == "cleaning"
    assert set(entry["files"]) == {"model.glb", "model_clean.glb", "meta.json"} and entry["meta"] == {"seed": 9}
    out = tmp_path / f"outputs/training_data/eiko/model_3d/{base['id']}"
    # 作ったあと、素体の身長(女性・ふつう)にそろえて整える
    assert client.calls == [(folder, base["id"], out, 9), ("clean", "blender", out, 1.60)]  # type: ignore[attr-defined]

    # 整え直すだけ(GPU は使わない)
    assert client.post(f"{url}/clean", json={}).status_code == 200
    assert _wait(client, character["id"], base["id"])["job"]["status"] == "done"
    assert client.calls[-1] == ("clean", "blender", out, 1.60)  # type: ignore[attr-defined]

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
