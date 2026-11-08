"""
下着・水着のコレクション/からかいコレクションのテンプレート(collection_templates.py)と、
好きなキャラで物語にする API(/api/collections)のテスト。NovelAI には接続しない。

実行方法:
  uv run pytest tests/test_collections.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.collection_templates import TEASE_POSES, CollectionOptions, build_collection  # noqa: E402
from python.models import ScriptedStoryRequest  # noqa: E402
from python.routes import collections as collection_routes  # noqa: E402

PAIR = [{"id": 1, "name": "しらね"}, {"id": 2, "name": "あかね"}]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(collection_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _character(name: str, age: int | None) -> int:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, name, "1girl, silver hair")
        if age is not None:
            db.update_character_sheet(conn, character["id"], {"age": age})
        return character["id"]
    finally:
        conn.close()


def test_tease_collection_gives_every_pose_to_every_character() -> None:
    script = build_collection(PAIR, CollectionOptions(kind="tease_lingerie"))
    scenes = script["scenes"]
    # 最初と最後のみんなのコマ + ポーズ × キャラ
    assert len(scenes) == 2 + len(TEASE_POSES) * 2
    solo = scenes[1:-1]
    for pose in TEASE_POSES:
        cast = [s["character_ids"][0] for s in solo if s["narration"].startswith(pose.label + "　")]
        assert cast == [1, 2], pose.key
    # 台本の形として通る(セリフの話し手はコマにいるキャラ)
    ScriptedStoryRequest(title=script["title"], scenes=scenes)
    for s in solo:
        assert all(line["speaker_id"] in s["character_ids"] for line in s["dialogue"])


def test_wedgie_only_in_wedgie_poses_and_content_negatives_everywhere() -> None:
    scenes = build_collection(PAIR, CollectionOptions(kind="tease_swimsuit"))["scenes"]
    for s in scenes:
        negative = s["negative_prompt"]
        assert "nipples" in negative and "cameltoe" in negative and "genitals" in negative
        assert "nsfw" not in negative
        wedgie_pose = "wedgie" in ",".join(s["character_actions"].values())
        assert ("wedgie" in negative) is not wedgie_pose
        assert "explicit" in s["prompt_tags"]
    # 顔なしの後ろ姿は、振り向きを打ち消し、頭は枠の外
    away = next(s for s in scenes if (s["narration"] or "").startswith("四つんばい(背面・顔なし)"))
    assert "looking back" in away["negative_prompt"] and "head out of frame" in away["character_actions"][1]
    # 脚の間の頭は、股のぞきのポーズだけ
    for s in scenes[1:-1]:
        through = (s["narration"] or "").startswith("股のぞき")
        assert ("head between legs" in s["negative_prompt"]) is not through


def test_collection_options_pick_poses_themes_body_and_style() -> None:
    script = build_collection(PAIR[:1], CollectionOptions(
        kind="tease_lingerie", poses=["squat_away", "upside_down"], body="sheet", style_tags="flat color", lines=False,
    ))
    scenes = script["scenes"]
    assert len(scenes) == 4 and all("flat color" in s["prompt_tags"] for s in scenes)
    assert all(not s["dialogue"] for s in scenes)
    assert "curvy" not in scenes[1]["character_outfits"][1]
    normal = build_collection(PAIR, CollectionOptions(kind="swimsuit", themes=["ハイレグ"]))["scenes"]
    # みんな + テーマの見出し + 2人×(正面・背面) + みんな。体つきの既定はすらっと
    assert len(normal) == 7 and normal[1]["character_ids"] == [] and "slender" in normal[2]["character_outfits"][1]
    assert "front view" in normal[2]["character_actions"][1] and "from behind" in normal[3]["character_actions"][1]
    assert normal[3]["narration"].startswith("ハイレグ・後ろ")
    # 背面のポーズは順に回る。立位体前屈は顔を見せず、脚の間の頭も打ち消す
    backs = [s for s in build_collection(PAIR[:1], CollectionOptions(kind="lingerie"))["scenes"]
             if "・後ろ" in (s["narration"] or "")]
    bend = [s for s in backs if "standing forward bend" in s["character_actions"][1]]
    assert len(bend) == 1 and "head between legs" in bend[0]["negative_prompt"] and "looking back" in bend[0]["negative_prompt"]
    assert "three-quarter view" in bend[0]["negative_prompt"] and "directly behind" in bend[0]["character_actions"][1]
    # キャラごとに違う衣装
    assert normal[2]["character_outfits"][1] != normal[4]["character_outfits"][2]
    with pytest.raises(ValueError):
        build_collection(PAIR, CollectionOptions(kind="tease_lingerie", poses=["nope"]))


def test_api_makes_a_story_only_with_adult_characters(client: TestClient) -> None:
    adult = _character("しらね", 22)
    assert client.get("/api/collections").json()["kinds"][0]["id"] == "lingerie"
    res = client.post("/api/collections", json={"kind": "lingerie", "character_ids": [adult], "themes": ["白レース"]})
    assert res.status_code == 200, res.text
    story = res.json()
    assert story["n_scenes"] == 5 and story["title"] == "しらねの下着コレクション"
    # 年齢を入れていないキャラ・未成年は作れない
    unknown_age = _character("だれか", None)
    res = client.post("/api/collections", json={"kind": "swimsuit", "character_ids": [adult, unknown_age]})
    assert res.status_code == 422 and "だれか" in res.json()["detail"]
    minor = _character("こども", 12)
    assert client.post("/api/collections", json={"kind": "tease_swimsuit", "character_ids": [minor]}).status_code == 422
