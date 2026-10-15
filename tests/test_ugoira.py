"""
うごイラ(ugoira.py と /api/ugoira)のテスト。頭の検出・判定モデル・NovelAI は差し替え、DB とファイルは一時フォルダにする。

実行方法:
  uv run pytest tests/test_ugoira.py -v
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, trash, ugoira  # noqa: E402

HEAD = (100.0, 100.0, 300.0, 300.0)


def test_regions_prompts_and_negatives() -> None:
    assert ugoira.region_box(HEAD, "eyes", (400, 600)) == (130, 176, 270, 248)
    assert ugoira.region_box(HEAD, "hair", (400, 600))[0] == 20
    mask = ugoira.region_mask(HEAD, "hair", (400, 600))
    # 髪の範囲から顔は外す
    assert mask.getpixel((200, 240)) == 0 and mask.getpixel((40, 200)) == 255
    # 目の範囲では、目のタグを外す(色は残す)。コマのタグは強める
    prompt = ugoira.frame_prompt("1girl, yellow eyes, narrowed eyes, sanpaku, smile, brown hair", "eyes", "closed eyes")
    assert prompt == "1.4::closed eyes::, 1girl, yellow eyes, smile, brown hair"
    assert ugoira.frame_prompt("1girl, grin, open mouth, teeth", "mouth", "closed mouth") == "1.4::closed mouth::, 1girl"
    assert ugoira.frame_negative("closed eyes") == "open eyes, eyes visible, pupils, iris"
    assert ugoira.frame_negative("half-closed eyes") == "wide eyes, closed eyes"
    assert ugoira.fit_size((1600, 1600)) == (1024, 1024) and ugoira.fit_size((832, 1216)) == (832, 1216)
    with pytest.raises(ugoira.UgoiraError):
        ugoira.pick_head([])


def test_composite_keeps_outside_the_region() -> None:
    source = Image.new("RGB", (400, 600), "white")
    generated = Image.new("RGB", (400, 600), "black")
    mask = ugoira.region_mask(HEAD, "mouth", source.size)
    result = ugoira.composite(source, generated, mask)
    assert result.getpixel((10, 10)) == (255, 255, 255)
    assert result.getpixel((200, 270)) == (0, 0, 0)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from python.client import get_client
    from python.routes import image, library, trash as trash_routes, ugoira as ugoira_routes

    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    for module in (ugoira, trash, library):
        monkeypatch.setattr(module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(library, "_served_path", lambda path: tmp_path / path)
    monkeypatch.setattr("python.image_tagger.tag_image", lambda img: [])
    monkeypatch.setattr("python.manga_v2.detect.detect_heads_in_image", lambda img: [HEAD])
    prompts: list[tuple[str, str]] = []

    class FakeImage:
        async def generate(self, params):
            prompts.append((params.prompt, params.negative_prompt or ""))
            return [Image.new("RGB", params.size, "black")]

    class JobClient:
        image = FakeImage()

        async def close(self):
            pass

    monkeypatch.setattr(image, "_job_client", lambda api_key: JobClient())
    app = FastAPI()
    for router in (ugoira_routes.router, trash_routes.router):
        app.include_router(router)
    app.dependency_overrides[get_client] = lambda: JobClient()
    (tmp_path / "outputs" / "history").mkdir(parents=True)
    Image.new("RGB", (400, 600), "white").save(tmp_path / "outputs" / "history" / "src.png")
    conn = db.get_connection()
    try:
        row = db.record_generation(
            conn, prompt="1girl, yellow eyes, smile", negative_prompt=None, model="nai-diffusion-4-5-full", size="[400, 600]",
            steps=28, scale=5.0, seed=7, chunk_ids=[], image_paths=["outputs/history/src.png"],
        )
    finally:
        conn.close()
    with TestClient(app) as test_client:
        yield test_client, f"history:{row['id']}:src.png", prompts


def _wait(client, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/ugoira/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("終わりませんでした")


def test_partial_makes_frames_and_exports(client, tmp_path: Path) -> None:
    test_client, key, prompts = client
    motions = test_client.get("/api/ugoira/motions").json()["motions"]
    blink = next(m for m in motions if m["name"] == "まばたき")
    job = _wait(test_client, test_client.post("/api/ugoira/partial", json={"key": key, "motion_id": blink["id"]}).json()["id"])
    assert job["status"] == "done", job
    project = test_client.get(f"/api/ugoira/{job['project_id']}").json()
    assert [f["delay"] for f in project["frames"]] == blink["delays"] and len(project["frames"]) == 4
    # 1コマ目(タグ空)は元のまま。ほかは描き直す。閉じた目のコマは open eyes をネガティブに入れる
    assert len(prompts) == 3 and prompts[1][0].startswith("1.4::closed eyes::") and "open eyes" in prompts[1][1]
    first = Image.open(tmp_path / project["frames"][0]["path"])
    assert first.getpixel((200, 220)) == (255, 255, 255)
    # 並べ替え・表示時間
    frames = list(reversed(project["frames"]))
    frames[0]["delay"] = 500
    saved = test_client.put(f"/api/ugoira/{project['id']}/frames", json={"frames": frames}).json()
    assert saved["frames"][0]["delay"] == 500 and saved["frames"][0]["path"] == project["frames"][-1]["path"]
    assert test_client.put(f"/api/ugoira/{project['id']}/frames", json={"frames": [{"path": "x.png", "delay": 100}]}).status_code == 422
    # 書き出し
    archive = zipfile.ZipFile(io.BytesIO(test_client.get(f"/api/ugoira/{project['id']}/export?format=zip").content))
    assert archive.namelist()[:4] == ["000000.jpg", "000001.jpg", "000002.jpg", "000003.jpg"]
    assert json.loads(archive.read("animation.json"))["frames"][0]["delay"] == 500
    gif = Image.open(io.BytesIO(test_client.get(f"/api/ugoira/{project['id']}/export?format=gif&max_side=200").content))
    # 同じ絵が続くコマは、GIF では1枚にまとまり表示時間が足される(全体の長さは変わらない)
    total = 0
    for index in range(gif.n_frames):
        gif.seek(index)
        total += gif.info["duration"]
    assert total == sum(f["delay"] for f in saved["frames"]) and max(gif.size) == 200
    webp = Image.open(io.BytesIO(test_client.get(f"/api/ugoira/{project['id']}/export?format=webp").content))
    assert webp.n_frames >= 2 and webp.info.get("loop") == 0


def test_motions_and_trash(client, tmp_path: Path) -> None:
    test_client, key, _ = client
    made = test_client.post("/api/ugoira/motions", json={
        "name": "ウインク", "region": "eyes", "frames": ["", "one eye closed"], "delays": [800, 300],
    }).json()
    assert made["name"] == "ウインク"
    assert test_client.post("/api/ugoira/motions", json={"name": "x", "region": "eyes", "frames": ["a", "b"], "delays": [5, 100]}).status_code == 422
    job = _wait(test_client, test_client.post("/api/ugoira/partial", json={"key": key, "motion_id": made["id"]}).json()["id"])
    project_id = job["project_id"]
    files = [tmp_path / f["path"] for f in test_client.get(f"/api/ugoira/{project_id}").json()["frames"]]
    assert test_client.delete(f"/api/ugoira/{project_id}").status_code == 204
    assert test_client.get(f"/api/ugoira/{project_id}").status_code == 404 and not any(f.exists() for f in files)
    assert not files[0].parent.exists()
    entry = test_client.get("/api/trash").json()["entries"][0]
    assert entry["kind"] == "ugoira"
    assert test_client.post(f"/api/trash/{entry['id']}/restore").status_code == 200
    assert test_client.get(f"/api/ugoira/{project_id}").status_code == 200 and all(f.exists() for f in files)
