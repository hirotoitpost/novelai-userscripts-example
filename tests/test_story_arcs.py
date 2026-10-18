"""話の流れのセット(/api/story-arcs)と、おまかせ漫画のシリーズの続き。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes import story_arcs  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    db.get_connection().close()
    app = FastAPI()
    app.include_router(story_arcs.router)
    with TestClient(app) as test_client:
        yield test_client


def test_default_arc_and_editing(client: TestClient) -> None:
    (summer,) = client.get("/api/story-arcs").json()
    # 既定のセット: 夏休みの4巻分。セットの後の流れ(受験シーズン)は説明に
    assert summer["name"].startswith("夏休み") and len(summer["beats"]) == 4
    assert "星座観測" in summer["beats"][0] and "花火大会" in summer["beats"][3] and "受験" in summer["description"]
    made = client.post("/api/story-arcs", json={"name": "文化祭", "description": "", "beats": ["準備", " ", "当日"]}).json()
    # 空の行は捨てる
    assert made["beats"] == ["準備", "当日"]
    assert client.post("/api/story-arcs", json={"name": "文化祭", "beats": ["x"]}).status_code == 409
    assert client.post("/api/story-arcs", json={"name": "空", "beats": [" "]}).status_code == 422
    changed = client.put(f"/api/story-arcs/{made['id']}", json={"name": "文化祭2", "beats": ["準備", "当日", "後夜祭"]}).json()
    assert changed["name"] == "文化祭2" and changed["beats"][-1] == "後夜祭"
    assert client.put("/api/story-arcs/999", json={"name": "x", "beats": ["x"]}).status_code == 404
    assert client.delete(f"/api/story-arcs/{made['id']}").status_code == 204
    assert client.delete(f"/api/story-arcs/{made['id']}").status_code == 404
    # 消した既定のセットは、起動し直しても戻らない(初めて表を作るときだけ入れる)
    client.delete(f"/api/story-arcs/{summer['id']}")
    db.get_connection().close()
    assert client.get("/api/story-arcs").json() == []
