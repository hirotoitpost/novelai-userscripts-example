"""
取り込んだ漫画のセリフ・場面の読み取り(manga_reading)と、取り込みの使い方(似た漫画を作る / 自分の作品を作り直す)の
テスト。文字の読み取り(OCR)と判定モデルは使わず、読めた列やタグを差し替えて確かめる。セリフは自作の文。

実行方法:
  uv run pytest tests/test_manga_reading.py -v
"""

from __future__ import annotations

import base64
import io
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, manga_import, manga_reading  # noqa: E402
from python.manga_draft import DraftCharacter, episode_prompts  # noqa: E402
from python.manga_import import structure_lines  # noqa: E402
from python.manga_reading import (  # noqa: E402
    PageReading,
    Speech,
    TextColumn,
    group_columns,
    line_features,
    order_in_panel,
    read_speech,
)
from python import manga_rebuild  # noqa: E402
from python.manga_rebuild import assign_prompts, panel_sfx, parse_assignment, parse_cleaned, script_panels  # noqa: E402
from python.manga_similar import apply_import_panels, wordless  # noqa: E402
from python.routes import manga_import as import_routes  # noqa: E402

CAST = [DraftCharacter(1, "あかり", "明るい後輩"), DraftCharacter(2, "そうた", "まじめな先輩")]


def column(x: float, y: float, w: float, h: float, text: str, score: float = 0.95) -> TextColumn:
    return TextColumn((x, y, x + w, y + h), text, score)


def test_columns_are_grouped_into_bubbles_and_read_right_to_left() -> None:
    columns = [
        column(100, 50, 30, 120, "帰りませんか"),  # 左の列(後に読む)
        column(135, 50, 30, 90, "いっしょに"),  # 右の列(先に読む)
        column(400, 300, 30, 100, "うわっ"),  # 離れた別の吹き出し
        column(100, 400, 200, 30, "よこがきの"),  # 横書きは上から下
        column(100, 435, 160, 30, "ふきだし"),
    ]
    texts = sorted("".join(c.text for c in group) for group in group_columns(columns))
    assert texts == ["いっしょに帰りませんか", "うわっ", "よこがきのふきだし"]


def test_read_speech_drops_sound_effects_and_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    columns = [
        column(135, 50, 30, 90, "いっしょに"),
        column(100, 50, 30, 120, "帰りませんか?"),
        column(500, 600, 200, 120, "がばっ", 0.7),  # 文字の領域に重ならない、確からしさの低い描き文字
        column(600, 50, 30, 60, "……"),  # 記号だけのセリフは残す
        column(60, 1100, 80, 30, "おわり"),
        column(700, 1150, 30, 20, "12"),  # ページ番号
    ]
    monkeypatch.setattr(manga_reading, "_ocr_columns", lambda image: columns)
    image = Image.new("RGB", (848, 1200), "white")
    boxes = [
        (95.0, 45.0, 170.0, 175.0),
        (595.0, 45.0, 635.0, 115.0),
        (55.0, 1095.0, 145.0, 1135.0),
        (695.0, 1145.0, 735.0, 1175.0),
    ]
    speeches = read_speech(image, boxes)
    assert sorted(s.text for s in speeches) == ["……", "いっしょに帰りませんか？"]
    # 文字の領域の検出が無いときは、確からしさの高い列だけを使う
    assert sorted(s.text for s in read_speech(image, None)) == ["……", "いっしょに帰りませんか？"]


def test_english_speech_is_kept_on_translated_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # 英訳の漫画: 吹き出しの英語は1語ずつ横書きの行に読める。「A」「?」のような短い行(縦長の枠)も同じ吹き出しにまとめる
    columns = [
        column(100, 100, 50, 20, "ISN'T"),
        column(105, 118, 40, 20, "THIS"),
        column(118, 136, 14, 20, "A"),
        column(100, 154, 50, 20, "CRIME"),
        column(118, 172, 12, 18, "?"),
        column(400, 100, 60, 20, "EVERY-"),  # 行末の「-」で切れた語は1語に戻す
        column(405, 118, 50, 20, "THING"),
        column(415, 136, 30, 20, "IS"),
        column(410, 154, 40, 20, "FINE"),
        column(700, 100, 20, 20, "H-"),  # 言いよどみの「-」は詰める
        column(690, 118, 50, 20, "HMPH,"),
        column(690, 136, 50, 20, "WHAT"),
        column(600, 600, 40, 20, "OK"),  # 2字の1語だけ(描き文字の読み違いのかけらと区別できない)は捨てる
    ]
    monkeypatch.setattr(manga_reading, "_ocr_columns", lambda image: columns)
    texts = sorted(s.text for s in read_speech(Image.new("RGB", (848, 1200), "white"), None))
    assert texts == ["EVERYTHING IS FINE", "H-HMPH, WHAT", "ISN'T THIS A CRIME？"]


