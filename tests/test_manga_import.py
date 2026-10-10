"""
漫画の取り込み(構成の参考)のテスト。試しのページ画像を描いて、枠線からのコマの検出・読む順・構成の説明と、
学習済みモデルの検出結果からの構成(モデルの結果は差し替える)、取り込み API(画像モデルは使わない)を確かめる。
DB とページの保存先は一時フォルダに差し替え、学習済みモデルのダウンロードもしない。

実行方法:
  uv run pytest tests/test_manga_import.py -v
"""

from __future__ import annotations

import base64
import io
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python import manga_import  # noqa: E402
from python.manga_import import (  # noqa: E402
    analyze_page,
    detect_panels,
    detect_panels_by_lines,
    load_pages,
    parse_role,
    reading_order,
    split_overlays,
    structure_lines,
)
from python.routes import manga_import as import_routes  # noqa: E402

W, H = 1200, 1700


def _page(boxes: list[tuple[int, int, int, int]]) -> Image.Image:
    """白いページに、太い黒枠のコマを描く。"""
    page = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(page)
    for x, y, w, h in boxes:
        draw.rectangle((x, y, x + w, y + h), outline="black", width=5)
    return page


def _grid() -> list[tuple[int, int, int, int]]:
    """2×2のコマ(左上・右上・左下・右下)。"""
    return [(50, 50, 540, 780), (610, 50, 540, 780), (50, 870, 540, 780), (610, 870, 540, 780)]


def test_detects_grid_panels_in_reading_order() -> None:
    panels = detect_panels_by_lines(_page(_grid()))
    assert len(panels) == 4
    # 右上 → 左上 → 右下 → 左下(上の段から、同じ段は右から)
    assert [p[0] > 600 for p in panels] == [True, False, True, False]
    assert [p[1] < 800 for p in panels] == [True, True, False, False]


def test_bubble_across_gutter_does_not_merge_panels() -> None:
    page = _page([(50, 50, 1100, 380), (50, 470, 1100, 380)])
    draw = ImageDraw.Draw(page)
    for y in (56, 476):  # 絵で埋まったコマ
        draw.rectangle((56, y, 1144, y + 368), fill=(150, 150, 150))
    # 上のコマから下のコマへはみ出す吹き出し(白い楕円が枠線を消す)
    draw.ellipse((800, 300, 1000, 560), fill="white", outline="black", width=4)
    assert len(detect_panels_by_lines(page)) == 2


def test_line_inside_artwork_does_not_split_a_panel() -> None:
    page = _page([(50, 50, 1100, 780)])
    draw = ImageDraw.Draw(page)
    # 絵で埋まったコマ(灰色)の中を横切る太い直線(机の縁など)
    draw.rectangle((56, 56, 1144, 824), fill=(150, 150, 150))
    draw.rectangle((56, 400, 1144, 406), fill="black")
    panels = detect_panels_by_lines(page)
    assert len(panels) == 1
    assert panels[0][3] > 700


def test_page_without_borders_is_one_panel() -> None:
    assert detect_panels_by_lines(Image.new("RGB", (W, H), "white")) == [(0, 0, W, H)]


def test_reading_order_rows() -> None:
    # 上の段に大きいコマ、下の段に左右2コマ
    rects = [(50, 900, 500, 700), (50, 50, 1100, 800), (600, 900, 500, 700)]
    assert reading_order(rects) == [(50, 50, 1100, 800), (600, 900, 500, 700), (50, 900, 500, 700)]


def test_overlay_art_is_not_a_panel() -> None:
    # 左に3段のコマ、右に3段すべてにまたがって立つ人物(モデルは frame として返す)
    tiers = [(0, 0, 700, 500), (0, 520, 700, 500), (0, 1040, 700, 500)]
    figure = (500, 0, 700, 1600)
    panels, overlays = split_overlays([*tiers, figure])
    assert panels == tiers
    assert overlays == [figure]


