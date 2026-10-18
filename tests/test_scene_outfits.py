"""場面ごとの服装: 台本の outfits を場面に残し、コマの生成ではキャラシートのふだんの服装の代わりに使う。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.character_sheet import character_prompt_tags  # noqa: E402
from python.manga_draft import DraftCharacter, episode_system, panel_to_scene, parse_episode  # noqa: E402


def test_script_keeps_outfits_of_drawn_people() -> None:
    cast = [DraftCharacter(id=1, name="陽菜", profile=""), DraftCharacter(id=2, name="怜", profile="")]
    data = {"panels": [{
        "characters": ["陽菜", "怜"],
        "outfits": {"陽菜": "swimsuit, swim_cap", "怜": "", "知らない人": "yukata"},
        "lines": [], "prompt_tags": "2girls, poolside",
    }]}
    (panel,) = parse_episode(data, cast, 1)
    # 描く人物の分だけ、タグを整えて残す(空・知らない人は捨てる)
    assert panel["outfits"] == {"陽菜": "swimsuit, swim cap"}
    scene = panel_to_scene(panel, cast)
    assert scene["character_outfits"] == {1: "swimsuit, swim cap"}
    assert "outfits は、ふだんの服装と違う服を着ている人物だけ" in episode_system(4)


def test_scene_outfit_replaces_the_usual_outfit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    conn = db.get_connection()
    try:
        hina = db.save_character(conn, "白石 陽菜", "1girl, brown hair", None)
        rei = db.save_character(conn, "黒澤 怜", "1girl, black hair", None)
        for c in (hina, rei):
            db.update_character_sheet(conn, c["id"], {"outfit_tags": "school uniform, cardigan"})
        story = db.create_story(conn, "プール", 1)
        db.add_story_scenes(conn, story["id"], [{"draft_title": None, "draft_text": "x", "draft_prompt_tags": "poolside"}])
        scene = db.list_story_scenes(conn, story["id"])[0]
        db.set_scene_characters(conn, scene["id"], [hina["id"], rei["id"]], {hina["id"]: "smile"}, {hina["id"]: "swimsuit"})
        by_scene = db.characters_by_scene(conn, story["id"])[scene["id"]]
        tags = {c["name"]: character_prompt_tags(c) for c in by_scene}
        # 服装を書いた人は水着、書かなかった人はふだんの服装
        assert "swimsuit" in tags["白石 陽菜"] and "school uniform" not in tags["白石 陽菜"]
        assert "school uniform" in tags["黒澤 怜"]
        # 登場人物だけを付け替えたときは、書いた服装・動作を残す
        db.set_scene_characters(conn, scene["id"], [hina["id"]])
        (only,) = db.characters_by_scene(conn, story["id"])[scene["id"]]
        assert only["outfit_tags"] == "swimsuit" and only["action_tags"] == "smile"
        # キャラシートのふだんの服装は変わらない
        assert db.get_character(conn, hina["id"])["outfit_tags"] == "school uniform, cardigan"
    finally:
        conn.close()
