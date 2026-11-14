"""
作業アシスタントの「次の操作」(assistant.py と /api/assistant/guide)のテスト。DB は一時フォルダ。

実行方法:
  uv run pytest tests/test_assistant.py -v
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import assistant, backup, db  # noqa: E402
from python.routes import assistant as assistant_route  # noqa: E402


@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    app = tmp_path / "app"
    monkeypatch.setattr(db, "_DB_PATH", app / "data" / "app.db")
    monkeypatch.setattr(db, "_PROJECT_ROOT", app)
    monkeypatch.setattr(backup, "_PROJECT_ROOT", app)
    db.get_connection().close()
    return app


def _story(n: int, written: bool = True) -> tuple[int, list[int]]:
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "テストの物語", n)
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": f"場面{i}", "draft_text": "本文", "draft_prompt_tags": "", "novelai_text": "書いた本文" if written else None}
            for i in range(n)
        ])
        ids = [s["id"] for s in db.list_story_scenes(conn, story["id"])]
    finally:
        conn.close()
    return story["id"], ids


def _steps(guide: dict) -> dict[str, dict]:
    return {s["key"]: s for s in guide["steps"]}


def test_manga_guide_walks_through_the_flow(root: Path) -> None:
    story_id, scenes = _story(4)
    guide = assistant.guide("story", {"page": "story", "story_id": str(story_id)})
    steps = _steps(guide)
    assert steps["scenes"]["status"] == "done" and steps["text"]["status"] == "done"
    # 人物がいない → 次は割り当て
    assert guide["next"] == "cast" and steps["cast"]["action"] == f"/story?story={story_id}#scene-list"

    conn = db.get_connection()
    try:
        mio = db.save_character(conn, "みお", "1girl")
        for scene_id in scenes[:3]:
            db.set_scene_characters(conn, scene_id, [mio["id"]])
        db.upsert_manga_panel(conn, story_id, scenes[0], "outputs/manga/v2/p0.png", 1, 832, 1216)
    finally:
        conn.close()
    guide = assistant.guide("story", {"page": "story", "story_id": str(story_id)})
    steps = _steps(guide)
    assert steps["cast"]["status"] == "info" and "3/4" in steps["cast"]["detail"]
    assert steps["references"]["status"] == "warn" and "みお" in steps["references"]["detail"]
    assert guide["next"] == "panels" and "1/4" in steps["panels"]["detail"] and "シーン 2 から" in steps["panels"]["detail"]

    # 全部描いて合成 → 仕上がり
    conn = db.get_connection()
    try:
        for scene_id in scenes[1:]:
            db.upsert_manga_panel(conn, story_id, scene_id, "outputs/manga/v2/p.png", 1, 832, 1216)
        final = root / "outputs" / "manga" / "v2" / "final.png"
        final.parent.mkdir(parents=True)
        final.write_bytes(b"png")
        # Windows のファイルの時刻は少し粗いので、合成は描いたあと(数秒後)にしたことにする
        os.utime(final, (time.time() + 5, time.time() + 5))
        db.update_story_final_image(conn, story_id, "outputs/manga/v2/final.png")
        conn.execute("UPDATE characters SET reference_image_path = 'outputs/manga/refs/mio.png' WHERE id = ?", (mio["id"],))
        conn.commit()
    finally:
        conn.close()
    steps = _steps(assistant.guide("story", {"page": "story", "story_id": str(story_id)}))
    assert steps["panels"]["status"] == "done" and steps["compose"]["status"] == "done" and "read" in steps

    # 合成のあとに描き直した → 合成し直す
    time.sleep(0.05)
    os.utime(final, (time.time() - 60, time.time() - 60))
    conn = db.get_connection()
    try:
        db.upsert_manga_panel(conn, story_id, scenes[2], "outputs/manga/v2/p2b.png", 2, 832, 1216)
    finally:
        conn.close()
    guide = assistant.guide("story", {"page": "story", "story_id": str(story_id)})
    assert _steps(guide)["compose"]["status"] == "warn" and guide["next"] == "compose"


def test_unwritten_story_and_missing_story(root: Path) -> None:
    story_id, _ = _story(3, written=False)
    guide = assistant.guide("story", {"page": "story", "story_id": str(story_id)})
    assert guide["next"] == "text" and "3" in _steps(guide)["text"]["detail"]
    assert assistant.guide("story", {"page": "story", "story_id": "9999"})["title"] == "物語が見つかりません"
    assert assistant.guide("story", {"page": "story", "story_id": "0"})["next"] == "open"


def test_backup_guide(root: Path, tmp_path: Path) -> None:
    guide = assistant.guide("admin", {"page": "admin"})
    assert guide["next"] == "disks"
    backup.save_settings(backup.Settings(disks=[backup.register_disk(backup.Disk(str(tmp_path / "d1")))], copies=2))
    steps = _steps(assistant.guide("admin", {"page": "admin"}))
    # 1台で置く数が 2 → 足す。まだ取っていない → 取る
    assert steps["copies"]["status"] == "todo" and steps["last"]["status"] == "todo"


def test_other_pages_get_a_default_guide(root: Path) -> None:
    guide = assistant.guide("gallery", {"page": "gallery"})
    assert guide["next"] is None and {s["key"] for s in guide["steps"]} == {"manga", "backup"}


def test_api_hides_admin_guide_outside_lan(root: Path) -> None:
    app = FastAPI()
    app.include_router(assistant_route.router)
    inside = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    assert inside.get("/api/assistant/guide?page=admin").json()["next"] == "disks"
    outside = TestClient(app, base_url="http://example.com", client=("8.8.8.8", 50000))
    assert outside.get("/api/assistant/guide?page=admin").json()["title"] == "このページ"
