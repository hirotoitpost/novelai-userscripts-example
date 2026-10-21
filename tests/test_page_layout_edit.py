"""
ページごとのコマ割りの確認・変更(コマ数からのパターン・ドラッグでの大きさの調整)と、manga style を付けるかのテスト。
NovelAI には接続しない。DB は一時フォルダ(conftest)。

実行方法:
  uv run pytest tests/test_page_layout_edit.py -v
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
from python.manga_v2.compose import plan_pages, slot_rects  # noqa: E402
from python.manga_v2.layout import (  # noqa: E402
    MAX_TREE_PANELS,
    PANEL,
    TEMPLATE_TREES,
    TEMPLATES,
    _content_rects,
    layout_page,
    layout_patterns,
    normalize_tree,
    tree_count,
    tree_layout,
    tree_rects,
)
from python.manga_v2.prompt import build_panel_prompt  # noqa: E402
from python.routes import manga_v2  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(manga_v2.router)
    with TestClient(app) as test_client:
        yield test_client


def _story(n: int) -> dict[str, Any]:
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "ページ", n)
        db.add_story_scenes(conn, story["id"], [{"draft_title": f"s{i}", "draft_text": "", "draft_prompt_tags": "x"} for i in range(n)])
        scenes = db.list_story_scenes(conn, story["id"])
        db.upsert_manga_panel(conn, story["id"], scenes[0]["id"], "outputs/manga/panels/a.png", 1, 832, 1216)
        return {"id": story["id"], "scenes": [s["id"] for s in scenes]}
    finally:
        conn.close()


def test_patterns_for_each_count() -> None:
    for count in range(1, MAX_TREE_PANELS + 1):
        patterns = layout_patterns(count)
        assert patterns and len({p["id"] for p in patterns}) == len(patterns)
        for pattern in patterns:
            rects = tree_rects(pattern["tree"])
            assert tree_count(pattern["tree"]) == len(rects) == count
            # 本文領域に収まり、重ならない(面積の合計が1)
            assert all(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 for x0, y0, x1, y1 in rects)
            assert sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects) == pytest.approx(1, abs=0.01)
    # 2段(2・2)は右上から読む
    rects = tree_rects(next(p for p in layout_patterns(4) if p["id"] == "r2-2")["tree"])
    assert rects[0][0] > rects[1][0] and rects[0][1] < rects[2][1]


def test_template_trees_match_templates() -> None:
    for template_id, tree in TEMPLATE_TREES.items():
        assert _content_rects(tree_rects(tree)) == _content_rects(TEMPLATES[template_id].panels)


def test_normalize_tree_checks_and_clamps() -> None:
    tree = normalize_tree({"dir": "rows", "sizes": [3, 1, 0.01], "items": [PANEL, PANEL, {"dir": "cols", "sizes": [1], "items": [PANEL]}]})
    # 割合は合計1、細すぎるものは最小の割合まで広げる。子が1つの分割はその子にする
    assert sum(tree["sizes"]) == pytest.approx(1, abs=0.001) and min(tree["sizes"]) >= 0.07
    assert tree["items"][2] == PANEL
    for bad in ({"dir": "diag", "sizes": [1], "items": [PANEL]}, {"dir": "rows", "sizes": [1], "items": []}, "x",
                {"dir": "rows", "sizes": [1, 2], "items": [PANEL]}):
        with pytest.raises(ValueError):
            normalize_tree(bad)
    # ページのコマ割りにすると、合成で使う輪郭(ページに対する割合)になる
    layout = tree_layout(normalize_tree(TEMPLATE_TREES["grid4"]))
    assert layout_page(layout).panels[0].rect == layout_page({"panels": layout["panels"]}).panels[0].rect
    assert len(layout["panels"]) == 4 and layout["tree"]["dir"] == "rows"


def test_view_edit_and_reset_page_layouts(client: TestClient) -> None:
    s = _story(5)
    view = client.get(f"/api/manga-v2/{s['id']}/page-layout", params={"template": "grid4"}).json()
    # 決めていなければテンプレートの割り付け: 4コマ+1コマ(最後のページは1ページ1コマ)
    assert view["explicit"] is False and [p["count"] for p in view["pages"]] == [4, 1]
    assert view["pages"][0]["tree"] == TEMPLATE_TREES["grid4"] and view["pages"][1]["tree"] == PANEL
    assert view["pages"][0]["scene_ids"] == s["scenes"][:4]

    # 1ページ目を3コマ(上に大ゴマ)に、2ページ目を2コマにする。1コマ目は縦長→横長になるので描き直し待ち
    three = next(p for p in layout_patterns(3) if p["id"] == "r1-2")["tree"]
    res = client.put(f"/api/manga-v2/{s['id']}/page-layout", json={"template": "grid4", "pages": [{"tree": three}, {"tree": TEMPLATE_TREES["stack2"]}]})
    assert res.status_code == 200, res.text
    assert res.json() == {"pages": 2, "stale_panels": 1}
    view = client.get(f"/api/manga-v2/{s['id']}/page-layout").json()
    assert view["explicit"] is True and [p["count"] for p in view["pages"]] == [3, 2]
    assert view["pages"][1]["scene_ids"] == s["scenes"][3:5]
    conn = db.get_connection()
    try:
        layouts = db.get_manga_v2_page_layouts(conn, s["id"])
        assert db.list_manga_panels(conn, s["id"])[0]["stale"] == 1
    finally:
        conn.close()
    # 合成・生成サイズも同じ割り付けに従う
    assert [(p.start, p.end) for p in plan_pages("grid4", 5, layouts)] == [(0, 3), (3, 5)]
    assert len(slot_rects("grid4", 5, layouts)) == 5

    # ドラッグで上の段を広げた木を保存し直す。写したコマ割りは番号で残せる
    dragged = {**layouts[0]["tree"], "sizes": [0.7, 0.3]}
    res = client.put(f"/api/manga-v2/{s['id']}/page-layout", json={"pages": [{"tree": dragged}, {"layout": 1}]})
    assert res.status_code == 200, res.text
    view = client.get(f"/api/manga-v2/{s['id']}/page-layout").json()
    assert view["pages"][0]["tree"]["sizes"] == [0.7, 0.3] and view["pages"][1]["tree"] == TEMPLATE_TREES["stack2"]

    # おかしな木・無い番号は弾く
    assert client.put(f"/api/manga-v2/{s['id']}/page-layout", json={"pages": [{"tree": {"dir": "x"}}]}).status_code == 422
    assert client.put(f"/api/manga-v2/{s['id']}/page-layout", json={"pages": [{"layout": 9}]}).status_code == 422
    assert client.put(f"/api/manga-v2/{s['id']}/page-layout", json={"template": "nope", "pages": [{"tree": PANEL}]}).status_code == 400

    # テンプレートに戻す
    assert client.delete(f"/api/manga-v2/{s['id']}/page-layout").status_code == 200
    assert client.get(f"/api/manga-v2/{s['id']}/page-layout").json()["explicit"] is False


def test_layout_patterns_endpoint(client: TestClient) -> None:
    res = client.get("/api/manga-v2/layout-patterns", params={"count": 6})
    assert res.status_code == 200
    patterns = res.json()
    assert all(len(p["rects"]) == 6 for p in patterns)
    assert client.get("/api/manga-v2/layout-patterns", params={"count": 0}).status_code == 422


def test_manga_style_can_be_left_out() -> None:
    assert build_panel_prompt("1girl", color=False, complexity=None).startswith("manga style, monochrome")
    assert build_panel_prompt("1girl", color=True, complexity=None).startswith("manga style, 1girl")
    assert build_panel_prompt("1girl", color=False, complexity=None, manga_style=False).startswith("monochrome, greyscale")
    assert "manga style" not in build_panel_prompt("1girl", color=True, complexity=None, manga_style=False)
