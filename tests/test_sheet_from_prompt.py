"""
画像生成ページ・メタデータ管理ページの入力をキャラシートに反映するための処理のテスト: プロンプトを容姿・服装・画風に
分けること、新規追加(同じ名前があれば作らない)、上書き(送った項目だけ)。DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_sheet_from_prompt.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.character_sheet import split_prompt  # noqa: E402
from python.image_tagger import BODY_WORDS, CLOTHES_WORDS, POSE_WORDS, _PERSON_WORDS, _is_person_tag  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


def test_prompt_is_split_into_sheet_fields() -> None:
    parts = split_prompt(
        "1girl, solo, black hair, short hair, yellow eyes, brown sweater, sitting, cafe, coffee cup, window, smile, "
        "very aesthetic, masterpiece, no text"
    )
    assert parts == {
        "appearance": "1girl, black hair, short hair, yellow eyes",
        "outfit": "brown sweater",
        "style": "very aesthetic, masterpiece, no text",
        # 表情・ポーズ・場所は場面ごとに変わるので、キャラシートには入れない
        "other": "solo, sitting, cafe, coffee cup, window, smile",
    }


def test_split_keeps_emphasis_and_reads_through_it() -> None:
    parts = split_prompt(
        "girl, {{blonde hair}}, 1.2::red eyes::, [glasses], hair ribbon, -2::hat::, blue_dress, waving"
    )
    # 強調の記号・重みはそのまま残す。身に着けるものは、髪の語が入っていても服装
    assert parts["appearance"] == "girl, {{blonde hair}}, 1.2::red eyes::"
    assert parts["outfit"] == "[glasses], hair ribbon, -2::hat::, blue_dress"
    assert parts["other"] == "waving" and parts["style"] == ""


def test_split_style_and_edge_cases() -> None:
    parts = split_prompt(
        "2girls, park, artist:someone, anime coloring, year 2024, watercolor style, high complexity\nbest quality"
    )
    assert parts["appearance"] == "2girls" and parts["other"] == "park"
    assert (
        parts["style"] == "artist:someone, anime coloring, year 2024, watercolor style, high complexity, best quality"
    )
    assert split_prompt("") == {"appearance": "", "outfit": "", "style": "", "other": ""}
    # 重複は1つにする
    assert split_prompt("long hair, Long Hair, , long hair")["appearance"] == "long hair"


def test_person_words_are_the_same_set_as_before() -> None:
    # 種類ごとに分けただけで、合わせた中身は変えていない(リバースプロンプトの人物の判定に使う)
    assert _PERSON_WORDS == BODY_WORDS | CLOTHES_WORDS | POSE_WORDS
    assert not (BODY_WORDS & CLOTHES_WORDS) and not (BODY_WORDS & POSE_WORDS) and not (CLOTHES_WORDS & POSE_WORDS)
    assert _is_person_tag("long hair") and _is_person_tag("school uniform") and _is_person_tag("hand on own cheek")
    assert not _is_person_tag("classroom")


# ---- API ----


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(story_routes.router)
    return TestClient(app)


def test_split_endpoint(client: TestClient) -> None:
    res = client.post("/api/story/characters/split-prompt", json={"prompt": "1boy, black hair, hoodie, running, park"})
    assert res.status_code == 200, res.text
    assert res.json() == {"appearance": "1boy, black hair", "outfit": "hoodie", "style": "", "other": "running, park"}


def test_new_character_gets_the_sheet_and_never_overwrites(client: TestClient) -> None:
    body = {
        "name": " 水野 あかり ",
        "appearance_tags": "1girl, black hair, short hair",
        "outfit_tags": "brown sweater",
        "style_tags": "very aesthetic, masterpiece",
        "negative_tags": "blurry",
        "seed": 123456789,
    }
    res = client.post("/api/story/characters/new", json=body)
    assert res.status_code == 200, res.text
    created = res.json()
    assert created["name"] == "水野 あかり" and created["appearance_tags"] == "1girl, black hair, short hair"
    assert (created["outfit_tags"], created["style_tags"], created["negative_tags"], created["seed"]) == (
        "brown sweater",
        "very aesthetic, masterpiece",
        "blurry",
        123456789,
    )
    # 同じ名前では作らない(黙って上書きしない)。元のキャラはそのまま
    again = client.post("/api/story/characters/new", json={"name": "水野 あかり", "appearance_tags": "1boy"})
    assert again.status_code == 409 and "既にあります" in again.json()["detail"]
    characters = client.get("/api/story/characters").json()
    assert [(c["name"], c["appearance_tags"]) for c in characters] == [("水野 あかり", "1girl, black hair, short hair")]
    # 名前だけでも作れる。空の名前は作れない
    assert client.post("/api/story/characters/new", json={"name": "名前だけ"}).json()["appearance_tags"] == ""
    assert client.post("/api/story/characters/new", json={"name": "   "}).status_code == 422


def test_overwrite_changes_only_the_fields_sent(client: TestClient) -> None:
    created = client.post(
        "/api/story/characters/new",
        json={"name": "そうた", "appearance_tags": "1boy, black hair", "outfit_tags": "hoodie", "seed": 7},
    ).json()
    res = client.put(
        f"/api/story/characters/{created['id']}/sheet",
        json={"appearance_tags": "1boy, brown hair", "negative_tags": "hat"},
    )
    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["appearance_tags"] == "1boy, brown hair" and updated["negative_tags"] == "hat"
    # 送らなかった項目(服装・基準シード)は変わらない
    assert updated["outfit_tags"] == "hoodie" and updated["seed"] == 7


def test_adult_flag_check_applies_to_new_characters(client: TestClient) -> None:
    res = client.post(
        "/api/story/characters/new",
        json={"name": "だめな例", "appearance_tags": "1girl", "outfit_tags": "school uniform", "is_adult": True},
    )
    assert res.status_code == 422 and "成人キャラにできません" in res.json()["detail"]
    assert client.get("/api/story/characters").json() == []