def test_detect_panels_prefers_model_frames() -> None:
    frames = [(610.0, 50.0, 1150.0, 830.0), (50.0, 50.0, 590.0, 830.0), (50.0, 870.0, 1150.0, 1650.0)]
    elements = {"frame": frames, "face": [], "body": [], "text": []}
    assert detect_panels(_page(_grid()), elements) == [(610, 50, 540, 780), (50, 50, 540, 780), (50, 870, 1100, 780)]
    # モデルが何も見つけなければ枠線から探す
    assert len(detect_panels(_page(_grid()), {"frame": [], "face": [], "body": [], "text": []})) == 4


def test_analyze_page_with_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "page.png"
    _page(_grid()).save(path)
    elements = {
        # 右上・左上・下(またがる絵として右側に縦長の frame も1つ)
        "frame": [(610, 50, 1150, 830), (50, 50, 590, 830), (50, 870, 1150, 1650), (700, 40, 1160, 1660)],
        # 右上: 大きな顔1つ(寄り) / 左上: 小さな顔2つ(引き) / 下: 体だけ
        "face": [(700, 100, 1000, 400), (100, 100, 120, 120), (300, 100, 320, 120)],
        "body": [(100, 900, 300, 1600)],
        "text": [(620, 60, 660, 300), (1100, 60, 1140, 300), (60, 900, 100, 1100)],
    }
    monkeypatch.setattr(manga_import, "detect_elements", lambda image: elements)
    panels, overlays = analyze_page(path)
    assert overlays == 1
    assert [(p.people, p.shot, p.text_blocks) for p in panels] == [(1, "close-up", 2), (2, "long", 0), (1, "medium", 1)]


def test_parse_role_from_answer_or_thinking() -> None:
    assert parse_role({"content": '{"role": "導入", "emotion": "喜び"}'}) == ("導入", "喜び")
    # 選択肢そのままでない答えは、含まれる語で拾う
    assert parse_role({"content": '{"role": "会話シーン", "emotion": "穏やか"}'}) == ("会話", "穏やか")
    # 答えが空で、思考の中に JSON があるとき
    thinking = 'まず人物を見る…最終的に {"role": "オチ", "emotion": "笑い"} とする'
    assert parse_role({"content": "", "thinking": thinking}) == ("オチ", "笑い")
    assert parse_role({"content": "", "thinking": "考え中…"}) is None
    assert parse_role({"content": '{"role": "", "emotion": ""}'}) is None


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_load_pages_from_pdf_and_images() -> None:
    pdf = io.BytesIO()
    first, second = _page(_grid()), _page([(50, 50, 1100, 1600)])
    first.save(pdf, format="PDF", save_all=True, append_images=[second])
    small = Image.new("RGB", (600, 850), "white")
    pages = load_pages([("book.pdf", pdf.getvalue()), ("extra.png", _png(small))])
    assert len(pages) == 3
    # 大きいページは幅1200に縮め、小さいページはそのまま
    assert pages[0].width == 1200
    assert pages[2].size == (600, 850)


def _sized(width: int) -> bytes:
    """幅でページを見分けられる白い画像(PNG)。"""
    return _png(Image.new("RGB", (width, 100), "white"))


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buf.getvalue()


def test_load_pages_from_zip() -> None:
    pdf = io.BytesIO()
    Image.new("RGB", (130, 100), "white").save(pdf, format="PDF")
    archive = _zip(
        [
            ("book/p10.png", _sized(110)),
            ("book/p2.png", _sized(102)),
            ("book/p1.jpg", _sized(101)),
            ("extra/z.pdf", pdf.getvalue()),
            ("__MACOSX/book/._p1.jpg", b"junk"),
            ("book/.hidden.png", b"junk"),
            ("book/Thumbs.db", b"junk"),
            ("book/readme.txt", b"not a page"),
        ]
    )
    pages = load_pages([("chapter.zip", archive)])
    # フォルダ・ファイル名の自然な順(p1 → p2 → p10)、付属ファイルや画像以外は読まない。PDF は幅1200で描く
    assert [p.width for p in pages] == [101, 102, 110, 1200]


