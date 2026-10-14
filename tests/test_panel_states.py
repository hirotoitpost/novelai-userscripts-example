"""
描き直しで作る人物の状態(panel_states と、manga_v2 の状態の描き直し・状態の API)のテスト。頭の検出と画像生成は差し替える。

実行方法:
  uv run pytest tests/test_panel_states.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.panel_states import Target, find_targets, matched_state, redraw_scene_tags, target_mask  # noqa: E402

STATES = [
    {"id": 1, "name": "透明", "match_tags": "invisible, invisibility", "redraw_tags": "empty clothes, floating clothes", "look": "outfit"},
    {"id": 2, "name": "幽霊", "match_tags": "ghost", "redraw_tags": "ghost, translucent body", "look": "full"},
]


def _member(name: str, actions: str = "", appearance: str = "1boy", outfit: str = "") -> dict:
    return {"name": name, "action_tags": actions, "appearance_tags": appearance, "outfit_tags": outfit}


def test_matched_state_by_action_tags() -> None:
    assert matched_state("standing, Invisible", STATES)["name"] == "透明"
    assert matched_state("ghost", STATES)["name"] == "幽霊"
    assert matched_state("standing", STATES) is None


def test_find_targets_maps_people_left_to_right() -> None:
    # 名前順に左から並ぶ。2人目(剛)が透明。頭は右から順に渡しても、左から並べ直す
    members = [_member("剛", "invisible"), _member("美咲", "", "1girl"), _member("ポチ", "", "dog, animal")]
    heads = [(300.0, 100.0, 400.0, 200.0), (100.0, 100.0, 200.0, 200.0)]
    targets, reason = find_targets(members, heads, (800, 400), STATES)
    assert reason is None
    assert [t.character["name"] for t in targets] == ["剛"]
    x0, y0, x1, y1 = targets[0].box
    # 頭の幅 100 の 1.5 倍ずつ広げ(0〜300)、右はとなり(中心 350)との中間 250 で止める。上は頭の 0.1 倍上、下は画面の下まで
    assert (x0, y0, x1, y1) == (0, 90, 250, 400)


def test_find_targets_skips_when_heads_do_not_match() -> None:
    members = [_member("剛", "invisible"), _member("美咲", "", "1girl")]
    targets, reason = find_targets(members, [(100.0, 100.0, 200.0, 200.0)], (800, 400), STATES)
    assert targets == [] and "合わない" in (reason or "")
    # 状態の人がいなければ、頭の数は見ない
    assert find_targets([_member("美咲")], [], (800, 400), STATES) == ([], None)


def test_redraw_tags_replace_match_tags_and_add_the_look() -> None:
    target = Target(_member("剛", "invisible", outfit="blue track jacket"), STATES[0], (0, 0, 10, 10))
    assert redraw_scene_tags("club room, invisible, open door", target) == (
        "club room, open door, empty clothes, floating clothes, blue track jacket"
    )


def test_target_mask_covers_the_box() -> None:
    mask = target_mask((200, 100), (50, 20, 150, 100)).convert("L")
    assert mask.getpixel((100, 60)) == 255
    assert mask.getpixel((10, 60)) == 0


def test_default_states_are_seeded_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    conn = db.get_connection()
    try:
        names = [s["name"] for s in db.list_redraw_states(conn)]
        assert names == ["透明", "幽霊", "石化"]
        db.delete_redraw_state(conn, db.list_redraw_states(conn)[1]["id"])
    finally:
        conn.close()
    # 消した例は、もう一度開いても戻らない
    conn = db.get_connection()
    try:
        assert [s["name"] for s in db.list_redraw_states(conn)] == ["透明", "石化"]
    finally:
        conn.close()


def test_redraw_states_inpaints_the_person(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    from python.models import MangaV2PanelsRequest
    from python.routes import manga_v2

    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    panel_dir = tmp_path / "outputs" / "manga" / "panels"
    panel_dir.mkdir(parents=True)
    monkeypatch.setattr(manga_v2, "_PANEL_DIR", panel_dir)
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "x", 3, 4)
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": str(i), "draft_text": "x", "draft_prompt_tags": "club room, invisible", "novelai_text": "x"}
            for i in range(3)
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        boy = db.save_character(conn, "剛", "1boy", None)
        girl = db.save_character(conn, "美咲", "1girl", None)
        for scene in scenes:
            # 1コマ目: 剛が透明 / 2コマ目: だれも状態なし / 3コマ目: 剛が透明だが、頭が1つしか見つからない
            actions = None if scene["scene_index"] == 1 else {boy["id"]: "invisible"}
            db.set_scene_characters(conn, scene["id"], [boy["id"], girl["id"]], actions)
            name = f"p{scene['scene_index']}.png"
            Image.new("RGB", (384, 192), "white").save(tmp_path / name)
            db.upsert_manga_panel(conn, story["id"], scene["id"], name, 500, 384, 192)
    finally:
        conn.close()

    heads = {
        "p0.png": [(50.0, 20.0, 100.0, 70.0), (250.0, 20.0, 300.0, 70.0)],
        "p1.png": [(50.0, 20.0, 100.0, 70.0), (250.0, 20.0, 300.0, 70.0)],
        "p2.png": [(50.0, 20.0, 100.0, 70.0)],
    }
    monkeypatch.setattr("python.manga_v2.detect.detect_heads", lambda path: heads[Path(path).name])
    calls: list = []

    class FakeImage:
        async def generate(self, params):
            calls.append(params)
            return [Image.new("RGB", params.size, "blue")]

    class FakeClient:
        def __init__(self, api_key):
            self.image = FakeImage()

        async def close(self):
            pass

    monkeypatch.setattr("novelai.AsyncNovelAI", FakeClient)

    class Job:
        message = ""

    summary = asyncio.run(manga_v2._redraw_states(Job(), story["id"], MangaV2PanelsRequest(color=True), "t"))
    assert summary["redrawn"] == [{"scene": 1, "who": ["剛(透明)"]}]
    assert [s["scene"] for s in summary["skipped"]] == [3]
    # 剛(左)の範囲だけを描き直す。シードはコマのシード + 101、「invisible」は描き直しのタグに置き換える
    assert len(calls) == 1
    params = calls[0]
    assert params.seed == 601 and params.inpaint is not None and params.inpaint.strength == 1.0
    assert "empty clothes" in params.prompt and "invisible, invisible" not in params.prompt
    conn = db.get_connection()
    try:
        paths = [p["image_path"] for p in db.list_manga_panels(conn, story["id"])]
    finally:
        conn.close()
    assert paths[0].startswith("outputs/manga/panels/") and paths[1:] == ["p1.png", "p2.png"]
    assert "シーン1 剛(透明)" in manga_v2.redraw_summary_text(summary)


def test_redraw_state_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from python.routes import manga_v2

    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(manga_v2.router)
    client = TestClient(app)
    base = "/api/manga-v2/redraw-states"
    assert [s["name"] for s in client.get(base).json()] == ["透明", "幽霊", "石化"]
    created = client.post(base, json={"name": "影", "match_tags": "shadow person, ", "redraw_tags": "silhouette, black body", "look": "none"})
    assert created.status_code == 200 and created.json()["match_tags"] == "shadow person"
    assert client.post(base, json={"name": "影", "match_tags": "a", "redraw_tags": "b"}).status_code == 409
    state_id = created.json()["id"]
    updated = client.put(f"{base}/{state_id}", json={"name": "影", "match_tags": "shadow", "redraw_tags": "silhouette", "look": "outfit"})
    assert updated.json()["look"] == "outfit"
    assert client.delete(f"{base}/{state_id}").status_code == 204
    assert client.delete(f"{base}/{state_id}").status_code == 404
