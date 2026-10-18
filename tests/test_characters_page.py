"""キャラ一覧のページで使う API: キャラごとの出ている作品と、キャラ別データセットの画像。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes import library, story  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(library, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_OUTPUTS", tmp_path / "outputs")
    db.get_connection().close()
    app = FastAPI()
    app.include_router(story.router)
    app.include_router(library.router)
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), "white").save(path)


def test_works_and_dataset_per_character(client) -> None:
    test_client, root = client
    conn = db.get_connection()
    try:
        hina = db.save_character(conn, "白石 陽菜", "1girl", "会長")
        rei = db.save_character(conn, "黒澤 怜", "1girl", "副会長")
        db.update_character_sheet(conn, rei["id"], {"trigger_word": "rei_k"})
        first = db.create_story(conn, "文化祭", 1)
        second = db.create_story(conn, "夏休み", 1)
        series = db.create_series(conn, "生徒会")
        db.set_story_series(conn, second["id"], series["id"], 2)
        for story_row in (first, second):
            db.add_story_scenes(conn, story_row["id"], [{"draft_text": "a"}, {"draft_text": "b"}])
            for scene in db.list_story_scenes(conn, story_row["id"]):
                db.set_scene_characters(conn, scene["id"], [hina["id"]])
    finally:
        conn.close()
    works = test_client.get("/api/story/character-works").json()
    # キーはキャラID。出ていないキャラは無い。シーンの数と、シリーズの巻なら題と巻
    assert set(works) == {str(hina["id"])}
    by_title = {w["title"]: w for w in works[str(hina["id"])]}
    assert by_title["文化祭"]["scenes"] == 2 and by_title["文化祭"]["series_title"] is None
    assert by_title["夏休み"]["series_title"] == "生徒会" and by_title["夏休み"]["volume_no"] == 2

    # データセットの画像: フォルダ名はトリガーワード(無ければ名前)。R18 のフォルダは成人向けの印
    _png(root / "outputs" / "training_data" / "白石_陽菜" / "generated" / "a.png")
    _png(root / "outputs" / "other_root" / "白石_陽菜" / "manual" / "b.png")
    _png(root / "outputs" / "training_data" / "rei_k" / "generated_r18" / "c.png")
    _png(root / "outputs" / "training_data" / "rei_k" / "rejected" / "d.png")
    counts = test_client.get("/api/library/character-datasets").json()
    assert counts == {str(hina["id"]): 2, str(rei["id"]): 1}
    images = test_client.get(f"/api/library/character-datasets/{rei['id']}").json()
    assert [Path(i["path"]).name for i in images] == ["c.png"] and images[0]["adult"] is True
    assert test_client.get("/api/library/character-datasets/999").json() == []
