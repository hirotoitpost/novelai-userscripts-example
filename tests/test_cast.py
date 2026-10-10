"""
物語の登場人物をそろえる仕組み(登録済みキャラへの対応・参照画像の自動選び)のテスト。
NovelAI と判定モデルは使わず、差し替える。

実行方法:
  uv run pytest tests/test_cast.py -v
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import cast, db, image_tagger  # noqa: E402
from python.image_tagger import TagScore  # noqa: E402
from python.routes import manga_v2  # noqa: E402

LIBRARY = [
    {"id": 1, "name": "矢野栄子", "appearance_tags": "1girl, brown hair"},
    {"id": 2, "name": "矢野先生", "appearance_tags": "1boy, glasses"},
    {"id": 3, "name": "九条 ゆら", "appearance_tags": "1girl, brown hair"},
    {"id": 4, "name": "佐倉 みお", "appearance_tags": ""},
    {"id": 5, "name": "美香", "appearance_tags": ""},
    {"id": 6, "name": "美香ちゃん", "appearance_tags": ""},
]


def _id(name: str, aliases: list[str] | None = None) -> int | None:
    found = cast.match_existing(name, aliases or [], LIBRARY)
    return found["id"] if found else None


def test_match_existing_by_name_or_part() -> None:
    assert _id("矢野栄子") == 1
    # 名だけ・姓だけで一人に絞れるとき
    assert _id("栄子") == 1
    assert _id("ゆら") == 3
    assert _id("九条") == 3
    assert _id("みお", ["佐倉みお"]) == 4
    # 呼び名(aliases)の完全一致
    assert _id("お嬢", ["九条ゆら"]) == 3
    # 二人に当てはまる(矢野)・1文字・知らない名前は決めない
    assert _id("矢野") is None
    assert _id("美") is None
    assert _id("源三") is None
    # 同じ名前があればそれ(「美香」は「美香ちゃん」の頭でもあるが、同名を優先)
    assert _id("美香") == 5


def test_auto_reference_picks_the_closest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manga_v2, "_REFERENCE_DIR", tmp_path / "outputs" / "manga" / "refs")
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "テスト", "1girl, blonde hair, glasses, adult woman")
    finally:
        conn.close()

    # 候補3枚: 色で見分け、判定の結果を差し替える(2枚目がキャラシートに一番近い)
    colours = [(200, 0, 0), (0, 200, 0), (0, 0, 200)]
    paths = []
    for i, colour in enumerate(colours):
        path = tmp_path / f"cand{i}.png"
        Image.new("RGB", (8, 8), colour).save(path)
        paths.append({"path": path.name, "seed": 100 + i})

    async def fake_candidates(api_key, ch, count, **_):
        return paths

    probs = {
        (200, 0, 0): {"blonde hair": 0.9, "glasses": 0.1},
        (0, 200, 0): {"blonde hair": 0.9, "glasses": 0.8},
        (0, 0, 200): {"blonde hair": 0.2, "glasses": 0.9},
    }

    def fake_tag(image: Image.Image):
        return [TagScore(t, p, "general") for t, p in probs[image.getpixel((0, 0))].items()]

    monkeypatch.setattr(manga_v2, "make_reference_candidates", fake_candidates)
    monkeypatch.setattr(image_tagger, "tag_image", fake_tag)
    # 語彙に無い "adult woman" は採点に使わない
    monkeypatch.setattr(image_tagger, "vocabulary", lambda: {"1girl", "blonde hair", "glasses"})

    result = asyncio.run(cast.auto_reference("key", character))
    assert result["seed"] == 101
    conn = db.get_connection()
    try:
        saved = db.get_character(conn, character["id"])
    finally:
        conn.close()
    assert saved is not None and saved["seed"] == 101
    with Image.open(io.BytesIO((tmp_path / saved["reference_image_path"]).read_bytes())) as ref:
        assert ref.getpixel((0, 0)) == (0, 200, 0)


def test_names_from_the_llm_are_grouped_with_their_parts() -> None:
    from python.routes.story import _add_name_parts, _group_names

    groups = _group_names(["はるか", "高橋はるか", "森川陽介"])
    assert [g["name"] for g in groups] == ["高橋はるか", "森川陽介"]
    text = "高橋はるかは眼鏡。はるかは待った。森川陽介が来た。陽介が言った。高橋さんと呼ばれた。"
    # 単独でも使われる姓・名を呼び名に足す(その一部の「るか」は足さない)
    assert _add_name_parts(groups[0], text)["aliases"] == ["高橋はるか", "はるか", "高橋"]
    assert _add_name_parts(groups[1], text)["aliases"] == ["森川陽介", "陽介"]
