"""
物語の編集画面(コマ割り・セリフ・タグ・登場人物をまとめて直し、上書き保存/別名保存する)のテスト。
NovelAI には接続しない。DB とコマの絵の置き場所は一時フォルダにする。

実行方法:
  uv run pytest tests/test_story_edit.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.manga_v2.compose import PanelContent, plan_pages  # noqa: E402
from python.routes import manga_v2  # noqa: E402
from python.routes import story_edit  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    panels = tmp_path / "outputs" / "manga" / "panels"
    monkeypatch.setattr(story_edit, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(story_edit, "_PANEL_DIR", panels)
    app = FastAPI()
    app.include_router(story_edit.router)
    with TestClient(app) as test_client:
        test_client.root = tmp_path  # type: ignore[attr-defined]
        yield test_client


def _story(root: Path) -> dict[str, Any]:
    """3シーンの物語。シーン0・2にコマの絵、シーン0の吹き出しに手動配置がある。"""
    conn = db.get_connection()
    try:
        hina = db.save_character(conn, "陽菜", "1girl, brown hair", None)
        rei = db.save_character(conn, "怜", "1girl, black hair", None)
        story = db.create_story(conn, "放課後", 3, panels_per_page=4)
        db.add_story_scenes(conn, story["id"], [
            {"draft_title": "教室", "draft_text": "陽菜は手を振った。\n「おはよう」\n怜はうなずいた。\n「うん」", "draft_prompt_tags": "classroom"},
            {"draft_title": "廊下", "draft_text": "二人は歩いた。", "draft_prompt_tags": "hallway"},
            {"draft_title": "屋上", "draft_text": "怜は空を見上げた。\n「いい天気」", "draft_prompt_tags": "rooftop"},
        ])
        scenes = db.list_story_scenes(conn, story["id"])
        for scene in scenes:
            db.set_scene_characters(conn, scene["id"], [hina["id"], rei["id"]])
        (root / "outputs" / "manga" / "panels").mkdir(parents=True)
        for scene in (scenes[0], scenes[2]):
            path = f"outputs/manga/panels/s{scene['id']}.png"
            (root / path).write_bytes(b"png")
            db.upsert_manga_panel(conn, story["id"], scene["id"], path, 7, 832, 1216)
        db.set_manga_v2_overrides(conn, story["id"], {f"{scenes[0]['id']}:0:b0": [0.1, 0.2], f"scale:{scenes[1]['id']}:0:b0": [1.5]})
        return {"id": story["id"], "scenes": [s["id"] for s in scenes], "hina": hina["id"], "rei": rei["id"]}
    finally:
        conn.close()


def _edited(client: TestClient, story_id: int) -> dict[str, Any]:
    res = client.get(f"/api/story/{story_id}/edit")
    assert res.status_code == 200, res.text
    return res.json()


def test_snapshot_reads_dialogue_and_speakers(client: TestClient) -> None:
    s = _story(client.root)  # type: ignore[attr-defined]
    data = _edited(client, s["id"])
    first = data["scenes"][0]
    assert first["dialogue_custom"] is False
    assert [line["text"] for line in first["dialogue"]] == ["おはよう", "うん"]
    # 話し手は合成と同じ推定の結果
    conn = db.get_connection()
    try:
        scene = db.list_story_scenes(conn, s["id"])[0]
    finally:
        conn.close()
    assert [line["speaker_id"] for line in first["dialogue"]] == manga_v2._scene_speakers(scene)[0]
    assert first["panel"] == {"image_path": f"outputs/manga/panels/s{s['scenes'][0]}.png", "stale": False}
    assert data["scenes"][1]["panel"] is None and data["panels_per_page"] == 4


def _body(data: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "title", "text", "prompt_tags", "narration", "sfx", "page_index", "characters")
    return {
        "title": data["title"],
        "panels_per_page": data["panels_per_page"],
        "custom_pages": data["custom_pages"],
        "scenes": [{k: scene[k] for k in keys} for scene in data["scenes"]],
    }


def test_overwrite_reorders_adds_deletes_and_marks_changed_panels(client: TestClient) -> None:
    s = _story(client.root)  # type: ignore[attr-defined]
    body = _body(_edited(client, s["id"]))
    first, second, third = body["scenes"]
    # 3番目を先頭に、2番目を消し、新しいシーンを足す。1番目はタグを変え、セリフを1行ずつ決める
    first["prompt_tags"] = "classroom, morning"
    first["dialogue"] = [{"text": "おはよう!", "speaker_id": s["rei"]}, {"text": "  ", "speaker_id": None}]
    new = {"text": "", "prompt_tags": "park", "characters": [{"id": s["hina"]}], "page_index": 1}
    third["page_index"], first["page_index"] = 0, 0
    body.update(custom_pages=True, scenes=[third, first, new])
    res = client.put(f"/api/story/{s['id']}/edit", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["stale_panels"] == 1

    data = _edited(client, s["id"])
    assert [sc["title"] for sc in data["scenes"]] == ["屋上", "教室", None]
    assert [sc["page_index"] for sc in data["scenes"]] == [0, 0, 1] and data["custom_pages"] is True
    rooftop, classroom, park = data["scenes"]
    # タグを変えたコマは描き直し待ち、変えていないコマはそのまま
    assert classroom["panel"]["stale"] is True and rooftop["panel"]["stale"] is False and park["panel"] is None
    assert classroom["dialogue_custom"] is True
    assert classroom["dialogue"] == [{"text": "おはよう!", "speaker_id": s["rei"]}]
    # 消したシーンの大きさの調整は捨て、残るシーンの手動配置は残す
    conn = db.get_connection()
    try:
        assert db.get_manga_v2_overrides(conn, s["id"]) == {f"{s['scenes'][0]}:0:b0": [0.1, 0.2]}
        scene = next(sc for sc in db.list_story_scenes(conn, s["id"]) if sc["id"] == classroom["id"])
        # 吹き出しと話し手は、決めたセリフを使う(本文は変えていない)
        assert manga_v2._lettering(scene)[0] == ["おはよう!"]
        assert manga_v2._scene_speakers(scene)[0] == [s["rei"]]
        assert scene["draft_text"] == "陽菜は手を振った。\n「おはよう」\n怜はうなずいた。\n「うん」"
        # 描き直し待ちのコマは、生成済みを飛ばすときも描き直す対象
        assert [p["stale"] for p in db.list_manga_panels(conn, s["id"])] == [0, 1]
    finally:
        conn.close()
    # ページの区切りは合成にも効く
    contents = [PanelContent(None, [], [], key=str(sc["id"])) for sc in data["scenes"]]
    assert manga_v2._page_groups(s["id"], contents) == [0, 0, 1]


def test_save_as_makes_a_new_story_and_leaves_the_original(client: TestClient) -> None:
    s = _story(client.root)  # type: ignore[attr-defined]
    body = _body(_edited(client, s["id"]))
    body["scenes"][2]["prompt_tags"] = "rooftop, sunset"
    body["title"] = "放課後(別案)"
    res = client.post(f"/api/story/{s['id']}/save-as", json=body)
    assert res.status_code == 200, res.text
    result = res.json()
    assert result["copied_panels"] == 2 and result["stale_panels"] == 1

    copy = _edited(client, result["story_id"])
    assert copy["title"] == "放課後(別案)" and len(copy["scenes"]) == 3
    new_ids = [sc["id"] for sc in copy["scenes"]]
    assert not set(new_ids) & set(s["scenes"])
    # 絵はファイルを写して使う(元のファイルとは別)
    first_panel = copy["scenes"][0]["panel"]
    assert first_panel["stale"] is False and first_panel["image_path"] != f"outputs/manga/panels/s{s['scenes'][0]}.png"
    assert (client.root / first_panel["image_path"]).is_file()  # type: ignore[attr-defined]
    assert copy["scenes"][2]["panel"]["stale"] is True
    conn = db.get_connection()
    try:
        # 手動配置はシーンIDを付け替えて引き継ぐ
        overrides = db.get_manga_v2_overrides(conn, result["story_id"])
        assert overrides == {f"{new_ids[0]}:0:b0": [0.1, 0.2], f"scale:{new_ids[1]}:0:b0": [1.5]}
        # 本文は元の書き方のまま写す
        assert db.list_story_scenes(conn, result["story_id"])[0]["draft_text"] == "陽菜は手を振った。\n「おはよう」\n怜はうなずいた。\n「うん」"
    finally:
        conn.close()
    # 元の物語は変わらない
    original = _edited(client, s["id"])
    assert original["title"] is None and original["scenes"][2]["prompt_tags"] == "rooftop"
    assert original["scenes"][2]["panel"]["stale"] is False


def test_rejected_edit_changes_nothing(client: TestClient) -> None:
    s = _story(client.root)  # type: ignore[attr-defined]
    body = _body(_edited(client, s["id"]))
    conn = db.get_connection()
    try:
        pregnant = next(st["id"] for st in db.list_dataset_states(conn) if st["name"] == "妊娠してお腹が大きい")
    finally:
        conn.close()
    body["scenes"][0]["prompt_tags"] = "changed"
    body["scenes"][1]["characters"][0]["state_id"] = pregnant
    assert client.put(f"/api/story/{s['id']}/edit", json=body).status_code == 403
    assert _edited(client, s["id"])["scenes"][0]["prompt_tags"] == "classroom"
    body["scenes"][1]["characters"][0]["state_id"] = None
    body["scenes"][1]["id"] = 99999
    assert client.put(f"/api/story/{s['id']}/edit", json=body).status_code == 422
    body["scenes"][1]["id"] = body["scenes"][0]["id"]
    assert client.put(f"/api/story/{s['id']}/edit", json=body).status_code == 422


def test_page_groups_split_pages_by_scene() -> None:
    # 区切りに従い、テンプレート(4コマ)より多いページはそのページの中だけで分ける
    plans = plan_pages("grid4", 7, None, [0, 0, 1, 1, 1, 1, 1])
    assert [(p.start, p.end) for p in plans] == [(0, 2), (2, 6), (6, 7)]
    # 区切りが無ければこれまでどおり
    assert [(p.start, p.end) for p in plan_pages("grid4", 7)] == [(0, 4), (4, 7)]
