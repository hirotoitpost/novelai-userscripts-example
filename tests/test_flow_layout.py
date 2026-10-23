"""
漫画v2: 話のつながりの見直し(場面・話題の飛びに入れる、つなぎのコマの案)と、コマの絵の縦横比に合うコマ割りの
候補のテスト。文章AIは呼ばず、答えを差し替える。DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_flow_layout.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, mcp_manga, panel_check  # noqa: E402
from python.client import get_client  # noqa: E402
from python.manga_v2.layout import TEMPLATE_TREES, aspect_layouts, aspect_mismatch, frame_aspects  # noqa: E402
from python.routes import manga_v2 as manga_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402

PANELS = [{"characters": ["ゆら"]}, {"characters": ["ゆら"]}, {"characters": []}, {"characters": ["ゆら"]}]


def test_apply_flow_keeps_only_valid_bridges() -> None:
    data = {"bridges": [
        {"after": 2, "reason": "家へ移る", "panels": [
            {"text": "夕方、家に着いた。", "narration": "その日の夕方", "prompt_tags": "house, sunset, scenery", "characters": ["ゆら", "誰か"]},
            {"text": "", "narration": ""},  # 中身が無い
            {"text": "3つ目は入れない"},
        ]},
        {"after": 2, "panels": [{"text": "同じ場所の2つ目の案"}]},  # 同じ場所は1つ
        {"after": 4, "panels": [{"text": "最後のコマの後ろ"}]},  # つなぎではない
        {"after": 0, "panels": [{"text": "範囲外"}]},
        {"after": "x", "panels": [{"text": "番号でない"}]},
        {"after": 1, "panels": [{"text": "「おはよう」", "prompt_tags": "classroom, looking at another"}]},
    ]}
    bridges = panel_check.apply_flow(data, PANELS, {"ゆら": 7})
    assert [b["after_index"] for b in bridges] == [0, 1]
    first = bridges[1]
    assert first["reason"] == "家へ移る" and len(first["scenes"]) == 1
    scene = first["scenes"][0]
    assert scene["character_ids"] == [7] and scene["narration"] == "その日の夕方" and "sunset" in scene["prompt_tags"]
    with pytest.raises(ValueError):
        panel_check.apply_flow({"oops": []}, PANELS, {})
    assert panel_check.apply_flow({"bridges": []}, PANELS, {}) == []


def test_flow_prompt_lists_every_panel() -> None:
    system, user = panel_check.flow_prompts("シュシュをなくす話", [
        {"characters": ["ゆら"], "lines": [{"speaker": "ゆら", "text": "ない！"}], "narration": "", "text": "", "prompt_tags": "park"},
        {"characters": [], "lines": [], "narration": "翌朝", "text": "", "prompt_tags": "room"},
    ], limit=3)
    assert "多くて3か所" in system
    assert "1コマ目: 人物 ゆら" in user and "ゆら「ない！」" in user and "2コマ目: 人物 なし | ナレーション 翌朝" in user


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_routes, "_PROJECT_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(manga_routes.router)
    app.dependency_overrides[get_client] = lambda: SimpleNamespace(api_key="k")
    return TestClient(app)


def test_review_flow_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    yura = client.post("/api/story/characters/new", json={"name": "ゆら", "appearance_tags": "1girl"}).json()["id"]
    story = client.post("/api/story/scripted", json={"title": "シュシュ", "scenes": [
        {"text": "「行ってきます」とゆらが言った。", "character_ids": [yura]},
        {"text": "公園。", "character_ids": [yura], "narration": "放課後"},
    ]}).json()
    seen: dict[str, Any] = {}

    async def fake_ask(api_key: str, system: str, user: str, **_: Any) -> Any:
        seen["user"] = user
        return {"bridges": [{"after": 1, "reason": "学校から公園", "panels": [
            {"text": "通学路を歩く。", "prompt_tags": "street, walking", "characters": ["ゆら"]}]}]}

    monkeypatch.setattr(panel_check, "_ask_json", fake_ask)
    res = client.post(f"/api/manga-v2/{story['id']}/review-flow", json={})
    assert res.status_code == 200, res.text
    suggestion = res.json()["suggestions"][0]
    assert suggestion["after_index"] == 0 and suggestion["scenes"][0]["character_ids"] == [yura]
    assert "1コマ目: 人物 ゆら" in seen["user"] and "行ってきます" in seen["user"] and "放課後" in seen["user"]

    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(panel_check, "_ask_json", broken)
    assert client.post(f"/api/manga-v2/{story['id']}/review-flow", json={}).status_code == 502


def test_mcp_review_flow_inserts_from_the_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, Any]] = []
    scene = {"title": None, "text": "", "prompt_tags": "", "narration": None, "sfx": [], "page_index": 0,
             "characters": [], "panel_size": None, "dialogue": [], "dialogue_custom": False}
    edit = {"title": "t", "panels_per_page": 4, "custom_pages": False,
            "scenes": [{**scene, "id": i, "title": f"s{i}"} for i in range(4)]}
    suggestions = [
        {"after_index": 0, "reason": "a", "scenes": [{"text": "A1"}, {"text": "A2"}]},
        {"after_index": 2, "reason": "b", "scenes": [{"text": "B1"}]},
    ]

    async def fake_call(method: str, path: str, body: Any = None, timeout: float = 120) -> Any:
        calls.append((method, path, body))
        if path.endswith("/review-flow"):
            return {"suggestions": suggestions}
        if method == "GET" and path.endswith("/edit"):
            return edit
        return {"stale_panels": 0}

    monkeypatch.setattr(mcp_manga, "_call", fake_call)

    class FakeMCP:
        tools: dict[str, Any] = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    mcp = FakeMCP()
    mcp_manga.register_manga_tools(mcp)  # type: ignore[arg-type]
    result = asyncio.run(mcp.tools["manga_review_flow"](3, apply=True))
    put = next(body for method, path, body in calls if method == "PUT")
    assert [s.get("title") or s["text"] for s in put["scenes"]] == ["s0", "A1", "A2", "s1", "s2", "B1", "s3"]
    assert result["inserted_scene_indexes"] == [1, 2, 5]
    # apply しなければ書き込まない
    calls.clear()
    asyncio.run(mcp.tools["manga_review_flow"](3))
    assert all(method != "PUT" for method, _, _ in calls)


def test_aspect_layouts_fit_landscape_pictures_better_than_vertical_strips() -> None:
    pictures = [1216 / 832] * 4
    strips = aspect_mismatch(frame_aspects(TEMPLATE_TREES["vertical4"]), pictures)
    best = aspect_layouts(pictures)
    assert strips > 0.5  # 縦4段の細い枠は、横長の絵の半分ほどを切り落とす
    assert best[0]["mismatch"] < 0.15 and best[0]["mismatch"] <= best[-1]["mismatch"]
    # 縦長の絵が4枚なら、2段2列がほぼそのまま入る
    portrait = aspect_layouts([832 / 1216] * 4)[0]
    assert portrait["id"] == "fit-2-2" and portrait["mismatch"] < 0.05


def test_layout_suggestions_endpoint(client: TestClient) -> None:
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": [{"text": f"{i}"} for i in range(5)]}).json()
    conn = db.get_connection()
    try:
        for scene in story["scenes"]:
            db.upsert_manga_panel(conn, story["id"], scene["id"], f"outputs/{scene['id']}.png", 1, 1216, 832)
    finally:
        conn.close()
    res = client.get(f"/api/manga-v2/{story['id']}/layout-suggestions?template=vertical4")
    assert res.status_code == 200, res.text
    pages = res.json()["pages"]
    assert [len(p["scene_ids"]) for p in pages] == [4, 1]
    first = pages[0]
    assert first["current_mismatch"] > 0.5 and first["suggestions"][0]["mismatch"] < first["current_mismatch"]
    # 候補の木は、そのままページのコマ割りに保存できる
    tree = first["suggestions"][0]["tree"]
    saved = client.put(f"/api/manga-v2/{story['id']}/page-layout", json={"template": "vertical4", "pages": [{"tree": tree}]})
    assert saved.status_code == 200, saved.text
