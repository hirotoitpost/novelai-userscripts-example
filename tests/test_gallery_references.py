"""
ギャラリーの「似た画像を生成」で引き継ぐ参照画像(GET /api/library/images/references)のテスト。
画像生成の履歴は保存した i2i の元画像とキャラ参照、漫画のコマは描いたときに送ったキャラ参照(記録が無い古いコマは
キャラシートの参照画像と、絵に埋め込まれた強さ)を返す。

実行方法:
  uv run pytest tests/test_gallery_references.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes import library  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(library, "_PROJECT_ROOT", tmp_path)
    db.get_connection().close()
    app = FastAPI()
    app.include_router(library.router)
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def _png(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), "white").save(path)
    return path.as_posix()


def test_history_image_keeps_its_i2i_and_character_references(client) -> None:
    test_client, root = client
    _png(root / "outputs" / "history" / "out.png")
    _png(root / "outputs" / "history" / "i2i.png")
    _png(root / "outputs" / "history" / "ref.png")
    conn = db.get_connection()
    try:
        row = db.record_generation(
            conn, prompt="1girl", negative_prompt=None, model="nai-diffusion-4-5-full", size="[832, 1216]",
            steps=28, scale=5.0, seed=7, chunk_ids=[], image_paths=["outputs/history/out.png"],
            i2i_image_path="outputs/history/i2i.png", i2i_strength=0.5, i2i_noise=0.1,
            character_references=[
                {"image_path": "outputs/history/ref.png", "type": "character", "fidelity": 0.8, "strength": 0.6},
                # ファイルが消えた参照は返さない
                {"image_path": "outputs/history/gone.png", "type": "character", "fidelity": 1.0, "strength": 1.0},
            ],
        )
    finally:
        conn.close()
    refs = test_client.get("/api/library/images/references", params={"key": f"history:{row['id']}:out.png"}).json()
    assert refs["i2i"] == {"image_path": "outputs/history/i2i.png", "strength": 0.5, "noise": 0.1}
    assert [(r["image_path"], r["type"], r["fidelity"], r["strength"]) for r in refs["character_references"]] == [
        ("outputs/history/ref.png", "character", 0.8, 0.6)
    ]


def _panel(root: Path, tags: str, comment: dict | None = None) -> tuple[int, dict[str, int]]:
    """ゆら(正面・後ろ姿の参照)・ひびき(正面の参照)・参照なしの3人が出るコマを1つ作る。"""
    _png(root / "outputs" / "refs" / "yura.png")
    _png(root / "outputs" / "refs" / "yura_back.png")
    _png(root / "outputs" / "refs" / "hibiki.png")
    panel = root / "outputs" / "manga" / "p.png"
    panel.parent.mkdir(parents=True, exist_ok=True)
    info = PngInfo()
    if comment is not None:
        info.add_text("Comment", json.dumps(comment))
    Image.new("RGB", (32, 48), "white").save(panel, pnginfo=info)
    conn = db.get_connection()
    try:
        yura = db.save_character(conn, "九条 ゆら", "1girl", "")
        hibiki = db.save_character(conn, "霧谷 ひびき", "1girl", "")
        nobody = db.save_character(conn, "参照なし", "1girl", "")
        db.update_character_reference(conn, yura["id"], "outputs/refs/yura.png")
        db.update_character_reference(conn, yura["id"], "outputs/refs/yura_back.png", view="back")
        db.update_character_reference(conn, hibiki["id"], "outputs/refs/hibiki.png")
        story_row = db.create_story(conn, "海", 1)
        db.add_story_scenes(conn, story_row["id"], [{"draft_text": "a", "draft_prompt_tags": tags}])
        scene = db.list_story_scenes(conn, story_row["id"])[0]
        db.set_scene_characters(conn, scene["id"], [yura["id"], hibiki["id"], nobody["id"]])
        db.upsert_manga_panel(conn, story_row["id"], scene["id"], "outputs/manga/p.png", 1, 832, 1216)
        panel_id = conn.execute("SELECT id FROM manga_panels").fetchone()["id"]
    finally:
        conn.close()
    return panel_id, {"story": story_row["id"], "scene": scene["id"]}


def test_panel_returns_the_recorded_references(client) -> None:
    test_client, root = client
    panel_id, ids = _panel(root, "beach")
    _png(root / "outputs" / "manga" / "refs" / "used" / "abc.png")
    recorded = [{"image_path": "outputs/manga/refs/used/abc.png", "type": "character&style", "fidelity": 0.9, "strength": 0.4}]
    conn = db.get_connection()
    try:
        db.upsert_manga_panel(conn, ids["story"], ids["scene"], "outputs/manga/p.png", 1, 832, 1216, recorded)
    finally:
        conn.close()
    refs = test_client.get("/api/library/images/references", params={"key": f"panel:{panel_id}"}).json()
    assert [{k: r[k] for k in ("image_path", "type", "fidelity", "strength")} for r in refs["character_references"]] == recorded
    assert refs["notes"] == []


def test_panel_drawn_without_references_has_none(client) -> None:
    test_client, root = client
    # 埋め込みの設定にキャラ参照が無い = 参照を使わずに描いたコマ
    panel_id, _ = _panel(root, "beach", {"seed": 1, "director_reference_strengths": []})
    refs = test_client.get("/api/library/images/references", params={"key": f"panel:{panel_id}"}).json()
    assert refs["character_references"] == []


def test_old_panel_uses_sheet_references_with_embedded_strengths(client) -> None:
    test_client, root = client
    # 記録する前のコマ: 埋め込みの強さ(2人分)と、今のキャラシートの参照(後ろ姿のコマは後ろ姿の参照)
    panel_id, _ = _panel(
        root, "beach, from behind",
        {"director_reference_strengths": [0.3, 0.5], "director_reference_secondary_strengths": [0.0, 0.2]},
    )
    refs = test_client.get("/api/library/images/references", params={"key": f"panel:{panel_id}"}).json()
    by_name = {r["label"]: r for r in refs["character_references"]}
    assert set(by_name) == {"九条 ゆら", "霧谷 ひびき"}
    assert (by_name["九条 ゆら"]["strength"], by_name["九条 ゆら"]["fidelity"]) == (0.3, 1.0)
    assert (by_name["霧谷 ひびき"]["strength"], by_name["霧谷 ひびき"]["fidelity"]) == (0.5, 0.8)
    # 送るときと同じ形(1024x1536)に整えた写し
    for r in refs["character_references"]:
        assert r["image_path"].startswith("outputs/manga/refs/used/")
        with Image.open(root / r["image_path"]) as image:
            assert image.size == (1024, 1536)
    assert refs["notes"]


def test_adopting_a_candidate_swaps_the_recorded_references(client) -> None:
    _, root = client
    _, ids = _panel(root, "beach")
    first = [{"image_path": "a.png", "type": "character&style", "fidelity": 1.0, "strength": 0.6}]
    second = [{"image_path": "b.png", "type": "character&style", "fidelity": 1.0, "strength": 0.3}]
    conn = db.get_connection()
    try:
        db.upsert_manga_panel(conn, ids["story"], ids["scene"], "outputs/manga/p.png", 1, 832, 1216, first)
        candidate = db.add_panel_candidate(conn, ids["story"], ids["scene"], "outputs/manga/c.png", 2, 832, 1216, second)
        db.adopt_panel_candidate(conn, ids["scene"], candidate)
        panel = conn.execute("SELECT * FROM manga_panels").fetchone()
        back = db.list_panel_candidates(conn, ids["scene"])
    finally:
        conn.close()
    assert db.panel_references(dict(panel)) == second
    assert [db.panel_references(c) for c in back] == [first]


def test_other_images_have_no_references(client) -> None:
    test_client, _ = client
    assert test_client.get("/api/library/images/references", params={"key": "illustration:3"}).json() == {
        "i2i": None, "character_references": [], "notes": [], "character": None
    }
    assert test_client.get("/api/library/images/references", params={"key": "history:abc:x.png"}).status_code == 400


def test_record_references_saves_what_was_sent_once(tmp_path: Path) -> None:
    import base64

    from python.novelai_image_v5 import CharacterReferenceInput, reference_image_b64
    from python.routes.manga_v2 import record_references

    _png(tmp_path / "ref.png")
    sent = reference_image_b64((tmp_path / "ref.png").read_bytes())
    refs = [CharacterReferenceInput(sent, 0.333, 0.9), CharacterReferenceInput(sent, 1.0)]
    recorded = record_references(refs, tmp_path)
    # 同じ画像は1つの写しにまとめ、強さは送ったとおり(小数2桁)
    assert recorded[0]["image_path"] == recorded[1]["image_path"]
    assert (recorded[0]["strength"], recorded[0]["fidelity"], recorded[0]["type"]) == (0.33, 0.9, "character&style")
    assert (tmp_path / recorded[0]["image_path"]).read_bytes() == base64.b64decode(sent)
