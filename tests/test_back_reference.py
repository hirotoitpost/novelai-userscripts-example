"""
漫画v2: 後ろ姿のコマ(from behind・facing away など)で、後ろ姿用のキャラ参照を使う・無ければ正面の参照を弱める
仕組みのテスト。画像生成は差し替え、DB と画像の保存先は一時フォルダにする。

実行方法:
  uv run pytest tests/test_back_reference.py -v
"""

from __future__ import annotations

import asyncio
import base64
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
from python.routes import manga_v2  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 12), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manga_v2, "_REFERENCE_DIR", tmp_path / "outputs" / "manga" / "refs")
    monkeypatch.setattr(manga_v2, "_PANEL_DIR", tmp_path / "outputs" / "manga" / "panels")
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(manga_v2.router)
    with TestClient(app) as test_client:
        yield test_client


def test_back_view_detection() -> None:
    assert manga_v2.is_back_view("underwater, pov, from behind, back")
    assert manga_v2.is_back_view("beach", "lying on stomach, facing away")
    assert manga_v2.is_back_view("Back View, 1girl")
    # 振り向いて顔を見せる・手の甲 は後ろ姿ではない
    assert not manga_v2.is_back_view("looking back, smile")
    assert not manga_v2.is_back_view("back of hand, close-up")
    assert not manga_v2.is_back_view("peeking from behind wooden door")  # 言い回しの中の from behind
    assert not manga_v2.is_back_view("from behind, looking back")
    assert manga_v2.is_back_view("1.4::from behind::, smile")


def test_register_and_remove_back_reference(client: TestClient) -> None:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "ゆら", "1girl, brown hair")
        db.update_character_sheet(conn, character["id"], {"seed": 11})
    finally:
        conn.close()
    image = "data:image/png;base64," + base64.b64encode(_png((0, 0, 255))).decode()
    res = client.put(f"/api/manga-v2/characters/{character['id']}/reference", json={"image": image, "view": "back", "seed": 99})
    assert res.status_code == 204, res.text
    listed = next(c for c in client.get("/api/story/characters").json() if c["id"] == character["id"])
    assert listed["back_reference_image_path"].startswith("outputs/manga/refs/char")
    assert listed["reference_image_path"] is None
    assert listed["seed"] == 11  # 後ろ姿の参照は基準シードを変えない

    assert client.delete(f"/api/manga-v2/characters/{character['id']}/reference?view=back").status_code == 204
    listed = next(c for c in client.get("/api/story/characters").json() if c["id"] == character["id"])
    assert listed["back_reference_image_path"] is None


def _references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tags: list[str], back: bool) -> list[tuple[bytes, float]]:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "ゆら", "1girl, brown hair")
        story = db.create_story(conn, "x", len(tags), 4)
        db.add_story_scenes(
            conn,
            story["id"],
            [{"draft_title": str(i), "draft_text": "a", "draft_prompt_tags": t, "novelai_text": "a"} for i, t in enumerate(tags)],
        )
        scenes = db.list_story_scenes(conn, story["id"])
        for scene in scenes:
            db.set_scene_characters(conn, scene["id"], [character["id"]])
    finally:
        conn.close()
    manga_v2.register_reference(character["id"], _png((255, 0, 0)))
    if back:
        manga_v2.register_reference(character["id"], _png((0, 0, 255)), view="back")
    seen: list[tuple[bytes, float]] = []

    async def fake_generate(api_key, prompt, negative, **kwargs):
        ref = kwargs["character_reference"][0]
        seen.append((base64.b64decode(ref.image_b64), ref.strength))
        return _png((1, 1, 1))

    monkeypatch.setattr(manga_v2, "generate_image_v5", fake_generate)

    class Job:
        message = ""
        progress = 0
        total = 0

    req = MangaV2PanelsRequest(use_character_reference=True, reference_strength=0.6)
    asyncio.run(manga_v2._run_panels(Job(), story["id"], req, "t", scenes))
    return seen


def _color(image_bytes: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return image.convert("RGB").getpixel((0, 0))


def test_back_view_scene_uses_the_back_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    seen = _references(tmp_path, monkeypatch, ["beach, smile", "underwater, from behind, back"], back=True)
    (front, front_strength), (back, back_strength) = seen
    assert _color(front) == (255, 0, 0) and front_strength == 0.6
    assert _color(back) == (0, 0, 255) and back_strength == 0.6


def test_back_view_without_back_reference_weakens_the_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    seen = _references(tmp_path, monkeypatch, ["beach, smile", "underwater, from behind, back"], back=False)
    (front, front_strength), (back, back_strength) = seen
    assert _color(front) == _color(back) == (255, 0, 0)
    assert front_strength == 0.6 and back_strength == pytest.approx(0.36)
