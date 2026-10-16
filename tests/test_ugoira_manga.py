"""
うごイラを嵌め込んだ漫画(manga_v2/animate.py と /api/manga-v2/{id}/ugoira・/animated)のテスト。
NovelAI には接続しない。DB と画像の保存先は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_ugoira_manga.py -v
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

from python import db, ugoira  # noqa: E402
from python.manga_v2.animate import MAX_STEPS, merge_timeline  # noqa: E402
from python.manga_v2.compose import plan_pages  # noqa: E402
from python.routes import manga_v2  # noqa: E402
from python.routes.story import router as story_router  # noqa: E402


def test_one_ugoira_keeps_its_own_timing() -> None:
    assert merge_timeline([[100, 200, 100]]) == [((0,), 100), ((1,), 200), ((2,), 100)]


def test_two_ugoira_share_one_loop() -> None:
    # 1周 200ms と 300ms → ページは 600ms で両方ちょうど1周し終わる
    steps = merge_timeline([[100, 100], [150, 150]])
    assert sum(d for _, d in steps) == 600
    assert steps[:3] == [((0, 0), 100), ((1, 0), 50), ((1, 1), 50)]
    assert steps[-1] == ((1, 1), 100)


def test_short_gaps_and_long_loops_are_kept_small() -> None:
    # 10ms しかない区切りは前の区切りに含める
    steps = merge_timeline([[100], [90, 10]])
    assert all(d >= 20 for _, d in steps)
    # 最小公倍数が長すぎるときは一番長いうごイラの1周に合わせる
    steps = merge_timeline([[1009], [1013]])
    assert sum(d for _, d in steps) == 1013
    assert len(merge_timeline([[20] * 200])) <= MAX_STEPS
    assert merge_timeline([]) == []


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manga_v2, "_PAGE_DIR", tmp_path / "outputs" / "manga" / "v2")
    monkeypatch.setattr(ugoira, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("python.manga_v2.compose.detect_heads", lambda path: [])
    app = FastAPI()
    app.include_router(story_router)
    app.include_router(manga_v2.router)
    with TestClient(app) as test_client:
        yield test_client


def _png(root: Path, rel: str, color: tuple[int, int, int]) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 400), color).save(path)
    return rel


def _story_with_ugoira(client: TestClient, tmp_path: Path) -> tuple[int, list[dict], int]:
    scenes = [{"text": f"「セリフ{i}」", "prompt_tags": "no humans, scenery"} for i in range(2)]
    story = client.post("/api/story/scripted", json={"title": "試し", "scenes": scenes}).json()
    conn = db.get_connection()
    try:
        rows = db.list_story_scenes(conn, story["id"])
        for scene in rows:
            rel = _png(tmp_path, f"outputs/manga/panels/s{scene['id']}.png", (200, 200, 200))
            db.upsert_manga_panel(conn, story["id"], scene["id"], rel, 1, 400, 400)
        panels = db.list_manga_panels(conn, story["id"])
    finally:
        conn.close()
    project_id = ugoira.create_project("まばたき", f"panel:{panels[0]['id']}", "partial")
    frames = [
        {"path": _png(tmp_path, f"outputs/ugoira/{project_id}/f{i}.png", color), "delay": delay, "tags": ""}
        for i, (color, delay) in enumerate([((255, 0, 0), 300), ((0, 0, 255), 100)])
    ]
    ugoira._store_frames(project_id, frames)
    return story["id"], rows, project_id


def test_animated_page_is_composed_and_exported(client: TestClient, tmp_path: Path) -> None:
    story_id, scenes, project_id = _story_with_ugoira(client, tmp_path)
    res = client.put(f"/api/manga-v2/{story_id}/ugoira", json={"scene_id": scenes[0]["id"], "project_id": project_id})
    assert res.status_code == 200, res.text
    link = res.json()[0]
    assert link["project_id"] == project_id and link["frames"] == 2
    assert not link["missing"] and not link["other_source"] and not link["stale"]

    body = client.post(f"/api/manga-v2/{story_id}/compose", json={"template": "vertical4"}).json()
    assert len(body["pages"]) == 1
    animated = body["animated_pages"][0]
    assert animated and animated.endswith(".webp")
    # 動くコマ(1コマ目)の左下の隅の近く(吹き出しの無いところ)
    x0, _, _, y1 = plan_pages("vertical4", 2)[0].spec.panels[0].rect
    point = (x0 + 20, y1 - 20)
    with Image.open(tmp_path / animated) as image:
        assert image.n_frames == 2
        assert image.size == Image.open(tmp_path / body["pages"][0]).size
        durations = []
        colors = []
        for index in range(image.n_frames):
            image.seek(index)
            frame = image.convert("RGB")
            durations.append(image.info["duration"])
            # 動くコマの色が、うごイラのコマの色になる
            colors.append(frame.getpixel(point))
    assert durations == [300, 100]
    assert colors[0][0] > 200 > colors[0][2] and colors[1][2] > 200 > colors[1][0]

    gif = client.get(f"/api/manga-v2/{story_id}/animated/0", params={"fmt": "gif", "max_side": 600})
    assert gif.status_code == 200 and gif.headers["content-type"] == "image/gif"
    with Image.open(io.BytesIO(gif.content)) as image:
        assert max(image.size) == 600 and image.n_frames == 2
    pixiv = client.get(f"/api/manga-v2/{story_id}/animated/0", params={"fmt": "zip"})
    with zipfile.ZipFile(io.BytesIO(pixiv.content)) as zf:
        meta = json.loads(zf.read("animation.json"))
    assert [f["delay"] for f in meta["frames"]] == [300, 100]
    assert client.get(f"/api/manga-v2/{story_id}/animated/1").status_code == 404

    # 外すと、次の合成からは動かない
    assert client.put(f"/api/manga-v2/{story_id}/ugoira", json={"scene_id": scenes[0]["id"], "project_id": None}).json() == []
    body = client.post(f"/api/manga-v2/{story_id}/compose", json={"template": "vertical4"}).json()
    assert body["animated_pages"] == [None]
    assert client.get(f"/api/manga-v2/{story_id}/animated/0").status_code == 404


def test_links_report_problems(client: TestClient, tmp_path: Path) -> None:
    story_id, scenes, project_id = _story_with_ugoira(client, tmp_path)
    # 別のコマから作ったうごイラ
    res = client.put(f"/api/manga-v2/{story_id}/ugoira", json={"scene_id": scenes[1]["id"], "project_id": project_id})
    assert res.json()[0]["other_source"] is True
    assert client.put(f"/api/manga-v2/{story_id}/ugoira", json={"scene_id": 99999, "project_id": project_id}).status_code == 404
    assert client.put(f"/api/manga-v2/{story_id}/ugoira", json={"scene_id": scenes[0]["id"], "project_id": 99999}).status_code == 404
