"""
漫画v2: 編集まわりの落とし穴を直したもののテスト。
- コマにいない人のセリフは、合成で吹き出しではなくナレーション(名前「…」)にする
- 1シーンのセリフを決める PUT /scenes/{id}/dialogue([] で吹き出しを消す)
- PUT /edit の知らない項目(台本の形の character_ids など)は黙って捨てずに 422

実行方法:
  uv run pytest tests/test_offpanel_dialogue.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.models import MangaV2ComposeRequest  # noqa: E402
from python.routes import manga_v2  # noqa: E402
from python.routes import story as story_routes  # noqa: E402
from python.routes import story_edit  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    app = FastAPI()
    for router in (story_routes.router, story_edit.router, manga_v2.router):
        app.include_router(router)
    return TestClient(app)


def _setup(client: TestClient, tmp_path: Path) -> tuple[int, int, dict[str, int]]:
    """ゆらだけが描かれるコマ。陽太はコマの外から話す。"""
    conn = db.get_connection()
    try:
        yura = db.save_character(conn, "ゆら", "1girl")
        yota = db.save_character(conn, "陽太", "1boy")
    finally:
        conn.close()
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": [
        {"text": "海", "character_ids": [yura["id"]], "dialogue": [{"text": "きれい", "speaker_id": yura["id"]}]},
    ]}).json()
    scene_id = story["scenes"][0]["id"]
    Image.new("RGB", (60, 80), (200, 220, 255)).save(tmp_path / "p.png")
    conn = db.get_connection()
    try:
        db.upsert_manga_panel(conn, story["id"], scene_id, "p.png", 1, 60, 80)
    finally:
        conn.close()
    return story["id"], scene_id, {"yura": yura["id"], "yota": yota["id"]}


def test_offpanel_line_becomes_narration(client: TestClient, tmp_path: Path) -> None:
    story_id, scene_id, ids = _setup(client, tmp_path)
    res = client.put(f"/api/manga-v2/scenes/{scene_id}/dialogue", json={"dialogue": [
        {"text": "きれい", "speaker_id": ids["yura"]},
        {"text": "来年も、最前列で見る", "speaker_id": ids["yota"]},
        {"text": "（届かない）", "speaker_id": ids["yota"]},
        {"text": "幽霊", "speaker_id": 9999},  # いないキャラは話し手なし(吹き出しのまま)
    ]})
    assert res.status_code == 204, res.text

    contents, _, _ = manga_v2._compose_inputs(story_id, MangaV2ComposeRequest())
    panel = contents[0]
    assert panel.dialogue == ["きれい", "幽霊"]
    assert panel.narration == "陽太「来年も、最前列で見る」　陽太（届かない）"

    speakers = client.get(f"/api/manga-v2/{story_id}/speakers").json()[0]["lines"]
    assert [(line["speaker_name"], line["offpanel"]) for line in speakers] == [
        ("ゆら", False), ("陽太", True), ("陽太", True), (None, False),
    ]


def test_empty_dialogue_clears_bubbles(client: TestClient, tmp_path: Path) -> None:
    story_id, scene_id, _ = _setup(client, tmp_path)
    assert client.put(f"/api/manga-v2/scenes/{scene_id}/dialogue", json={"dialogue": []}).status_code == 204
    contents, _, _ = manga_v2._compose_inputs(story_id, MangaV2ComposeRequest())
    assert contents[0].dialogue == []


def test_edit_keeps_offpanel_speaker_and_rejects_unknown_keys(client: TestClient, tmp_path: Path) -> None:
    story_id, scene_id, ids = _setup(client, tmp_path)
    data = client.get(f"/api/story/{story_id}/edit").json()
    scene = data["scenes"][0]
    item = {k: scene[k] for k in ("id", "title", "text", "prompt_tags", "narration", "sfx", "page_index", "characters", "panel_size")}
    item["dialogue"] = [{"text": "行こう", "speaker_id": ids["yota"]}]
    body = {"title": data["title"], "panels_per_page": data["panels_per_page"], "custom_pages": data["custom_pages"], "scenes": [item]}
    assert client.put(f"/api/story/{story_id}/edit", json=body).status_code == 200
    saved = client.get(f"/api/story/{story_id}/edit").json()["scenes"][0]["dialogue"]
    assert saved == [{"text": "行こう", "speaker_id": ids["yota"]}]  # コマにいない話し手も残る

    # 台本の形の character_ids は PUT /edit では使えない(黙って捨てずに知らせる)
    wrong = {**body, "scenes": [{**item, "character_ids": []}]}
    res = client.put(f"/api/story/{story_id}/edit", json=wrong)
    assert res.status_code == 422 and "character_ids" in res.text
