"""
R18 のパターン(adult_patterns.py と /api/adult-patterns)のテスト。判定モデル・NovelAI は呼ばず、差し替える。

実行方法:
  uv run pytest tests/test_adult_patterns.py -v
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import adult_patterns, content_guard, db  # noqa: E402
from python.image_tagger import TagScore  # noqa: E402
from python.routes import adult_patterns as pattern_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(pattern_routes, "_ROOT", tmp_path)
    content_guard.reset_cache()
    app = FastAPI()
    app.include_router(pattern_routes.router)
    app.include_router(story_routes.router)
    return TestClient(app)


def _png() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_patterns_are_seeded_and_checked(client: TestClient) -> None:
    data = client.get("/api/adult-patterns").json()
    assert [k["key"] for k in data["kinds"]] == ["play", "scenario"]
    plays = data["patterns"]["play"]
    assert any(p["name"] == "正常位" and "ぱんっ" in p["sfx"] for p in plays)
    assert data["patterns"]["scenario"]
    body = {"kind": "play", "name": "膝枕", "tags": "lap pillow", "sfx": "ぽふ", "lines": "おやすみ"}
    made = client.post("/api/adult-patterns", json=body).json()
    assert made["name"] == "膝枕" and made["images"] == []
    assert client.post("/api/adult-patterns", json=body).status_code == 409
    # 未成年を思わせる語(タグ・本文の年齢)は入れられない。成人の衣装の制服は入れられる
    for bad in ({"tags": "loli, sex"}, {"description": "中学生の二人"}, {"lines": "16歳だよ"}):
        assert client.post("/api/adult-patterns", json={"kind": "play", "name": "x", **bad}).status_code == 422, bad
    assert client.post("/api/adult-patterns", json={"kind": "play", "name": "制服", "tags": "school uniform, sex"}).status_code == 200
    assert client.post("/api/adult-patterns", json={"kind": "nope", "name": "x"}).status_code == 422
    # 書き換え・消す
    res = client.put(f"/api/adult-patterns/{made['id']}", json={**body, "sfx": "ぽふっ"})
    assert res.json()["sfx"] == "ぽふっ"
    assert client.put(f"/api/adult-patterns/{made['id']}", json={**body, "kind": "scenario"}).status_code == 422
    assert client.delete(f"/api/adult-patterns/{made['id']}").json() == {"deleted": True}


def test_reference_images_add_tags_and_refuse_minor_cues(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import image_tagger

    play = next(p for p in client.get("/api/adult-patterns").json()["patterns"]["play"] if p["name"] == "正常位")
    url = f"/api/adult-patterns/{play['id']}/images"
    scores = [
        TagScore("sex", 0.9, "general"), TagScore("legs up", 0.8, "general"), TagScore("brown hair", 0.9, "general"),
        TagScore("on bed", 0.6, "general"), TagScore("explicit", 0.9, "rating"),
    ]
    monkeypatch.setattr(image_tagger, "tag_image", lambda image: scores)
    first = client.post(url, json={"data": _png()}).json()["images"][0]
    # 構図・行為のタグだけ(人物の見た目・評価は入れない)
    assert first["tags"] == "sex, legs up, on bed"
    assert (tmp_path / first["path"]).is_file()
    monkeypatch.setattr(image_tagger, "tag_image", lambda image: [*scores[:1], TagScore("cowgirl position", 0.8, "general")])
    images = client.post(url, json={"data": _png()}).json()["images"]
    assert len(images) == 2
    # 使うたびに1枚ずつ順に、画像のタグを足す
    pattern = {**play, "images": images}
    assert "legs up" in adult_patterns.play_tags(pattern, 0) and "cowgirl position" in adult_patterns.play_tags(pattern, 1)
    # 未成年の手がかりが出た画像は受け付けず、保存もしない
    monkeypatch.setattr(image_tagger, "tag_image", lambda image: [TagScore("child", 0.4, "general")])
    res = client.post(url, json={"data": _png()})
    assert res.status_code == 422 and "child" in res.json()["detail"]
    assert len(list((tmp_path / "outputs" / "adult_patterns" / str(play["id"])).glob("*.png"))) == 2
    # タグを手で直す・消す(ファイルも消える)
    assert client.put(f"{url}/{first['id']}", json={"tags": "loli"}).status_code == 422
    assert client.put(f"{url}/{first['id']}", json={"tags": "legs up"}).json()["images"][0]["tags"] == "legs up"
    client.delete(f"{url}/{first['id']}")
    assert not (tmp_path / first["path"]).is_file()
    scenario = client.get("/api/adult-patterns").json()["patterns"]["scenario"][0]
    assert client.post(f"/api/adult-patterns/{scenario['id']}/images", json={"data": _png()}).status_code == 422


def test_draft_notes_and_sfx_vocabulary() -> None:
    play = {"kind": "play", "name": "正常位", "tags": "sex, missionary", "sfx": "ぱんっ\nずちゅ", "lines": "あっ", "images": []}
    scenario = {"kind": "scenario", "name": "再会", "tags": "", "description": "再会 → 告白", "sfx": "", "lines": "好き", "images": []}
    notes = adult_patterns.draft_notes([scenario, play])
    assert "再会 → 告白" in notes and "ぱんっ、ずちゅ" in notes and "sex, missionary" in notes
    assert adult_patterns.sfx_vocabulary([play, scenario], "bedroom, missionary") == ["ぱんっ", "ずちゅ"]
    assert adult_patterns.sfx_vocabulary([play], "cafe") == []


def test_scene_apply_only_for_adult_casts(client: TestClient) -> None:
    play = next(p for p in client.get("/api/adult-patterns").json()["patterns"]["play"] if p["name"] == "騎乗位")
    conn = db.get_connection()
    try:
        adult = db.save_character(conn, "大人", "1girl", None)
        db.update_character_sheet(conn, adult["id"], {"age": 25, "is_adult": 1})
        nobody = db.save_character(conn, "年齢なし", "1girl", None)
        db.update_character_sheet(conn, nobody["id"], {"is_adult": 1})
        story = db.create_story(conn, "夜", 1)
        db.add_story_scenes(conn, story["id"], [{"draft_title": None, "draft_text": "x", "draft_prompt_tags": "bedroom"}])
        scene = db.list_story_scenes(conn, story["id"])[0]
    finally:
        conn.close()
    url = f"/api/story/scenes/{scene['id']}/adult-pattern"
    # 登場キャラがいない・年齢の入っていないキャラがいるシーンでは使えない(成人フラグだけでは足りない)
    assert client.post(url, json={"pattern_id": play["id"]}).status_code == 422
    for cast, status in (([adult["id"], nobody["id"]], 422), ([adult["id"]], 200)):
        conn = db.get_connection()
        try:
            db.set_scene_characters(conn, scene["id"], cast)
        finally:
            conn.close()
        res = client.post(url, json={"pattern_id": play["id"]})
        assert res.status_code == status, res.text
    assert "cowgirl position" in res.json()["tags"]
    conn = db.get_connection()
    try:
        saved = db.get_scene(conn, scene["id"])
    finally:
        conn.close()
    # シーンのタグに足し、描き文字が無ければ最初の1つを入れる
    assert saved["draft_prompt_tags"].startswith("bedroom, sex, cowgirl position") and "ぱちゅっ" in saved["sfx"]
    assert client.post(url, json={"pattern_id": 999}).status_code == 404


def test_draft_dataset_and_import_uses(client: TestClient) -> None:
    from fastapi import HTTPException

    from python import manga_draft
    from python.character_sheet import VariationShot
    from python.routes import lora_dataset, manga_draft as draft_routes, manga_import

    patterns = client.get("/api/adult-patterns").json()["patterns"]
    play = next(p for p in patterns["play"] if p["name"] == "後背位")
    kiss = next(p for p in patterns["play"] if p["name"] == "キス")
    conn = db.get_connection()
    try:
        adult = db.save_character(conn, "大人", "1girl", None)
        db.update_character_sheet(conn, adult["id"], {"age": 30, "is_adult": 1})
        adult = db.get_character(conn, adult["id"])
        young = db.save_character(conn, "年齢なし", "1girl", None)
    finally:
        conn.close()

    # 漫画ドラフト: 成人キャラだけで使え、そのあいだは成人向けの決まりを入れる
    with pytest.raises(HTTPException):
        draft_routes._adult_patterns([play["id"]], [adult["id"], young["id"]])
    assert draft_routes._adult_patterns([play["id"]], [adult["id"]])[0]["name"] == "後背位"
    assert draft_routes._adult_patterns([], [young["id"]]) == []
    token = manga_draft._ADULT_RULES.set(True)
    try:
        assert "18歳以上" in manga_draft._content_rule()
    finally:
        manga_draft._ADULT_RULES.reset(token)
    assert "全年齢" in manga_draft._content_rule()

    # キャラ別データセット: R18 で成人キャラだけ。1枚ごとに順にプレイを足す
    shots = [VariationShot(stem=f"{i}", prompt="kujo, 1girl") for i in range(3)]
    with pytest.raises(HTTPException):
        lora_dataset._with_plays(shots, [play["id"]], adult, False)
    with pytest.raises(HTTPException):
        lora_dataset._with_plays(shots, [play["id"]], {**adult, "age": None}, True)
    planned = lora_dataset._with_plays(shots, [play["id"], kiss["id"]], adult, True)
    assert "doggystyle" in planned[0].prompt and "french kiss" in planned[1].prompt and "doggystyle" in planned[2].prompt
    assert planned[0].situation == "後背位"

    # 取り込んだ R18 の作品: 場面のタグがプレイと重なるコマの、読みの無い描き文字に、プレイの描き文字を候補として足す
    pages = [{"panels": [
        {"scene_tags": ["bedroom"], "action_tags": ["doggystyle"], "sfx": [{"box": [0, 0, 1, 1], "reading": ""}]},
        {"scene_tags": ["cafe"], "action_tags": [], "sfx": [{"box": [0, 0, 1, 1], "reading": ""}]},
    ]}]
    suggested = manga_import._pattern_suggestions({"adult": 1}, pages, {"0-0-0": ["どん"]})
    assert suggested["0-0-0"][0] == "どん" and "ぱんっぱんっ" in suggested["0-0-0"] and "0-1-0" not in suggested
    assert manga_import._pattern_suggestions({"adult": 0}, pages, {}) == {}
