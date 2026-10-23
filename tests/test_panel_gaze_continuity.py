"""
コマの確かめ: 会話のコマの目線(こちらを見ていないか)と、続き具合(出てはいけない物・状態で外したタグ)のテスト。
シーンの範囲にまとめて状態・服装・出てはいけない物を書き込む API も見る。DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_panel_gaze_continuity.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.panel_check import check_panel, is_conversation, redraw_tags, unwanted_tags  # noqa: E402
from python.routes import story as story_routes  # noqa: E402
from python.routes import story_edit as story_edit_routes  # noqa: E402

VOCABULARY = {"pink scrunchie", "hair scrunchie", "looking at viewer", "slim", "lowres", "hat"}


def test_gaze_is_checked_only_on_conversation_panels() -> None:
    looking = {"1girl": 0.9, "looking at viewer": 0.8}
    assert not check_panel(looking, [], 1, conversation=True).ok
    assert check_panel(looking, [], 1, conversation=False).ok
    assert check_panel({"1girl": 0.9, "looking at viewer": 0.3}, [], 1, conversation=True).ok
    assert "目線(こちらを見ている)" in check_panel(looking, [], 1, conversation=True).problems()


def test_conversation_needs_dialogue_and_a_person_and_no_exemption() -> None:
    assert is_conversation(True, 1, "classroom, smile")
    assert not is_conversation(False, 2, "classroom")
    assert not is_conversation(True, 0, "letter on desk")
    # こちらを見ると決めたコマ(カメラ目線・主観・鏡)は確かめない
    assert not is_conversation(True, 1, "selfie, Looking At Viewer")
    assert not is_conversation(True, 2, "pov, hand")
    assert not is_conversation(True, 1, "mirror, bathroom")


def test_unwanted_tags_come_from_absent_and_removed_tags() -> None:
    tags = unwanted_tags("Pink scrunchie, unknown thing", ["hair scrunchie, slim", ""], VOCABULARY)
    # 判定モデルに無い語・体型の語は除く
    assert tags == ["pink scrunchie", "hair scrunchie"]
    check = check_panel({"1girl": 0.9, "pink scrunchie": 0.6, "hair scrunchie": 0.2}, [], 1, absent=tags)
    assert not check.ok and check.unwanted == ["pink scrunchie"]
    assert "出てはいけない: pink scrunchie" in check.problems()


def test_key_prefers_no_unwanted_over_gaze_and_score() -> None:
    with_prop = check_panel({"1girl": 0.9, "hat": 0.9}, [], 1, absent=["hat"])
    looking = check_panel({"1girl": 0.9, "looking at viewer": 0.9}, [], 1, conversation=True)
    assert looking.key() > with_prop.key()


def test_redraw_tags_turn_the_gaze_and_suppress_unwanted() -> None:
    solo = check_panel({"1girl": 0.9, "looking at viewer": 0.9, "pink scrunchie": 0.9}, [], 1, True, ["pink scrunchie"])
    first = redraw_tags(solo, 1, 1)
    assert "looking to the side" in first and "three-quarter view" in first
    assert "-1.5::looking at viewer::" in first and "-1::pink scrunchie::" in first
    second = redraw_tags(solo, 1, 2)
    assert "profile" in second and "-2::looking at viewer::" in second and "-2::pink scrunchie::" in second
    pair = check_panel({"2girls": 0.9, "looking at viewer": 0.9}, [], 2, True)
    assert {"looking at each other", "eye contact"} <= set(redraw_tags(pair, 2, 1))


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(story_edit_routes.router)
    return TestClient(app)


def _character(client: TestClient, name: str) -> int:
    res = client.post("/api/story/characters/new", json={"name": name, "appearance_tags": "1girl, pink scrunchie"})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_scene_range_writes_state_outfit_and_absent(client: TestClient) -> None:
    yura, friend = _character(client, "ゆら"), _character(client, "みお")
    scenes = [{"text": f"シーン{i}", "character_ids": [yura, friend] if i != 2 else [friend]} for i in range(5)]
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": scenes}).json()
    ids = [s["id"] for s in story["scenes"]]
    conn = db.get_connection()
    try:
        state = db.save_dataset_state(conn, None, "髪を下ろす(テスト)", "hair down", "pink scrunchie, hair scrunchie")
    finally:
        conn.close()
    state_id = state["id"] if isinstance(state, dict) else state

    res = client.post(
        f"/api/story/{story['id']}/scene-range",
        json={"from_scene": 2, "to_scene": 4, "character_id": yura, "state_id": state_id, "outfit": "pajamas",
              "absent_tags": "pink scrunchie"},
    )
    assert res.status_code == 200, res.text
    # 状態・服装はゆらのいるシーンだけ、出てはいけない物は範囲の全シーン
    assert res.json()["scene_ids"] == ids[1:4]
    conn = db.get_connection()
    try:
        full = db.get_story(conn, story["id"])
    finally:
        conn.close()
    by_index = {s["scene_index"]: s for s in full["scenes"]}
    for index in (1, 3):
        member = next(c for c in by_index[index]["characters"] if c["id"] == yura)
        assert member["state_id"] == state_id and member["scene_outfit_tags"] == "pajamas"
        assert "pink scrunchie" in member["state_remove_tags"]
        # 一緒にいるキャラは変えない
        other = next(c for c in by_index[index]["characters"] if c["id"] == friend)
        assert other["state_id"] is None and not other["scene_outfit_tags"]
    assert [by_index[i]["absent_tags"] for i in range(5)] == [None, "pink scrunchie", "pink scrunchie", "pink scrunchie", None]
    first = next(c for c in by_index[0]["characters"] if c["id"] == yura)
    assert first["state_id"] is None and first["state_remove_tags"] == ""

    # 状態だけ戻す(服装はそのまま)
    res = client.post(
        f"/api/story/{story['id']}/scene-range",
        json={"from_scene": 1, "to_scene": 5, "character_id": yura, "state_id": None},
    )
    assert res.json()["scene_ids"] == [ids[0], ids[1], ids[3], ids[4]]
    conn = db.get_connection()
    try:
        full = db.get_story(conn, story["id"])
    finally:
        conn.close()
    member = next(c for c in full["scenes"][1]["characters"] if c["id"] == yura)
    assert member["state_id"] is None and member["scene_outfit_tags"] == "pajamas"
    assert full["scenes"][1]["absent_tags"] == "pink scrunchie"


def test_scene_range_rejects_bad_requests(client: TestClient) -> None:
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": [{"text": "a"}]}).json()
    url = f"/api/story/{story['id']}/scene-range"
    assert client.post(url, json={"from_scene": 1, "to_scene": 1}).status_code == 422
    assert client.post(url, json={"from_scene": 1, "to_scene": 1, "outfit": "x"}).status_code == 422
    assert client.post(url, json={"from_scene": 2, "to_scene": 1, "absent_tags": "x"}).status_code == 422
    assert client.post("/api/story/9999/scene-range", json={"from_scene": 1, "to_scene": 1, "absent_tags": "x"}).status_code == 404


def test_absent_tags_round_trip(client: TestClient) -> None:
    story = client.post(
        "/api/story/scripted", json={"title": "t", "scenes": [{"text": "a", "absent_tags": "pink scrunchie"}, {"text": "b"}]}
    ).json()
    first, second = story["scenes"]
    assert client.patch(f"/api/story/scenes/{second['id']}", json={"absent_tags": "hat"}).status_code == 204
    edit = client.get(f"/api/story/{story['id']}/edit").json()
    assert [s["absent_tags"] for s in edit["scenes"]] == ["pink scrunchie", "hat"]
