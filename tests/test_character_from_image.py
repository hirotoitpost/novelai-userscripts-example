"""
画像からキャラを自動で作るテスト(判定モデル・文章モデルは使わない)。

実行方法:
  uv run pytest tests/test_character_from_image.py -v
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import character_from_image, db, image_tagger, manga_similar  # noqa: E402
from python.image_tagger import TagScore  # noqa: E402
from python.routes import manga_v2  # noqa: E402


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture()
def named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(image_tagger, "character_names", lambda: {"hatsune miku"})
    seen: dict = {"references": []}

    async def name(api_key, people, genre, setting, *, adult=False, lines=None):  # noqa: ANN001
        seen["people"] = people
        seen["setting"] = setting
        answers = [
            {"name": "初音ミク", "age": 16, "profile": "歌うのが好き", "look": ""},
            {"name": "佐藤 花", "age": 25, "profile": "会社員", "look": "black hair"},
        ]
        return answers[: len(people)]

    monkeypatch.setattr(manga_similar, "name_people", name)
    monkeypatch.setattr(manga_v2, "register_reference", lambda cid, data, seed=None: seen["references"].append((cid, seed)))
    return seen


def test_generated_image_uses_the_embedded_character_prompts(named: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        image_tagger,
        "embedded_prompt",
        lambda data: {
            "positive": "masterpiece, best quality, 2girls, classroom, smile",
            "negative": "lowres",
            "characters": [
                {"prompt": "1girl, hatsune miku, aqua hair, twintails, school uniform, smile", "negative": "glasses"},
                {"prompt": "1girl, long hair, black hair, office lady, white shirt", "negative": ""},
            ],
            "settings": {"seed": 123},
        },
    )
    conn = db.get_connection()
    try:
        db.save_character(conn, "佐藤 花", "1girl", "自分のキャラ")
    finally:
        conn.close()
    result = asyncio.run(character_from_image.create_from_image("k", _png()))
    first, second = result["characters"]
    assert result["source"] == "metadata"
    # キャラ名のタグは名前の手がかり(キャラ名)にし、容姿の性別の次に入れる。表情・場所はキャラシートに入れない
    assert named["people"][0].character == "hatsune miku" and named["people"][1].character == ""
    assert first["name"] == "初音ミク" and first["appearance_tags"].startswith("1girl, hatsune miku")
    assert "twintails" in first["appearance_tags"] and "smile" not in first["appearance_tags"]
    assert "school uniform" in first["outfit_tags"] and first["style_tags"] == "masterpiece, best quality"
    assert first["negative_tags"] == "glasses" and "classroom" in named["setting"]
    # 未成年と考えた人物は未成年。成人と考えた人物も成人にはしない
    assert first["age"] == 16 and first["is_minor"] == 1
    assert second["name"] == "佐藤 花2" and not second["is_adult"] and second["age"] is None
    # 二人の画像は参照画像にしない
    assert named["references"] == []


def test_one_person_image_becomes_the_reference(named: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        image_tagger,
        "embedded_prompt",
        lambda data: {
            "positive": "masterpiece, 1girl, beach, white shirt, smile",
            "negative": "",
            "characters": [{"prompt": "girl, black hair", "negative": ""}],
            "settings": {"seed": 77},
        },
    )
    result = asyncio.run(character_from_image.create_from_image("k", _png()))
    character = result["characters"][0]
    # 1人なら、全体のプロンプトに書いた服装も入れる(場所・表情は入れない)
    assert character["appearance_tags"] == "1girl, black hair" and character["outfit_tags"] == "white shirt"
    assert named["references"] == [(character["id"], 77)]

    monkeypatch.setattr(
        image_tagger,
        "embedded_prompt",
        lambda data: {"positive": "2girls, black hair", "negative": "", "characters": [], "settings": {}},
    )
    with pytest.raises(ValueError):
        asyncio.run(character_from_image.create_from_image("k", _png()))


def test_image_without_metadata_uses_the_tagger(named: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_tagger, "embedded_prompt", lambda data: None)
    monkeypatch.setattr(image_tagger, "split_characters", lambda image: [])
    monkeypatch.setattr(
        image_tagger,
        "tag_image",
        lambda image: [
            TagScore("1girl", 0.99, "general"),
            TagScore("aqua hair", 0.9, "general"),
            TagScore("twintails", 0.8, "general"),
            TagScore("hatsune miku", 0.95, "character"),
            TagScore("smile", 0.7, "general"),
        ],
    )
    result = asyncio.run(character_from_image.create_from_image("k", _png()))
    assert result["source"] == "tagger"
    assert named["people"][0].character == "hatsune miku"
    assert result["characters"][0]["appearance_tags"].startswith("1girl, hatsune miku")
    assert named["references"] == [(result["characters"][0]["id"], None)]
