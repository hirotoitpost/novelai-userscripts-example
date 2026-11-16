"""
漫画の読み上げ(/api/manga-voice): 台本の並び・話し手の声・心の声・コマにいない人のセリフ・ナレーション・効果音・
声の割り当て。VOICEVOX には接続しない(話者の一覧を差し替える)。

実行方法:
  uv run pytest tests/test_manga_voice.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, voice  # noqa: E402
from python.routes import manga_voice  # noqa: E402
from python.routes.manga_voice import _read_text  # noqa: E402
from python.routes.story import router as story_router  # noqa: E402

VOICES = [
    {"id": 8, "name": "春日部つむぎ", "style": "ノーマル"},
    {"id": 2, "name": "四国めたん", "style": "ノーマル"},
    {"id": 37, "name": "四国めたん", "style": "ささやき"},
    {"id": 11, "name": "玄野武宏", "style": "ノーマル"},
    {"id": 14, "name": "冥鳴ひまり", "style": "ノーマル"},
]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(voice, "safe_speakers", lambda: VOICES)
    app = FastAPI()
    app.include_router(story_router)
    app.include_router(manga_voice.router)
    with TestClient(app) as test_client:
        yield test_client


def _story(client: TestClient) -> tuple[int, int, int]:
    conn = db.get_connection()
    try:
        girl = db.save_character(conn, "ちひろ", "1girl, milk tea hair")
        boy = db.save_character(conn, "陽太", "1boy, black hair")
    finally:
        conn.close()
    scenes = [
        {"text": "「おはよう」", "narration": "朝。", "prompt_tags": "1girl", "character_ids": [girl["id"]]},
        {"text": "（眠い……）", "prompt_tags": "1girl", "character_ids": [girl["id"]], "sfx": ["ドキッ"],
         "dialogue": [{"text": "（眠い……）", "speaker_id": girl["id"]}]},
        # 陽太はコマにいない: 合成ではナレーションの枠、読むときは陽太の声
        {"text": "「起きてる?」", "prompt_tags": "1girl", "character_ids": [girl["id"]],
         "dialogue": [{"text": "「起きてる?」", "speaker_id": boy["id"]}]},
    ]
    story = client.post("/api/story/scripted", json={"title": "朝", "scenes": scenes}).json()
    # 台本から作るとコマにいない話し手は外れるので、編集画面で決めたのと同じ形で入れ直す
    conn = db.get_connection()
    try:
        last = db.list_story_scenes(conn, story["id"])[2]
        db.update_scene_dialogue(conn, last["id"], [{"text": "「起きてる?」", "speaker_id": boy["id"]}])
    finally:
        conn.close()
    return story["id"], girl["id"], boy["id"]


def test_read_text_drops_brackets_and_leading_pause() -> None:
    assert _read_text("「……ウエストは、ちょっと」") == "ウエストは、ちょっと"
    assert _read_text("「『ちひろ、何してんの笑』……送る相手!!」") == "ちひろ、何してんの笑、送る相手!!"
    assert _read_text("（眠い……）") == "眠い"


def test_readout_order_and_voices(client: TestClient) -> None:
    story_id, girl, boy = _story(client)
    data = client.get(f"/api/manga-voice/{story_id}").json()
    kinds = [(i["scene_index"], i["kind"], i["element"]) for i in data["items"]]
    assert kinds == [(0, "narration", "narration:0"), (0, "line", "bubble:0"), (1, "thought", "bubble:0"),
                     (2, "line", "narration:0")]
    narration, hello, thought, offpanel = data["items"]
    # ナレーションは既定のナレーターの声(冥鳴ひまり)
    assert narration["speaker"] == 14 and data["narrator"]["speaker_name"] == "冥鳴ひまり"
    # 声を決めていない女性キャラ・男性キャラは、それぞれの候補から
    assert hello["character_id"] == girl and hello["speaker"] in (8, 2)
    assert offpanel["character_id"] == boy and offpanel["speaker"] == 11
    assert thought["read"] == "眠い"
    assert sorted(data["credits"]) == sorted({f"VOICEVOX:{v['name']}" for v in VOICES if v["id"] in
                                              {i["speaker"] for i in data["items"]}})
    # 効果音は選んだときだけ
    with_sfx = client.get(f"/api/manga-voice/{story_id}?sfx=true").json()
    assert [i["text"] for i in with_sfx["items"] if i["kind"] == "sfx"] == ["ドキッ"]


def test_assign_voices(client: TestClient) -> None:
    story_id, girl, _ = _story(client)
    assert client.put(f"/api/manga-voice/characters/{girl}", json={"speaker": 2, "speed": 1.1, "pitch": 0.05}).status_code == 200
    assert client.put(f"/api/manga-voice/{story_id}/narrator", json={"speaker": 8}).status_code == 200
    data = client.get(f"/api/manga-voice/{story_id}").json()
    hello = data["items"][1]
    assert (hello["speaker"], hello["speed"], hello["pitch"]) == (2, 1.1, 0.05)
    # 心の声は同じ話者のささやきのスタイル
    assert data["items"][2]["speaker"] == 37
    assert data["items"][0]["speaker"] == 8 and data["narrator"]["assigned"]
    cast = {c["id"]: c for c in data["cast"]}
    assert cast[girl]["assigned"] and cast[girl]["speaker_name"] == "四国めたん"
    # 既定の声に戻す
    client.put(f"/api/manga-voice/characters/{girl}", json={"speaker": None})
    assert not {c["id"]: c for c in client.get(f"/api/manga-voice/{story_id}").json()["cast"]}[girl]["assigned"]
    assert client.put("/api/manga-voice/characters/99999", json={"speaker": 2}).status_code == 404
