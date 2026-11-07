"""
コマの絵の精度の測定(panel_accuracy.py と、manga_v2 の panel-accuracy)のテスト。判定モデルは差し替える。

実行方法:
  uv run pytest tests/test_panel_accuracy.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, panel_check  # noqa: E402
from python.panel_accuracy import (  # noqa: E402
    AccuracyReport,
    category_of,
    expected_tags,
    forbidden_tags,
    is_render_tag,
    measure_panel,
)
from python.panel_check import checkable_tags  # noqa: E402
from python.routes import manga_v2  # noqa: E402

VOCAB = {
    "long hair", "silver hair", "kanzashi", "white bikini", "walking", "beach", "cowboy shot", "nipples", "steam",
    "smile", "sex", "nude", "flat chest", "moonlight", "glasses", "1girl", "looking at viewer", "anime coloring",
}


def test_render_tags_are_not_picture_content() -> None:
    for tag in ("anime coloring", "painterly", "realistic skin", "flat color", "cel shading", "soft colors", "bad hands"):
        assert is_render_tag(tag), tag
    for tag in ("flat chest", "moonlight", "long hair", "holding photo", "kanzashi"):
        assert not is_render_tag(tag), tag
    assert category_of("anime coloring") == "style"
    assert category_of("kanzashi") == "outfits"
    assert category_of("red eyeliner") == "appearance"
    assert category_of("jitome") == "expressions"
    assert category_of("cowboy shot") == "framings"


def test_expected_and_forbidden_tags() -> None:
    scene = {
        "draft_prompt_tags": "beach, cowboy shot, 1girl, {{anime coloring}}, 1.5::walking::, -1::sex::",
        "negative_prompt": "nipples, steam, bad hands, anime coloring, looking at viewer, kanzashi",
        "absent_tags": "glasses",
    }
    member = {
        "appearance_tags": "1girl, long hair, silver hair",
        "outfit_tags": "white bikini, kanzashi, no makeup",
        "action_tags": "smile",
        "negative_tags": "flat chest",
    }
    expected = expected_tags(scene, [member], VOCAB)
    assert expected == {
        "beach": "locations", "cowboy shot": "framings", "walking": "poses", "long hair": "appearance",
        "silver hair": "appearance", "white bikini": "outfits", "kanzashi": "outfits", "smile": "expressions",
    }
    # 塗り・画質・目線・描くはずのタグ(kanzashi)は、出てはいけない物にしない
    assert forbidden_tags(scene, [member], VOCAB, expected) == ["nipples", "steam", "glasses", "flat chest"]
    # ある人のネガティブでも、同じコマの別の人が身に着けるものは除く(栄子の glasses と、先生の眼鏡)
    teacher = {"appearance_tags": "1boy", "outfit_tags": "black-framed glasses"}
    assert "glasses" not in forbidden_tags(scene, [member, teacher], VOCAB, expected)


def test_rating_problems() -> None:
    scene = {"scene_index": 0, "draft_prompt_tags": "beach"}
    calm = measure_panel(1, scene, [], VOCAB, {"beach": 0.9}, {"general": 0.9, "explicit": 0.05}, story_adult=False)
    assert calm.rating == "general" and calm.rating_problem is None and calm.hits == ["beach"]
    leak = measure_panel(1, scene, [], VOCAB, {}, {"general": 0.1, "explicit": 0.8}, story_adult=False)
    assert leak.rating_problem == "全年齢の物語なのに explicit と判定された"
    assert measure_panel(1, scene, [], VOCAB, {}, {"explicit": 0.8}, story_adult=True).rating_problem is None
    # 性的な描写を指定したコマが、そう描かれなかった
    act = {"scene_index": 1, "draft_prompt_tags": "sex"}
    missed = measure_panel(1, act, [], VOCAB, {}, {"sensitive": 0.7}, story_adult=True)
    assert missed.wants_adult and "描写が描かれていない" in (missed.rating_problem or "")
    # 服装の nude だけでは性的な場面にしない(入浴など)
    bath = measure_panel(1, scene, [{"outfit_tags": "nude", "scene_outfit_tags": "nude"}], VOCAB, {}, {"questionable": 0.6}, False)
    assert not bath.wants_adult and bath.rating_problem is None


def test_report_aggregates_categories_and_weak_tags() -> None:
    scene = {"scene_index": 0, "draft_prompt_tags": "beach, walking", "negative_prompt": "steam"}
    report = AccuracyReport()
    report.panels.append(measure_panel(1, scene, [], VOCAB, {"beach": 0.9, "steam": 0.5}, {"general": 0.9}, False))
    report.panels.append(measure_panel(1, {**scene, "scene_index": 1}, [], VOCAB, {"beach": 0.9, "walking": 0.3}, {"sensitive": 0.9}, False))
    data = report.to_dict(include_panels=False)
    assert data["tags_checked"] == 4 and data["tags_hit"] == 3 and data["rate"] == 0.75
    assert data["categories"]["locations"] == {"label": "場所・背景", "checked": 2, "hit": 2, "rate": 1.0}
    assert data["categories"]["poses"]["rate"] == 0.5
    assert data["weakest_tags"][0]["tag"] == "walking"
    assert data["unwanted"] == [{"story_id": 1, "scene_index": 0, "tag": "steam", "probability": 0.5}]
    assert data["ratings"] == {"general": 1, "sensitive": 1, "questionable": 0, "explicit": 0}
    assert "panels" not in data


def test_panel_check_ignores_render_tags() -> None:
    assert checkable_tags("beach, anime coloring, walking", [], VOCAB) == ["beach", "walking"]


def test_panel_accuracy_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "x", 2, 4)
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": "1", "draft_text": "a", "draft_prompt_tags": "beach, walking", "novelai_text": "x"},
            {"draft_title": "2", "draft_text": "b", "draft_prompt_tags": "beach", "novelai_text": "x"},
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        girl = db.save_character(conn, "しらね", "1girl, long hair", None)
        for scene, name in zip(scenes, ("a.png", "b.png")):
            db.set_scene_characters(conn, scene["id"], [girl["id"]])
            conn.execute("UPDATE story_scenes SET negative_prompt = 'nipples' WHERE id = ?", (scene["id"],))
            Image.new("RGB", (8, 8)).save(tmp_path / name)
            db.upsert_manga_panel(conn, story["id"], scene["id"], name, 1, 8, 8)
        conn.commit()
    finally:
        conn.close()
    scores = {
        "a.png": ({"beach": 0.9, "long hair": 0.8}, {"general": 0.9}),
        "b.png": ({"beach": 0.9, "long hair": 0.7, "nipples": 0.6}, {"explicit": 0.7}),
    }
    monkeypatch.setattr(panel_check, "tagger_vocabulary", lambda: VOCAB)
    monkeypatch.setattr(manga_v2, "_tagger_scores", lambda path: scores[path])
    app = FastAPI()
    app.include_router(manga_v2.router)
    client = TestClient(app)

    data = client.post("/api/manga-v2/panel-accuracy", json={"story_ids": [story["id"]]}).json()
    assert data["panels_checked"] == 2
    assert data["tags_checked"] == 5 and data["tags_hit"] == 4  # beach×2, walking, long hair×2 のうち walking が外れ
    assert data["unwanted"] == [{"story_id": story["id"], "scene_index": 1, "tag": "nipples", "probability": 0.6}]
    assert data["rating_problems"] == [
        {"story_id": story["id"], "scene_index": 1, "rating": "explicit", "problem": "全年齢の物語なのに explicit と判定された"}
    ]
    assert [p["scene_index"] for p in data["panels"]] == [0, 1]
    only = client.post(
        "/api/manga-v2/panel-accuracy", json={"story_ids": [story["id"]], "scene_indexes": [0], "include_panels": False}
    ).json()
    assert only["panels_checked"] == 1 and "panels" not in only
    assert client.post("/api/manga-v2/panel-accuracy", json={"story_ids": [99999]}).status_code == 404
