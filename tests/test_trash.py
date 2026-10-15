"""
ごみ箱(trash.py と /api/trash、ギャラリー・本棚・キャラの削除)のテスト。DB とファイルは一時フォルダにする。

実行方法:
  uv run pytest tests/test_trash.py -v
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, trash  # noqa: E402
from python.routes import library, story as story_routes, trash as trash_routes  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(trash, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_OUTPUTS", tmp_path / "outputs")
    db.get_connection().close()
    app = FastAPI()
    for router in (trash_routes.router, library.router, story_routes.router):
        app.include_router(router)
    yield TestClient(app)


def _png(tmp_path: Path, rel: str) -> str:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(path)
    return rel


def _history(tmp_path: Path, names: list[str]) -> int:
    paths = [_png(tmp_path, f"outputs/history/{n}") for n in names]
    conn = db.get_connection()
    try:
        return db.record_generation(
            conn, prompt="1girl, park", negative_prompt=None, model="m", size="[8, 8]", steps=1, scale=1.0, seed=1,
            chunk_ids=[], image_paths=paths,
        )["id"]
    finally:
        conn.close()


def _paths(entry_id: int) -> list[str] | None:
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT image_paths FROM generation_history WHERE id = ?", (entry_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row["image_paths"]) if row else None


def test_gallery_images_go_to_the_trash_and_come_back(client: TestClient, tmp_path: Path) -> None:
    entry = _history(tmp_path, ["a.png", "b.png"])
    keys = [f"history:{entry}:a.png", f"history:{entry}:b.png"]
    assert client.post("/api/library/images/delete", json={"keys": keys[:1]}).json() == {"deleted": 1}
    assert _paths(entry) == ["outputs/history/b.png"] and not (tmp_path / "outputs/history/a.png").exists()
    client.post("/api/library/images/delete", json={"keys": keys[1:]})
    # 最後の1枚で履歴の行ごと消える
    assert _paths(entry) is None
    listed = client.get("/api/trash").json()
    assert listed["retention_days"] == 30 and [e["kind"] for e in listed["entries"]] == ["history_image", "history_image"]
    assert client.get(f"/api/trash/{listed['entries'][0]['id']}/files/0").status_code == 200
    # 戻す順はどちらからでもよい(行が無ければ入れ直し、あれば画像を足す)
    for item in listed["entries"]:
        assert client.post(f"/api/trash/{item['id']}/restore").status_code == 200
    assert sorted(_paths(entry)) == ["outputs/history/a.png", "outputs/history/b.png"]
    assert (tmp_path / "outputs/history/a.png").is_file() and client.get("/api/trash").json()["entries"] == []


def _book(tmp_path: Path) -> tuple[int, int, int]:
    conn = db.get_connection()
    try:
        series = db.create_series(conn, "シリーズ")
        story = db.create_story(conn, "[漫画] 本", 1, 4)
        db.set_story_title(conn, story["id"], "本")
        db.set_story_series(conn, story["id"], series["id"], 1)
        db.add_story_scenes(conn, story["id"], [{"draft_title": "1", "draft_text": "a", "draft_prompt_tags": "", "novelai_text": "a"}])
        scene = db.list_story_scenes(conn, story["id"])[0]
        character = db.save_character(conn, "みお", "1girl")
        db.set_scene_characters(conn, scene["id"], [character["id"]], {character["id"]: "smile"})
        db.upsert_manga_panel(conn, story["id"], scene["id"], _png(tmp_path, f"outputs/manga/panels/story{story['id']}_p.png"), 1, 8, 8)
        db.set_bookmark(conn, "book", str(story["id"]), True)
        author = db.create_author(conn, "作者")
        db.add_work_author(conn, "series", series["id"], author["id"])
    finally:
        conn.close()
    _png(tmp_path, f"outputs/manga/v2/story{story['id']}_page0.png")
    return story["id"], series["id"], character["id"]


def test_book_goes_to_the_trash_with_everything(client: TestClient, tmp_path: Path) -> None:
    story_id, series_id, character_id = _book(tmp_path)
    assert client.delete(f"/api/library/books/{story_id}").status_code == 204
    conn = db.get_connection()
    try:
        assert db.get_story(conn, story_id) is None and db.get_series(conn, series_id) is None
    finally:
        conn.close()
    assert not (tmp_path / f"outputs/manga/v2/story{story_id}_page0.png").exists()
    entry = client.get("/api/trash").json()["entries"][0]
    assert entry["kind"] == "book" and entry["label"] == "本" and entry["files"] == 2
    assert client.post(f"/api/trash/{entry['id']}/restore").status_code == 200
    conn = db.get_connection()
    try:
        restored = db.get_story(conn, story_id)
        assert restored["series_id"] == series_id and db.get_series(conn, series_id)["title"] == "シリーズ"
        scene = restored["scenes"][0]
        assert [(c["id"], c["action_tags"]) for c in scene["characters"]] == [(character_id, "smile")]
        assert db.list_manga_panels(conn, story_id)[0]["image_path"].endswith("_p.png")
        assert str(story_id) in db.list_bookmarks(conn, "book")
        assert conn.execute("SELECT COUNT(*) FROM work_authors WHERE work_kind = 'series'").fetchone()[0] == 1
    finally:
        conn.close()
    assert (tmp_path / f"outputs/manga/v2/story{story_id}_page0.png").is_file()


def test_character_restore_conflict_and_purge(client: TestClient, tmp_path: Path) -> None:
    conn = db.get_connection()
    try:
        created = db.save_character(conn, "みお", "1girl")
    finally:
        conn.close()
    assert client.delete(f"/api/story/characters/{created['id']}").status_code == 204
    entry_id = client.get("/api/trash").json()["entries"][0]["id"]
    # 同じ名前のキャラを作り直したら戻せない(何も変えない)
    conn = db.get_connection()
    try:
        db.save_character(conn, "みお", "1boy")
    finally:
        conn.close()
    conflict = client.post(f"/api/trash/{entry_id}/restore")
    assert conflict.status_code == 409 and "同じ名前のキャラ" in conflict.json()["detail"]
    assert client.get("/api/trash").json()["entries"][0]["id"] == entry_id
    assert client.delete(f"/api/trash/{entry_id}").status_code == 204
    assert client.get("/api/trash").json()["entries"] == []


def test_expired_entries_are_purged(client: TestClient, tmp_path: Path) -> None:
    entry = _history(tmp_path, ["old.png"])
    client.post("/api/library/images/delete", json={"keys": [f"history:{entry}:old.png"]})
    trash_id = client.get("/api/trash").json()["entries"][0]["id"]
    conn = db.get_connection()
    try:
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        conn.execute("UPDATE trash_entries SET expires_at = ?", (past,))
        conn.commit()
    finally:
        conn.close()
    assert client.get("/api/trash").json()["entries"] == []
    assert not (tmp_path / "data" / "trash" / str(trash_id)).exists()
