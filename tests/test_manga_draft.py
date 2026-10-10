"""
漫画ドラフト(大枠シナリオ → 台本 → 物語)のテスト。LLM は呼ばず、応答の検査と台本の組み立てを確かめる。
DB は一時ファイルに差し替えるので、アプリの data/app.db には触れない。

実行方法:
  uv run pytest tests/test_manga_draft.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.manga_draft import (  # noqa: E402
    DraftCharacter,
    draft_characters,
    extract_json,
    panel_to_scene,
    parse_episode,
    parse_outlines,
)
from python.routes.manga_draft import router as draft_router  # noqa: E402
from python.routes.manga_v2 import _lettering, _scene_speakers  # noqa: E402
from python.routes.manga_v2 import router as manga_v2_router  # noqa: E402
from python.routes.series import router as series_router  # noqa: E402
from python.routes.story import router as story_router  # noqa: E402

EIKO = {"id": 1, "name": "矢野栄子", "appearance_tags": "1girl, light brown hair", "notes": None}
SENSEI = {"id": 2, "name": "矢野先生", "appearance_tags": "1boy, black hair", "notes": "栄子の夫。国語教師。"}
CAST = draft_characters([EIKO, SENSEI], {1: "恥ずかしがり屋の主婦"})


def test_draft_characters_use_short_names_and_profiles() -> None:
    assert CAST == [
        DraftCharacter(1, "栄子", "恥ずかしがり屋の主婦"),
        DraftCharacter(2, "先生", "栄子の夫。国語教師。"),
    ]
    yura = draft_characters([{"id": 3, "name": "九条 ゆら"}])[0]
    assert yura.name == "ゆら"


def test_extract_json_tolerates_wrapping() -> None:
    text = '<think>考え中</think>はい、どうぞ。\n```json\n{"a": [1, 2]}\n```'
    assert extract_json(text) == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("JSON はありません")


def test_parse_outlines_keeps_complete_options() -> None:
    data = {
        "outlines": [
            {"title": "紅葉と迷子", "logline": "はぐれる話", "episodes": ["1", "2", "3"]},
            # 実機で見たキー名の崩れ(log線)と、多すぎる話数
            {"title": "あとがき", "log線": "先生の話", "episodes": ["a", "b", "c", "d"]},
            {"title": "足りない", "episodes": ["x"]},
            "壊れた要素",
        ]
    }
    outlines = parse_outlines(data, 3)
    assert [o["title"] for o in outlines] == ["紅葉と迷子", "あとがき"]
    assert outlines[1] == {"title": "あとがき", "logline": "先生の話", "episodes": ["a", "b", "c"]}


def _raw_panel(**extra) -> dict:
    return {
        "characters": ["栄子", "先生"],
        "lines": [{"speaker": "栄子", "kind": "speech", "text": "先生、見て！"}],
        "narration": "",
        "sfx": ["キラキラ"],
        "prompt_tags": "1girl, 1boy, autumn_leaves, park",
        **extra,
    }


def test_parse_episode_cleans_the_script() -> None:
    raw = [
        _raw_panel(),
        _raw_panel(characters=["栄子", "通行人"], lines=[{"speaker": "通行人", "text": "「あの」"}]),
        _raw_panel(lines=[{"speaker": "先生", "kind": "thought", "text": "（かわいい）"}, {"text": ""}]),
        _raw_panel(prompt_tags="1girl, 1girl, smile", sfx=["ニ", "ヤ"]),
    ]
    panels = parse_episode({"panels": raw}, CAST)
    assert panels[0]["prompt_tags"] == "1girl, 1boy, autumn leaves, park"
    # 知らない人物は描かず、話し手は空(不明)にする。かっこは外す
    assert panels[1]["characters"] == ["栄子"]
    assert panels[1]["lines"] == [{"speaker": "", "kind": "speech", "text": "あの"}]
    assert panels[2]["lines"] == [{"speaker": "先生", "kind": "thought", "text": "かわいい"}]
    assert panels[3]["prompt_tags"] == "1girl, smile"
    assert panels[3]["sfx"] == ["ニヤ"]  # 1文字ずつに分かれた効果音はつなげる
    with pytest.raises(ValueError):
        parse_episode({"panels": raw[:3]}, CAST)


def test_panel_to_scene_makes_speakers_unambiguous() -> None:
    panel = {
        "characters": ["栄子", "先生"],
        "lines": [
            {"speaker": "先生", "kind": "speech", "text": "栄子さん、行こうか"},
            {"speaker": "栄子", "kind": "thought", "text": "ドキドキする"},
            {"speaker": "栄子", "kind": "speech", "text": "はい"},
        ],
        "narration": "紅葉の名所",
        "sfx": ["ザワザワ"],
        "prompt_tags": "1girl, 1boy, park",
    }
    scene = panel_to_scene(panel, CAST, "1話1")
    assert scene["character_ids"] == [1, 2]
    assert scene["narration"] == "紅葉の名所"
    # 合成と同じ規則で読むと、台本どおりの話し手・吹き出しになる
    story_scene = {"novelai_text": scene["text"], "draft_text": scene["text"], "characters": [EIKO, SENSEI]}
    lines = _lettering(story_scene)[0]
    speakers, _ = _scene_speakers(story_scene)
    assert lines == ["栄子さん、行こうか", "（ドキドキする）", "はい"]
    assert speakers == [2, 1, 1]


def test_panel_without_lines_still_has_text() -> None:
    scene = panel_to_scene({"characters": [], "lines": [], "narration": "翌朝", "prompt_tags": "no humans"}, CAST)
    assert scene["text"] == "翌朝"
    assert _lettering({"novelai_text": scene["text"], "draft_text": scene["text"]})[0] == []


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    for router in (draft_router, story_router, manga_v2_router, series_router):
        app.include_router(router)
    return TestClient(app)


def test_create_story_from_the_draft(client: TestClient) -> None:
    eiko = client.post("/api/story/characters", json={"name": "矢野栄子", "appearance_tags": "1girl"}).json()["id"]
    sensei = client.post("/api/story/characters", json={"name": "矢野先生", "appearance_tags": "1boy"}).json()["id"]
    first = client.post("/api/story/scripted", json={"title": "1巻", "scenes": [{"text": "「はじめまして」"}]}).json()
    series = client.post("/api/series", json={"title": "矢野夫妻", "story_ids": [first["id"]]}).json()

    episode = [_raw_panel(), _raw_panel(), _raw_panel(), _raw_panel(lines=[])]
    body = {
        "title": "紅葉と迷子",
        "character_ids": [eiko, sensei],
        "episodes": [parse_episode({"panels": episode}, CAST)],
        "series_id": series["id"],
    }
    res = client.post("/api/manga-draft/create", json=body)
    assert res.status_code == 200, res.text
    story = res.json()
    assert len(story["scenes"]) == 4
    assert story["scenes"][0]["draft_title"] == "1話1"
    # シリーズの次の巻(2巻)になる
    volumes = client.get(f"/api/series/{series['id']}").json()["volumes"]
    assert [v["volume_no"] for v in volumes] == [1, 2]
    speakers = client.get(f"/api/manga-v2/{story['id']}/speakers").json()
    assert speakers[0]["lines"] == [{"text": "先生、見て！", "speaker_id": eiko, "speaker_name": "矢野栄子"}]


def test_actions_are_per_character() -> None:
    raw = [
        _raw_panel(actions={"栄子": "blush, looking_away", "先生": "hand on own chin", "通行人": "running"}),
        _raw_panel(characters=["栄子"], actions={"先生": "smile"}),
        _raw_panel(actions="壊れた値"),
        _raw_panel(),
    ]
    panels = parse_episode({"panels": raw}, CAST)
    # 描く人物の分だけ残し、タグを整える
    assert panels[0]["actions"] == {"栄子": "blush, looking away", "先生": "hand on own chin"}
    assert panels[1]["actions"] == {}
    assert panels[2]["actions"] == {}
    scene = panel_to_scene(panels[0], CAST)
    assert scene["character_actions"] == {1: "blush, looking away", 2: "hand on own chin"}
