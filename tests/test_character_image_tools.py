"""
キャラの生成した画像のページから使う道具のテスト: 一覧の画像のキー(ギャラリーと同じキー / file:<パス>)、
file:<パス> の画像を探す(find_image)、「似た画像を生成」でキャラシートの参照画像を足してキャラを返す、
生成履歴にキャラを記録して、そのキャラの一覧に並べる。

実行方法:
  uv run pytest tests/test_character_image_tools.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import character_images, db  # noqa: E402
from python.routes import character_images as routes  # noqa: E402
from python.routes import library  # noqa: E402


def _png(root: Path, rel: str, comment: str | None = None) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngInfo()
    if comment:
        info.add_text("Software", "NovelAI")
        info.add_text("Source", "NovelAI Diffusion V4.5 4BDE2A90")
        info.add_text("Comment", comment)
    Image.new("RGB", (64, 96), "white").save(path, pnginfo=info)
    return rel


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(character_images, "_ROOT", tmp_path)
    monkeypatch.setattr(routes, "_ROOT", tmp_path)
    monkeypatch.setattr(library, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(library, "_SERVED_DIRS", (tmp_path / "outputs" / "history", tmp_path / "outputs" / "manga"))
    monkeypatch.setattr(library, "_gallery_cache", {})
    db.get_connection().close()
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(library.router)
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def _character(root: Path) -> dict:
    conn = db.get_connection()
    try:
        mio = db.save_character(conn, "みお", "1girl")
        db.update_character_sheet(conn, mio["id"], {"trigger_word": "mio_t"})
        db.update_character_reference(conn, mio["id"], _png(root, f"outputs/manga/refs/char{mio['id']}_aaaa0001.png"))
        return db.get_character(conn, mio["id"])
    finally:
        conn.close()


def test_items_carry_gallery_or_file_keys(client) -> None:
    test_client, root = client
    mio = _character(root)
    conn = db.get_connection()
    try:
        history = db.record_generation(
            conn, prompt="mio_t, 1girl", negative_prompt=None, model="m", size="[64, 96]", steps=1, scale=1.0, seed=9,
            chunk_ids=[], image_paths=[_png(root, "outputs/history/h1.png")],
        )
    finally:
        conn.close()
    items = {i["path"]: i for i in test_client.get(f"/api/character-images/{mio['id']}").json()["items"]}
    # ギャラリーにある画像(生成履歴)はギャラリーのキー、無い画像(参照画像)は file:<パス>
    assert items["outputs/history/h1.png"]["key"] == f"history:{history['id']}:h1.png"
    ref = f"outputs/manga/refs/char{mio['id']}_aaaa0001.png"
    assert items[ref]["key"] == f"file:{ref}"


def test_find_image_reads_embedded_settings_for_file_keys(client) -> None:
    _, root = client
    rel = _png(root, "outputs/manga/refs/candidates/char1_5_abc.png", comment='{"prompt": "1girl, silver hair", "seed": 5}')
    item = library.find_image(f"file:{rel}")
    assert item is not None and item.prompt == "1girl, silver hair" and item.seed == 5
    assert item.width == 64 and item.height == 96
    # 配信しないフォルダ・無いファイルは None
    _png(root, "data/secret.png")
    assert library.find_image("file:data/secret.png") is None
    assert library.find_image("file:outputs/history/none.png") is None


def test_references_add_the_character_sheet_and_link_the_character(client) -> None:
    test_client, root = client
    mio = _character(root)
    rel = _png(root, "outputs/manga/refs/candidates/x.png")
    plain = test_client.get("/api/library/images/references", params={"key": f"file:{rel}"}).json()
    assert plain["character"] is None and plain["character_references"] == []
    linked = test_client.get(
        "/api/library/images/references", params={"key": f"file:{rel}", "character_id": mio["id"]}
    ).json()
    assert linked["character"] == {"id": mio["id"], "name": "みお"}
    assert linked["character_references"] == [{
        "image_path": mio["reference_image_path"], "type": "character&style", "fidelity": 1.0, "strength": 0.6, "label": "みお",
    }]
    assert test_client.get(
        "/api/library/images/references", params={"key": f"file:{rel}", "character_id": 9999}
    ).status_code == 404


def test_generations_linked_to_a_character_are_listed_for_it(client) -> None:
    test_client, root = client
    mio = _character(root)
    conn = db.get_connection()
    try:
        # トリガーワードの無いプロンプトでも、このキャラから作った画像なら並ぶ
        linked = db.record_generation(
            conn, prompt="1girl, beach", negative_prompt=None, model="m", size="[64, 96]", steps=1, scale=1.0, seed=1,
            chunk_ids=[], image_paths=[_png(root, "outputs/history/linked.png")], character_id=mio["id"],
        )
        db.record_generation(
            conn, prompt="1girl, beach", negative_prompt=None, model="m", size="[64, 96]", steps=1, scale=1.0, seed=2,
            chunk_ids=[], image_paths=[_png(root, "outputs/history/other.png")],
        )
        assert db.get_generation_entry(conn, linked["id"])["character_id"] == mio["id"]
    finally:
        conn.close()
    paths = {i["path"] for i in test_client.get(f"/api/character-images/{mio['id']}").json()["items"]}
    assert "outputs/history/linked.png" in paths and "outputs/history/other.png" not in paths
