"""
漫画v2: コマを描くときのネガティブ(コマ > ページ > 物語)、台本のセリフと話し手、「」の中の『』、モノクロの合成のテスト。
DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_manga_negatives_mono.py -v
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
from python.manga_v2.compose import PanelContent, _draw_picture, is_colored  # noqa: E402
from python.routes import manga_v2 as manga_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(manga_routes.router)
    return TestClient(app)


def _story(client: TestClient, scenes: list[dict]) -> dict:
    res = client.post("/api/story/scripted", json={"title": "t", "panels_per_page": 2, "scenes": scenes})
    assert res.status_code == 200, res.text
    return res.json()


def test_negative_priority_scene_over_page_over_story(client: TestClient) -> None:
    story = _story(client, [{"text": f"シーン{i}"} for i in range(4)])
    ids = [s["id"] for s in story["scenes"]]
    res = client.put(
        f"/api/manga-v2/{story['id']}/negatives",
        json={"story": "story-neg", "pages": {"1": "page1-neg"}, "scenes": {str(ids[3]): "scene-neg"}},
    )
    assert res.status_code == 200, res.text
    conn = db.get_connection()
    try:
        full = db.get_story(conn, story["id"])
        # grid4 は1ページ4コマなので、2ページ目を作るため2コマのテンプレートで割り付ける
        negatives = manga_routes.scene_negatives(conn, full, "stack2", [])
    finally:
        conn.close()
    assert negatives[ids[0]] == "story-neg" and negatives[ids[1]] == "story-neg"
    assert negatives[ids[2]] == "page1-neg"
    assert negatives[ids[3]] == "scene-neg"
    # 送らなかった項目は変えない。空にすると消える
    res = client.put(f"/api/manga-v2/{story['id']}/negatives", json={"scenes": {str(ids[3]): ""}})
    assert res.json() == {"story": "story-neg", "pages": {"1": "page1-neg"}, "scenes": {}}


def test_scripted_dialogue_and_nested_quotes(client: TestClient) -> None:
    created = client.post("/api/story/characters/new", json={"name": "ひなた", "appearance_tags": "toddler"}).json()
    story = _story(
        client,
        [
            {"text": "「……これからは、ちゃんと『パパ』って呼びますね」\n栄子が言った。"},
            {"text": "ひなたが手をのばした。", "character_ids": [created["id"]],
             "dialogue": [{"text": "せんせ！", "speaker_id": created["id"]}, {"text": "  ", "speaker_id": None}],
             "negative_prompt": "adult"},
        ],
    )
    first, second = story["scenes"]
    # 「」の中の『』で切れない
    assert manga_routes._lettering(first)[0] == ["……これからは、ちゃんと『パパ』って呼びますね"]
    conn = db.get_connection()
    try:
        scenes = db.list_story_scenes(conn, story["id"])
    finally:
        conn.close()
    assert scenes[1]["dialogue"] == [{"text": "せんせ！", "speaker_id": created["id"]}]
    assert scenes[1]["negative_prompt"] == "adult"
    assert manga_routes._scene_speakers(scenes[1])[0] == [created["id"]]
    assert second["id"] == scenes[1]["id"]


def test_monochrome_compose_and_color_detection(tmp_path: Path) -> None:
    colored = tmp_path / "blue.png"
    Image.new("RGB", (64, 64), (30, 120, 220)).save(colored)
    grey = tmp_path / "grey.png"
    Image.new("RGB", (64, 64), (128, 128, 128)).save(grey)
    assert is_colored(colored) and not is_colored(grey)

    page = Image.new("RGB", (64, 64), (255, 255, 255))
    _draw_picture(page, (0, 0, 64, 64), PanelContent(colored, monochrome=True))
    r, g, b = page.getpixel((32, 32))
    assert r == g == b
