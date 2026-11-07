"""
ギャラリーの一覧の作り置き(routes/library.py の _cached_gallery_images)と、次のページの縮小画像の先読みのテスト。

実行方法:
  uv run pytest tests/test_gallery_cache.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes import library  # noqa: E402


def _record(root: Path, name: str) -> None:
    path = root / "outputs" / "history" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), "white").save(path)
    conn = db.get_connection()
    try:
        db.record_generation(
            conn, prompt="1girl", negative_prompt=None, model="nai-diffusion-4-5-full", size="[32, 48]",
            steps=28, scale=5.0, seed=7, chunk_ids=[], image_paths=[f"outputs/history/{name}"],
        )
    finally:
        conn.close()


@pytest.fixture()
def gallery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(library, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(library, "_THUMB_DIR", tmp_path / "outputs" / ".thumbs")
    monkeypatch.setattr(library, "_SERVED_DIRS", (tmp_path / "outputs" / "history",))
    monkeypatch.setattr(library, "_gallery_cache", {})
    db.get_connection().close()
    builds = []
    real_build = library._build_gallery_images

    def counted() -> list:
        builds.append(1)
        return real_build()

    monkeypatch.setattr(library, "_build_gallery_images", counted)
    app = FastAPI()
    app.include_router(library.router)
    with TestClient(app) as client:
        yield client, tmp_path, builds


def test_list_is_reused_until_the_database_changes(gallery) -> None:
    client, root, builds = gallery
    _record(root, "a.png")
    assert client.get("/api/library/images").json()["total"] == 1
    assert client.get("/api/library/images?offset=0&limit=1").json()["total"] == 1
    assert len(builds) == 1  # 2回目は作り置きを使う

    time.sleep(0.01)
    _record(root, "b.png")  # DB が変われば作り直す
    assert client.get("/api/library/images").json()["total"] == 2
    assert len(builds) == 2


def test_bookmarks_and_manual_marks_apply_without_rebuilding(gallery) -> None:
    client, root, builds = gallery
    _record(root, "a.png")
    key = client.get("/api/library/images").json()["items"][0]["key"]
    conn = db.get_connection()
    try:
        db.set_bookmark(conn, "image", key, True)
        db.set_adult_mark(conn, "image", key, True)
    finally:
        conn.close()
    item = client.get("/api/library/images").json()["items"][0]
    assert item["bookmarked"] is True and item["adult"] is True and item["adult_manual"] is True
    # 作り置きの中身は書き換えられていない
    cached = library._gallery_cache[str(root)][2][0]
    assert cached.bookmarked is False and cached.adult_manual is None


def test_dataset_folder_changes_invalidate_the_list(gallery) -> None:
    client, root, builds = gallery
    _record(root, "a.png")
    client.get("/api/library/images")
    folder = root / "outputs" / "training_data" / "x" / "generated"
    folder.mkdir(parents=True)
    signature = library._gallery_signature()
    client.get("/api/library/images")
    assert len(builds) == 2  # データセットのフォルダが増えた
    time.sleep(0.05)
    Image.new("RGB", (8, 8)).save(folder / "new.png")
    assert library._gallery_signature() != signature


def test_old_lists_are_rebuilt(gallery, monkeypatch: pytest.MonkeyPatch) -> None:
    client, root, builds = gallery
    _record(root, "a.png")
    client.get("/api/library/images")
    monkeypatch.setattr(library, "_GALLERY_MAX_AGE", 0.0)
    client.get("/api/library/images")
    assert len(builds) == 2


def test_next_page_thumbnails_are_prefetched(gallery) -> None:
    client, root, builds = gallery
    for name in ("a.png", "b.png", "c.png"):
        _record(root, name)
    client.get("/api/library/images?limit=1")
    library._prefetcher.submit(lambda: None).result(timeout=10)  # 先読みが終わるのを待つ
    thumbs = list((root / "outputs" / ".thumbs").glob("*.webp"))
    assert len(thumbs) == 1  # 次のページ(1枚)の分だけ
    # 縮小画像を頼むと、作ってあるものを返す
    response = client.get("/api/library/thumb?path=outputs/history/b.png&w=360")
    assert response.status_code == 200
    assert len(list((root / "outputs" / ".thumbs").glob("*.webp"))) in (1, 2)


def test_warm_gallery_cache_builds_the_list(gallery) -> None:
    client, root, builds = gallery
    _record(root, "a.png")
    assert library.warm_gallery_cache() == 1
    assert len(builds) == 1
    client.get("/api/library/images")
    assert len(builds) == 1
