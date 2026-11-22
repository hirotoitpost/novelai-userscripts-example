"""
漫画v2: 1コマに候補の絵を何枚か描き、見比べて1枚を選ぶ機能(manga_panel_candidates)のテスト。
画像生成は差し替え、DB は一時フォルダにする。

実行方法:
  uv run pytest tests/test_panel_candidates.py -v
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.models import MangaV2PanelsRequest  # noqa: E402
from python.routes import manga_v2 as manga_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402
from python.routes import story_edit as edit_routes  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_routes, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manga_routes, "_PANEL_DIR", tmp_path / "outputs" / "manga" / "panels")
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(edit_routes.router)
    app.include_router(manga_routes.router)
    return TestClient(app)


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 60), color).save(buf, format="PNG")
    return buf.getvalue()


class _Job:
    message = ""
    progress = 0
    total = 0


def _story(client: TestClient) -> tuple[int, list[dict]]:
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": [{"text": "海"}, {"text": "砂浜"}]}).json()
    conn = db.get_connection()
    try:
        return story["id"], db.list_story_scenes(conn, story["id"])
    finally:
        conn.close()


def _generate(story_id: int, scenes: list[dict], monkeypatch: pytest.MonkeyPatch, **req) -> list[int]:
    seeds: list[int] = []
    colors = iter([(255, 0, 0), (0, 255, 0), (0, 0, 255), (9, 9, 9), (99, 99, 99)])

    async def fake_generate(api_key, prompt, negative, **kwargs):
        seeds.append(kwargs["seed"])
        return _png(next(colors))

    monkeypatch.setattr(manga_routes, "generate_image_v5", fake_generate)
    asyncio.run(manga_routes._run_panels(_Job(), story_id, MangaV2PanelsRequest(**req), "t", scenes))
    return seeds


def test_candidates_leave_the_panel_and_shift_seeds(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    story_id, scenes = _story(client)
    first = scenes[0]
    # まず普通に1枚描く(コマの絵になる)
    _generate(story_id, [first], monkeypatch, settings={"seed": 100})
    conn = db.get_connection()
    try:
        before = db.list_manga_panels(conn, story_id)[0]["image_path"]
    finally:
        conn.close()

    seeds = _generate(story_id, [first], monkeypatch, candidates=3, settings={"seed": 100})
    assert seeds == [100, 100 + 1009, 100 + 2 * 1009]
    conn = db.get_connection()
    try:
        assert db.list_manga_panels(conn, story_id)[0]["image_path"] == before  # コマの絵は変えない
    finally:
        conn.close()
    entries = client.get(f"/api/manga-v2/scenes/{first['id']}/candidates").json()
    assert [e["number"] for e in entries] == [1, 2, 3]
    assert [e["seed"] for e in entries] == seeds
    edit = client.get(f"/api/story/{story_id}/edit").json()
    assert [s["candidates"] for s in edit["scenes"]] == [3, 0]


def test_adopt_swaps_with_the_current_panel(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    story_id, scenes = _story(client)
    first = scenes[0]
    _generate(story_id, [first], monkeypatch, settings={"seed": 5})
    _generate(story_id, [first], monkeypatch, candidates=2, settings={"seed": 7})
    entries = client.get(f"/api/manga-v2/scenes/{first['id']}/candidates").json()

    res = client.post(f"/api/manga-v2/scenes/{first['id']}/candidates/{entries[1]['id']}/adopt")
    assert res.status_code == 200, res.text
    assert res.json()["image_path"] == entries[1]["image_path"] and res.json()["remaining"] == 2
    conn = db.get_connection()
    try:
        panel = db.list_manga_panels(conn, story_id)[0]
    finally:
        conn.close()
    assert panel["image_path"] == entries[1]["image_path"] and panel["seed"] == 7 + 1009
    # 元の絵(seed 5)は候補に戻っている
    after = client.get(f"/api/manga-v2/scenes/{first['id']}/candidates").json()
    assert sorted(e["seed"] for e in after) == [5, 7]

    # 別のシーンの候補の ID では選べない
    other = client.post(f"/api/manga-v2/scenes/{scenes[1]['id']}/candidates/{after[0]['id']}/adopt")
    assert other.status_code == 404

    # 選んで残りを消す
    res = client.post(f"/api/manga-v2/scenes/{first['id']}/candidates/{after[0]['id']}/adopt?discard_others=true")
    assert res.json()["remaining"] == 0


def test_sheet_and_discard(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    story_id, scenes = _story(client)
    first = scenes[0]
    assert client.get(f"/api/manga-v2/scenes/{first['id']}/candidates/sheet").status_code == 404
    _generate(story_id, [first], monkeypatch, settings={"seed": 1})
    _generate(story_id, [first], monkeypatch, candidates=3, settings={"seed": 1})

    res = client.get(f"/api/manga-v2/scenes/{first['id']}/candidates/sheet?tile=120")
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(res.content)) as sheet:
        # 「今」+ 候補3枚 = 4枚を横3枚ずつ → 2段
        assert sheet.width == 3 * 120 + 4 * 8 and sheet.height > 2 * 180

    entries = client.get(f"/api/manga-v2/scenes/{first['id']}/candidates").json()
    assert client.delete(f"/api/manga-v2/scenes/{first['id']}/candidates?candidate_id={entries[0]['id']}").json() == {"removed": 1}
    assert client.delete(f"/api/manga-v2/scenes/{first['id']}/candidates").json() == {"removed": 2}
    assert client.get(f"/api/manga-v2/scenes/{first['id']}/candidates").json() == []


def test_candidates_go_away_with_the_scene(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    story_id, scenes = _story(client)
    _generate(story_id, [scenes[0]], monkeypatch, candidates=2, settings={"seed": 1})
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM story_scenes WHERE id = ?", (scenes[0]["id"],))
        conn.commit()
        assert db.count_panel_candidates(conn, story_id) == {}
    finally:
        conn.close()


def test_as_candidate_draws_one_candidate_with_override_tags(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """対話生成の「対話で描き直す」: 1枚でも候補に入れ、保存していない作画タグ・出てはいけない物で描く。"""
    story_id, scenes = _story(client)
    first = scenes[0]
    _generate(story_id, [first], monkeypatch, settings={"seed": 5})
    prompts: list[tuple[str, str]] = []

    async def fake_generate(api_key, prompt, negative, **kwargs):
        prompts.append((prompt, negative))
        return _png((1, 2, 3))

    monkeypatch.setattr(manga_routes, "generate_image_v5", fake_generate)
    req = MangaV2PanelsRequest(as_candidate=True, prompt_tags="looking back, peace sign", absent_tags="umbrella",
                               settings={"seed": 9})
    asyncio.run(manga_routes._run_panels(_Job(), story_id, req, "t", [first]))
    assert "looking back, peace sign" in prompts[0][0] and "umbrella" in prompts[0][1]
    entries = client.get(f"/api/manga-v2/scenes/{first['id']}/candidates").json()
    assert len(entries) == 1 and entries[0]["seed"] == 9
    conn = db.get_connection()
    try:
        assert db.get_scene(conn, first["id"])["draft_prompt_tags"] != "looking back, peace sign"  # 保存はしない
    finally:
        conn.close()


def test_override_tags_need_a_single_scene(client: TestClient) -> None:
    story_id, _ = _story(client)
    res = client.post(f"/api/manga-v2/{story_id}/panels", json={"prompt_tags": "smile", "skip_existing": False},
                      headers={"Authorization": "Bearer test"})
    assert res.status_code == 422


def test_studio_adopt_saves_tags_and_swaps_panel(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from python.routes import studio as studio_routes

    client.app.include_router(studio_routes.router)
    story_id, scenes = _story(client)
    first = scenes[0]
    _generate(story_id, [first], monkeypatch, settings={"seed": 5})
    _generate(story_id, [first], monkeypatch, as_candidate=True, prompt_tags="peace sign", settings={"seed": 7})
    info = client.get(f"/api/studio/panel/{first['id']}").json()
    assert info["story_id"] == story_id and info["candidates"] == 1 and "cast" not in info
    cand = client.get(f"/api/manga-v2/scenes/{first['id']}/candidates").json()[0]

    res = client.post(f"/api/studio/panel/{first['id']}/adopt",
                      json={"candidate_id": cand["id"], "prompt_tags": "peace sign", "absent_tags": "umbrella"})
    assert res.status_code == 200, res.text
    conn = db.get_connection()
    try:
        scene = db.get_scene(conn, first["id"])
        panel = db.list_manga_panels(conn, story_id)[0]
    finally:
        conn.close()
    assert scene["draft_prompt_tags"] == "peace sign" and scene["absent_tags"] == "umbrella"
    assert panel["image_path"] == cand["image_path"] and not panel["stale"]
    # 前のコマの絵は候補に戻る。同じ候補をもう一度採用しようとすると 404
    assert len(client.get(f"/api/manga-v2/scenes/{first['id']}/candidates").json()) == 1
    assert client.post(f"/api/studio/panel/{first['id']}/adopt", json={"candidate_id": cand["id"]}).status_code == 404