def test_latin_fragments_on_japanese_pages_are_still_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    # 日本語の漫画: 描き文字を英字に読み違えたかけらはセリフにしない
    columns = [
        column(135, 50, 30, 90, "いっしょに"),
        column(100, 50, 30, 120, "帰りませんか"),
        column(500, 300, 30, 90, "おはよう"),
        column(500, 600, 80, 30, "OPFTHE"),
    ]
    monkeypatch.setattr(manga_reading, "_ocr_columns", lambda image: columns)
    texts = sorted(s.text for s in read_speech(Image.new("RGB", (848, 1200), "white"), None))
    assert texts == ["いっしょに帰りませんか", "おはよう"]


def test_credits_and_stray_fragments_are_not_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    # 広告・クレジットの文字と、英訳の漫画に残る日本語のかけら(絵や描き文字の読み違い)はセリフにしない
    columns = [
        column(100, 100, 60, 20, "GOOD"),
        column(100, 118, 70, 20, "MORNING"),
        column(400, 100, 80, 20, "SEE YOU"),
        column(400, 118, 60, 20, "LATER"),
        column(700, 100, 30, 30, "山"),
        column(100, 900, 400, 20, "No reproduction or republication without permission."),
        column(600, 900, 30, 200, "好評発売中"),
    ]
    monkeypatch.setattr(manga_reading, "_ocr_columns", lambda image: columns)
    texts = sorted(s.text for s in read_speech(Image.new("RGB", (848, 1200), "white"), None))
    assert texts == ["GOOD MORNING", "SEE YOU LATER"]


def test_cleaned_lines_replace_the_read_text() -> None:
    panels = [
        {"lines": [{"text": "SANACHAN OS SI CUTE", "box": [1, 2, 3, 4]}, {"text": "Golban P"}], "people": 1},
        {"lines": [], "people": 0},
        {"lines": [{"text": "INGS ARE GOING WELL"}]},
    ]
    data = {"panels": [{"lines": ["「サナちゃん、今日もかわいいね」", ""]}, {"lines": []}, {"lines": ["a", "b"]}]}
    cleaned = parse_cleaned(data, panels)
    # 整えた文面に入れ替え、空文字にしたセリフは外す。ほかの読み取り(場所など)は残す
    assert cleaned[0]["lines"] == [{"text": "サナちゃん、今日もかわいいね", "box": [1, 2, 3, 4]}]
    assert cleaned[0]["people"] == 1 and cleaned[1]["lines"] == []
    # セリフの数が合わないコマは、読み取ったまま
    assert cleaned[2]["lines"] == [{"text": "INGS ARE GOING WELL"}]
    with pytest.raises(ValueError):
        parse_cleaned({"panels": []}, panels)


def test_clean_page_keeps_the_read_text_when_the_model_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    calls: list[str] = []

    async def broken(api_key, system, user, **kwargs):
        calls.append(user)
        raise RuntimeError("応答を読み取れません")

    monkeypatch.setattr(manga_rebuild, "_ask_json", broken)
    panels = [{"lines": [{"text": "HELLO THERE"}]}]
    assert asyncio.run(manga_rebuild.clean_page("key", panels)) == panels
    assert "セリフ1: 「HELLO THERE」" in calls[0]
    # セリフの無いページは文章モデルに聞かない
    calls.clear()
    assert asyncio.run(manga_rebuild.clean_page("key", [{"lines": []}])) == [{"lines": []}]
    assert calls == []


