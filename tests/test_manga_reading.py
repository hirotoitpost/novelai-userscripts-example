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
from python.manga_reading import Speech, TextColumn, group_columns, line_features, order_in_panel, read_speech  # noqa: E402
from python.manga_rebuild import assign_prompts, parse_assignment, script_panels  # noqa: E402
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


def test_line_features_describe_the_line_without_its_text() -> None:
    assert line_features("いっしょに帰りませんか？") == {"chars": 12, "ending": "問いかけ", "polite": True}
    assert line_features("はなれろって！") == {"chars": 7, "ending": "強い調子", "polite": False}
    assert line_features("その、えっと……") == {"chars": 8, "ending": "言いよどみ", "polite": False}
    assert line_features("帰ろう〜♪")["ending"] == "ふつう"


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
        "read_speech",
        lambda image, boxes: [
            Speech((500, 100, 560, 260), "いっしょに帰りませんか？"),
            Speech((100, 700, 160, 800), "うわっ"),
        ],
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
