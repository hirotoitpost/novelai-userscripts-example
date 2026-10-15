"""
キャラシートの書き出し・取り込み(character_transfer.py と /api/story/characters/export・import)のテスト。

実行方法:
  uv run pytest tests/test_character_transfer.py -v
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import character_transfer, content_guard, db, guard_password  # noqa: E402
from python.routes import content_guard as guard_routes, story as story_routes  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(character_transfer, "_PROJECT_ROOT", tmp_path)
    content_guard.reset_cache()
    guard_password.reset_state()
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(guard_routes.router)
    yield TestClient(app)
    content_guard.reset_cache()
    guard_password.reset_state()


def _character(tmp_path: Path, name: str, **sheet) -> int:
    conn = db.get_connection()
    try:
        created = db.save_character(conn, name, sheet.pop("appearance_tags", "1girl"), "メモ")
        db.update_character_sheet(conn, created["id"], sheet)
        ref = tmp_path / "outputs" / "manga" / "refs" / f"char{created['id']}.png"
        ref.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(ref)
        db.update_character_reference(conn, created["id"], f"outputs/manga/refs/char{created['id']}.png")
        return created["id"]
    finally:
        conn.close()


def _export(client: TestClient, ids: list[int]) -> bytes:
    res = client.post("/api/story/characters/export", json={"ids": ids})
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    return res.content


def _import(client: TestClient, data: bytes, choices: list[dict], headers: dict | None = None):
    return client.post(
        "/api/story/characters/import", files={"file": ("c.zip", data, "application/zip")},
        data={"choices": json.dumps(choices)}, headers=headers or {},
    )


def test_export_and_import_round_trip(client: TestClient, tmp_path: Path) -> None:
    mio = _character(tmp_path, "みお", outfit_tags="blouse", seed=42, is_adult=1)
    data = _export(client, [mio])
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("characters.json"))
    assert manifest["characters"][0]["outfit_tags"] == "blouse" and manifest["characters"][0]["reference"] == "refs/1.png"
    plan = client.post("/api/story/characters/import/preview", files={"file": ("c.zip", data)}).json()["characters"]
    # 同じ名前があるので、はじめは別名(みお(2))
    assert plan[0]["exists"] and plan[0]["action"] == "rename" and plan[0]["new_name"] == "みお(2)"
    res = _import(client, data, [{"index": 0, "action": "rename", "name": "みお(2)"}])
    assert res.status_code == 200, res.text
    new_id = res.json()["imported"][0]["id"]
    conn = db.get_connection()
    try:
        copy = db.get_character(conn, new_id)
    finally:
        conn.close()
    assert copy["name"] == "みお(2)" and copy["outfit_tags"] == "blouse" and copy["seed"] == 42 and copy["is_adult"] == 1
    assert copy["notes"] == "メモ" and (tmp_path / copy["reference_image_path"]).is_file()
    # 同じ名前で「新しく」は断る
    assert _import(client, data, [{"index": 0, "action": "new"}]).status_code == 409


def test_import_keeps_the_minor_safeguard(client: TestClient, tmp_path: Path) -> None:
    yura = _character(tmp_path, "ゆら", is_minor=1)
    data = _export(client, [yura])
    # 未成年の定義を外したファイルで上書きするのは、パスワードが要る
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("characters.json"))
    manifest["characters"][0]["is_minor"] = False
    loosened = io.BytesIO()
    with zipfile.ZipFile(loosened, "w") as zf:
        zf.writestr("characters.json", json.dumps(manifest))
    assert _import(client, loosened.getvalue(), [{"index": 0, "action": "overwrite"}]).status_code == 403
    # 成人と未成年が両方立っていたら、未成年として入れる
    manifest["characters"][0].update({"is_minor": True, "is_adult": True, "name": "ゆら別"})
    both = io.BytesIO()
    with zipfile.ZipFile(both, "w") as zf:
        zf.writestr("characters.json", json.dumps(manifest))
    made = _import(client, both.getvalue(), [{"index": 0, "action": "new"}]).json()["imported"][0]
    conn = db.get_connection()
    try:
        row = db.get_character(conn, made["id"])
    finally:
        conn.close()
    assert row["is_minor"] == 1 and row["is_adult"] == 0
    # 未成年を示すタグがあるキャラを成人として入れることはできない
    manifest["characters"][0].update({"is_minor": False, "is_adult": True, "name": "制服", "outfit_tags": "school uniform"})
    tagged = io.BytesIO()
    with zipfile.ZipFile(tagged, "w") as zf:
        zf.writestr("characters.json", json.dumps(manifest))
    assert _import(client, tagged.getvalue(), [{"index": 0, "action": "new"}]).status_code == 422


def test_bad_files_are_refused(client: TestClient) -> None:
    res = client.post("/api/story/characters/import/preview", files={"file": ("x.zip", b"not a zip")})
    assert res.status_code == 422
    other = io.BytesIO()
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("characters.json", json.dumps({"format": "other", "characters": []}))
    assert client.post("/api/story/characters/import/preview", files={"file": ("x.zip", other.getvalue())}).status_code == 422
