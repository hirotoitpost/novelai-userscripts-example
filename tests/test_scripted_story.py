"""
台本からの物語作成(/api/story/scripted)・シーンの手直し・話し手の判定 API のテスト。
DB は一時ファイルに差し替えるので、アプリの data/app.db には触れない。

実行方法:
  uv run pytest tests/test_scripted_story.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes.manga_v2 import router as manga_v2_router  # noqa: E402
from python.routes.series import router as series_router  # noqa: E402
from python.routes.story import router as story_router  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    for router in (story_router, manga_v2_router, series_router):
        app.include_router(router)
    return TestClient(app)


def _character(client: TestClient, name: str, tags: str) -> int:
    res = client.post("/api/story/characters", json={"name": name, "appearance_tags": tags})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _script(eiko: int, sensei: int) -> list[dict]:
    return [
        {
            "title": "お誘い1",
            "text": "「栄子さん、今度の日曜日、空いてる？」\n先生がにこにこしながら聞いた。",
            "prompt_tags": "1boy, holding two tickets, smile, living room",
            "character_ids": [sensei],
            "narration": "金曜日の夜",
        },
        {
            "title": "お誘い2",
            "text": "「ねえ、先生」\n「ん？」",
            "prompt_tags": "1girl, 1boy, living room",
            "character_ids": [eiko, sensei],
            "sfx": ["ドキッ"],
        },
        {"title": "月", "text": "夜空に月が浮かんでいた。", "prompt_tags": "no humans, full moon, scenery"},
    ]


def test_create_scripted_story(client: TestClient) -> None:
    eiko = _character(client, "矢野栄子", "1girl, light brown hair")
    sensei = _character(client, "矢野先生", "1boy, black hair, glasses")

    res = client.post("/api/story/scripted", json={"title": "テスト 1巻", "scenes": _script(eiko, sensei)})
    assert res.status_code == 200, res.text
    story = res.json()
    assert story["title"] == "テスト 1巻"
    assert story["premise"] == "[漫画] テスト 1巻"
    assert story["status"] == "written"
    scenes = story["scenes"]
    assert [s["draft_title"] for s in scenes] == ["お誘い1", "お誘い2", "月"]
    assert scenes[0]["novelai_text"] == scenes[0]["draft_text"]
    assert {c["id"] for c in scenes[1]["characters"]} == {eiko, sensei}
    assert scenes[0]["narration"] == "金曜日の夜"
    assert scenes[1]["sfx"] == ["ドキッ"]
    # 効果音を書かなかったシーンは「無し」(None だとAI提案の対象になる)
    assert scenes[2]["sfx"] == []


def test_speakers_follow_the_script(client: TestClient) -> None:
    eiko = _character(client, "矢野栄子", "1girl, light brown hair")
    sensei = _character(client, "矢野先生", "1boy, black hair, glasses")
    story_id = client.post("/api/story/scripted", json={"title": "t", "scenes": _script(eiko, sensei)}).json()["id"]

    speakers = client.get(f"/api/manga-v2/{story_id}/speakers").json()
    lines = [[(line["text"], line["speaker_name"]) for line in s["lines"]] for s in speakers]
    assert lines[0] == [("栄子さん、今度の日曜日、空いてる？", "矢野先生")]
    assert lines[1] == [("ねえ、先生", "矢野栄子"), ("ん？", "矢野先生")]
    assert lines[2] == []  # 地の文だけのシーンは吹き出しなし


def test_patch_scene(client: TestClient) -> None:
    eiko = _character(client, "矢野栄子", "1girl")
    sensei = _character(client, "矢野先生", "1boy")
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": _script(eiko, sensei)}).json()
    scene_id = story["scenes"][0]["id"]

    res = client.patch(f"/api/story/scenes/{scene_id}", json={"text": "「やあ」", "prompt_tags": "1boy, waving"})
    assert res.status_code == 204
    scene = client.get(f"/api/story/{story['id']}").json()["scenes"][0]
    assert scene["draft_text"] == scene["novelai_text"] == "「やあ」"
    assert scene["draft_prompt_tags"] == "1boy, waving"
    assert scene["draft_title"] == "お誘い1"  # 送らなかった項目は変わらない
    assert client.patch("/api/story/scenes/999999", json={"title": "x"}).status_code == 404


def test_rejects_unknown_characters(client: TestClient) -> None:
    res = client.post(
        "/api/story/scripted", json={"title": "t", "scenes": [{"text": "「a」", "character_ids": [12345]}]}
    )
    assert res.status_code == 422
    assert "12345" in res.json()["detail"]


def test_series_volume(client: TestClient) -> None:
    eiko = _character(client, "矢野栄子", "1girl")
    sensei = _character(client, "矢野先生", "1boy")
    first = client.post("/api/story/scripted", json={"title": "1巻", "scenes": _script(eiko, sensei)}).json()
    series = client.post("/api/series", json={"title": "矢野夫妻", "story_ids": [first["id"]]}).json()

    body = {"title": "2巻", "scenes": _script(eiko, sensei), "series_id": series["id"], "volume_no": 2}
    second = client.post("/api/story/scripted", json=body)
    assert second.status_code == 200, second.text
    volumes = client.get(f"/api/series/{series['id']}").json()["volumes"]
    assert [(v["volume_no"], v["story_id"]) for v in volumes] == [(1, first["id"]), (2, second.json()["id"])]

    # 同じ巻はもう作れない。シリーズと巻は両方そろえて渡す
    assert client.post("/api/story/scripted", json=body).status_code == 409
    half = {"title": "x", "scenes": _script(eiko, sensei), "series_id": series["id"]}
    assert client.post("/api/story/scripted", json=half).status_code == 422
