"""
出版(パブリッシュ)のテスト: 画像から NovelAI の生成情報(PNG のテキストと、アルファに隠した埋め込み)が取り除かれること、
出版物として別に保存されて ZIP にまとまること、元の画像は変わらないこと。

実行方法:
  uv run pytest tests/test_publish.py -v
"""

from __future__ import annotations

import gzip
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, publish  # noqa: E402
from python.nai_metadata import read_comment  # noqa: E402
from python.routes import library as library_routes  # noqa: E402
from python.routes import publish as publish_routes  # noqa: E402

COMMENT = {"prompt": "1girl, park, bench", "uc": "blurry", "steps": 23, "seed": 12345, "width": 64, "height": 64}


def text_png(color: tuple[int, int, int] = (200, 120, 60)) -> bytes:
    """生成情報を PNG のテキストに持つ画像。"""
    info = PngInfo()
    info.add_text("Comment", json.dumps(COMMENT))
    info.add_text("Source", "NovelAI Diffusion V4.5 4BDE2A90")
    info.add_text("Software", "NovelAI")
    info.add_text("Description", COMMENT["prompt"])
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def stealth_png(color: tuple[int, int, int] = (60, 120, 200)) -> bytes:
    """生成情報を、アルファチャンネルの最下位ビットに隠して持つ画像(PNG のテキストは無い)。"""
    payload = gzip.compress(
        json.dumps({"Comment": json.dumps(COMMENT), "Source": "NovelAI Diffusion V4.5 4BDE2A90"}).encode()
    )
    data = b"stealth_pngcomp" + (len(payload) * 8).to_bytes(4, "big") + payload
    bits = np.unpackbits(np.frombuffer(data, np.uint8))
    rgba = np.full((64, 64, 4), 255, np.uint8)
    rgba[..., :3] = color
    alpha = rgba[..., 3].T.reshape(-1).copy()  # 列の順に並べる
    assert bits.size <= alpha.size
    alpha[: bits.size] = (alpha[: bits.size] & 0xFE) | bits
    rgba[..., 3] = alpha.reshape((64, 64)).T
    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("make", [text_png, stealth_png])
def test_metadata_is_removed(make: Any) -> None:
    source = make()
    assert read_comment(source) is not None  # 元の画像には入っている
    clean = publish.strip_metadata(source)
    assert read_comment(clean) is None
    with Image.open(io.BytesIO(clean)) as image, Image.open(io.BytesIO(source)) as original:
        assert image.mode == "RGB" and image.size == original.size
        assert not any(image.info.get(key) for key in ("Comment", "Source", "Software", "Description"))
        # 絵そのものは変わらない
        assert np.array_equal(np.asarray(image), np.asarray(original.convert("RGB")))
    publish.check(clean)
    with pytest.raises(publish.MetadataRemains):
        publish.check(source)


def test_names_are_made_safe() -> None:
    assert publish.safe_name('夏の本: 1/2 "完"?', "x") == "夏の本_ 1_2 _完__"
    assert publish.safe_name("  ..  ", "fallback") == "fallback"
    assert publish._folder_names([publish.Entry("images", "画像")]) == [""]
    entries = [publish.Entry("book", "同じ題"), publish.Entry("book", "同じ題"), publish.Entry("book", "")]
    assert publish._folder_names(entries) == ["同じ題", "同じ題_2", "item3"]


