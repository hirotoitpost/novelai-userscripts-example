"""
シリーズの巻番号を付け直す(PUT /api/series/{id}/volume-order)のテスト。DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_series_volume_order.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes import series as series_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(series_routes.router)
    return TestClient(app)


def test_volumes_are_renumbered_in_the_given_order(client: TestClient) -> None:
    ids = []
    for title in ("一", "二", "三"):
        res = client.post("/api/story/scripted", json={"title": title, "scenes": [{"text": "x"}]})
        ids.append(res.json()["id"])
    series = client.post("/api/series", json={"title": "s", "story_ids": ids}).json()
    # 最後に作った巻を真ん中へ
    res = client.put(f"/api/series/{series['id']}/volume-order", json={"story_ids": [ids[0], ids[2], ids[1]]})
    assert res.status_code == 200, res.text
    assert [(v["volume_no"], v["story_id"]) for v in res.json()["volumes"]] == [(1, ids[0]), (2, ids[2]), (3, ids[1])]
    # 巻が足りない・重なる並びは受け付けない
    assert client.put(f"/api/series/{series['id']}/volume-order", json={"story_ids": [ids[0], ids[1]]}).status_code == 422
    assert client.put(f"/api/series/{series['id']}/volume-order", json={"story_ids": [ids[0], ids[0], ids[1]]}).status_code == 422
