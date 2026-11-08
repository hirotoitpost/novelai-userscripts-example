"""
シーンごとの容姿(scene_characters.appearance_tags)と、後ろ姿のコマで顔だけのタグを外す仕組みのテスト。
NovelAI には接続しない。DB は一時フォルダにする。

実行方法:
  uv run pytest tests/test_scene_appearance.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.character_sheet import character_minor_tags, drop_face_tags  # noqa: E402
from python.routes import manga_v2  # noqa: E402
from python.routes import story as story_routes  # noqa: E402
from python.routes import story_edit  # noqa: E402

SHEET = "1girl, silver hair, high ponytail, hair between eyes, yellow eyes, sharp eyes, beautiful face, face visible, blush"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(story_edit.router)
    with TestClient(app) as test_client:
        yield test_client


def _scene() -> tuple[int, int, int]:
    conn = db.get_connection()
    try:
        akane = db.save_character(conn, "あかね", SHEET, None)
        story = db.create_story(conn, "からかい", 1, panels_per_page=4)
        db.add_story_scenes(conn, story["id"], [{"draft_title": "", "draft_text": "x", "draft_prompt_tags": "bedroom"}])
        scene = db.list_story_scenes(conn, story["id"])[0]
        return story["id"], scene["id"], akane["id"]
    finally:
        conn.close()


def test_drop_face_tags_keeps_hair_and_body() -> None:
    assert drop_face_tags(SHEET) == "1girl, silver hair, high ponytail, hair between eyes"
    assert drop_face_tags("1girl, 1.2::light purple eyes::, jitome, long eyelashes, large breasts") == "1girl, large breasts"
    assert drop_face_tags("") == ""
    # 前髪は後ろからは見えないので外す(hair を含む髪のタグは残す)
    assert drop_face_tags("white hair, bob cut, blunt bangs, swept bangs, hair between eyes") == "white hair, bob cut, hair between eyes"


def test_back_view_cast_drops_face_tags_only_when_facing_away() -> None:
    member = {"id": 1, "appearance_tags": SHEET, "action_tags": "all fours, facing away"}
    away = manga_v2.back_view_cast("bedroom", [member])[0]
    assert "face visible" not in away["appearance_tags"] and "yellow eyes" not in away["appearance_tags"]
    assert "silver hair" in away["appearance_tags"]
    # 振り向いて顔を見せるコマ・正面のコマは、容姿をそのまま使う
    back = {**member, "action_tags": "from behind, looking back over shoulder"}
    assert manga_v2.back_view_cast("bedroom", [back])[0]["appearance_tags"] == SHEET
    assert manga_v2.back_view_cast("bedroom, from behind", [{**member, "action_tags": "smile"}])[0]["appearance_tags"] != SHEET
    assert manga_v2.back_view_cast("bedroom", [{**member, "action_tags": "smile"}])[0]["appearance_tags"] == SHEET
    # 元の辞書は書き換えない
    assert member["appearance_tags"] == SHEET


def test_scene_appearance_replaces_the_sheet_and_is_kept_on_recast(client: TestClient) -> None:
    story_id, scene_id, akane = _scene()
    res = client.put(f"/api/story/scenes/{scene_id}/characters", json={
        "character_ids": [akane], "actions": {akane: "smile"}, "appearances": {akane: "1girl, silver hair, hair down"},
    })
    assert res.status_code == 204, res.text
    conn = db.get_connection()
    try:
        member = db.characters_by_scene(conn, story_id)[scene_id][0]
        assert member["appearance_tags"] == "1girl, silver hair, hair down"
        assert member["scene_appearance_tags"] == "1girl, silver hair, hair down"
        assert member["sheet_appearance_tags"] == SHEET
        # 容姿を渡さずに登場人物を付け替えても、書いた容姿は残る
        db.set_scene_characters(conn, scene_id, [akane], actions={akane: "grin"})
        assert db.characters_by_scene(conn, story_id)[scene_id][0]["scene_appearance_tags"] == "1girl, silver hair, hair down"
        # 空にするとキャラシートの容姿に戻る
        db.set_scene_characters(conn, scene_id, [akane], appearances={akane: "  "})
        member = db.characters_by_scene(conn, story_id)[scene_id][0]
        assert member["appearance_tags"] == SHEET and member["scene_appearance_tags"] is None
    finally:
        conn.close()


def test_edit_screen_round_trips_scene_appearance(client: TestClient) -> None:
    story_id, scene_id, akane = _scene()
    conn = db.get_connection()
    try:
        db.set_scene_characters(conn, scene_id, [akane])
    finally:
        conn.close()
    data = client.get(f"/api/story/{story_id}/edit").json()
    assert data["scenes"][0]["characters"][0]["appearance_tags"] == ""
    body = {
        "title": data["title"], "panels_per_page": data["panels_per_page"], "custom_pages": data["custom_pages"],
        "scenes": [{
            **{k: data["scenes"][0][k] for k in ("id", "title", "text", "prompt_tags", "narration", "sfx", "page_index")},
            "characters": [{"id": akane, "action_tags": "", "outfit_tags": "", "appearance_tags": "1girl, silver hair"}],
        }],
    }
    res = client.put(f"/api/story/{story_id}/edit", json=body)
    assert res.status_code == 200, res.text
    assert client.get(f"/api/story/{story_id}/edit").json()["scenes"][0]["characters"][0]["appearance_tags"] == "1girl, silver hair"


def test_scene_appearance_cannot_hide_minor_cues_of_the_sheet() -> None:
    # 場面の容姿で子どもを示すタグを消しても、差し替える前のキャラシートの容姿で手がかりを見る
    member = {"name": "x", "appearance_tags": "1girl, silver hair", "sheet_appearance_tags": "1girl, child, silver hair"}
    assert character_minor_tags(member)
