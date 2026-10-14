"""
台本と絵のずれを減らす処理(panel_check と、manga_v2 の確かめと描き直し)のテスト。判定モデル・画像生成・文章モデルは
差し替える。

実行方法:
  uv run pytest tests/test_panel_check.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, panel_check  # noqa: E402
from python.panel_check import apply_review, check_panel, checkable_tags, is_person, redraw_tags  # noqa: E402

VOCAB = {"cafe", "coffee", "pancake", "invisible", "open door", "surprised", "holding cup", "close-up", "night", "rock"}


def test_checkable_tags_skip_people_style_and_expressions() -> None:
    tags = checkable_tags("1boy, cafe, close-up, night, Coffee, unknown thing", ["surprised, holding cup", "invisible"], VOCAB)
    assert tags == ["cafe", "coffee", "holding cup", "invisible"]
    assert is_person("1girl, brown hair") and not is_person("white fox, adult fox, animal")


def test_check_panel_counts_people_and_tags() -> None:
    probabilities = {"1girl": 0.98, "1boy": 0.97, "cafe": 0.9, "coffee": 0.1}
    result = check_panel(probabilities, ["cafe", "coffee"], 2)
    assert result.score == 0.5 and result.people_ok and result.missing == ["coffee"] and result.ok
    assert not check_panel(probabilities, ["coffee", "invisible"], 2).ok
    # 確かめるタグが1つだけなら、点数では外れにしない
    assert check_panel(probabilities, ["coffee"], 2).ok
    # 人数: 2人のはずが1人、人のいないはずのコマに人
    assert not check_panel({"1girl": 0.9}, [], 2).people_ok
    assert not check_panel({"1girl": 0.9}, [], 0).people_ok
    assert check_panel({"no humans": 0.9}, [], 0).people_ok and check_panel({"2girls": 0.8}, [], 2).people_ok


def test_redraw_emphasises_missing_tags_and_suppresses_people() -> None:
    missing = check_panel({"1boy": 0.9, "1girl": 0.9}, ["coffee", "invisible"], 2)
    assert redraw_tags(missing, 2, 1) == ["1.5::coffee::", "1.5::invisible::"]
    assert redraw_tags(missing, 2, 2) == ["2::coffee::", "2::invisible::"]
    empty = check_panel({"1girl": 0.9}, [], 0)
    assert redraw_tags(empty, 0, 1) == ["no humans", "-1::1girl::", "-1::1boy::"]
    assert redraw_tags(check_panel({"1girl": 0.9}, [], 2), 2, 1) == ["1.5::multiple people::"]


def test_review_adds_needed_props_to_the_script() -> None:
    panels = [
        {"characters": ["蓮"], "lines": [{"speaker": "蓮", "text": "コーヒー寒天…"}], "prompt_tags": "cafe", "fixes": []},
        {"characters": [], "lines": [], "prompt_tags": "cafe, table"},
    ]
    system, user = panel_check.review_prompts("寒天を試食する", panels)
    assert "見えていないと話が通じない" in system and "セリフ 蓮「コーヒー寒天…」" in user
    reviewed = apply_review({"panels": [{"add_tags": ["coffee jelly", "Cafe", "2girls", "spoon"]}, {"add_tags": ""}]}, panels)
    assert reviewed[0]["prompt_tags"] == "cafe, coffee jelly, spoon"
    assert reviewed[0]["fixes"] == ["見直しで作画タグに足した: coffee jelly, spoon"]
    assert reviewed[1] == panels[1] and panels[0]["prompt_tags"] == "cafe"
    with pytest.raises(ValueError):
        apply_review({"panels": []}, panels)


def test_check_and_redraw_keeps_the_better_picture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    from python.models import MangaV2PanelsRequest
    from python.routes import manga_v2

    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    conn = db.get_connection()
    try:
        # 描き直しで作る状態(透明 など)のタグは確かめないので、ここでは状態を無くして、確かめと描き直しだけを見る
        for state in db.list_redraw_states(conn):
            db.delete_redraw_state(conn, state["id"])
        story = db.create_story(conn, "x", 2, 4)
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": "1", "draft_text": "「消えた！」", "draft_prompt_tags": "open door", "novelai_text": "x"},
            {"draft_title": "2", "draft_text": "「おいしい」", "draft_prompt_tags": "cafe, coffee", "novelai_text": "x"},
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        boy = db.save_character(conn, "剛", "1boy", None)
        for scene in scenes:
            # 「透明」は人物の動作のタグに入っている(確かめにも使う)
            actions = {boy["id"]: "invisible"} if scene["scene_index"] == 0 else None
            db.set_scene_characters(conn, scene["id"], [boy["id"]], actions)
        for scene, name in zip(scenes, ("first.png", "good.png")):
            Image.new("RGB", (8, 8)).save(tmp_path / name)
            db.upsert_manga_panel(conn, story["id"], scene["id"], name, 100 + scene["scene_index"], 8, 8)
    finally:
        conn.close()
    # 絵ごとの判定の結果(ファイル名で決める)
    probabilities = {
        "first.png": {"1boy": 0.9},
        "try1.png": {"0boy": 0.9},
        "try2.png": {"1boy": 0.9, "open door": 0.9, "invisible": 0.5},
        "good.png": {"1boy": 0.9, "cafe": 0.9, "coffee": 0.9},
    }
    monkeypatch.setattr(panel_check, "tagger_vocabulary", lambda: VOCAB)
    monkeypatch.setattr(panel_check, "tag_probabilities", lambda image: probabilities[image.info["name"]])
    real_open = Image.open

    def tagged_open(path, *args, **kwargs):
        image = real_open(path, *args, **kwargs)
        image.info["name"] = Path(path).name
        return image

    monkeypatch.setattr(manga_v2.Image, "open", tagged_open)
    prompts: list[str] = []

    async def fake_panels(job, story_id, req, api_key, targets):
        target = targets[0]
        prompts.append(target["draft_prompt_tags"])
        name = f"try{len(prompts)}.png"
        Image.new("RGB", (8, 8)).save(tmp_path / name)
        connection = db.get_connection()
        try:
            db.upsert_manga_panel(connection, story_id, target["id"], name, req.settings.seed, 8, 8)
        finally:
            connection.close()

    monkeypatch.setattr(manga_v2, "_run_panels", fake_panels)

    class Job:
        message = ""

    summary = asyncio.run(manga_v2._check_and_redraw(Job(), story["id"], MangaV2PanelsRequest(), "t", 2))
    assert summary == {"checked": 2, "flagged": 1, "fixed": 1, "unresolved": []}
    # 1回目は描かれなかったタグを 1.5 で、2回目は 2 で強めた
    assert prompts == [
        "open door, 1.5::open door::, 1.5::invisible::",
        "open door, 2::open door::, 2::invisible::",
    ]
    conn = db.get_connection()
    try:
        assert [p["image_path"] for p in db.list_manga_panels(conn, story["id"])] == ["try2.png", "good.png"]
    finally:
        conn.close()
    assert "1コマを描き直しでそろえました" in manga_v2.check_summary_text(summary)
    text = manga_v2.check_summary_text({"checked": 3, "flagged": 2, "fixed": 1, "unresolved": [{"scene": 2, "missing": ["coffee"], "people_ok": True}]})
    assert "そろわなかったコマ: シーン2(coffee)" in text
    assert "ずれは見つかりませんでした" in manga_v2.check_summary_text({"checked": 3, "flagged": 0, "fixed": 0, "unresolved": []})