def test_cast_tags_fill_in_people_the_detector_missed(monkeypatch: pytest.MonkeyPatch) -> None:
    # 顔の見えない体の寄りや、重なった2人は、顔・体の検出では数え漏れる。写っている人のタグの人数を使う
    assert manga_import.tagged_people(["1girl", "1boy"]) == 2 and manga_import.tagged_people(["6+girls"]) == 6
    tags = {0: (["close-up"], [], ["1girl"]), 1: ([], [], ["1girl", "1boy"]), 2: ([], [], [])}
    crops = iter(range(3))
    monkeypatch.setattr(manga_import, "describe_panel", lambda crop: tags[next(crops)])
    monkeypatch.setattr(manga_import, "read_page", lambda image, boxes, faces=None: PageReading([], []))
    panels = [
        manga_import.PanelInfo((0, 0, 400, 400), 0, "no humans", 0),
        manga_import.PanelInfo((400, 0, 400, 400), 1, "long", 0),
        manga_import.PanelInfo((0, 400, 800, 400), 0, "no humans", 0),
    ]
    manga_import._add_details(Image.new("RGB", (800, 800), "white"), panels, None, keep_text=False)
    assert [(p.people, p.shot) for p in panels] == [(1, "close-up"), (2, "long"), (0, "no humans")]


def test_line_features_describe_the_line_without_its_text() -> None:
    assert line_features("いっしょに帰りませんか？") == {"chars": 12, "ending": "問いかけ", "polite": True}
    assert line_features("はなれろって！") == {"chars": 7, "ending": "強い調子", "polite": False}
    assert line_features("その、えっと……") == {"chars": 8, "ending": "言いよどみ", "polite": False}
    assert line_features("帰ろう〜♪")["ending"] == "ふつう"
    # 英語のセリフの文字数は、日本語にしたときの字数の目安(英字 14 字 × 0.6)
    assert line_features("ISN'T THIS A CRIME？") == {"chars": 8, "ending": "問いかけ", "polite": False}


def test_reading_order_in_a_panel() -> None:
    rect = (0, 0, 600, 300)
    speeches = [
        Speech((50, 20, 100, 90), "左上"),
        Speech((500, 30, 560, 90), "右上"),
        Speech((300, 220, 360, 280), "下"),
    ]
    assert [s.text for s in order_in_panel(speeches, rect)] == ["右上", "左上", "下"]


def test_structure_lines_carry_scene_and_speech_shape_but_no_text() -> None:
    panels = [
        {
            "shot": "medium",
            "people": 2,
            "text_blocks": 2,
            "role": "",
            "emotion": "",
            "detailed": True,
            "scene_tags": ["hallway", "school"],
            "action_tags": ["arm hug", "blush"],
            "lines": [
                {"chars": 12, "ending": "問いかけ", "polite": True, "text": "いっしょに帰りませんか？"},
                {"chars": 3, "ending": "ふつう", "polite": False, "text": "うわっ"},
            ],
        },
        {"shot": "long", "people": 0, "text_blocks": 3, "role": "", "emotion": "", "detailed": True, "lines": []},
        # 場面・セリフを読んでいない(前からある)取り込みは、文字の領域の数だけが手がかり
        {"shot": "close-up", "people": 1, "text_blocks": 3, "role": "オチ", "emotion": ""},
    ]
    lines = structure_lines(panels)
    assert lines == [
        "中くらい・2人・セリフ2個(12字・問いかけ・丁寧 / 3字)・場面: hallway, school・所作: arm hug, blush",
        "引き・セリフなし",
        "寄り・1人・セリフ多め・役割: オチ",
    ]
    assert "帰りませんか" not in "".join(lines)
    _, user = episode_prompts({"title": "題", "logline": "", "episodes": ["一話"]}, 0, CAST, structure=lines)
    assert "場面を再現する" in user and "所作のタグを、当てはまる人物の actions" in user
    assert "「セリフなし」のコマは lines を空に" in user and "帰りませんか" not in user


