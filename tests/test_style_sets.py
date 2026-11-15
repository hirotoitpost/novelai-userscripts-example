"""
絵柄セット: 作る(アップロード・ギャラリーの画像から)・書き換える・消す、キャラに選ばせる、生成で使う形に読み込む。

実行方法:
  uv run pytest tests/test_style_sets.py -v
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, style_sets  # noqa: E402
from python.mcp_reference import style_reference  # noqa: E402
from python.routes import story as story_routes  # noqa: E402
from python.routes import style_sets as style_routes  # noqa: E402


def _png(color: tuple[int, int, int] = (200, 100, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 24), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """利用者のフォルダ(single ではプロジェクトのフォルダ)を一時フォルダにする。"""
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    return tmp_path


@pytest.fixture()
def client(files: Path):
    app = FastAPI()
    app.include_router(style_routes.router)
    app.include_router(story_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _character(name: str) -> int:
    conn = db.get_connection()
    try:
        return db.save_character(conn, name, "1girl")["id"]
    finally:
        conn.close()


def _create(client: TestClient, name: str = "細線", **extra) -> dict:
    body = {"name": name, "image": base64.b64encode(_png()).decode(), "style_tags": "thin lineart",
            "negative_tags": "thick lineart", **extra}
    response = client.post("/api/style-sets", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_create_copies_image_and_lists_members(client: TestClient, files: Path) -> None:
    anzu = _character("あんず")
    created = _create(client, character_ids=[anzu])
    assert created["strength"] == 0.8 and created["fidelity"] == 1.0
    assert created["image_path"].startswith("outputs/manga/style_sets/")
    assert (files / created["image_path"]).is_file()
    assert [m["name"] for m in created["members"]] == ["あんず"]
    assert client.get("/api/style-sets").json()[0]["id"] == created["id"]
    # 同じ名前は作らない
    duplicate = client.post("/api/style-sets", json={"name": "細線", "image": base64.b64encode(_png()).decode()})
    assert duplicate.status_code == 409


def test_create_needs_an_image(client: TestClient) -> None:
    assert client.post("/api/style-sets", json={"name": "画像なし"}).status_code == 422
    bad = client.post("/api/style-sets", json={"name": "壊れた画像", "image": "not-base64!!"})
    assert bad.status_code == 422


def test_create_from_gallery_file_key(client: TestClient, files: Path) -> None:
    source = files / "outputs" / "manga" / "refs" / "char1_ref.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(_png((10, 20, 30)))
    response = client.post("/api/style-sets", json={"name": "参照から", "gallery_key": "file:outputs/manga/refs/char1_ref.png"})
    assert response.status_code == 200, response.text
    copied = files / response.json()["image_path"]
    assert copied != source and copied.is_file()


def test_update_add_remove_and_delete(client: TestClient) -> None:
    created = _create(client)
    sid = created["id"]
    updated = client.put(f"/api/style-sets/{sid}", json={"strength": 0.55, "notes": "柔らかい塗り"}).json()
    assert updated["strength"] == 0.55 and updated["notes"] == "柔らかい塗り" and updated["style_tags"] == "thin lineart"
    a, b = _character("しおり"), _character("ノア")
    after_add = client.put(f"/api/style-sets/{sid}/characters", json={"character_ids": [a, b]}).json()
    assert {m["name"] for m in after_add["members"]} == {"しおり", "ノア"}
    after_remove = client.delete(f"/api/style-sets/{sid}/characters/{a}").json()
    assert [m["name"] for m in after_remove["members"]] == ["ノア"]
    assert client.delete(f"/api/style-sets/{sid}").status_code == 204
    conn = db.get_connection()
    try:
        assert db.get_character(conn, b)["style_set_id"] is None  # キャラは残り、セットなしに戻る
    finally:
        conn.close()


def test_character_sheet_picks_a_style_set(client: TestClient) -> None:
    sid = _create(client)["id"]
    made = client.post("/api/story/characters/new", json={"name": "新キャラ", "style_set_id": sid})
    assert made.status_code == 200, made.text
    assert made.json()["style_set_id"] == sid
    cid = made.json()["id"]
    assert client.put(f"/api/story/characters/{cid}/sheet", json={"style_set_id": 9999}).status_code == 422
    cleared = client.put(f"/api/story/characters/{cid}/sheet", json={"style_set_id": None})
    assert cleared.status_code == 200 and cleared.json()["style_set_id"] is None


def test_loading_for_generation(client: TestClient) -> None:
    sid = _create(client, strength=0.7)["id"]
    cid = _character("ノア")
    assert style_sets.for_character({"style_set_id": None}) is None
    client.put(f"/api/style-sets/{sid}/characters", json={"character_ids": [cid]})
    # キャラが選んだセットを使う / セットを直接指定する
    from_character = style_reference(None, cid)
    assert from_character is not None and from_character.strength == 0.7
    assert from_character.style_tags == "thin lineart" and from_character.negative_tags == "thick lineart"
    assert style_reference(sid, None).name == "細線"
    assert style_reference(None, None) is None
    with pytest.raises(ValueError, match="見つかりません"):
        style_reference(9999, None)
