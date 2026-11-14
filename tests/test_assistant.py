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
    assert guide["next"] is None and {s["key"] for s in guide["steps"]} == {"home", "manga", "characters"}
    assert guide["help"] == "character-images"


def _character(name: str, reference: str | None = None) -> dict:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, name, "1girl")
        if reference:
            conn.execute("UPDATE characters SET reference_image_path = ? WHERE id = ?", (reference, character["id"]))
            conn.commit()
        return db.get_character(conn, character["id"])
    finally:
        conn.close()


def test_characters_guide(root: Path) -> None:
    assert assistant.guide("characters", {"page": "characters"})["next"] == "none"
    mio = _character("みお")
    _character("ゆら", "outputs/manga/refs/char2_aa.png")
    steps = _steps(assistant.guide("characters", {"page": "characters"}))
    assert steps["references"]["status"] == "info" and "みお" in steps["references"]["detail"]
    assert steps["references"]["action"] == f"/character-images?character={mio['id']}"
    assert "1 人" in steps["back"]["detail"]


def test_character_images_guide(root: Path) -> None:
    assert assistant.guide("character-images", {"page": "character-images"})["next"] == "choose"
    mio = _character("みお")
    guide = assistant.guide("character-images", {"page": "character-images", "character_id": str(mio["id"])})
    assert guide["title"] == "みお の画像" and guide["next"] == "reference"
    assert "まだ画像がありません" in _steps(guide)["reference"]["detail"]
    # 候補があり、参照画像も決まった → 候補の片付けを案内
    candidates = root / "outputs" / "manga" / "refs" / "candidates"
    candidates.mkdir(parents=True)
    for i in range(2):
        (candidates / f"char{mio['id']}_{i}_ab{i}.png").write_bytes(b"png")
    conn = db.get_connection()
    try:
        conn.execute("UPDATE characters SET reference_image_path = ? WHERE id = ?",
                     (f"outputs/manga/refs/candidates/char{mio['id']}_0_ab0.png", mio["id"]))
        conn.commit()
    finally:
        conn.close()
    steps = _steps(assistant.guide("character-images", {"page": "character-images", "character_id": str(mio["id"])}))
    assert steps["reference"]["status"] == "done"
    assert steps["cleanup"]["action"].endswith("#image-cleanup") and "2 枚" in steps["cleanup"]["detail"]


def test_home_guide(root: Path, tmp_path: Path) -> None:
    story_id, scenes = _story(3)
    conn = db.get_connection()
    try:
        db.upsert_manga_panel(conn, story_id, scenes[0], "outputs/manga/v2/p.png", 1, 832, 1216)
    finally:
        conn.close()
    _character("みお")
    guide = assistant.guide("home", {"page": "home", "is_admin": "0"})
    steps = _steps(guide)
    assert guide["next"] == f"story:{story_id}" and "未生成 2 コマ" in steps[f"story:{story_id}"]["detail"]
    assert "characters" in steps and "backup" not in steps  # 管理者でなければバックアップは出さない
    steps = _steps(assistant.guide("home", {"page": "home", "is_admin": "1"}))
    assert steps["backup"]["status"] == "warn"  # 写し先が未設定


def test_api_hides_admin_guide_outside_lan(root: Path) -> None:
    app = FastAPI()
    app.include_router(assistant_route.router)
    inside = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    assert inside.get("/api/assistant/guide?page=admin").json()["next"] == "disks"
    outside = TestClient(app, base_url="http://example.com", client=("8.8.8.8", 50000))
    assert outside.get("/api/assistant/guide?page=admin").json()["title"] == "このページ"


def test_chat_passes_the_page_state_and_never_claims_to_operate(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import app_settings
    from python.routes import help as help_route

    app_settings.reset_cache()
    story_id, _ = _story(2)
    calls: list[tuple[str, list[dict[str, str]]]] = []

    async def fake_answer(engine: str, messages: list[dict[str, str]], api_key: str | None) -> str:
        calls.append((engine, messages))
        return "<think>x</think>1. [シーン一覧へ](/story?story=1#scene-list) 移ります。"

    async def fake_key(authorization: str | None) -> str | None:
        return "token" if authorization else None

    monkeypatch.setattr(help_route, "ask_model", fake_answer)
    monkeypatch.setattr(help_route, "novelai_key", fake_key)
    app = FastAPI()
    app.include_router(assistant_route.router)
    client = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))

    res = client.post("/api/assistant/chat", json={"message": "次は何をすればいい?", "page": "story", "story_id": story_id},
                      headers={"Authorization": "Bearer x"}).json()
    assert res["engine"] == "novelai" and res["answer"].startswith("1. [シーン一覧へ]")
    system = calls[-1][1][0]["content"]
    assert "操作できません" in system and "# 今の画面の状況" in system
    assert "[これから] 登場人物を割り当てる" in system and "← 次はこれ" in system
    assert f"(/story?story={story_id}#scene-list)" in system
    # トークンが無ければ Ollama(自動)
    res = client.post("/api/assistant/chat", json={"message": "こんにちは"}).json()
    assert res["engine"] == "ollama"
    assert client.get("/api/assistant/chat/engines").json()["default"] == "auto"
    app_settings.reset_cache()