def test_apply_import_panels_reproduces_scene_and_keeps_silent_panels_silent() -> None:
    script = [
        {
            "characters": ["あかり"],
            "actions": {},
            "lines": [{"speaker": "あかり", "kind": "speech", "text": "新しいセリフ"}],
            "prompt_tags": "1girl, smile",
        },
        {
            "characters": ["あかり", "そうた"],
            "actions": {"あかり": "blush"},
            "lines": [{"speaker": "", "kind": "speech", "text": "あれ"}],
            "prompt_tags": "2people",
        },
        {
            "characters": [],
            "actions": {},
            "lines": [{"speaker": "", "kind": "speech", "text": "あ"}],
            "prompt_tags": "sky",
        },
    ]
    imported = [
        {
            "detailed": True,
            "scene_tags": ["from behind", "hallway"],
            "action_tags": ["running", "open mouth"],
            "lines": [{"chars": 5}],
        },
        {"detailed": True, "scene_tags": ["classroom"], "action_tags": ["blush", "arm hug"], "lines": []},
        {"scene_tags": ["rooftop"], "lines": []},  # 読んでいない取り込みには何もしない
    ]
    result = apply_import_panels(script, imported)
    assert result[0]["prompt_tags"] == "from behind, hallway, 1girl, smile"
    assert result[0]["actions"] == {"あかり": "running, open mouth"}
    assert result[0]["lines"][0]["text"] == "新しいセリフ"
    # 二人のコマは、だれの所作か決められないので足さない。セリフの無かったコマは無しのまま
    assert result[1]["prompt_tags"] == "classroom, 2people" and result[1]["actions"] == {"あかり": "blush"}
    assert result[1]["lines"] == []
    assert result[2]["prompt_tags"] == "sky" and result[2]["lines"]

    assert wordless([[{"detailed": True, "lines": []}], [{"detailed": True, "lines": []}]])
    assert not wordless([[{"detailed": True, "lines": [{"chars": 2}]}]])
    assert not wordless([[{"lines": []}]])


def test_rebuild_keeps_the_text_and_lets_the_model_pick_speakers_only() -> None:
    panels = [
        {
            "shot": "medium",
            "people": 2,
            "cast_tags": ["1girl", "1boy"],
            "scene_tags": ["hallway"],
            "action_tags": ["arm hug", "blush"],
            "lines": [{"text": "いっしょに帰りませんか？"}, {"text": "うわっ"}],
        },
        {"shot": "close-up", "people": 1, "scene_tags": [], "action_tags": ["smile"], "lines": []},
    ]
    system, user = assign_prompts(panels, CAST, {"あかり": "1girl, brown hair", "そうた": "1boy, black hair"})
    assert "セリフの文面は返さない" in system
    assert (
        "セリフ1: 「いっしょに帰りませんか？」" in user and "写っている人: 1girl, 1boy" in user and "セリフなし" in user
    )

    answer = {
        "panels": [
            {
                "characters": ["あかり", "そうた", "知らない人"],
                "actions": {"あかり": "arm hug, blush, nude", "知らない人": "blush"},
                "speakers": ["あかり", "だれか"],
                "kinds": ["speech", "thought"],
            },
            {"characters": ["そうた"], "actions": {"そうた": "smile"}, "speakers": ["そうた"], "kinds": []},
        ]
    }
    assigned = parse_assignment(answer, panels, CAST)
    # 知らない名前は外し、渡していないタグは捨てる。セリフの数と合わない答えは、話し手を空にする
    assert assigned[0] == {
        "characters": ["あかり", "そうた"],
        "actions": {"あかり": "arm hug, blush"},
        "speakers": ["あかり", ""],
        "kinds": ["speech", "thought"],
    }
    assert assigned[1]["speakers"] == [] and assigned[1]["kinds"] == []
    script = script_panels(panels, assigned)
    assert [line["text"] for line in script[0]["lines"]] == ["いっしょに帰りませんか？", "うわっ"]
    assert script[0]["lines"][0]["speaker"] == "あかり" and script[0]["lines"][1]["kind"] == "thought"
    assert script[0]["prompt_tags"] == "upper body, hallway"
    assert script[1]["lines"] == [] and script[1]["prompt_tags"] == "close-up"
    with pytest.raises(ValueError):
        parse_assignment({"panels": []}, panels, CAST)


# ---- API ----


