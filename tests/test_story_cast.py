"""
物語の登場人物をそろえる処理(routes/story.py の _run_extract_characters)と、物語の自動漫画化のキャラ参照のテスト。
文章モデルは使わず、名前の候補と容姿の描写を差し替えて確かめる。

実行方法:
  uv run pytest tests/test_story_cast.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.models import StoryAutoMangaRequest  # noqa: E402
from python.routes import story as story_routes  # noqa: E402

FOX = "white fox, adult fox, animal"


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    connection = db.get_connection()
    yield connection
    connection.close()


def _story(conn: Any, title: str, text: str) -> dict[str, Any]:
    story = db.create_story(conn, f"[漫画] {title}", 1, 4)
    db.add_story_scenes(conn, story["id"], [{"draft_title": "1", "draft_text": text, "draft_prompt_tags": "", "novelai_text": text}])
    db.set_story_title(conn, story["id"], title)
    return db.get_story(conn, story["id"])


def _cast(monkeypatch: pytest.MonkeyPatch, story: dict[str, Any]) -> story_routes._Job:
    monkeypatch.setattr(story_routes, "_collect_name_candidates", lambda text: [{"name": "お母さん", "aliases": ["お母さん"]}])

    async def confirm(names):
        return names

    async def describe(name, passages):
        return "1girl, mature female, apron"

    monkeypatch.setattr(story_routes, "_confirm_people", confirm)
    monkeypatch.setattr(story_routes, "_describe_appearance", describe)
    monkeypatch.setattr(story_routes, "_appearance_passages", lambda text, aliases: [text])
    job = story_routes._Job(story_id=story["id"], kind="cast")
    asyncio.run(story_routes._run_extract_characters(job, story))
    return job


def _assigned(conn: Any, story_id: int) -> list[str]:
    return [c["name"] for c in db.story_characters(conn, story_id)]


def test_same_name_in_another_story_gets_its_own_character(conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fox_story = _story(conn, "手袋を買いに", "お母さんは子狐を抱きました。")
    fox = db.save_character(conn, "お母さん", FOX)
    db.set_scene_characters(conn, fox_story["scenes"][0]["id"], [fox["id"]])

    other = _story(conn, "台所のひみつ", "お母さんがエプロンをつけて台所に立った。")
    job = _cast(monkeypatch, other)
    # 別の物語の「お母さん」(狐)は書き換えず、この物語用のキャラを作って割り当てる
    assert _assigned(conn, other["id"]) == ["お母さん(台所のひみつ)"]
    assert db.get_character(conn, fox["id"])["appearance_tags"] == FOX
    created = next(c for c in db.list_characters(conn) if c["name"] == "お母さん(台所のひみつ)")
    assert created["appearance_tags"] == "1girl, mature female, apron"
    assert "この物語用に分けて作りました: お母さん(台所のひみつ)" in job.message
    # もう一度そろえても、同じキャラを使う(増えない)
    _cast(monkeypatch, db.get_story(conn, other["id"]))
    assert sum(1 for c in db.list_characters(conn) if c["name"].startswith("お母さん")) == 2


def test_same_series_and_unused_characters_are_reused(conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    series = db.create_series(conn, "狐の親子")
    first = _story(conn, "1巻", "お母さんは子狐を抱きました。")
    fox = db.save_character(conn, "お母さん", FOX)
    db.set_scene_characters(conn, first["scenes"][0]["id"], [fox["id"]])
    second = _story(conn, "2巻", "お母さんは町を見ました。")
    db.set_story_series(conn, first["id"], series["id"], 1)
    db.set_story_series(conn, second["id"], series["id"], 2)
    _cast(monkeypatch, db.get_story(conn, second["id"]))
    # 同じシリーズの巻のキャラは、そのまま使う(容姿も変えない)
    assert _assigned(conn, second["id"]) == ["お母さん"] and db.get_character(conn, fox["id"])["appearance_tags"] == FOX

    # どの物語にも使っていない登録済みのキャラも、そのまま使う
    db.save_character(conn, "先生", "1girl, glasses")
    monkeypatch.setattr(story_routes, "_collect_name_candidates", lambda text: [{"name": "先生", "aliases": ["先生"]}])
    lonely = _story(conn, "教室", "先生が黒板に書いた。")

    async def confirm(names):
        return names

    monkeypatch.setattr(story_routes, "_confirm_people", confirm)
    asyncio.run(story_routes._run_extract_characters(story_routes._Job(story_id=lonely["id"], kind="cast"), lonely))
    assert _assigned(conn, lonely["id"]) == ["先生"]


def test_story_label_for_new_names() -> None:
    assert story_routes._story_label({"id": 3, "title": "台所のひみつ"}) == "台所のひみつ"
    assert story_routes._story_label({"id": 3, "premise": "[漫画] 手袋を買いに\n本文"}) == "手袋を買いに"
    long = story_routes._story_label({"id": 3, "premise": "新美南吉「手袋を買いに」(青空文庫より) とても長い題名"})
    assert long == "新美南吉「手袋を買いに」(青空文庫より) とても長い題名"[:16]
    assert story_routes._story_label({"id": 7}) == "物語7"


@pytest.mark.parametrize(("flag", "expected"), [(None, True), (True, True), (False, False)])
def test_auto_manga_can_skip_character_reference(conn: Any, monkeypatch: pytest.MonkeyPatch, flag, expected) -> None:
    from python.routes import manga_v2

    story = _story(conn, "参照の確認", "お母さんは子狐を抱きました。")
    fox = db.save_character(conn, "お母さん", FOX)
    db.update_character_sheet(conn, fox["id"], {})
    conn.execute("UPDATE characters SET reference_image_path = 'outputs/x.png' WHERE id = ?", (fox["id"],))
    conn.commit()
    db.set_scene_characters(conn, story["scenes"][0]["id"], [fox["id"]])
    seen: dict[str, Any] = {}

    async def no_cast(job, story, api_key, references):
        return None

    async def fake_panels(job, story_id, req, api_key, targets):
        seen["reference"] = req.use_character_reference

    monkeypatch.setattr(story_routes, "_run_cast", no_cast)
    monkeypatch.setattr(manga_v2, "_run_panels", fake_panels)
    monkeypatch.setattr(manga_v2, "compose", lambda story_id, req: {"pages": []})
    req = StoryAutoMangaRequest(use_character_reference=flag)
    job = story_routes._Job(story_id=story["id"], kind="auto_manga")
    asyncio.run(story_routes._run_auto_manga(job, story["id"], req, "t", story_routes.TagOptions()))
    assert seen["reference"] is expected


def test_cast_presets(conn: Any) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(story_routes.router)
    client = TestClient(app)
    a = db.save_character(conn, "ひなた", "1girl", None)
    b = db.save_character(conn, "蓮", "1boy", None)
    url = "/api/story/cast-presets"
    saved = client.post(url, json={"name": " カフェ組 ", "character_ids": [a["id"], b["id"], a["id"]]}).json()
    assert saved["name"] == "カフェ組" and saved["character_ids"] == [a["id"], b["id"]]
    # 同じ名前は上書き
    assert client.post(url, json={"name": "カフェ組", "character_ids": [b["id"]]}).json()["id"] == saved["id"]
    assert client.get(url).json() == [{"id": saved["id"], "name": "カフェ組", "character_ids": [b["id"]]}]
    assert client.post(url, json={"name": "x", "character_ids": [999]}).status_code == 422
    assert client.post(url, json={"name": "x", "character_ids": []}).status_code == 422
    assert client.delete(f"{url}/{saved['id']}").status_code == 204
    assert client.delete(f"{url}/{saved['id']}").status_code == 404
    assert client.get(url).json() == []
