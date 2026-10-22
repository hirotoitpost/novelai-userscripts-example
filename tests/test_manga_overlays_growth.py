"""
漫画v2: コマの絵に重ねるもの(文字・別のコマの絵を四隅に合わせて貼る)と、巻ごとの成長段階のテスト。
DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_manga_overlays_growth.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.manga_v2.overlay import OverlayPiece, draw_pieces, fit_aspect, paste_warped  # noqa: E402
from python.routes import manga_v2 as manga_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_routes, "_PROJECT_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(manga_routes.router)
    return TestClient(app)


def test_warp_fills_the_quad_only() -> None:
    target = Image.new("RGB", (100, 100), (255, 255, 255))
    red = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    paste_warped(target, red, [(20, 20), (80, 30), (70, 80), (30, 70)])
    assert target.getpixel((50, 50)) == (255, 0, 0)  # 四隅の内側
    assert target.getpixel((5, 5)) == (255, 255, 255)  # 外側
    assert target.getpixel((22, 60)) == (255, 255, 255)  # 斜めの辺の外側


def test_overlay_follows_the_picture_coordinates() -> None:
    picture = Image.new("RGB", (200, 100), (255, 255, 255))
    piece = OverlayPiece(Image.new("RGBA", (4, 4), (0, 0, 255, 255)), ((0.5, 0.5), (1.0, 0.5), (1.0, 1.0), (0.5, 1.0)))
    draw_pieces(picture, [piece], lambda x, y: (x * 200, y * 100))
    assert picture.getpixel((150, 75)) == (0, 0, 255) and picture.getpixel((50, 25)) == (255, 255, 255)


def test_fit_aspect_keeps_text_shape() -> None:
    wide = Image.new("RGBA", (200, 50), (0, 0, 0, 255))
    fitted = fit_aspect(wide, ((0, 0), (1, 0), (1, 1), (0, 1)))  # 正方形の四隅
    assert fitted.size == (200, 200)


def _story_with_panels(client: TestClient, tmp_path: Path) -> tuple[int, list[int]]:
    res = client.post("/api/story/scripted", json={"title": "t", "scenes": [{"text": "写真"}, {"text": "写真立て"}]})
    story = res.json()
    ids = [s["id"] for s in story["scenes"]]
    (tmp_path / "outputs").mkdir(exist_ok=True)
    conn = db.get_connection()
    try:
        for color, scene_id in zip(((0, 200, 0), (240, 240, 240)), ids):
            path = tmp_path / "outputs" / f"p{scene_id}.png"
            Image.new("RGB", (100, 100), color).save(path)
            db.upsert_manga_panel(conn, story["id"], scene_id, f"outputs/p{scene_id}.png", 1, 100, 100)
    finally:
        conn.close()
    return story["id"], ids


def test_overlays_are_saved_checked_and_previewed(client: TestClient, tmp_path: Path) -> None:
    _, (photo, frame) = _story_with_panels(client, tmp_path)
    quad = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]]
    res = client.put(f"/api/manga-v2/scenes/{frame}/overlays", json={"overlays": [{"kind": "panel", "scene_id": photo, "quad": quad}]})
    assert res.status_code == 200, res.text
    preview = client.get(f"/api/manga-v2/scenes/{frame}/overlay-preview")
    assert preview.status_code == 200
    from io import BytesIO

    with Image.open(BytesIO(preview.content)) as image:
        assert image.convert("RGB").getpixel((50, 50)) == (0, 200, 0)  # 写真(緑のコマ)が四隅の中に
        assert image.convert("RGB").getpixel((5, 5)) == (240, 240, 240)
    # 自分自身・ほかの物語のコマは写真にできない。文字は空にできない
    bad = client.put(f"/api/manga-v2/scenes/{frame}/overlays", json={"overlays": [{"kind": "panel", "scene_id": frame, "quad": quad}]})
    assert bad.status_code == 422
    assert client.put(f"/api/manga-v2/scenes/{frame}/overlays", json={"overlays": [{"kind": "text", "text": " ", "quad": quad}]}).status_code == 422
    assert client.get(f"/api/manga-v2/scenes/{frame}/overlays").json()[0]["scene_id"] == photo


def test_growth_stage_replaces_the_look(client: TestClient) -> None:
    base = client.post("/api/story/characters/new", json={"name": "ひなた", "appearance_tags": "baby", "is_minor": True}).json()
    one = client.post("/api/story/characters/new", json={"name": "ひなた(1歳)", "appearance_tags": "toddler, 1 year old",
                                                         "growth_of": base["id"], "age": 1}).json()
    three = client.post("/api/story/characters/new", json={"name": "ひなた(3歳)", "appearance_tags": "little girl, 3 years old",
                                                           "outfit_tags": "skirt", "growth_of": base["id"], "age": 3}).json()
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": [
        {"text": "a", "character_ids": [one["id"]]},
        {"text": "b", "character_ids": [base["id"]], "character_outfits": {str(base["id"]): "yukata"}},
    ]}).json()

    res = client.put(f"/api/manga-v2/{story['id']}/growth", json={"age": 2})
    assert res.status_code == 200, res.text
    assert res.json()["people"][0]["chosen_id"] == one["id"]  # 2歳以下で一番大きい段階
    res = client.put(f"/api/manga-v2/{story['id']}/growth", json={"stages": {str(base["id"]): three["id"]}})
    assert res.json()["people"][0]["chosen_id"] == three["id"]

    conn = db.get_connection()
    try:
        cast = db.characters_by_scene(conn, story["id"])
    finally:
        conn.close()
    first, second = (cast[s["id"]][0] for s in story["scenes"])
    # 見た目は選んだ段階、名前・ID はシーンのキャラのまま。その場面の服装は段階より優先する
    assert first["appearance_tags"] == "little girl, 3 years old" and first["id"] == one["id"]
    assert second["appearance_tags"] == "little girl, 3 years old" and second["outfit_tags"] == "yukata"
    assert second["name"] == "ひなた" and second["is_minor"]
    # ほかの人物の段階は選べない
    assert client.put(f"/api/manga-v2/{story['id']}/growth", json={"stages": {str(one["id"]): three["id"]}}).status_code == 422