def _page() -> bytes:
    """2コマ(上下)のページ。"""
    image = Image.new("RGB", (800, 1200), "white")
    draw = ImageDraw.Draw(image)
    for top in (40, 620):
        draw.rectangle((40, top, 760, top + 540), outline="black", width=6)
        draw.ellipse((300, top + 150, 500, top + 400), fill=(120, 120, 120))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(import_routes, "_IMPORT_DIR", tmp_path / "imports")
    monkeypatch.setattr(manga_import, "detect_elements", lambda image: None)
    monkeypatch.setattr(manga_import, "describe_panel", lambda crop: (["hallway"], ["smile"], ["1girl"]))
    monkeypatch.setattr(
        manga_import,
        "read_page",
        lambda image, boxes, faces=None: PageReading(
            [Speech((500, 100, 560, 260), "いっしょに帰りませんか？"), Speech((100, 700, 160, 800), "うわっ")],
            # 描き文字の候補: 上のコマに大きいもの、下のコマに小さいもの
            [(100.2, 100.0, 300.0, 300.4), (600.0, 900.0, 660.0, 960.0)],
        ),
    )
    app = FastAPI()
    app.include_router(import_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _import(client: TestClient, purpose: str | None) -> dict:
    body: dict = {
        "title": "自作の2コマ",
        "files": [{"name": "p1.png", "data": base64.b64encode(_page()).decode()}],
        "use_vision": False,
    }
    if purpose:
        body["purpose"] = purpose
    res = client.post("/api/manga-import", json=body)
    assert res.status_code == 200, res.text
    import_id = res.json()["id"]
    for _ in range(200):
        job = client.get(f"/api/manga-import/{import_id}/job").json()
        if job and job["status"] != "running":
            assert job["status"] == "done", job
            return client.get(f"/api/manga-import/{import_id}").json()
        time.sleep(0.05)
    raise AssertionError("読み取りが終わりませんでした")


def test_similar_import_keeps_only_the_shape_of_lines(client: TestClient) -> None:
    item = _import(client, "similar")
    assert item["purpose"] == "similar"
    first, second = item["analysis"]["pages"][0]["panels"]
    assert first["scene_tags"] == ["hallway"] and first["action_tags"] == ["smile"] and first["cast_tags"] == ["1girl"]
    assert first["lines"] == [{"chars": 12, "ending": "問いかけ", "polite": True}]
    assert second["lines"] == [{"chars": 3, "ending": "ふつう", "polite": False}]
    # 描き文字は場所と大きさだけ(読みは無い)
    assert first["sfx"] == [{"box": [100, 100, 300, 300], "size": "大", "reading": ""}]
    assert second["sfx"] == [{"box": [600, 900, 660, 960], "size": "小", "reading": ""}]
    assert "帰りませんか" not in str(item)
    # セリフの文面が無いので、手直しも作り直しもできない
    res = client.put(f"/api/manga-import/{item['id']}/pages/0/panels/0/lines", json={"lines": ["x"]})
    assert res.status_code == 409
    res = client.post(f"/api/manga-import/{item['id']}/rebuild", json={}, headers={"Authorization": "Bearer t"})
    assert res.status_code == 409


def test_rebuild_import_keeps_the_text_and_lets_it_be_fixed(client: TestClient) -> None:
    item = _import(client, "rebuild")
    assert item["purpose"] == "rebuild"
    first = item["analysis"]["pages"][0]["panels"][0]
    assert first["lines"][0]["text"] == "いっしょに帰りませんか？" and first["lines"][0]["box"] == [500, 100, 560, 260]
    res = client.put(
        f"/api/manga-import/{item['id']}/pages/0/panels/0/lines",
        json={"lines": ["いっしょに帰りましょう！", "  ", "ね？"]},
    )
    assert res.status_code == 200, res.text
    lines = res.json()["analysis"]["pages"][0]["panels"][0]["lines"]
    assert [line["text"] for line in lines] == ["いっしょに帰りましょう！", "ね？"]
    assert lines[0]["ending"] == "強い調子" and lines[0]["box"] == [500, 100, 560, 260] and "box" not in lines[1]
    assert client.put(f"/api/manga-import/{item['id']}/pages/0/panels/9/lines", json={"lines": []}).status_code == 404
    # 読み取り直すと、使い方を変えられる(似た漫画用に読み直すと、文面は残らない)
    assert client.post(f"/api/manga-import/{item['id']}/analyze?use_vision=false&purpose=bad").status_code == 422
    assert client.post(f"/api/manga-import/{item['id']}/analyze?use_vision=false&purpose=similar").status_code == 200
    for _ in range(200):
        job = client.get(f"/api/manga-import/{item['id']}/job").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    again = client.get(f"/api/manga-import/{item['id']}").json()
    assert again["purpose"] == "similar" and "帰りま" not in str(again)


def test_old_imports_stay_structure_only(client: TestClient) -> None:
    conn = db.get_connection()
    try:
        old = db.create_manga_import(conn, "前からある取り込み", 1)
    finally:
        conn.close()
    assert old["purpose"] == "structure"
    assert client.get(f"/api/manga-import/{old['id']}").json()["purpose"] == "structure"


# ---- 描き文字 ----


def test_plain_reading_always_asks_for_recognition(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    RapidOCR は直前の呼び出しの指定を覚えている。場所だけを探したあとでも文字を読めるように、
    読むときは検出・向き・認識をいつも指定する。
    """
    calls: list[dict] = []

    class Engine:
        sticky = {"use_rec": True}

        def __call__(self, image, **flags):
            self.sticky.update(flags)
            calls.append(dict(flags))

            class Result:
                boxes = [[(0, 0), (40, 0), (40, 200), (0, 200)]]
                # 認識を切ったままだと、文字は返らない
                txts = ("こんにちは",) if Engine.sticky["use_rec"] else None
                scores = (0.99,) if Engine.sticky["use_rec"] else None

            return Result()

    monkeypatch.setattr(manga_reading, "ocr_engine", lambda: Engine())
    image = Image.new("RGB", (400, 400), "white")
    assert manga_reading._detect_text_boxes(image, 0.5) == [(0.0, 0.0, 80.0, 400.0)]
    assert calls[-1] == {"use_det": True, "use_cls": False, "use_rec": False}
    columns = manga_reading._ocr_columns(image)
    assert [c.text for c in columns] == ["こんにちは"]
    assert calls[-1] == {"use_det": True, "use_cls": True, "use_rec": True}


def test_sfx_candidates_skip_speech_faces_and_end_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    image = Image.new("RGB", (1000, 1000), "white")
    speech = [(700.0, 100.0, 760.0, 300.0)]
    face = [(400.0, 400.0, 600.0, 600.0)]
    found = {
        1.0: [
            (705.0, 110.0, 755.0, 290.0),  # セリフ
            (470.0, 470.0, 520.0, 510.0),  # 顔の中の小さなもの(目)
            (100.0, 100.0, 180.0, 220.0),  # 描き文字の一部
            (10.0, 10.0, 20.0, 20.0),  # 小さすぎる
            (40.0, 900.0, 160.0, 950.0),  # 終わりの印
        ],
        0.5: [(90.0, 90.0, 260.0, 330.0)],  # 同じ描き文字の全体
        0.33: [(0.0, 0.0, 1000.0, 1000.0)],  # 大きすぎる
    }
    monkeypatch.setattr(manga_reading, "_detect_text_boxes", lambda source, scale: found[scale])
    boxes = manga_reading.detect_sfx(image, speech, face, exclude_boxes=[(42.0, 902.0, 158.0, 948.0)])
    # 一部と全体は1つにまとめ、標準的な大きさ(中央値の2倍まで)でいちばん大きい枠を選ぶ
    assert boxes == [(90.0, 90.0, 260.0, 330.0)]


def test_end_marks_are_found_from_the_page_reading() -> None:
    columns = [column(40, 900, 120, 50, "おわり"), column(700, 100, 50, 200, "いっしょに帰りませんか？")]
    assert manga_reading.end_mark_boxes(columns) == [(40, 900, 160, 950)]


def test_sfx_reach_the_scripts_as_presence_or_as_entered_readings() -> None:
    panel = {
        "shot": "medium",
        "people": 1,
        "text_blocks": 0,
        "detailed": True,
        "lines": [],
        "sfx": [
            {"box": [0, 0, 10, 10], "size": "小", "reading": "ぽつ"},
            {"box": [0, 0, 90, 90], "size": "大", "reading": "どーん"},
            {"box": [0, 0, 20, 20], "size": "小", "reading": ""},
            {"box": None, "size": "中", "reading": "ざわ"},
        ],
    }
    # 似た漫画: 有無と大きさだけ(読みは渡さない)
    line = structure_lines([panel])[0]
    assert line == "中くらい・1人・セリフなし・描き文字: 大1・中1・小2" and "どーん" not in line
    _, user = episode_prompts({"title": "題", "logline": "", "episodes": ["一話"]}, 0, CAST, structure=[line])
    assert "「描き文字」のあるコマは、sfx に効果音を1つ入れる" in user
    # 作り直し: 読みを入れたものを、大きいものから2つまで
    assert panel_sfx(panel) == ["どーん", "ざわ"]
    assigned = [{"characters": ["あかり"], "actions": {}, "speakers": [], "kinds": []}]
    assert script_panels([panel], assigned)[0]["sfx"] == ["どーん", "ざわ"]
    assert panel_sfx({"lines": []}) == []


def test_sfx_can_be_removed_named_and_added(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _import(client, "rebuild")
    url = f"/api/manga-import/{item['id']}/pages/0/panels/0/sfx"
    res = client.put(
        url,
        json={
            "sfx": [
                {"box": [100, 100, 300, 300], "reading": " どーん "},
                {"box": None, "reading": "ざわ"},
                {"box": None, "reading": "  "},
            ]
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["analysis"]["pages"][0]["panels"][0]["sfx"] == [
        {"box": [100, 100, 300, 300], "size": "大", "reading": "どーん"},
        {"box": None, "size": "中", "reading": "ざわ"},
    ]
    assert client.put(url, json={"sfx": [{"box": [900, 100, 950, 200], "reading": ""}]}).status_code == 422
    assert client.put(url.replace("panels/0", "panels/9"), json={"sfx": []}).status_code == 404
    # 切り抜き(まわりに余白を足す)
    crop = client.get(f"/api/manga-import/{item['id']}/pages/0/crop?x0=100&y0=100&x1=300&y1=300")
    assert crop.status_code == 200 and Image.open(io.BytesIO(crop.content)).size == (212, 212)
    assert client.get(f"/api/manga-import/{item['id']}/pages/0/crop?x0=300&y0=100&x1=100&y1=300").status_code == 422
    assert client.get(f"/api/manga-import/{item['id']}/pages/5/crop?x0=0&y0=0&x1=9&y1=9").status_code == 404

    # 読みの候補: 読みを入れた描き文字(上のコマ)から、読みの無いもの(下のコマ)へ
    import numpy as np

    from python import sfx_similarity

    monkeypatch.setattr(sfx_similarity, "features", lambda images: np.eye(len(images), 4, dtype=np.float32))
    res = client.get(f"/api/manga-import/{item['id']}/sfx-suggestions").json()
    assert res == {"available": True, "suggestions": {"0-1-0": ["どーん"]}}

    def broken(images):
        raise RuntimeError("モデルを取得できません")

    import_routes._sfx_features.clear()
    monkeypatch.setattr(sfx_similarity, "features", broken)
    res = client.get(f"/api/manga-import/{item['id']}/sfx-suggestions").json()
    assert res["available"] is False and res["suggestions"] == {}
    # 外す
    assert client.put(url, json={"sfx": []}).json()["analysis"]["pages"][0]["panels"][0]["sfx"] == []


def test_similar_imports_never_keep_sfx_readings(client: TestClient) -> None:
    item = _import(client, "similar")
    url = f"/api/manga-import/{item['id']}/pages/0/panels/0/sfx"
    res = client.put(url, json={"sfx": [{"box": [100, 100, 300, 300], "reading": "どーん"}, {"box": None, "reading": "ざわ"}]})
    assert res.status_code == 200, res.text
    assert res.json()["analysis"]["pages"][0]["panels"][0]["sfx"] == [
        {"box": [100, 100, 300, 300], "size": "大", "reading": ""}
    ]
    assert "どーん" not in str(client.get(f"/api/manga-import/{item['id']}").json())


def test_rank_readings_lists_each_reading_once() -> None:
    import numpy as np

    from python.sfx_similarity import _prepare, rank_readings

    known = np.array([[1, 0], [0.9, 0.1], [0, 1]], np.float32)
    assert rank_readings(np.array([1, 0], np.float32), known, ["どーん", "どーん", "ざわ"]) == ["どーん", "ざわ"]
    assert rank_readings(np.array([1, 0], np.float32), known[:0], []) == []
    assert _prepare(Image.new("RGB", (300, 80), "white")).shape == (3, 224, 224)
