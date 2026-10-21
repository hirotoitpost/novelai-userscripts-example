"""
コマの絵の背景を消す(前面に重ねたコマで人物だけを見せる)テスト。人物の切り分けのモデル・NovelAI は使わず、
差し替える。DB とコマの絵の置き場所は一時フォルダにする。

実行方法:
  uv run pytest tests/test_panel_cutout.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import anime_seg, db  # noqa: E402
from python.client import get_client  # noqa: E402
from python.manga_v2 import cutout  # noqa: E402
from python.manga_v2.compose import LetteringStyle, PanelContent, compose_page  # noqa: E402
from python.manga_v2.layout import TEMPLATE_TREES, layout_page, normalize_cutout, normalize_floats, tree_layout  # noqa: E402
from python.manga_v2.lettering import resolve_font  # noqa: E402
from python.routes import manga_v2  # noqa: E402


def _circle(image: Image.Image) -> np.ndarray:
    """真ん中の円を人物とみなす(切り分けのモデルの代わり)。"""
    w, h = image.size
    yy, xx = np.mgrid[0:h, 0:w]
    return (((xx - w / 2) ** 2 + (yy - h / 2) ** 2) < (min(w, h) * 0.3) ** 2).astype(np.float32)


@pytest.fixture(autouse=True)
def fake_segmentation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anime_seg, "person_mask", _circle)


def _image(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (400, 400), color).save(path)
    return path


def test_local_mask_is_cached_next_to_the_picture(tmp_path: Path) -> None:
    picture = _image(tmp_path / "panel.png", (0, 255, 0))
    mask = cutout.local_mask(picture)
    assert mask.getpixel((200, 200)) == 255 and mask.getpixel((5, 5)) == 0
    assert cutout.mask_path(picture, "local").is_file()
    # 2回目は残したものを使う(モデルを呼ばない)
    assert cutout.best_mask(picture).getpixel((200, 200)) == 255


def test_mask_from_novelai_results() -> None:
    size = (100, 100)
    transparent = Image.new("RGBA", size, (0, 0, 0, 0))
    transparent.paste((255, 0, 0, 255), (25, 25, 75, 75))
    assert cutout.mask_from_novelai([Image.new("RGB", size, "gray"), transparent], size).getpixel((50, 50)) == 255
    black_white = Image.new("L", size, 0)
    black_white.paste(255, (25, 25, 75, 75))
    mask = cutout.mask_from_novelai([black_white.convert("RGB")], size)
    assert mask.getpixel((50, 50)) == 255 and mask.getpixel((5, 5)) == 0
    with pytest.raises(ValueError):
        cutout.mask_from_novelai([Image.new("RGB", size, (120, 80, 40))], size)


def test_cutout_panel_shows_only_the_person_with_an_outline(tmp_path: Path) -> None:
    back = _image(tmp_path / "back.png", (255, 0, 0))
    front = _image(tmp_path / "front.png", (0, 0, 255))
    floats = [{"rect": [0.2, 0.2, 0.8, 0.6], "after": 1, "border": False,
               "cutout": {"outline": True, "outline_color": "#ffff00", "outline_width": 6}}]
    spec = layout_page(tree_layout(TEMPLATE_TREES["single1"], None, None, floats))
    assert spec.panels[1].cutout == {"outline": True, "outline_color": "#ffff00", "outline_width": 6}
    style = LetteringStyle(font_path=resolve_font(None), sfx_font_path=resolve_font(None))
    page, _ = compose_page(spec, [PanelContent(back, key="0"), PanelContent(front, key="1")], style)
    x0, y0, x1, y1 = spec.panels[1].rect
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    # 人物(円)は前面のコマの絵、そのまわりは後ろのコマが透けて見え、境目には縁取りの色
    assert page.getpixel((cx, cy)) == (0, 0, 255)
    assert page.getpixel((x0 + 5, y0 + 5)) == (255, 0, 0)
    radius = min(x1 - x0, y1 - y0) * 0.3
    ring = page.getpixel((cx, round(cy - radius - 3)))
    assert ring[0] > 200 and ring[1] > 200 and ring[2] < 60


def test_normalize_cutout() -> None:
    assert normalize_cutout(True) == {"outline": False, "outline_color": "#ffffff", "outline_width": 8}
    assert normalize_cutout({"outline": True, "outline_width": 99})["outline_width"] == 40
    assert normalize_floats([{"rect": [0, 0, 0.5, 0.5], "cutout": True}], 1)[0]["cutout"]["outline"] is False
    with pytest.raises(ValueError):
        normalize_cutout({"outline_color": "yellow"})


class _FakeTools:
    def __init__(self) -> None:
        self.calls = 0

    async def remove_background(self, image: bytes) -> list[Image.Image]:
        self.calls += 1
        result = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        result.paste((0, 0, 255, 255), (100, 100, 300, 300))
        return [result, Image.new("RGB", (400, 400), "white"), Image.new("RGB", (400, 400), "white")]


class _FakeClient:
    def __init__(self) -> None:
        self.tools = _FakeTools()


def test_cutout_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    conn = db.get_connection()
    try:
        story = db.create_story(conn, "切り抜き", 1)
        db.add_story_scenes(conn, story["id"], [{"draft_title": None, "draft_text": "", "draft_prompt_tags": "x"}])
        scene = db.list_story_scenes(conn, story["id"])[0]
        (tmp_path / "outputs/manga/panels").mkdir(parents=True)
        _image(tmp_path / "outputs/manga/panels/p.png", (0, 255, 0))
        db.upsert_manga_panel(conn, story["id"], scene["id"], "outputs/manga/panels/p.png", 1, 400, 400)
    finally:
        conn.close()
    fake = _FakeClient()
    app = FastAPI()
    app.include_router(manga_v2.router)
    app.dependency_overrides[get_client] = lambda: fake
    with TestClient(app) as client:
        made = client.post(f"/api/manga-v2/scenes/{scene['id']}/cutout").json()
        assert made["method"] == "local" and (tmp_path / made["preview_path"]).is_file()
        res = client.post(f"/api/manga-v2/scenes/{scene['id']}/cutout/novelai")
        assert res.status_code == 200, res.text
        assert fake.tools.calls == 1
        status = client.get(f"/api/manga-v2/scenes/{scene['id']}/cutout").json()
        assert status["methods"] == ["novelai", "local"] and status["width"] == 400
        # NovelAI の切り抜きがあれば、そちらを使う
        assert cutout.best_mask(tmp_path / "outputs/manga/panels/p.png").getpixel((110, 110)) == 255
        assert client.delete(f"/api/manga-v2/scenes/{scene['id']}/cutout").status_code == 204
        assert client.get(f"/api/manga-v2/scenes/{scene['id']}/cutout").json()["methods"] == ["local"]
        # 絵の無いシーンは 404
        assert client.post("/api/manga-v2/scenes/99999/cutout").status_code == 404