def test_load_pages_mixes_zip_and_single_files() -> None:
    cbz = _zip([("002.png", _sized(202)), ("001.png", _sized(201))])
    pages = load_pages([("cover.png", _sized(300)), ("vol.cbz", cbz), ("last.png", _sized(400))])
    assert [p.width for p in pages] == [300, 201, 202, 400]


def test_zip_too_large_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manga_import, "MAX_ZIP_BYTES", 10)
    with pytest.raises(ValueError, match="大きすぎます"):
        load_pages([("big.zip", _zip([("p1.png", _sized(100))]))])


def test_structure_lines_hide_the_content() -> None:
    panels = [
        {"shot": "long", "people": 2, "text_blocks": 1, "role": "導入", "emotion": "期待"},
        {"shot": "close-up", "people": 1, "text_blocks": 4, "role": "", "emotion": ""},
        {"shot": "no humans", "people": 0, "text_blocks": 0, "role": "場面転換", "emotion": ""},
    ]
    assert structure_lines(panels) == [
        "引き・2人・セリフ少なめ・役割: 導入・感情: 期待",
        "寄り・1人・セリフ多め",
        "人物なし・セリフなし・役割: 場面転換",
    ]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(import_routes, "_IMPORT_DIR", tmp_path / "imports")
    # 学習済みモデルはダウンロードせず、枠線から探す方法で読み取る
    monkeypatch.setattr(manga_import, "detect_elements", lambda image: None)
    app = FastAPI()
    app.include_router(import_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _wait(client: TestClient, import_id: int) -> dict:
    for _ in range(120):
        job = client.get(f"/api/manga-import/{import_id}/job").json()
        if job and job["status"] != "running":
            return job
        time.sleep(0.25)
    raise AssertionError("読み取りが終わりませんでした")


def test_import_api(client: TestClient, tmp_path: Path) -> None:
    data = base64.b64encode(_png(_page(_grid()))).decode()
    res = client.post(
        "/api/manga-import",
        json={
            "title": "試し",
            "files": [{"name": "p1.png", "data": f"data:image/png;base64,{data}"}],
            "use_vision": False,
        },
    )
    assert res.status_code == 200, res.text
    import_id = res.json()["id"]
    assert _wait(client, import_id)["status"] == "done"

    item = client.get(f"/api/manga-import/{import_id}").json()
    assert item["status"] == "analyzed"
    assert item["panel_count"] == 4
    panel = item["analysis"]["pages"][0]["panels"][0]
    assert set(panel) == {"box", "people", "shot", "text_blocks", "role", "emotion"}
    assert item["analysis"]["pages"][0]["overlays"] == 0
    assert panel["shot"] == "no humans" and panel["role"] == ""
    assert [i["id"] for i in client.get("/api/manga-import").json()] == [import_id]
    assert client.get(f"/api/manga-import/{import_id}/pages/0").headers["content-type"] == "image/png"

    assert client.delete(f"/api/manga-import/{import_id}").status_code == 204
    assert client.get(f"/api/manga-import/{import_id}").status_code == 404
    assert not (tmp_path / "imports" / str(import_id)).exists()


def test_import_api_accepts_zip(client: TestClient) -> None:
    archive = _zip([("p2.png", _png(_page(_grid()))), ("p1.png", _png(_page(_grid())))])
    data = base64.b64encode(archive).decode()
    res = client.post(
        "/api/manga-import",
        json={"title": "zip", "files": [{"name": "a.zip", "data": data}], "use_vision": False},
    )
    assert res.status_code == 200, res.text
    assert res.json()["page_count"] == 2
    _wait(client, res.json()["id"])
    # 壊れた zip は読めない
    broken = base64.b64encode(b"PK\x03\x04 broken").decode()
    res = client.post("/api/manga-import", json={"title": "x", "files": [{"name": "b.zip", "data": broken}]})
    assert res.status_code == 400


def test_rejects_unreadable_files(client: TestClient) -> None:
    res = client.post(
        "/api/manga-import", json={"title": "x", "files": [{"name": "a.png", "data": "bm90IGFuIGltYWdl"}]}
    )
    assert res.status_code == 400
