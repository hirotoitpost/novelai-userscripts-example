"""
ギャラリーの「似た画像を生成」で引き継ぐ参照画像(GET /api/library/images/references)のテスト。
画像生成の履歴は保存した i2i の元画像とキャラ参照、漫画のコマは登場キャラのキャラシートの参照画像を返す。

実行方法:
  uv run pytest tests/test_gallery_references.py -v
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
from python.routes import library  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(library, "_PROJECT_ROOT", tmp_path)
    db.get_connection().close()
    app = FastAPI()
    app.include_router(library.router)
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def _png(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), "white").save(path)
    return path.as_posix()


def test_history_image_keeps_its_i2i_and_character_references(client) -> None:
    test_client, root = client
    _png(root / "outputs" / "history" / "out.png")
    _png(root / "outputs" / "history" / "i2i.png")
    _png(root / "outputs" / "history" / "ref.png")
    conn = db.get_connection()
    try:
        row = db.record_generation(
            conn, prompt="1girl", negative_prompt=None, model="nai-diffusion-4-5-full", size="[832, 1216]",
            steps=28, scale=5.0, seed=7, chunk_ids=[], image_paths=["outputs/history/out.png"],
            i2i_image_path="outputs/history/i2i.png", i2i_strength=0.5, i2i_noise=0.1,
            character_references=[
                {"image_path": "outputs/history/ref.png", "type": "character", "fidelity": 0.8, "strength": 0.6},
                # ファイルが消えた参照は返さない
                {"image_path": "outputs/history/gone.png", "type": "character", "fidelity": 1.0, "strength": 1.0},
            ],
        )
    finally:
        conn.close()
    refs = test_client.get("/api/library/images/references", params={"key": f"history:{row['id']}:out.png"}).json()
    assert refs["i2i"] == {"image_path": "outputs/history/i2i.png", "strength": 0.5, "noise": 0.1}
    assert [(r["image_path"], r["type"], r["fidelity"], r["strength"]) for r in refs["character_references"]] == [
        ("outputs/history/ref.png", "character", 0.8, 0.6)
    ]


def test_panel_uses_the_characters_references(client) -> None:
    test_client, root = client
    _png(root / "outputs" / "refs" / "yura.png")
    _png(root / "outputs" / "refs" / "yura_back.png")
    _png(root / "outputs" / "refs" / "hibiki.png")
    conn = db.get_connection()
    try:
        yura = db.save_character(conn, "九条 ゆら", "1girl", "")
        hibiki = db.save_character(conn, "霧谷 ひびき", "1girl", "")
        nobody = db.save_character(conn, "参照なし", "1girl", "")
        db.update_character_reference(conn, yura["id"], "outputs/refs/yura.png")
        db.update_character_reference(conn, yura["id"], "outputs/refs/yura_back.png", view="back")
        db.update_character_reference(conn, hibiki["id"], "outputs/refs/hibiki.png")
        story_row = db.create_story(conn, "海", 1)
        db.add_story_scenes(
            conn, story_row["id"], [{"draft_text": "a", "draft_prompt_tags": "beach, from behind"}]
        )
        scene = db.list_story_scenes(conn, story_row["id"])[0]
        db.set_scene_characters(conn, scene["id"], [yura["id"], hibiki["id"], nobody["id"]])
        db.upsert_manga_panel(conn, story_row["id"], scene["id"], "outputs/manga/p.png", 1, 832, 1216)
        panel_id = conn.execute("SELECT id FROM manga_panels").fetchone()["id"]
    finally:
        conn.close()
    refs = test_client.get("/api/library/images/references", params={"key": f"panel:{panel_id}"}).json()
    by_name = {r["label"]: r for r in refs["character_references"]}
    # 後ろ姿のコマ: 後ろ姿の参照があればそれ、無ければ正面の参照を弱めて使う。参照の無いキャラは入れない
    assert set(by_name) == {"九条 ゆら", "霧谷 ひびき"}
    assert by_name["九条 ゆら"]["image_path"] == "outputs/refs/yura_back.png" and by_name["九条 ゆら"]["strength"] == 1.0
    assert by_name["霧谷 ひびき"]["image_path"] == "outputs/refs/hibiki.png" and by_name["霧谷 ひびき"]["strength"] < 1.0
    assert refs["i2i"] is None and refs["notes"]


def test_other_images_have_no_references(client) -> None:
    test_client, _ = client
    assert test_client.get("/api/library/images/references", params={"key": "illustration:3"}).json() == {
        "i2i": None, "character_references": [], "notes": []
    }
    assert test_client.get("/api/library/images/references", params={"key": "history:abc:x.png"}).status_code == 400