# ---- API ----


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(publish, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(publish, "PUBLICATIONS_DIR", tmp_path / "outputs" / "publications")
    monkeypatch.setattr(publish_routes, "_PROJECT_ROOT", tmp_path)
    history = tmp_path / "outputs" / "history"
    pages = tmp_path / "outputs" / "manga" / "v2"
    history.mkdir(parents=True)
    pages.mkdir(parents=True)
    (history / "a.png").write_bytes(text_png())
    (history / "b.png").write_bytes(stealth_png())
    (pages / "p0.png").write_bytes(stealth_png((10, 20, 30)))
    (pages / "p1.png").write_bytes(text_png((30, 20, 10)))
    images = [
        SimpleNamespace(key="history:1:0", path="outputs/history/a.png"),
        SimpleNamespace(key="history:2:0", path="outputs/history/b.png"),
        SimpleNamespace(key="history:3:0", path="outputs/history/missing.png"),
    ]
    books = {
        7: {"title": "公園の一日", "pages": ["outputs/manga/v2/p0.png", "outputs/manga/v2/p1.png"], "text": "本文"},
        8: {"title": "文章だけの本", "pages": [], "text": "むかしむかし。"},
    }

    def get_book(story_id: int) -> dict[str, Any]:
        if story_id not in books:
            raise HTTPException(status_code=404, detail="story not found")
        return books[story_id]

    monkeypatch.setattr(library_routes, "_gallery_images", lambda: images)
    monkeypatch.setattr(library_routes, "get_book", get_book)
    app = FastAPI()
    app.include_router(publish_routes.router)
    return TestClient(app), tmp_path


def test_publication_is_saved_separately_and_zipped(api: tuple[TestClient, Path]) -> None:
    client, root = api
    originals = {p: p.read_bytes() for p in (root / "outputs").rglob("*.png")}
    res = client.post(
        "/api/publications",
        json={"title": "夏の本", "image_keys": ["history:2:0", "history:1:0", "nope"], "book_ids": [7, 8, 99]},
    )
    assert res.status_code == 200, res.text
    pub = res.json()
    assert pub["title"] == "夏の本" and pub["file_count"] == 5 and pub["bytes"] > 0
    assert [(i["kind"], i["title"], i["files"]) for i in pub["items"]] == [
        ("images", "画像", 2),
        ("book", "公園の一日", 2),
        ("book", "文章だけの本", 1),
    ]
    # 出版物は別の場所に保存される。元の画像は変わらない(生成情報も残っている)
    folder = root / pub["dir"]
    assert folder == root / "outputs" / "publications" / f"pub_{pub['id']}"
    assert {p: p.read_bytes() for p in originals} == originals
    saved = sorted(p.relative_to(folder / "files").as_posix() for p in (folder / "files").rglob("*") if p.is_file())
    assert saved == [
        "公園の一日/001.png",
        "公園の一日/002.png",
        "文章だけの本/本文.txt",
        "画像/001.png",
        "画像/002.png",
    ]
    for path in (folder / "files").rglob("*.png"):
        assert read_comment(path.read_bytes()) is None
    # ZIP も同じ中身。選んだ順に並ぶ(history:2 が 001)
    with zipfile.ZipFile(root / pub["zip_path"]) as archive:
        assert sorted(archive.namelist()) == saved
        assert archive.read("文章だけの本/本文.txt").decode("utf-8") == "文章だけの本\n\nむかしむかし。\n"
        first = Image.open(io.BytesIO(archive.read("画像/001.png")))
        assert first.getpixel((0, 0)) == (60, 120, 200)
        for name in archive.namelist():
            if name.endswith(".png"):
                assert read_comment(archive.read(name)) is None

    assert [p["id"] for p in client.get("/api/publications").json()] == [pub["id"]]
    download = client.get(f"/api/publications/{pub['id']}/download")
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    assert download.content == (root / pub["zip_path"]).read_bytes()
    assert client.get(f"/api/publications/{pub['id']}/cover").headers["content-type"] == "image/png"

    assert client.delete(f"/api/publications/{pub['id']}").status_code == 204
    assert not folder.exists() and client.get("/api/publications").json() == []
    assert client.get(f"/api/publications/{pub['id']}/download").status_code == 404
    # 出版物を消しても、元の画像は残る
    assert all(p.exists() for p in originals)


def test_single_entry_has_no_folder(api: tuple[TestClient, Path]) -> None:
    client, root = api
    pub = client.post("/api/publications", json={"title": "一冊: 公園", "book_ids": [7]}).json()
    with zipfile.ZipFile(root / pub["zip_path"]) as archive:
        assert sorted(archive.namelist()) == ["001.png", "002.png"]
    assert Path(pub["zip_path"]).name == "一冊_ 公園.zip"


def test_nothing_to_publish(api: tuple[TestClient, Path]) -> None:
    client, root = api
    assert client.post("/api/publications", json={"title": "空"}).status_code == 422
    assert (
        client.post("/api/publications", json={"title": "空", "image_keys": ["nope"], "book_ids": [99]}).status_code
        == 404
    )
    # ファイルが消えている画像だけを選んだとき
    res = client.post("/api/publications", json={"title": "空", "image_keys": ["history:3:0"]})
    assert res.status_code == 422 and "出版できるファイルがありません" in res.json()["detail"]
    assert client.get("/api/publications").json() == []
    assert not list((root / "outputs" / "publications").glob("pub_*"))


def test_no_publication_when_metadata_cannot_be_removed(
    api: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root = api

    def still_there(data: bytes) -> None:
        raise publish.MetadataRemains("NovelAI の生成情報が残っています。")

    monkeypatch.setattr(publish, "check", still_there)
    res = client.post("/api/publications", json={"title": "だめ", "image_keys": ["history:1:0"]})
    assert res.status_code == 500 and "出版物は作っていません" in res.json()["detail"]
    assert client.get("/api/publications").json() == []
    assert not list((root / "outputs" / "publications").glob("pub_*"))
