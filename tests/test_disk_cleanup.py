"""
ディスクの整理(/api/admin/cleanup。片付けられるファイルの一覧と、ごみ箱への片付け)のテスト。

実行方法:
  uv run pytest tests/test_disk_cleanup.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, trash  # noqa: E402
from python.routes import admin, trash as trash_routes  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(admin, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(trash, "_PROJECT_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(admin.router)
    app.include_router(trash_routes.router)
    return TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))


def _file(tmp_path: Path, rel: str) -> None:
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_bytes(b"x" * 10)


def _setup(tmp_path: Path) -> int:
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "x", 1, 4)
        db.update_story_final_image(conn, story["id"], f"outputs/manga/v2/story{story['id']}_final_cccc1111.png")
        db.add_story_scenes(conn, story["id"], [{"draft_title": "1", "draft_text": "a", "draft_prompt_tags": "", "novelai_text": "a"}])
        scene = db.list_story_scenes(conn, story["id"])[0]
        db.upsert_manga_panel(conn, story["id"], scene["id"], "outputs/manga/panels/used.png", 1, 8, 8)
    finally:
        conn.close()
    sid = story["id"]
    for rel in (
        f"outputs/manga/v2/story{sid}_page0_cccc1111.png", f"outputs/manga/v2/story{sid}_final_cccc1111.png",  # いまの版
        f"outputs/manga/v2/story{sid}_page0_aaaa0000.png", f"outputs/manga/v2/story{sid}_final_aaaa0000.png",  # 前の版
        "outputs/manga/v2/story999_page0_bbbb0000.png",  # 無くなった物語
        "outputs/manga/panels/used.png", "outputs/manga/panels/old.png",
        "outputs/manga/story999_page0_dddd.png",  # v1 の使われていない挿絵
    ):
        _file(tmp_path, rel)
    return sid


def test_lists_only_unused_files(client: TestClient, tmp_path: Path) -> None:
    sid = _setup(tmp_path)
    composed = {f["name"] for f in client.get("/api/admin/cleanup/composed/files").json()["files"]}
    assert composed == {f"story{sid}_page0_aaaa0000.png", f"story{sid}_final_aaaa0000.png", "story999_page0_bbbb0000.png"}
    assert [f["name"] for f in client.get("/api/admin/cleanup/panels/files").json()["files"]] == ["old.png"]
    assert [f["name"] for f in client.get("/api/admin/cleanup/v1_pages/files").json()["files"]] == ["story999_page0_dddd.png"]
    storage = {c["category"]: c["files"] for c in client.get("/api/admin/storage").json()["cleanup"]}
    assert storage["composed"] == 3 and storage["v1_pages"] == 1


def test_cleanup_goes_to_the_trash_and_can_be_restored(client: TestClient, tmp_path: Path) -> None:
    sid = _setup(tmp_path)
    # 選んだものだけ(いま片付けてよくないもの=いまの版は、名前を渡しても片付けない)
    res = client.post("/api/admin/cleanup/composed", json={"names": [f"story{sid}_page0_aaaa0000.png", f"story{sid}_page0_cccc1111.png"]})
    assert res.json()["deleted"] == 1
    assert not (tmp_path / f"outputs/manga/v2/story{sid}_page0_aaaa0000.png").exists()
    assert (tmp_path / f"outputs/manga/v2/story{sid}_page0_cccc1111.png").exists()
    entry = client.get("/api/trash").json()["entries"][0]
    assert entry["kind"] == "cleanup" and entry["files"] == 1
    assert client.post(f"/api/trash/{entry['id']}/restore").status_code == 200
    assert (tmp_path / f"outputs/manga/v2/story{sid}_page0_aaaa0000.png").exists()
    # 名前を渡さなければ全部
    assert client.post("/api/admin/cleanup/panels").json()["deleted"] == 1
    assert (tmp_path / "outputs/manga/panels/used.png").exists()
