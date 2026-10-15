"""
未成年キャラの安全対策(minor_guard.py)と、コンテンツガードのパスワード・書き出し/取り込み(guard_password.py、
routes/content_guard.py)のテスト。画像生成は差し替え、DB は一時フォルダにする。

実行方法:
  uv run pytest tests/test_guard_safety.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import content_guard, db, guard_password, minor_guard  # noqa: E402
from python.models import MangaV2PanelsRequest  # noqa: E402
from python.routes import content_guard as guard_routes  # noqa: E402
from python.routes import lora_dataset as dataset_routes  # noqa: E402
from python.routes import manga_v2  # noqa: E402
from python.routes import story as story_routes  # noqa: E402

PASSWORD = "correct-horse"
YURA = {"id": 1, "name": "九条 ゆら", "appearance_tags": "1girl, brown hair", "is_adult": 0, "is_minor": 0}
SEXUAL = "1girl, nude, bedroom"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    content_guard.reset_cache()
    guard_password.reset_state()
    app = FastAPI()
    for router in (guard_routes.router, story_routes.router, dataset_routes.router):
        app.include_router(router)
    yield TestClient(app)
    content_guard.reset_cache()
    guard_password.reset_state()


def _unlock(client: TestClient) -> dict[str, str]:
    if not guard_password.is_set():
        assert client.post("/api/content-guard/password/setup", json={"password": PASSWORD}).status_code == 204
    token = client.post("/api/content-guard/unlock", json={"password": PASSWORD}).json()["token"]
    return {"X-Guard-Token": token}


# ---- キャラの年齢と場面の判定 ----


def test_character_age_from_definition_and_cues(client: TestClient) -> None:
    assert minor_guard.character_age(YURA).state == minor_guard.UNDEFINED
    assert minor_guard.character_age({**YURA, "is_adult": 1}).state == minor_guard.ADULT
    assert minor_guard.character_age({**YURA, "is_minor": 1}).state == minor_guard.MINOR
    # 手がかりは成人の定義より強い: 本文で名前と同じ文に「女子高生」
    text = "九条ゆらは女子高生だ。母は四十歳になる。"
    age = minor_guard.character_age({**YURA, "is_adult": 1}, text)
    assert age.state == minor_guard.MINOR and "女子高生" in age.reasons[0]
    # 名前の出ない文の語(四十歳 は数でも当たらない)は、そのキャラの手がかりにならない
    assert minor_guard.character_age({**YURA, "name": "母"}, "九条ゆらは女子高生だ。").state == minor_guard.UNDEFINED
    assert minor_guard.minor_words("母は四十歳。弟は十二歳、妹は7歳") == ["7歳", "十二歳"]
    # キャラシートのタグとメモ
    assert minor_guard.character_age({**YURA, "is_adult": 1, "outfit_tags": "school uniform"}).state == minor_guard.MINOR
    assert minor_guard.character_age({**YURA, "is_adult": 1, "notes": "中学生の妹"}).state == minor_guard.MINOR


def test_sexual_scene_decisions(client: TestClient) -> None:
    adult = {**YURA, "is_adult": 1}
    assert minor_guard.decide("1girl, cafe", [YURA]).action == minor_guard.OK
    assert minor_guard.decide(SEXUAL, [adult]).action == minor_guard.OK
    assert minor_guard.decide(SEXUAL, [{**YURA, "is_minor": 1}]).action == minor_guard.BLOCK
    # 動作のタグが性的でも性的な場面
    assert minor_guard.decide("1girl, bed", [{**YURA, "is_minor": 1, "action_tags": "sex"}]).action == minor_guard.BLOCK
    # 未定義のキャラ・キャラなしは、既定では止める
    assert minor_guard.decide(SEXUAL, [YURA]).action == minor_guard.BLOCK
    assert minor_guard.decide(SEXUAL, []).action == minor_guard.BLOCK
    content_guard.set_value("safety.undefined_age", "all_ages")
    assert minor_guard.decide(SEXUAL, [YURA, adult]).action == minor_guard.ALL_AGES
    # 未成年がいれば、扱いの設定に関係なく止める
    assert minor_guard.decide(SEXUAL, [{**YURA, "is_minor": 1}]).action == minor_guard.BLOCK
    assert minor_guard.all_ages_tags("nsfw, 1girl, nude, explicit, pussy, bedroom") == "1girl, bedroom"


# ---- 守りが強まるか緩むか ----


def test_change_direction(client: TestClient) -> None:
    minor = content_guard.get("dataset.minor_tags")
    assert content_guard.change_direction("dataset.minor_tags", [*minor, "kodomo test"])["direction"] == "stricter"
    removed = content_guard.change_direction("dataset.minor_tags", minor[1:])
    assert removed["direction"] == "looser" and removed["removed"] == [minor[0].lower()]
    # 性器のタグは減るほど守りが強い
    genital = content_guard.get("scene.genital_tags")
    assert content_guard.change_direction("scene.genital_tags", genital[1:])["direction"] == "stricter"
    assert content_guard.change_direction("library.adult_text_min_hits", 5)["direction"] == "looser"
    assert content_guard.change_direction("library.adult_text_min_hits", 1)["direction"] == "stricter"
    assert content_guard.change_direction("safety.undefined_age", "all_ages")["direction"] == "looser"
    assert content_guard.change_direction("general.safe_negative", "nsfw")["direction"] == "looser"


# ---- パスワード ----


def test_loosening_needs_the_password(client: TestClient) -> None:
    def put(key: str, value: object, headers: dict[str, str] | None = None):
        return client.put(f"/api/content-guard/rules/{key}", json={"value": value}, headers=headers or {})

    minor = content_guard.get("dataset.minor_tags")
    # パスワードがまだ無いあいだは、緩める変更はできない(強める変更・はじめの値に戻すことはできる)
    locked = put("dataset.minor_tags", minor[1:])
    assert locked.status_code == 403 and locked.json()["detail"]["password_set"] is False
    assert put("dataset.minor_tags", [*minor, "kodomo test"]).status_code == 200
    assert client.post("/api/content-guard/reset").status_code == 200
    assert put("safety.undefined_age", "all_ages").status_code == 403
    headers = _unlock(client)
    assert put("safety.undefined_age", "all_ages", headers).status_code == 200
    assert client.get("/api/content-guard/password", headers=headers).json() == {"set": True, "unlocked": True, "min_length": 8}


def test_password_setup_change_and_lockout(client: TestClient) -> None:
    assert client.post("/api/content-guard/password/setup", json={"password": "short"}).status_code == 422
    headers = _unlock(client)
    # 2回目の設定はできない(変えるときは今のパスワード)
    assert client.post("/api/content-guard/password/setup", json={"password": "another-one"}).status_code == 409
    assert client.post("/api/content-guard/password/change", json={"current": "wrong-pass", "new": "another-one"}).status_code == 403
    assert client.post("/api/content-guard/password/change", json={"current": PASSWORD, "new": "another-one"}).status_code == 204
    # 変えると、前に開けた合言葉は使えない
    assert client.get("/api/content-guard/password", headers=headers).json()["unlocked"] is False
    assert client.post("/api/content-guard/unlock", json={"password": PASSWORD}).status_code == 403
    for _ in range(guard_password.MAX_FAILURES - 1):
        client.post("/api/content-guard/unlock", json={"password": "wrong-pass"})
    # 続けて間違えたら、合っていてもしばらく受け付けない
    assert client.post("/api/content-guard/unlock", json={"password": "another-one"}).status_code == 429
    # パスワードそのものは保存しない
    conn = db.get_connection()
    try:
        row = dict(conn.execute("SELECT * FROM guard_secret").fetchone())
    finally:
        conn.close()
    assert "another-one" not in str(row)


# ---- 書き出し・取り込み ----


def test_export_and_import(client: TestClient) -> None:
    data = client.get("/api/content-guard/export").json()
    assert data["rules"]["safety.undefined_age"] == "block" and data["profiles"] == []
    data["rules"]["dataset.minor_tags"] = [*data["rules"]["dataset.minor_tags"], "kodomo test"]
    data["rules"]["nope"] = ["x"]
    data["profiles"] = [{"name": "水着なし", "blocked_tags": ["swimsuit"], "negative_tags": "swimsuit"}]
    plan = client.post("/api/content-guard/import/preview", json={"data": data}).json()
    by_key = {r["key"]: r for r in plan["rules"]}
    assert by_key["dataset.minor_tags"]["direction"] == "stricter" and by_key["dataset.minor_tags"]["added"] == ["kodomo test"]
    assert by_key["nope"]["direction"] == "invalid" and by_key["general.safe_negative"]["direction"] == "same"
    assert plan["profiles"][0]["direction"] == "stricter"
    # 強める変更だけなら、パスワードなしで取り込める
    assert client.post("/api/content-guard/import", json={"data": data}).json() == {"rules": 1, "profiles": 1}
    assert "kodomo test" in content_guard.get("dataset.minor_tags")
    # 緩める変更(語を減らす・扱いを変える)はパスワードが要る。選んだ項目だけ取り込む
    looser = client.get("/api/content-guard/export").json()
    looser["rules"]["safety.undefined_age"] = "all_ages"
    looser["rules"]["dataset.blocked_tags"] = []
    assert client.post("/api/content-guard/import", json={"data": looser}).status_code == 403
    done = client.post(
        "/api/content-guard/import", json={"data": looser, "keys": ["safety.undefined_age"]}, headers=_unlock(client)
    ).json()
    assert done["rules"] == 1 and content_guard.get("safety.undefined_age") == "all_ages" and content_guard.get("dataset.blocked_tags")
    assert client.post("/api/content-guard/import/preview", json={"data": {"rules": {}}}).status_code == 422
    # ガードプロファイルを消すのも緩める変更
    profile_id = client.get("/api/lora-dataset/guards").json()[0]["id"]
    assert client.delete(f"/api/lora-dataset/guards/{profile_id}").status_code == 403


# ---- キャラシートの年齢区分 ----


def test_minor_definition_on_character_sheet(client: TestClient) -> None:
    created = client.post("/api/story/characters/new", json={"name": "九条 ゆら", "appearance_tags": "1girl"}).json()
    sheet = f"/api/story/characters/{created['id']}/sheet"
    marked = client.put(sheet, json={"is_minor": True}).json()
    assert marked["is_minor"] is True and marked["is_adult"] is False
    # 外す・成人にする・別名で外す・消すは、パスワードが要る
    assert client.put(sheet, json={"is_minor": False}).status_code == 403
    assert client.put(sheet, json={"is_adult": True}).status_code == 403
    duplicate = f"/api/story/characters/{created['id']}/duplicate"
    assert client.post(duplicate, json={"name": "ゆら2", "sheet": {"is_minor": False}}).status_code == 403
    copied = client.post(duplicate, json={"name": "ゆら3"}).json()
    assert copied["is_minor"] is True
    assert client.delete(f"/api/story/characters/{created['id']}").status_code == 403
    headers = _unlock(client)
    assert client.put(sheet, json={"is_minor": False}, headers=headers).json()["is_minor"] is False
    ages = {c["name"]: c for c in client.get("/api/content-guard/characters").json()}
    assert ages["ゆら3"]["state"] == "minor" and ages["九条 ゆら"]["state"] == "undefined"


# ---- 生成の流れ ----


def test_panels_skip_blocked_scenes(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "x", 3, 4)
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": "1", "draft_text": "a", "draft_prompt_tags": "1girl, cafe", "novelai_text": "a"},
            {"draft_title": "2", "draft_text": "b", "draft_prompt_tags": SEXUAL, "novelai_text": "b"},
            {"draft_title": "3", "draft_text": "c", "draft_prompt_tags": SEXUAL, "novelai_text": "c"},
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        minor = db.save_character(conn, "ゆら", "1girl")
        db.update_character_sheet(conn, minor["id"], {"is_minor": 1})
        adult = db.save_character(conn, "真理", "1girl, mature female")
        db.update_character_sheet(conn, adult["id"], {"is_adult": 1})
        db.set_scene_characters(conn, scenes[0]["id"], [minor["id"]])
        db.set_scene_characters(conn, scenes[1]["id"], [minor["id"]])
        db.set_scene_characters(conn, scenes[2]["id"], [adult["id"]])
        # 前に描いた絵が残っている
        db.upsert_manga_panel(conn, story["id"], scenes[1]["id"], "old.png", 1, 8, 8)
    finally:
        conn.close()
    prompts: list[str] = []

    async def fake_generate(api_key, prompt, negative, **kwargs):
        prompts.append(prompt)
        return b"png"

    monkeypatch.setattr(manga_v2, "generate_image_v5", fake_generate)
    monkeypatch.setattr(manga_v2, "_PANEL_DIR", tmp_path / "panels")

    class Job:
        message = ""
        progress = 0
        total = 0

    job = Job()
    asyncio.run(manga_v2._run_panels(job, story["id"], MangaV2PanelsRequest(), "t", scenes))
    # 未成年の性的な場面(シーン2)は描かず、前の絵も外す。全年齢の場面と、成人だけの性的な場面は描く
    assert len(prompts) == 2 and "nude" in prompts[1]
    conn = db.get_connection()
    try:
        assert {p["scene_id"] for p in db.list_manga_panels(conn, story["id"])} == {scenes[0]["id"], scenes[2]["id"]}
    finally:
        conn.close()
    assert "シーン2のコマは描きませんでした" in job.guard_note
    events = client.get("/api/content-guard/events").json()
    assert events[0]["action"] == "block" and "ゆら" in events[0]["detail"]


def test_adult_tagging_is_refused_for_stories_with_minor_cues(client: TestClient) -> None:
    options = story_routes.TagOptions(adult=True, api_key="t")
    guarded, reason = story_routes._guarded_options(options, {"id": 1, "raw_text": "ゆらは女子高生だった。", "memory": ""}, "test")
    assert guarded.adult is False and "女子高生" in reason
    kept, none = story_routes._guarded_options(options, {"id": 1, "raw_text": "真理は会社員だった。", "memory": ""}, "test")
    assert kept.adult is True and none == ""
