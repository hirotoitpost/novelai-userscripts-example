"""
物語のシーンでの状態とシチュエーション(キャラ別データセットのもの)。
状態はシーンのキャラごとに保存し、コマ・挿絵の生成では容姿+体型を状態の見た目にする。
シチュエーションは、選んだときにシーンのタグとキャラごとの服装・動作へ書き込む。

実行方法:
  uv run pytest tests/test_scene_states_situations.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.character_sheet import character_prompt_tags  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(story_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _scene(*, adult: bool = False) -> tuple[int, int, dict[str, Any]]:
    """(story_id, scene_id, キャラ)。キャラは体型の欄が slim のキャラ。"""
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "白石 陽菜", "1girl, brown hair", None)
        db.update_character_sheet(
            conn, character["id"], {"body_tags": "slim, flat stomach", "outfit_tags": "hoodie", "is_adult": int(adult)}
        )
        story = db.create_story(conn, "ジム", 1)
        db.add_story_scenes(conn, story["id"], [{"draft_title": None, "draft_text": "x", "draft_prompt_tags": "day"}])
        scene = db.list_story_scenes(conn, story["id"])[0]
        db.set_scene_characters(conn, scene["id"], [character["id"]], {character["id"]: "smile"})
        return story["id"], scene["id"], db.get_character(conn, character["id"])
    finally:
        conn.close()


def _states() -> dict[str, int]:
    conn = db.get_connection()
    try:
        return {s["name"]: s["id"] for s in db.list_dataset_states(conn)}
    finally:
        conn.close()


def _cast(story_id: int, scene_id: int) -> list[dict[str, Any]]:
    conn = db.get_connection()
    try:
        return db.characters_by_scene(conn, story_id)[scene_id]
    finally:
        conn.close()


def test_state_changes_the_look_only_in_that_scene(client: TestClient) -> None:
    story_id, scene_id, character = _scene()
    full = _states()["食べ過ぎでお腹が出る"]
    res = client.put(
        f"/api/story/scenes/{scene_id}/characters",
        json={"character_ids": [character["id"]], "states": {str(character["id"]): full}},
    )
    assert res.status_code == 204, res.text
    (member,) = _cast(story_id, scene_id)
    tags = character_prompt_tags(member)
    # 体型の欄を状態の体型に置き換える。服装・動作はそのまま
    assert "round belly" in tags and "slim" not in tags and "hoodie" in tags
    assert member["state_id"] == full and member["state_name"] == "食べ過ぎでお腹が出る" and member["action_tags"] == "smile"
    # 登場人物の付け替えでは状態を残す。キャラシートは変わらない
    conn = db.get_connection()
    try:
        db.set_scene_characters(conn, scene_id, [character["id"]])
        assert db.get_character(conn, character["id"])["body_tags"] == "slim, flat stomach"
        # 状態を消したら、ふだんの見た目に戻る
        db.delete_dataset_state(conn, full)
    finally:
        conn.close()
    (member,) = _cast(story_id, scene_id)
    assert member["state_id"] is None and "slim" in character_prompt_tags(member)


def test_adult_only_state_needs_an_adult(client: TestClient) -> None:
    pregnant = _states()["妊娠してお腹が大きい"]
    _, scene_id, child = _scene()
    res = client.put(
        f"/api/story/scenes/{scene_id}/characters",
        json={"character_ids": [child["id"]], "states": {str(child["id"]): pregnant}},
    )
    assert res.status_code == 403 and "成人キャラ" in res.json()["detail"]
    _, scene_id, adult = _scene(adult=True)
    res = client.put(
        f"/api/story/scenes/{scene_id}/characters",
        json={"character_ids": [adult["id"]], "states": {str(adult["id"]): pregnant}},
    )
    assert res.status_code == 204, res.text
    assert client.put(
        f"/api/story/scenes/{scene_id}/characters",
        json={"character_ids": [adult["id"]], "states": {str(adult["id"]): 999}},
    ).status_code == 404


def test_situation_writes_tags_into_the_scene(client: TestClient) -> None:
    story_id, scene_id, _ = _scene()
    conn = db.get_connection()
    try:
        gym = next(s for s in db.list_dataset_situations(conn) if s["name"] == "ジムで運動")
    finally:
        conn.close()
    res = client.post(
        f"/api/story/scenes/{scene_id}/situation",
        json={"situation_id": gym["id"], "choices": {"poses": "stretching"}},
    )
    assert res.status_code == 204, res.text
    conn = db.get_connection()
    try:
        assert db.get_scene(conn, scene_id)["draft_prompt_tags"] == "day, gym, indoors"
    finally:
        conn.close()
    (member,) = _cast(story_id, scene_id)
    # 選ばなかった軸は最初の値。服装はその場面の服装に、ポーズ・表情は動作に足す
    assert member["scene_outfit_tags"] == gym["outfits"][0]
    assert member["action_tags"] == "smile, stretching, sweat, determined"
    # 無い値は選べない
    res = client.post(
        f"/api/story/scenes/{scene_id}/situation",
        json={"situation_id": gym["id"], "choices": {"poses": "swimming"}},
    )
    assert res.status_code == 422
    assert client.post(f"/api/story/scenes/{scene_id}/situation", json={"situation_id": 999}).status_code == 404
