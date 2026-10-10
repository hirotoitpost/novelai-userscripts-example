"""
「漫画にする」のジョブ(/api/manga-v2/{id}/make: 足りないコマの生成 → ページの合成)のテスト。
NovelAI には接続せず、画像生成を差し替える。DB と画像の保存先は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_manga_make.py -v
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes import manga_v2  # noqa: E402
from python.routes.story import router as story_router  # noqa: E402

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manga_v2, "_PANEL_DIR", tmp_path / "outputs" / "manga" / "panels")
    monkeypatch.setattr(manga_v2, "_PAGE_DIR", tmp_path / "outputs" / "manga" / "v2")
    calls: list[int] = []

    async def fake_generate(api_key: str, prompt: str, negative: str, *, width: int, height: int, **_: object) -> bytes:
        calls.append(len(calls))
        buf = io.BytesIO()
        Image.new("RGB", (width, height), (200, 220, 240)).save(buf, format="PNG")
        return buf.getvalue()

    monkeypatch.setattr(manga_v2, "generate_image_v5", fake_generate)
    app = FastAPI()
    app.include_router(story_router)
    app.include_router(manga_v2.router)
    with TestClient(app) as test_client:
        test_client.calls = calls  # type: ignore[attr-defined]
        yield test_client


def _wait(client: TestClient, story_id: int) -> dict:
    for _ in range(200):
        job = client.get(f"/api/story/{story_id}/job").json()
        if job and job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("ジョブが終わりませんでした")


def test_make_generates_missing_panels_then_composes(client: TestClient) -> None:
    scenes = [{"text": f"「セリフ{i}」", "prompt_tags": "no humans, scenery"} for i in range(3)]
    story = client.post("/api/story/scripted", json={"title": "試し", "scenes": scenes}).json()
    body = {"panels": {"template": "vertical4"}, "compose": {"template": "vertical4"}}

    res = client.post(f"/api/manga-v2/{story['id']}/make", json=body, headers=AUTH)
    assert res.status_code == 200, res.text
    assert res.json()["kind"] == "manga"
    job = _wait(client, story["id"])
    assert job["status"] == "done", job
    assert job["message"] == "漫画ができました(1ページ)"
    assert len(client.calls) == 3  # type: ignore[attr-defined]

    final = client.get(f"/api/story/{story['id']}").json()["final_image_path"]
    assert final and final.startswith("outputs/manga/v2/")

    # 押し直すと、描き終わったコマは描き直さず合成だけ行う
    client.post(f"/api/manga-v2/{story['id']}/make", json=body, headers=AUTH)
    assert _wait(client, story["id"])["status"] == "done"
    assert len(client.calls) == 3  # type: ignore[attr-defined]


def test_make_rejects_unknown_template(client: TestClient) -> None:
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": [{"text": "「a」"}]}).json()
    res = client.post(f"/api/manga-v2/{story['id']}/make", json={"compose": {"template": "nope"}}, headers=AUTH)
    assert res.status_code == 400


def test_jobs_list_keeps_running_and_recent_jobs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from python.routes import story as story_routes

    # 画面を再読み込みしても処理状況を出し直せるよう、終わったジョブもしばらく一覧に残る
    monkeypatch.setattr(story_routes, "_jobs", {})
    scenes = [{"text": "「セリフ」", "prompt_tags": "no humans, scenery"}]
    story = client.post("/api/story/scripted", json={"title": "一覧", "scenes": scenes}).json()
    body = {"panels": {"template": "vertical4"}, "compose": {"template": "vertical4"}}
    client.post(f"/api/manga-v2/{story['id']}/make", json=body, headers=AUTH)
    assert _wait(client, story["id"])["status"] == "done"

    jobs = client.get("/api/story/jobs").json()
    assert [(j["story_id"], j["kind"], j["status"]) for j in jobs] == [(story["id"], "manga", "done")]
    assert jobs[0]["started_at"] <= jobs[0]["ended_at"]

    # 終わってから時間が経ったものは出さない
    monkeypatch.setattr(story_routes, "_RECENT_JOB_SECONDS", -1)
    assert client.get("/api/story/jobs").json() == []
