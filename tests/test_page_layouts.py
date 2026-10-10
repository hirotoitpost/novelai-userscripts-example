"""
取り込んだ作品のコマ割りを写して漫画にする仕組みのテスト(コマ割りの変換・ページへの割り付け・物語への写し・
1ページ=1話の構成)。NovelAI には接続しない。DB とファイルの保存先は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_page_layouts.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.manga_draft import episode_system  # noqa: E402
from python.manga_v2.compose import LetteringStyle, PanelContent, compose_pages  # noqa: E402
from python.manga_v2.layout import PAGE_HEIGHT, PAGE_WIDTH, layout_rects, normalize_boxes, scene_rects  # noqa: E402
from python.manga_v2.lettering import resolve_font  # noqa: E402
from python.routes import manga_import as import_routes  # noqa: E402
from python.routes import manga_v2  # noqa: E402
from python.routes.manga_draft import _import_structure  # noqa: E402
from python.routes.story import router as story_router  # noqa: E402

# 取り込んだページ(幅1000×高さ1500): 上に横長1コマ、下に左右2コマ(読む順は右→左)
PAGE = (1000, 1500)
BOXES = [(50, 50, 900, 600), (520, 700, 430, 750), (50, 700, 430, 750)]


def test_normalize_and_scale_layout() -> None:
    layout = normalize_boxes(BOXES, *PAGE)
    assert layout[0] == (0.05, 0.0333, 0.95, 0.4333)
    rects = layout_rects(layout)
    # 出力ページ(1200×1700)に同じ割合で置く
    assert rects[0] == (60, round(0.0333 * PAGE_HEIGHT), 1140, round(0.4333 * PAGE_HEIGHT))
    assert all(0 <= x0 < x1 <= PAGE_WIDTH and 0 <= y0 < y1 <= PAGE_HEIGHT for x0, y0, x1, y1 in rects)


def test_scene_rects_fall_back_to_template() -> None:
    layout = normalize_boxes(BOXES, *PAGE)
    rects = scene_rects([layout], "stack2", 5)
    assert rects[:3] == layout_rects(layout)
    # 写したコマ割りを使い切ったら、テンプレート(上下2コマ)で続ける
    assert rects[3][1] < rects[4][1]


def test_compose_pages_follow_layouts() -> None:
    layout = normalize_boxes(BOXES, *PAGE)
    style = LetteringStyle(font_path=resolve_font(None), sfx_font_path=resolve_font(None))
    panels = [PanelContent(None, key=str(i)) for i in range(5)]
    pages = compose_pages("stack2", panels, style, [layout])
    # 1ページ目は写したコマ割りの3コマ、残り2コマはテンプレートの1ページ
    assert len(pages) == 2


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(import_routes, "_IMPORT_DIR", tmp_path / "imports")
    app = FastAPI()
    app.include_router(story_router)
    app.include_router(manga_v2.router)
    with TestClient(app) as test_client:
        yield test_client


def _import_with_pages(tmp_path: Path) -> int:
    """読み取り済みの取り込み(2ページ)を DB とページ画像で作る。"""
    conn = db.get_connection()
    try:
        item = db.create_manga_import(conn, "参考", 2)
        panel = {"people": 1, "shot": "medium", "text_blocks": 1, "role": "導入", "emotion": ""}
        analysis = {
            "pages": [
                {"panels": [{**panel, "box": list(b)} for b in BOXES], "overlays": 0},
                {"panels": [{**panel, "box": [50, 50, 900, 1400], "role": "オチ"}], "overlays": 0},
            ]
        }
        db.update_manga_import(conn, item["id"], status="analyzed", analysis=analysis)
    finally:
        conn.close()
    folder = tmp_path / "imports" / str(item["id"])
    folder.mkdir(parents=True)
    for index in range(2):
        Image.new("RGB", PAGE, "white").save(folder / f"page_{index + 1:03d}.png")
    return item["id"]


def test_put_page_layouts(client: TestClient, tmp_path: Path) -> None:
    import_id = _import_with_pages(tmp_path)
    story = client.post("/api/story/scripted", json={"title": "t", "scenes": [{"text": "「a」"}] * 4}).json()

    res = client.put(f"/api/manga-v2/{story['id']}/page-layouts", json={"import_id": import_id})
    assert res.status_code == 200, res.text
    assert res.json() == {"pages": 2, "panels": 4}
    conn = db.get_connection()
    try:
        layouts = db.get_manga_v2_page_layouts(conn, story["id"])
    finally:
        conn.close()
    assert [len(layout) for layout in layouts] == [3, 1]

    # null でテンプレートに戻す
    assert client.put(f"/api/manga-v2/{story['id']}/page-layouts", json={"import_id": None}).json()["pages"] == 0
    assert client.put(f"/api/manga-v2/{story['id']}/page-layouts", json={"import_id": 999}).status_code == 404


def test_import_structure_is_one_page_per_episode(client: TestClient, tmp_path: Path) -> None:
    import_id = _import_with_pages(tmp_path)
    structure = _import_structure(import_id)
    assert [len(episode) for episode in structure] == [3, 1]
    assert "役割: オチ" in structure[1][0]


def test_episode_system_uses_panel_count() -> None:
    assert "ちょうど4コマ" in episode_system(4) and "起承転結" in episode_system(4)
    three = episode_system(3)
    assert "ちょうど3コマ" in three and "ちょうど3つ" in three and "最後のコマにオチ" in three
    # JSON の例はそのまま残る
    assert '{"panels":[' in three
