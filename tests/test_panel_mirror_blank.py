"""
漫画v2: コマの絵の左右反転(元の絵を残して差し替え、もう一度で戻す)と、重ねを白い空白(写真立ての枠の中など)に
合わせる機能、MCP のシーン指定(story_id + scene_index)のテスト。DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_panel_mirror_blank.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, mcp_manga  # noqa: E402
from python.face_sheet import NEGATIVE as FACE_NEGATIVE  # noqa: E402
from python.manga_v2.overlay import blank_regions, mirror_quad, pick_blank  # noqa: E402
from python.routes import manga_v2 as manga_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


def _frame_picture() -> Image.Image:
    """灰色の絵に、白い枠の中(左上寄りの大きい四角と、右下の小さい四角)と、縁に接する白い帯。"""
    image = Image.new("RGB", (400, 300), (120, 120, 120))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 199, 159), fill=(255, 255, 255))
    draw.rectangle((280, 200, 359, 259), fill=(250, 250, 250))
    draw.rectangle((0, 280, 399, 299), fill=(255, 255, 255))  # 縁に接する白(背景の余白)
    return image


def test_blank_regions_find_enclosed_white_rectangles() -> None:
    regions = blank_regions(_frame_picture())
    assert len(regions) == 2
    big, small = regions
    assert big["area"] > small["area"]
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = big["quad"]
    assert abs(x0 - 0.1) < 0.02 and abs(y0 - 40 / 300) < 0.02
    assert abs(x2 - 0.5) < 0.02 and abs(y2 - 160 / 300) < 0.02
    assert x1 > x0 and y3 > y0
    # near に近い方(含むもの)を選ぶ。無ければ一番大きいもの
    assert pick_blank(regions, [0.8, 0.75]) is small
    assert pick_blank(regions, None) is big
    assert pick_blank([], None) is None


def test_mirror_quad_keeps_corner_order() -> None:
    quad = [[0.1, 0.2], [0.4, 0.2], [0.4, 0.6], [0.1, 0.6]]
    assert mirror_quad(quad) == [[0.6, 0.2], [0.9, 0.2], [0.9, 0.6], [0.6, 0.6]]


def test_face_sheet_negative_suppresses_character_sheets() -> None:
    assert "multiple views" in FACE_NEGATIVE and "character sheet" in FACE_NEGATIVE


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_routes, "_PROJECT_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(manga_routes.router)
    return TestClient(app)


def _story(client: TestClient, tmp_path: Path) -> tuple[int, list[int]]:
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": [{"text": "写真"}, {"text": "写真立て"}]}).json()
    ids = [s["id"] for s in story["scenes"]]
    (tmp_path / "outputs").mkdir(exist_ok=True)
    left_red = Image.new("RGB", (400, 300), (120, 120, 120))
    ImageDraw.Draw(left_red).rectangle((0, 0, 9, 9), fill=(255, 0, 0))
    left_red.save(tmp_path / "outputs" / "photo.png")
    _frame_picture().save(tmp_path / "outputs" / "frame.png")
    conn = db.get_connection()
    try:
        db.upsert_manga_panel(conn, story["id"], ids[0], "outputs/photo.png", 7, 400, 300)
        db.upsert_manga_panel(conn, story["id"], ids[1], "outputs/frame.png", 8, 400, 300)
    finally:
        conn.close()
    return story["id"], ids


def test_mirror_keeps_the_original_and_toggles_back(client: TestClient, tmp_path: Path) -> None:
    story_id, (photo, frame) = _story(client, tmp_path)
    quad = [[0.1, 0.1], [0.3, 0.1], [0.3, 0.3], [0.1, 0.3]]
    client.put(f"/api/manga-v2/scenes/{photo}/overlays", json={"overlays": [{"kind": "text", "text": "名", "quad": quad}]})

    res = client.post(f"/api/manga-v2/scenes/{photo}/mirror")
    assert res.status_code == 200, res.text
    assert res.json() == {"image_path": "outputs/photo.mirrored.png", "mirrored": True}
    with Image.open(tmp_path / "outputs" / "photo.mirrored.png") as image:
        assert image.getpixel((395, 5)) == (255, 0, 0)  # 赤い角が右へ
    with Image.open(tmp_path / "outputs" / "photo.png") as image:
        assert image.getpixel((5, 5)) == (255, 0, 0)  # 元の絵は残る
    conn = db.get_connection()
    try:
        panel = next(p for p in db.list_manga_panels(conn, story_id) if p["scene_id"] == photo)
    finally:
        conn.close()
    assert panel["image_path"] == "outputs/photo.mirrored.png" and panel["seed"] == 7
    overlay = client.get(f"/api/manga-v2/scenes/{photo}/overlays").json()[0]
    assert overlay["quad"] == mirror_quad(quad)

    # もう一度で元に戻る(重ねも戻る)
    assert client.post(f"/api/manga-v2/scenes/{photo}/mirror").json() == {"image_path": "outputs/photo.png", "mirrored": False}
    back = client.get(f"/api/manga-v2/scenes/{photo}/overlays").json()[0]["quad"]
    assert all(abs(a - b) < 1e-9 for p, q in zip(back, quad) for a, b in zip(p, q))


def test_overlay_without_quad_fits_the_blank(client: TestClient, tmp_path: Path) -> None:
    _, (photo, frame) = _story(client, tmp_path)
    regions = client.get(f"/api/manga-v2/scenes/{frame}/blank-regions").json()
    assert len(regions) == 2
    res = client.put(f"/api/manga-v2/scenes/{frame}/overlays", json={"overlays": [
        {"kind": "panel", "scene_id": photo},
        {"kind": "text", "text": "2026", "near": [0.8, 0.75]},
    ]})
    assert res.status_code == 200, res.text
    first, second = res.json()
    assert first["quad"] == regions[0]["quad"] and second["quad"] == regions[1]["quad"]
    assert "near" not in second
    # 空白の無い絵では、四隅を指定するよう知らせる
    res = client.put(f"/api/manga-v2/scenes/{photo}/overlays", json={"overlays": [{"kind": "text", "text": "x"}]})
    assert res.status_code == 422


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


def test_mcp_tools_take_story_and_scene_index(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, Any]] = []
    story = {"scenes": [{"id": 40, "scene_index": 0}, {"id": 41, "scene_index": 1}]}

    async def fake_call(method: str, path: str, body: Any = None, timeout: float = 120) -> Any:
        calls.append((method, path, body))
        if path == "/api/story/5":
            return story
        if path.endswith("/mirror"):
            return {"image_path": "outputs/x.mirrored.png", "mirrored": True}
        if path.endswith("/upscale"):
            return {"upscaled_path": "outputs/x.upscaled.png"}
        return {}

    monkeypatch.setattr(mcp_manga, "_call", fake_call)
    mcp = _FakeMCP()
    mcp_manga.register_manga_tools(mcp)  # type: ignore[arg-type]
    tools = mcp.tools

    asyncio.run(tools["manga_mirror_panel"](story_id=5, scene_index=1))
    assert calls[-1][:2] == ("POST", "/api/manga-v2/scenes/41/mirror")
    asyncio.run(tools["manga_update_scene"](story_id=5, scene_index=0, prompt_tags="x"))
    assert calls[-1] == ("PATCH", "/api/story/scenes/40", {"prompt_tags": "x"})
    # 食い違い・範囲外・物語の外のシーンは書き換えない
    for kwargs in ({"scene_id": 99, "story_id": 5, "scene_index": 0}, {"story_id": 5, "scene_index": 7},
                   {"scene_id": 99, "story_id": 5}, {"scene_index": 0}, {}):
        with pytest.raises(ValueError):
            asyncio.run(tools["manga_update_scene"](prompt_tags="y", **kwargs))
    assert all(body != {"prompt_tags": "y"} for _, _, body in calls)
    # scene_id だけなら、そのまま(これまでどおり)
    asyncio.run(tools["manga_upscale_panel"](12))
    assert calls[-1][:2] == ("POST", "/api/manga-v2/scenes/12/upscale")
