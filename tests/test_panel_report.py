"""
漫画v2: コマの絵の自動の確かめに足した、後ろ姿の向きと人数の重複の確かめ(panel_check.py)と、描き直さずに怪しいコマを
知らせる panel-report のテスト。判定モデルは差し替える。

実行方法:
  uv run pytest tests/test_panel_report.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, panel_check  # noqa: E402
from python.panel_check import check_panel, counts_to_check, is_back_view, redraw_tags  # noqa: E402
from python.routes import manga_v2  # noqa: E402

# 2026-11 実機の判定の値(後ろ姿のはずが正面を向いたコマ / 後ろ姿にできたコマ)
FACING = {"1girl": 1.0, "looking at viewer": 0.91}
BACK = {"1girl": 0.99, "from behind": 0.78, "facing away": 0.56}


def test_back_view_detection() -> None:
    assert is_back_view("underwater, pov, from behind, back")
    assert is_back_view("lying on stomach, facing away")
    assert not is_back_view("from behind, looking back, smile")  # 振り向いて顔を見せる
    assert not is_back_view("back of hand")


def test_facing_check() -> None:
    assert not check_panel(FACING, [], 1, back_view=True).facing_ok
    assert check_panel(BACK, [], 1, back_view=True).ok
    # 後ろ姿のコマでなければ、こちらを見ていても向きは外れにしない
    assert check_panel(FACING, [], 1).ok
    problems = check_panel(FACING, [], 1, back_view=True).problems()
    assert problems == ["向き(後ろ姿のはずが、こちらを向いている)"]
    tags = redraw_tags(check_panel(FACING, [], 1, back_view=True), 1, 1)
    assert "1.5::from behind::" in tags and "-1.5::looking at viewer::" in tags


def test_duplicate_people_check() -> None:
    assert counts_to_check(["1girl, brown hair"], "beach") == (1, 0)
    assert counts_to_check(["1girl", "1boy"], "beach") == (1, 1)
    assert counts_to_check(["1girl", "1girl", "1girl"], "beach") is None  # 3人以上は数え分けが当てにならない
    assert counts_to_check(["1girl"], "crowd, festival") is None  # 背景の人を描くコマ
    assert counts_to_check(["cat"], "beach") is None  # 性別の分からないキャラ

    twin = check_panel({"2girls": 0.8, "multiple girls": 0.8}, [], 1, counts=(1, 0))
    assert twin.extra_people == 1 and not twin.ok
    assert "人数が1人多い(同じキャラの重複など)" in twin.problems()
    assert "solo" in redraw_tags(twin, 1, 1)
    pair = check_panel({"1girl": 0.9, "1boy": 0.9}, [], 2, counts=(1, 1))
    assert pair.extra_people == 0 and pair.ok
    assert check_panel({"2girls": 0.8, "1boy": 0.9}, [], 2, counts=(1, 1)).extra_people == 1


def test_panel_report_lists_only_suspicious_panels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    conn = db.get_connection()
    try:
        for state in db.list_redraw_states(conn):
            db.delete_redraw_state(conn, state["id"])
        story = db.create_story(conn, "x", 3, 4)
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": "1", "draft_text": "a", "draft_prompt_tags": "underwater, from behind", "novelai_text": "x"},
            {"draft_title": "2", "draft_text": "b", "draft_prompt_tags": "underwater, from behind", "novelai_text": "x"},
            {"draft_title": "3", "draft_text": "c", "draft_prompt_tags": "beach", "novelai_text": "x"},
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        girl = db.save_character(conn, "ゆら", "1girl, brown hair", None)
        for scene, name in zip(scenes, ("facing.png", "back.png", "twin.png")):
            db.set_scene_characters(conn, scene["id"], [girl["id"]])
            Image.new("RGB", (8, 8)).save(tmp_path / name)
            db.upsert_manga_panel(conn, story["id"], scene["id"], name, 1, 8, 8)
    finally:
        conn.close()
    sea = {"underwater": 0.9}
    probabilities = {
        "facing.png": FACING | sea,
        "back.png": BACK | sea,
        "twin.png": {"2girls": 0.8, "multiple girls": 0.9, "beach": 0.9},
    }
    monkeypatch.setattr(panel_check, "tagger_vocabulary", lambda: {"underwater", "beach"})
    monkeypatch.setattr(panel_check, "tag_probabilities", lambda image: probabilities[image.info["name"]])
    real_open = Image.open

    def tagged_open(path, *args, **kwargs):
        image = real_open(path, *args, **kwargs)
        image.info["name"] = Path(path).name
        return image

    monkeypatch.setattr(manga_v2.Image, "open", tagged_open)
    app = FastAPI()
    app.include_router(manga_v2.router)
    client = TestClient(app)

    report = client.post(f"/api/manga-v2/{story['id']}/panel-report", json={}).json()
    assert report["checked"] == 3
    assert [(f["scene_index"], f["problems"]) for f in report["flagged"]] == [
        (0, ["向き(後ろ姿のはずが、こちらを向いている)"]),
        (2, ["人数が1人多い(同じキャラの重複など)"]),
    ]
    only = client.post(f"/api/manga-v2/{story['id']}/panel-report", json={"scene_indexes": [1]}).json()
    assert only == {"checked": 1, "flagged": []}
