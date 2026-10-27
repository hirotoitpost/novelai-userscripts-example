"""
キャラごとの生成した画像(character_images.py と /api/character-images)のテスト。DB とファイルは一時フォルダにする。

実行方法:
  uv run pytest tests/test_character_images.py -v
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import character_images, db, trash  # noqa: E402
from python.routes import character_images as routes  # noqa: E402
from python.routes import manga_v2  # noqa: E402

_OLD = time.time() - 60 * 86400


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(character_images, "_ROOT", tmp_path)
    monkeypatch.setattr(routes, "_ROOT", tmp_path)
    monkeypatch.setattr(trash, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manga_v2, "_REFERENCE_DIR", tmp_path / "outputs/manga/refs")
    db.get_connection().close()
    app = FastAPI()
    app.include_router(routes.router)
    yield TestClient(app)


def _png(tmp_path: Path, rel: str, *, old: bool = False) -> str:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(path)
    if old:
        os.utime(path, (_OLD, _OLD))
    return rel


def _setup(tmp_path: Path) -> dict[str, int]:
    """キャラ2人(みお・ほか)と、物語1つ(シーン0 にみお)。候補・参照画像・コマ・データセット・生成履歴を置く。"""
    conn = db.get_connection()
    try:
        mio = db.save_character(conn, "みお", "1girl")
        other = db.save_character(conn, "ほか", "1girl")
        db.update_character_sheet(conn, mio["id"], {"trigger_word": "mio_t"})
        story = db.create_story(conn, "[漫画] 本", 1, 4)
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": "1", "draft_text": "a", "draft_prompt_tags": "", "novelai_text": "a"},
            {"draft_title": "2", "draft_text": "b", "draft_prompt_tags": "", "novelai_text": "b"},
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        db.set_scene_characters(conn, scenes[0]["id"], [mio["id"]], {})
        db.set_scene_characters(conn, scenes[1]["id"], [other["id"]], {})
        sid = story["id"]
        db.upsert_manga_panel(conn, sid, scenes[0]["id"], _png(tmp_path, f"outputs/manga/panels/story{sid}_scene0_aaaa1111.png"), 5, 8, 8)
        db.update_character_reference(conn, mio["id"], _png(tmp_path, f"outputs/manga/refs/char{mio['id']}_cccc0001.png", old=True))
        history = db.record_generation(
            conn, prompt="mio_t, 1girl, park", negative_prompt=None, model="m", size="[8, 8]", steps=1, scale=1.0, seed=9,
            chunk_ids=[], image_paths=[_png(tmp_path, "outputs/history/h1.png"), _png(tmp_path, "outputs/history/h2.png")],
        )
        other_history = db.record_generation(
            conn, prompt="1girl, beach", negative_prompt=None, model="m", size="[8, 8]", steps=1, scale=1.0, seed=3,
            chunk_ids=[], image_paths=[_png(tmp_path, "outputs/history/o1.png")],
        )
        conn.execute("UPDATE generation_history SET created_at = '2020-01-01T00:00:00+00:00'")
        db.set_bookmark(conn, "image", f"history:{history['id']}:h2.png", True)
        conn.commit()
    finally:
        conn.close()
    cid = mio["id"]
    # 描き直す前のコマ(シーン0)と、ほかのキャラのシーンのコマ
    _png(tmp_path, f"outputs/manga/panels/story{sid}_scene0_bbbb2222.png", old=True)
    _png(tmp_path, f"outputs/manga/panels/story{sid}_scene1_dddd4444.png", old=True)
    _png(tmp_path, f"outputs/manga/refs/candidates/char{cid}_12345_abcdef.png", old=True)
    _png(tmp_path, f"outputs/manga/refs/candidates/char{cid}_777_fedcba.png")
    _png(tmp_path, f"outputs/manga/refs/candidates/char{other['id']}_1_aaaaaa.png", old=True)
    _png(tmp_path, f"outputs/manga/refs/char{cid}_dddd0002.png", old=True)
    _png(tmp_path, "outputs/training_data/mio_t/generated/0001.png")
    _png(tmp_path, "outputs/training_data/mio_t/body_base/1_front.png")
    return {"character": cid, "other": other["id"], "story": sid, "history": history["id"], "other_history": other_history["id"]}


def test_lists_every_image_of_the_character(client: TestClient, tmp_path: Path) -> None:
    ids = _setup(tmp_path)
    data = client.get(f"/api/character-images/{ids['character']}").json()
    by_path = {i["path"]: i for i in data["items"]}
    cid, sid = ids["character"], ids["story"]
    assert by_path[f"outputs/manga/refs/candidates/char{cid}_12345_abcdef.png"]["seed"] == 12345
    assert by_path[f"outputs/manga/refs/char{cid}_cccc0001.png"]["in_use"] is True
    assert by_path[f"outputs/manga/refs/char{cid}_dddd0002.png"]["in_use"] is False
    assert by_path[f"outputs/manga/panels/story{sid}_scene0_aaaa1111.png"]["kind"] == "panel"
    assert by_path[f"outputs/manga/panels/story{sid}_scene0_bbbb2222.png"]["kind"] == "old_panel"
    assert by_path["outputs/training_data/mio_t/generated/0001.png"]["kind"] == "dataset"
    assert by_path["outputs/training_data/mio_t/body_base/1_front.png"]["kind"] == "body_base"
    # トリガーワードのある生成履歴だけ。ブックマークしたものは使用中
    assert by_path["outputs/history/h1.png"]["in_use"] is False and by_path["outputs/history/h2.png"]["in_use"] is True
    # ほかのキャラの候補・シーン、トリガーワードの無い履歴は出ない
    assert not any("scene1" in p or "o1.png" in p or f"char{ids['other']}_" in p for p in by_path)


def test_choose_reference_from_the_list(client: TestClient, tmp_path: Path) -> None:
    ids = _setup(tmp_path)
    cid = ids["character"]
    # 前の参照画像は指し直す(写さない)
    res = client.put(f"/api/character-images/{cid}/reference", json={"path": f"outputs/manga/refs/char{cid}_dddd0002.png"})
    assert res.json()["reference_image_path"] == f"outputs/manga/refs/char{cid}_dddd0002.png"
    # 候補は写して、シードを基準シードにする
    res = client.put(f"/api/character-images/{cid}/reference",
                     json={"path": f"outputs/manga/refs/candidates/char{cid}_12345_abcdef.png"})
    path = res.json()["reference_image_path"]
    assert path.startswith(f"outputs/manga/refs/char{cid}_") and (tmp_path / path).is_file()
    conn = db.get_connection()
    try:
        character = db.get_character(conn, cid)
    finally:
        conn.close()
    assert character["reference_image_path"] == path and character["seed"] == 12345
    # 一覧に無いパス(ほかのキャラの候補・フォルダの外)は選べない
    for bad in (f"outputs/manga/refs/candidates/char{ids['other']}_1_aaaaaa.png", "../secret.png"):
        assert client.put(f"/api/character-images/{cid}/reference", json={"path": bad}).status_code == 404


def test_cleanup_moves_only_old_unused_images_to_the_trash(client: TestClient, tmp_path: Path) -> None:
    ids = _setup(tmp_path)
    cid, sid = ids["character"], ids["story"]
    body = {"kinds": ["candidate", "reference", "old_panel", "history"], "older_than_days": 30, "character_id": cid}
    found = {i["path"] for i in client.post("/api/character-images/cleanup/preview", json=body).json()["items"]}
    # 新しい候補・いまの参照画像・いまのコマ・ブックマークした履歴・データセットは入らない
    assert found == {
        f"outputs/manga/refs/candidates/char{cid}_12345_abcdef.png",
        f"outputs/manga/refs/char{cid}_dddd0002.png",
        f"outputs/manga/panels/story{sid}_scene0_bbbb2222.png",
        "outputs/history/h1.png",
    }
    # 選んだものだけ(一覧に無いものは無視)
    chosen = [f"outputs/manga/refs/candidates/char{cid}_12345_abcdef.png", "outputs/history/h1.png",
              f"outputs/manga/refs/char{cid}_cccc0001.png"]
    result = client.post("/api/character-images/cleanup", json={**body, "paths": chosen}).json()
    assert result["deleted"] == 2 and len(result["trash_ids"]) == 2
    assert not (tmp_path / chosen[0]).exists() and not (tmp_path / chosen[1]).exists() and (tmp_path / chosen[2]).is_file()
    assert (tmp_path / f"outputs/manga/refs/char{cid}_dddd0002.png").is_file()
    # 生成履歴は行からも外れ、ごみ箱から戻せる
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT image_paths FROM generation_history WHERE id = ?", (ids["history"],)).fetchone()
    finally:
        conn.close()
    assert "h1.png" not in row["image_paths"]
    for entry_id in result["trash_ids"]:
        trash.restore(entry_id)
    assert (tmp_path / chosen[0]).is_file() and (tmp_path / chosen[1]).is_file()


def test_cleanup_of_everything_includes_other_history(client: TestClient, tmp_path: Path) -> None:
    ids = _setup(tmp_path)
    body = {"kinds": ["candidate", "history"], "older_than_days": 30}
    found = {i["path"] for i in client.post("/api/character-images/cleanup/preview", json=body).json()["items"]}
    assert "outputs/history/o1.png" in found and f"outputs/manga/refs/candidates/char{ids['other']}_1_aaaaaa.png" in found
    assert "outputs/history/h2.png" not in found
    # 種類を選ばないものは入らない・paths が無ければ止める
    assert not any("panels" in p for p in found)
    assert client.post("/api/character-images/cleanup", json=body).status_code == 422
