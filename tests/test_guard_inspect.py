"""
今あるデータの点検(guard_inspect.py と /api/content-guard/inspect)のテスト。DB は一時フォルダにする。

実行方法:
  uv run pytest tests/test_guard_inspect.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import content_guard, db  # noqa: E402
from python.routes import content_guard as guard_routes, library  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(library, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_OUTPUTS", tmp_path / "outputs")
    content_guard.reset_cache()
    app = FastAPI()
    app.include_router(guard_routes.router)
    yield TestClient(app)
    content_guard.reset_cache()


def _png(tmp_path: Path, rel: str) -> str:
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(tmp_path / rel)
    return rel


def test_inspect_lists_what_the_current_settings_would_stop(client: TestClient, tmp_path: Path) -> None:
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "x", 3, 4)
        db.set_story_title(conn, story["id"], "放課後")
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": "1", "draft_text": "ゆらは女子高生だ。", "draft_prompt_tags": "1girl, nude", "novelai_text": "ゆらは女子高生だ。"},
            {"draft_title": "2", "draft_text": "真理と翔", "draft_prompt_tags": "1girl, 1boy, sex", "novelai_text": "真理と翔"},
            {"draft_title": "3", "draft_text": "カフェ", "draft_prompt_tags": "1girl, cafe", "novelai_text": "カフェ"},
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        yura = db.save_character(conn, "ゆら", "1girl")
        db.update_character_sheet(conn, yura["id"], {"is_adult": 1})
        mari = db.save_character(conn, "真理", "1girl, mature female")
        db.update_character_sheet(conn, mari["id"], {"is_adult": 1})
        sho = db.save_character(conn, "翔", "1boy")
        db.set_scene_characters(conn, scenes[0]["id"], [yura["id"]])
        db.set_scene_characters(conn, scenes[1]["id"], [mari["id"], sho["id"]])
        db.set_scene_characters(conn, scenes[2]["id"], [yura["id"]])
        db.upsert_manga_panel(conn, story["id"], scenes[0]["id"], _png(tmp_path, "outputs/manga/panels/p0.png"), 1, 8, 8)
        db.record_generation(
            conn, prompt="1girl, nude, school uniform", negative_prompt=None, model="m", size="[8, 8]", steps=1, scale=1.0,
            seed=1, chunk_ids=[], image_paths=[_png(tmp_path, "outputs/history/h.png")],
        )
        db.record_generation(
            conn, prompt="1girl, cafe, school uniform", negative_prompt=None, model="m", size="[8, 8]", steps=1, scale=1.0,
            seed=1, chunk_ids=[], image_paths=[_png(tmp_path, "outputs/history/ok.png")],
        )
    finally:
        conn.close()
    report = client.get("/api/content-guard/inspect").json()
    # ゆら: 成人と定義しているが、本文で「女子高生」→ 未成年として扱われる
    yura_row = next(c for c in report["characters"] if c["name"] == "ゆら")
    assert yura_row["defined"] == "adult" and "成人と定義しているが" in yura_row["problem"] and "女子高生" in yura_row["cues"][0]
    scenes = {s["scene_index"]: s for s in report["stories"][0]["scenes"]}
    # シーン1: 未成年で止める(コマが残っている)。シーン2: 翔が未定義(今の扱いは 止める)。シーン3: 性的でないので出ない
    assert scenes[0]["kind"] == "minor" and scenes[0]["images"][0]["key"].startswith("panel:")
    assert scenes[1]["kind"] == "undefined" and 2 not in scenes
    assert report["stories"][0]["adult_tagging_with_minor_words"] is True
    # 生成画像: 性的で未成年を思わせるタグのものだけ
    assert [i["path"] for i in report["images"]] == ["outputs/history/h.png"]
    summary = report["summary"]
    assert summary["minor_scenes"] == 1 and summary["undefined_scenes"] == 1 and summary["minor_scene_images"] == 1
