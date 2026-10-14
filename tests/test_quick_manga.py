"""
おまかせ漫画(quick_manga)と題材の候補のテスト。文章モデル・画像生成・外部の作品DBは使わず、差し替えて確かめる。

実行方法:
  uv run pytest tests/test_quick_manga.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, quick_manga  # noqa: E402
from python.quick_manga import _genre_keys, parse_cast, parse_themes, theme_prompts, trends, unique_name  # noqa: E402
from python.routes import quick_manga as routes  # noqa: E402


def test_cast_answer_becomes_character_sheets() -> None:
    answer = {
        "characters": [
            {"name": " 朝霧 ひなた ", "profile": "元気な新人", "appearance": "1girl, orange_hair, short hair", "body": "petite", "outfit": ["apron", "white shirt"]},
            {"name": "朝霧 ひなた", "profile": "重なり", "appearance": "1girl"},
            {"name": "", "appearance": "1boy"},
            {"name": "ポチ", "profile": "犬", "appearance": "dog, animal, brown fur"},
            {"name": "名無し", "appearance": "black hair, glasses"},
            "こわれた",
        ]
    }
    cast = parse_cast(answer, 4)
    assert [c["name"] for c in cast] == ["朝霧 ひなた", "ポチ", "名無し"]
    assert cast[0] == {
        "name": "朝霧 ひなた", "notes": "元気な新人", "appearance_tags": "1girl, orange hair, short hair",
        "body_tags": "petite", "outfit_tags": "apron, white shirt",
    }
    # 動物はそのまま。人数のタグが無い人には 1other を足す
    assert cast[1]["appearance_tags"] == "dog, animal, brown fur" and cast[2]["appearance_tags"].startswith("1other, ")
    assert len(parse_cast(answer, 1)) == 1
    with pytest.raises(ValueError):
        parse_cast({"characters": []}, 2)
    assert unique_name("ひなた", {"ひなた", "ひなた2"}) == "ひなた3" and unique_name("蓮", set()) == "蓮"


def test_themes_and_trends(monkeypatch: pytest.MonkeyPatch) -> None:
    assert parse_themes({"themes": [" 海辺のカフェで… ", "", "雨の日の図書室で…"]}) == ["海辺のカフェで…", "雨の日の図書室で…"]
    with pytest.raises(ValueError):
        parse_themes({"themes": "x"})
    assert parse_themes({"themes": ["実は超常_slapstick_な性格だった话", "学園でSLAPSTICKな騒動。Vtuberも"]}) == [
        "実は超常な性格だった話", "学園でな騒動。Vtuberも"]
    assert _genre_keys("ラブコメ") == (102, "ラブコメ", "Romance") and _genre_keys("ほのぼの") == (302, "ほのぼの", "Slice of Life")
    assert _genre_keys("ファンタジー") == (201, None, "Fantasy") and _genre_keys("なんでも") == (307, "コメディ", "Comedy")

    calls = {"narou": 0}

    def narou(genre_id: int, word=None, limit: int = 50) -> list[str]:
        calls["narou"] += 1
        return ["幼馴染", "社会人"]

    def anilist(genre: str, limit: int = 50) -> list[str]:
        raise RuntimeError("HTTP Error 403")

    monkeypatch.setattr(quick_manga, "narou_keywords", narou)
    monkeypatch.setattr(quick_manga, "anilist_tags", anilist)
    monkeypatch.setattr(quick_manga, "_trend_cache", {})
    found = trends("ラブコメ")
    assert found["narou"] == ["幼馴染", "社会人"] and found["anilist"] == [] and "403" in found["errors"][0]
    # 同じジャンルは取り置きを使う
    trends("ラブコメ")
    assert calls["narou"] == 1
    system, user = theme_prompts("ラブコメ", found)
    assert "特定の作品の筋・人物・設定は使わない" in system and "幼馴染、社会人" in user and "人気の漫画のタグ" not in user
    assert theme_prompts("ラブコメ", None)[1] == "ジャンル: ラブコメ"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    db.get_connection().close()
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/quick-manga/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("終わりませんでした")


def _fake_writer(monkeypatch: pytest.MonkeyPatch) -> dict:
    from python import manga_draft
    from python.routes import manga_v2

    seen: dict = {}

    async def fake_ask(api_key, system, user, *, max_tokens, temperature):
        if "キャラクターデザイナー" in system:
            return {"characters": [
                {"name": "水瀬 しずく", "profile": "総務部", "appearance": "1girl, brown hair", "body": "slim", "outfit": "blouse"},
                {"name": "真島 航", "profile": "営業部", "appearance": "1boy, black hair", "body": "tall", "outfit": "suit"},
            ]}
        if "物語の編集者" in system or "編集者です。指定のジャンル" in system:
            return {"themes": ["会社で…"]}
        raise AssertionError(system[:40])

    async def fake_outlines(api_key, theme, genre, cast, episodes, *, notes="", previous=None, structure=None):
        seen["cast"] = [c.name for c in cast]
        return [{"title": "正体バレ", "logline": "", "episodes": ["一話", "二話"][:episodes]}]

    async def fake_episode(api_key, outline, index, cast, *, notes="", previous_panels=None, structure=None):
        return [{"characters": [cast[0].name], "actions": {}, "lines": [{"speaker": cast[0].name, "kind": "speech", "text": f"{index}話"}],
                 "narration": "", "sfx": [], "prompt_tags": "office", "fixes": []} for _ in range(4)]

    async def fake_reference(api_key, character, count=None):
        seen.setdefault("references", []).append((character["name"], count))
        return {"path": "x", "seed": 1, "score": 0.9, "scores": []}

    async def fake_panels(job, story_id, req, api_key, targets):
        seen["panels"] = (req.template, req.color, req.use_character_reference, len(targets))

    monkeypatch.setattr(quick_manga, "_ask_json", fake_ask)
    monkeypatch.setattr(manga_draft, "generate_outlines", fake_outlines)
    monkeypatch.setattr(manga_draft, "generate_episode", fake_episode)
    monkeypatch.setattr("python.cast.auto_reference", fake_reference)
    monkeypatch.setattr(manga_v2, "_run_panels", fake_panels)
    monkeypatch.setattr(manga_v2, "compose", lambda story_id, req: {"pages": ["p1", "p2"]})
    return seen


def test_quick_manga_makes_cast_story_and_pages(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_writer(monkeypatch)
    conn = db.get_connection()
    try:
        db.save_character(conn, "真島 航", "1boy, existing", None)
    finally:
        conn.close()
    res = client.post("/api/quick-manga", json={"theme": "幼馴染がVtuberだと気づく", "genre": "ラブコメ"}, headers={"Authorization": "Bearer t"})
    job = _wait(client, res.json()["id"])
    assert job["status"] == "done", job
    # 登録済みのキャラと同じ名前は、番号を足して別に作る(書き換えない)
    assert [c["name"] for c in job["characters"]] == ["水瀬 しずく", "真島 航2"]
    conn = db.get_connection()
    try:
        made = {c["name"]: c for c in db.list_characters(conn)}
        assert made["真島 航"]["appearance_tags"] == "1boy, existing"
        assert made["水瀬 しずく"]["body_tags"] == "slim" and made["水瀬 しずく"]["outfit_tags"] == "blouse"
        assert made["水瀬 しずく"]["notes"] == "総務部" and made["水瀬 しずく"]["style_tags"]
        scenes = db.list_story_scenes(conn, job["story_id"])
    finally:
        conn.close()
    assert len(scenes) == 8 and job["title"] == "正体バレ"
    assert seen["references"] == [("水瀬 しずく", 2), ("真島 航2", 2)]
    assert seen["panels"] == ("vertical4", True, False, 8)
    assert "漫画ができました: 正体バレ(2ページ・8コマ)" in job["message"]


def test_quick_manga_with_chosen_cast_can_stop_at_the_script(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_writer(monkeypatch)
    conn = db.get_connection()
    try:
        chosen = db.save_character(conn, "藤堂 蓮", "1boy, glasses", "先輩")
    finally:
        conn.close()
    body = {"theme": "新メニュー", "character_ids": [chosen["id"]], "episodes": 1, "stop_after_script": True, "references": False}
    job = _wait(client, client.post("/api/quick-manga", json=body, headers={"Authorization": "Bearer t"}).json()["id"])
    assert job["status"] == "done" and job["characters"] == [] and "台本ができました" in job["message"]
    assert seen["cast"] == ["蓮"] and "panels" not in seen and "references" not in seen
    missing = {"theme": "x", "character_ids": [999]}
    assert "見つかりません" in _wait(client, client.post("/api/quick-manga", json=missing, headers={"Authorization": "Bearer t"}).json()["id"])["detail"]
    assert client.post("/api/quick-manga", json={"theme": ""}, headers={"Authorization": "Bearer t"}).status_code == 422
    assert client.get("/api/quick-manga/jobs/nope").status_code == 404


def test_theme_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_writer(monkeypatch)
    monkeypatch.setattr(quick_manga, "trends", lambda genre: {"narou": ["社会人"], "anilist": ["Office"], "errors": []})
    res = client.get("/api/quick-manga/themes", params={"genre": "ラブコメ"}, headers={"Authorization": "Bearer t"}).json()
    assert res == {"themes": ["会社で…"], "trends": {"narou": ["社会人"], "anilist": ["Office"], "errors": []}}
    plain = client.get("/api/quick-manga/themes", params={"genre": "ラブコメ", "trends": "false"}, headers={"Authorization": "Bearer t"}).json()
    assert plain["trends"]["narou"] == []
