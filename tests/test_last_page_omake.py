"""
漫画v2の最後のページ: コマが足りないときに、少ないコマ割りに切り替える(fill)・空いたマスを余白にする(blank)・
余白におまけ絵を置く(omake)。おまけ絵(/api/manga-v2/{id}/omake)の生成は差し替える。NovelAI には接続しない。

実行方法:
  uv run pytest tests/test_last_page_omake.py -v
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.client import get_client  # noqa: E402
from python.manga_v2.compose import LetteringStyle, compose_page, plan_pages  # noqa: E402
from python.manga_v2.lettering import DEFAULT_SFX_FONT_ID, resolve_font  # noqa: E402
from python.manga_v2.layout import panel_rects, unused_rect  # noqa: E402
from python.routes import manga_v2  # noqa: E402
from python.routes.story import router as story_router  # noqa: E402

_PINK = (255, 0, 160)
_GREY = (200, 200, 200)


def test_fill_switches_to_a_smaller_template() -> None:
    plans = plan_pages("grid4", 6)
    assert [(p.start, p.end, p.template) for p in plans] == [(0, 4, "grid4"), (4, 6, "stack2")]


def test_blank_keeps_the_template_cells() -> None:
    plans = plan_pages("grid4", 6, last_page="blank")
    last = plans[-1]
    assert last.template == "grid4" and len(last.spec.panels) == 4 and last.end - last.start == 2
    assert last.spec.extras == ()
    # コマの生成サイズも同じ割り付けで決める(上の2マスの形のまま)
    from python.manga_v2.compose import slot_rects

    assert slot_rects("grid4", 6, last_page="blank")[4:] == panel_rects("grid4")[:2]
    # ちょうど割り切れる物語は変わらない
    assert [p.template for p in plan_pages("grid4", 8, last_page="blank")] == ["grid4", "grid4"]


def test_omake_goes_in_the_unused_cells(tmp_path: Path) -> None:
    omake = tmp_path / "omake.png"
    plans = plan_pages("grid4", 6, last_page="omake", omake=omake)
    rect, path = plans[-1].spec.extras[0]
    assert path == omake
    # 下の2マス(右下・左下)をまとめた範囲
    bottom = panel_rects("grid4")[2:]
    assert rect == (min(r[0] for r in bottom), min(r[1] for r in bottom), max(r[2] for r in bottom), max(r[3] for r in bottom))
    # おまけ絵を作っていなければ余白のまま
    assert plan_pages("grid4", 6, last_page="omake")[-1].spec.extras == ()


def test_unused_rect_avoids_used_cells() -> None:
    rects = panel_rects("grid4")
    # 1コマだけなら空きは L 字。まとめると右上のコマに重なるので、一番大きい空きのマスにする
    spare = unused_rect("grid4", 1)
    assert spare in rects[1:]
    assert unused_rect("grid4", 3) == rects[3]
    assert unused_rect("grid4", 4) is None


def test_compose_page_draws_the_omake_without_a_border(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("python.manga_v2.compose.detect_heads", lambda path: [])
    omake = tmp_path / "omake.png"
    Image.new("RGB", (1216, 832), _PINK).save(omake)
    plan = plan_pages("grid4", 2, last_page="omake", omake=omake)[0]
    fonts = resolve_font(None), resolve_font(None, DEFAULT_SFX_FONT_ID)
    page, _ = compose_page(plan.spec, [], LetteringStyle(*fonts))
    x0, y0, x1, y1 = plan.spec.extras[0][0]
    assert page.getpixel(((x0 + x1) // 2, (y0 + y1) // 2)) == _PINK
    # 範囲の9割に収めるので、範囲の端は地の色のまま
    assert page.getpixel((x0 + 2, (y0 + y1) // 2)) == (255, 255, 255)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manga_v2, "_PAGE_DIR", tmp_path / "outputs" / "manga" / "v2")
    monkeypatch.setattr(manga_v2, "_OMAKE_DIR", tmp_path / "outputs" / "manga" / "omake")
    monkeypatch.setattr("python.manga_v2.compose.detect_heads", lambda path: [])
    app = FastAPI()
    app.include_router(story_router)
    app.include_router(manga_v2.router)
    app.dependency_overrides[get_client] = lambda: SimpleNamespace(api_key="k")
    with TestClient(app) as test_client:
        yield test_client


def _png_bytes(color: tuple[int, int, int], size: tuple[int, int] = (1216, 832)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _story(client: TestClient, tmp_path: Path, count: int) -> int:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "ちひろ", "1girl, milk tea hair")
    finally:
        conn.close()
    scenes = [
        {"text": f"「セリフ{i}」", "prompt_tags": "1girl, smile", "character_ids": [character["id"]]} for i in range(count)
    ]
    story = client.post("/api/story/scripted", json={"title": "試し", "scenes": scenes}).json()
    conn = db.get_connection()
    try:
        for scene in db.list_story_scenes(conn, story["id"]):
            rel = f"outputs/manga/panels/s{scene['id']}.png"
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(_png_bytes(_GREY, (832, 1216)))
            db.upsert_manga_panel(conn, story["id"], scene["id"], rel, 1, 832, 1216)
    finally:
        conn.close()
    return story["id"]


def test_make_omake_and_compose_with_it(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    story_id = _story(client, tmp_path, 6)
    sent: dict = {}

    async def fake_generate(api_key, prompt, negative, **kwargs):
        sent.update(prompt=prompt, negative=negative, **kwargs)
        return _png_bytes(_PINK)

    monkeypatch.setattr(manga_v2, "generate_image_v5", fake_generate)
    assert client.get(f"/api/manga-v2/{story_id}/omake").json() is None

    # 合成の設定が grid4 なら、最後のページは下2マスが空く → 横長で描く
    client.post(f"/api/manga-v2/{story_id}/compose", json={"template": "grid4"})
    res = client.post(f"/api/manga-v2/{story_id}/omake", json={"extra_tags": "holding baby", "seed": 7})
    assert res.status_code == 200, res.text
    omake = res.json()
    assert omake["seed"] == 7 and omake["size"] == "landscape" and (tmp_path / omake["image_path"]).is_file()
    assert (sent["width"], sent["height"]) == (1216, 832)
    assert "chibi" in sent["prompt"] and "hearts" in sent["prompt"] and "holding baby" in sent["prompt"]
    assert "nsfw" in sent["negative"]  # 成人向けの物語でも全年齢で描く
    assert len(sent["character_tags"]) == 1 and "milk tea hair" in sent["character_tags"][0]
    # 生成した絵はギャラリー(生成の履歴)にも残る
    conn = db.get_connection()
    try:
        history = db.list_generation_history(conn)
    finally:
        conn.close()
    assert any(h["id"] == omake["history_id"] for h in history)

    body = client.post(f"/api/manga-v2/{story_id}/compose", json={"template": "grid4", "last_page": "omake"}).json()
    assert len(body["pages"]) == 2
    with Image.open(tmp_path / body["pages"][1]) as page:
        bottom = panel_rects("grid4")[2:]
        cx = (min(r[0] for r in bottom) + max(r[2] for r in bottom)) // 2
        cy = (min(r[1] for r in bottom) + max(r[3] for r in bottom)) // 2
        assert page.convert("RGB").getpixel((cx, cy)) == _PINK
    # 合成の設定に残り、次の合成・ダウンロードでも同じ扱いになる
    story = client.get(f"/api/story/{story_id}").json()
    assert story["manga_v2_compose_settings"]["last_page"] == "omake"

    # 外すと余白(ファイルは残す)
    assert client.delete(f"/api/manga-v2/{story_id}/omake").status_code == 204
    assert client.get(f"/api/manga-v2/{story_id}/omake").json() is None
    assert (tmp_path / omake["image_path"]).is_file()
    body = client.post(f"/api/manga-v2/{story_id}/compose", json={"template": "grid4", "last_page": "omake"}).json()
    with Image.open(tmp_path / body["pages"][1]) as page:
        assert page.convert("RGB").getpixel((cx, cy)) == (255, 255, 255)


def test_omake_needs_characters(client: TestClient, tmp_path: Path) -> None:
    story = client.post("/api/story/scripted", json={"title": "風景", "scenes": [{"text": "海", "prompt_tags": "no humans"}]}).json()
    res = client.post(f"/api/manga-v2/{story['id']}/omake", json={})
    assert res.status_code == 400


def test_omake_background_blends_into_the_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("python.manga_v2.compose.detect_heads", lambda path: [])
    # うっすら灰色の地(240)に、濃い色の人物
    omake = tmp_path / "omake.png"
    image = Image.new("RGB", (1216, 832), (240, 240, 236))
    image.paste(Image.new("RGB", (200, 400), _PINK), (508, 216))
    image.save(omake)
    plan = plan_pages("grid4", 2, last_page="omake", omake=omake)[0]
    fonts = resolve_font(None), resolve_font(None, DEFAULT_SFX_FONT_ID)
    page, _ = compose_page(plan.spec, [], LetteringStyle(*fonts))
    x0, y0, x1, y1 = plan.spec.extras[0][0]
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    assert page.getpixel((cx, cy)) == _PINK
    # 絵の地は、ページの地(白)にそろって境目が出ない
    assert page.getpixel((x0 + (x1 - x0) // 10, cy)) == (255, 255, 255)


def test_omake_tags_weight_chibi() -> None:
    assert "1.6::chibi::" in manga_v2.OMAKE_TAGS and "hearts" in manga_v2.OMAKE_TAGS


def test_select_an_earlier_omake(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    story_id = _story(client, tmp_path, 6)
    colors = iter([(255, 0, 0), (0, 0, 255)])

    async def fake_generate(api_key, prompt, negative, **kwargs):
        return _png_bytes(next(colors))

    monkeypatch.setattr(manga_v2, "generate_image_v5", fake_generate)
    first = client.post(f"/api/manga-v2/{story_id}/omake", json={"seed": 1}).json()
    second = client.post(f"/api/manga-v2/{story_id}/omake", json={"seed": 2}).json()
    assert client.get(f"/api/manga-v2/{story_id}/omake").json()["image_path"] == second["image_path"]
    candidates = client.get(f"/api/manga-v2/{story_id}/omake/candidates").json()
    assert {c["image_path"] for c in candidates} == {first["image_path"], second["image_path"]}

    # 作り直す前の絵に戻す
    key = next(c["gallery_key"] for c in candidates if c["image_path"] == first["image_path"])
    monkeypatch.setattr("python.routes.library._PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("python.routes.library._is_served", lambda full: True)
    res = client.put(f"/api/manga-v2/{story_id}/omake", json={"gallery_key": key})
    assert res.status_code == 200, res.text
    assert client.get(f"/api/manga-v2/{story_id}/omake").json()["image_path"] == first["image_path"]
    # 無い画像は 404
    missing = client.put(f"/api/manga-v2/{story_id}/omake", json={"gallery_key": "file:outputs/manga/omake/none.png"})
    assert missing.status_code == 404
