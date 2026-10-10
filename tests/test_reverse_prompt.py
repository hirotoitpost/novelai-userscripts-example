"""
画像からのプロンプトの逆引き(/api/llm/reverse-prompt/tags)のテスト。WD Tagger のモデルは使わず
(数百MBあるため)、判定の結果を差し替える。

実行方法:
  uv run pytest tests/test_reverse_prompt.py -v
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import image_tagger  # noqa: E402
from python.image_tagger import TagScore, build_prompt  # noqa: E402
from python.routes.llm import router  # noqa: E402

SCORES = [
    TagScore("general", 0.9, "rating"),
    TagScore("smile", 0.95, "general"),
    TagScore("1girl", 0.99, "general"),
    TagScore("kujou karen", 0.9, "character"),
    TagScore("glasses", 0.55, "general"),
    TagScore("1boy", 0.6, "general"),
    TagScore("hat", 0.3, "general"),
    TagScore("someone else", 0.6, "character"),
]


def _data_url(image: Image.Image, info: PngInfo | None = None) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=info)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(image_tagger, "tag_image", lambda image: SCORES)
    # 人物ごとの分け方(頭の判定のモデルを使う)は test_split_characters で確かめる
    monkeypatch.setattr(image_tagger, "split_characters", lambda image: [])
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def test_build_prompt_orders_like_novelai() -> None:
    # 人数 → キャラ名 → そのほか(確率の高い順)。しきい値未満は入れない
    assert build_prompt(SCORES) == "1girl, 1boy, kujou karen, smile, glasses"
    assert build_prompt(SCORES, general_threshold=0.25, character_threshold=0.5) == (
        "1girl, 1boy, kujou karen, someone else, smile, glasses, hat"
    )


def test_tagger_for_images_without_metadata(client: TestClient) -> None:
    res = client.post("/api/llm/reverse-prompt/tags", json={"image": _data_url(Image.new("RGB", (64, 64), "white"))})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "tagger"
    assert body["positive"] == "1girl, 1boy, kujou karen, smile, glasses"
    assert body["rating"] == "general"
    # 画面でしきい値を変えて選び直せるよう、候補は全部返す(評価のタグは除く)
    assert [t["tag"] for t in body["tags"]] == [s.tag for s in SCORES if s.category != "rating"]
    assert body["negative"]


def test_embedded_novelai_prompt_is_returned_as_is(client: TestClient) -> None:
    info = PngInfo()
    info.add_text("Software", "NovelAI")
    info.add_text("Source", "NovelAI Diffusion V4.5 4BDE2A90")
    comment = {
        "prompt": "1girl, 1boy, kitchen",
        "uc": "lowres",
        "seed": 39006,
        "steps": 27,
        "scale": 7.0,
        "sampler": "k_euler_ancestral",
        "width": 832,
        "height": 1216,
        "v4_prompt": {
            "caption": {
                "base_caption": "1girl, 1boy, kitchen",
                "char_captions": [
                    {"char_caption": "1girl, brown hair", "centers": [{"x": 0.3, "y": 0.5}]},
                    {"char_caption": "1boy, glasses", "centers": [{"x": 0.7, "y": 0.5}]},
                ],
            }
        },
        "v4_negative_prompt": {
            "caption": {
                "base_caption": "lowres",
                "char_captions": [
                    {"char_caption": "short hair", "centers": []},
                    {"char_caption": "", "centers": []},
                ],
            }
        },
    }
    info.add_text("Comment", json.dumps(comment))
    res = client.post(
        "/api/llm/reverse-prompt/tags", json={"image": _data_url(Image.new("RGB", (64, 64), "white"), info)}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "metadata"
    assert body["positive"] == "1girl, 1boy, kitchen" and body["negative"] == "lowres"
    assert body["characters"] == [
        {"prompt": "1girl, brown hair", "negative": "short hair", "x": 0.3, "y": 0.5},
        {"prompt": "1boy, glasses", "negative": "", "x": 0.7, "y": 0.5},
    ]
    assert body["settings"]["seed"] == 39006 and body["settings"]["width"] == 832
    assert body["tags"] == []


def test_framing_picks_the_single_most_likely() -> None:
    # 構図は確率が低めに出るので、しきい値に関係なく一番確かな1つだけ
    scores = [
        TagScore("1girl", 0.99, "general"),
        TagScore("upper body", 0.33, "general"),
        TagScore("cowboy shot", 0.41, "general"),
        TagScore("full body", 0.1, "general"),
    ]
    assert build_prompt(scores) == "1girl, cowboy shot"
    assert build_prompt([TagScore("1girl", 0.99, "general"), TagScore("portrait", 0.15, "general")]) == "1girl"


def test_split_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    from python.manga_v2 import detect

    # 左右に二人。左の人だけ金髪・眼鏡、右の人だけ黒髪。笑顔は二人とも(全体に残す)
    monkeypatch.setattr(detect, "detect_heads_in_image", lambda image: [(260, 40, 340, 120), (60, 40, 140, 120)])
    left = [
        TagScore("1girl", 0.95, "general"),
        TagScore("blonde hair", 0.9, "general"),
        TagScore("glasses", 0.8, "general"),
        TagScore("smile", 0.9, "general"),
    ]
    right = [
        TagScore("1boy", 0.9, "general"),
        TagScore("black hair", 0.85, "general"),
        TagScore("smile", 0.8, "general"),
    ]
    crops = iter([left, right])
    monkeypatch.setattr(image_tagger, "tag_image", lambda image: next(crops))
    guesses = image_tagger.split_characters(Image.new("RGB", (400, 400), "white"))
    assert [g.prompt for g in guesses] == ["1girl, blonde hair, glasses", "1boy, black hair"]
    assert [g.center[0] for g in guesses] == [0.3, 0.7]
    # 一人なら分けない
    monkeypatch.setattr(detect, "detect_heads_in_image", lambda image: [(60, 40, 140, 120)])
    assert image_tagger.split_characters(Image.new("RGB", (400, 400), "white")) == []


def test_monochrome_from_pixels() -> None:
    from python.image_tagger import _add_monochrome

    grey = Image.new("RGB", (64, 64), (120, 120, 120))
    # 灰色の絵に、髪のような小さな色の部分(2%)
    spot = grey.copy()
    spot.paste((220, 60, 120), (0, 0, 9, 9))
    colour = Image.new("RGB", (64, 64), (200, 60, 60))
    girl = [TagScore("1girl", 0.9, "general"), TagScore("greyscale", 0.4, "general")]
    assert [s.tag for s in _add_monochrome(grey, girl)] == ["monochrome", "greyscale", "1girl"]
    assert [s.tag for s in _add_monochrome(spot, girl)] == ["monochrome", "greyscale", "spot color", "1girl"]
    assert [s.tag for s in _add_monochrome(colour, girl)] == ["1girl", "greyscale"]


def test_broken_image_is_rejected(client: TestClient) -> None:
    res = client.post("/api/llm/reverse-prompt/tags", json={"image": "data:image/png;base64,AAAA"})
    assert res.status_code == 400


def test_tuning_style_implied_and_panel_negative() -> None:
    from python.image_tagger import build_negative

    scores = [
        TagScore("1girl", 0.99, "general"),
        TagScore("breasts", 0.9, "general"),
        TagScore("large breasts", 0.8, "general"),
        TagScore("mole", 0.7, "general"),
        TagScore("mole under eye", 0.6, "general"),
        TagScore("kitchen", 0.6, "general"),
        TagScore("monochrome", 0.3, "general"),  # 絵柄のタグは低いしきい値で拾い、人数の次に置く
        TagScore("hat", 0.3, "general"),
    ]
    prompt = build_prompt(scores)
    # 大きさの語を付けたタグ(large breasts)があれば元のタグ(breasts)は外す。ほかの詳しいタグでは外さない
    assert prompt == "1girl, monochrome, large breasts, mole, mole under eye, kitchen"
    # コマ割りの漫画でなければ、コマが並んだ絵にならないようネガティブに足す
    assert build_negative("lowres", prompt) == "lowres, multiple views, comic, panels, border"
    assert build_negative("lowres", "comic, 1girl") == "lowres"
    # 画像にあるタグはネガティブから外す
    assert build_negative("lowres, blurry", "1girl, blurry, border") == "lowres, multiple views, comic, panels"
