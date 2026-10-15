"""
作品(物語)の書き出し・取り込み(work_transfer.py と /api/library/books/{id}/export・import)のテスト。

実行方法:
  uv run pytest tests/test_work_transfer.py -v
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

from python import character_transfer, content_guard, db, work_transfer  # noqa: E402
from python.routes import library  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    for module in (work_transfer, character_transfer):
        monkeypatch.setattr(module, "_PROJECT_ROOT", tmp_path)
    content_guard.reset_cache()
    app = FastAPI()
    app.include_router(library.router)
    yield TestClient(app)
    content_guard.reset_cache()


def _png(tmp_path: Path, rel: str, color: str = "red") -> str:
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(tmp_path / rel)
    return rel


def _story(tmp_path: Path, tags: list[str], minor: bool = False) -> tuple[int, list[int]]:
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "[漫画] 雨の日", len(tags), 4)
        db.set_story_title(conn, story["id"], "雨の日")
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": str(i), "draft_text": f"本文{i}", "draft_prompt_tags": t, "novelai_text": f"本文{i}"} for i, t in enumerate(tags)
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        mio = db.save_character(conn, "みお", "1girl, brown hair")
        db.update_character_sheet(conn, mio["id"], {"is_minor": 1} if minor else {"is_adult": 1})
        db.update_character_reference(conn, mio["id"], _png(tmp_path, "outputs/manga/refs/mio.png", "blue"))
        for scene in scenes:
            db.set_scene_characters(conn, scene["id"], [mio["id"]], {mio["id"]: "smile"})
            db.upsert_manga_panel(conn, story["id"], scene["id"], _png(tmp_path, f"outputs/manga/panels/s{scene['id']}.png"), 7, 8, 8)
        conn.execute(
            "UPDATE stories SET manga_v2_overrides = ? WHERE id = ?",
            (json.dumps({f"{scenes[0]['id']}:0:bubble:0": [0.1, 0.2], f"scale:{scenes[0]['id']}:0:bubble:0": [1.2, 0]}), story["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    # 合成したページ: いまの版(abc12345)と、前の版(old00000。書き出さない)
    for token in ("old00000", "abc12345"):
        _png(tmp_path, f"outputs/manga/v2/story{story['id']}_page0_{token}.png")
        _png(tmp_path, f"outputs/manga/v2/story{story['id']}_final_{token}.png")
    conn = db.get_connection()
    try:
        db.update_story_final_image(conn, story["id"], f"outputs/manga/v2/story{story['id']}_final_abc12345.png")
    finally:
        conn.close()
    return story["id"], [s["id"] for s in scenes]


def _export(client: TestClient, story_id: int) -> tuple[bytes, int]:
    res = client.get(f"/api/library/books/{story_id}/export")
    assert res.status_code == 200, res.text
    return res.content, int(res.headers["x-skipped-images"])


def test_round_trip_makes_a_new_story(client: TestClient, tmp_path: Path) -> None:
    story_id, _ = _story(tmp_path, ["1girl, cafe", "1girl, rain"])
    data, skipped = _export(client, story_id)
    assert skipped == 0
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "work.json" in names and sum(n.startswith("panels/") for n in names) == 2
    # 合成したページは、いまの版だけ(ページ1枚と、つなげた1枚)
    assert sorted(n.split("_", 1)[1] for n in names if n.startswith("composed/")) == sorted(
        [f"story{story_id}_final_abc12345.png", f"story{story_id}_page0_abc12345.png"])
    plan = client.post("/api/library/books/import/preview", files={"file": ("w.zip", data)}).json()
    assert plan["title"] == "雨の日" and plan["scenes"] == 2 and plan["characters"][0]["action"] == "existing"
    # 同じ名前のキャラは使わず、別名で新しく入れる
    res = client.post(
        "/api/library/books/import", files={"file": ("w.zip", data)},
        data={"choices": json.dumps([{"index": 0, "action": "rename", "name": "みお(取り込み)"}])},
    )
    assert res.status_code == 200, res.text
    new_id = res.json()["story_id"]
    assert new_id != story_id and res.json()["skipped"] == 0
    conn = db.get_connection()
    try:
        story = db.get_story(conn, new_id)
        assert story["title"] == "雨の日" and [s["draft_prompt_tags"] for s in story["scenes"]] == ["1girl, cafe", "1girl, rain"]
        cast = story["scenes"][0]["characters"][0]
        assert cast["name"] == "みお(取り込み)" and cast["action_tags"] == "smile" and cast["is_adult"] == 1
        assert (tmp_path / cast["reference_image_path"]).is_file()
        panels = db.list_manga_panels(conn, new_id)
        assert len(panels) == 2 and all((tmp_path / p["image_path"]).is_file() for p in panels)
        # 吹き出しの位置は新しいシーンの id に付け替わる
        first = story["scenes"][0]["id"]
        overrides = json.loads(conn.execute("SELECT manga_v2_overrides FROM stories WHERE id = ?", (new_id,)).fetchone()[0])
        assert set(overrides) == {f"{first}:0:bubble:0", f"scale:{first}:0:bubble:0"}
    finally:
        conn.close()
    # 取り込んだ物語の版として置き直し、本棚が見る final_image_path に付く
    conn = db.get_connection()
    try:
        final = db.get_story(conn, new_id)["final_image_path"]
    finally:
        conn.close()
    token = final.rsplit("_", 1)[1].removesuffix(".png")
    assert (tmp_path / final).is_file() and (tmp_path / f"outputs/manga/v2/story{new_id}_page0_{token}.png").is_file()


def test_sexual_scenes_with_minors_are_exported_without_images(client: TestClient, tmp_path: Path) -> None:
    story_id, _ = _story(tmp_path, ["1girl, cafe", "1girl, nude, bed"], minor=True)
    data, skipped = _export(client, story_id)
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("work.json"))
    # 性的な場面のコマの画像と、合成したページは入らない(本文とタグは入る)
    assert skipped == 1 and len(manifest["panels"]) == 1 and manifest["composed"] == [] and len(manifest["scenes"]) == 2
    # 取り込むときも、この PC のキャラで判定し直す(書き出し側で外していなくても入れない)
    conn = db.get_connection()
    try:
        story = db.get_story(conn, story_id)
        mio = story["scenes"][0]["characters"][0]["id"]
        manifest["panels"].append({"scene": 1, "file": manifest["panels"][0]["file"], "seed": 1, "width": 8, "height": 8})
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as dst:
            for name in src.namelist():
                if name != "work.json":
                    dst.writestr(name, src.read(name))
            dst.writestr("work.json", json.dumps(manifest))
        result = work_transfer.import_work(conn, manifest, zipfile.ZipFile(io.BytesIO(buf.getvalue())), [mio])
        assert result["skipped"] == 1 and len(db.list_manga_panels(conn, result["story_id"])) == 1
    finally:
        conn.close()


def test_bad_zip_is_refused(client: TestClient) -> None:
    assert client.post("/api/library/books/import/preview", files={"file": ("x.zip", b"nope")}).status_code == 422
