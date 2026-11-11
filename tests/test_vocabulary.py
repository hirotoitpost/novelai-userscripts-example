"""
語彙(vocabulary.py)と、開発管理者のページの語彙の API(/api/admin/vocabulary)のテスト。
DB は一時フォルダ(conftest)にする。

実行方法:
  uv run pytest tests/test_vocabulary.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, vocabulary  # noqa: E402
from python.collection_templates import has_long_hair, hair_down_appearance  # noqa: E402
from python.routes import admin  # noqa: E402

_DEFAULTS = json.loads((Path(vocabulary.__file__).with_name("vocabulary_defaults.json")).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh() -> None:
    vocabulary.reset_cache()


def test_defaults_file_is_well_formed() -> None:
    keys = [e["key"] for e in _DEFAULTS["entries"]]
    assert len(keys) == len(set(keys))
    for entry in _DEFAULTS["entries"]:
        assert entry["group"] and entry["label"] and entry["description"], entry["key"]
        assert entry["value"] and all(isinstance(v, str) and v.strip() == v for v in entry["value"]), entry["key"]
        assert len(entry["value"]) == len(set(entry["value"])), entry["key"]


def test_first_read_seeds_the_db() -> None:
    assert "monochrome" in vocabulary.words("tagger.style_tags")
    conn = db.get_system_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM vocabulary").fetchone()[0]
    finally:
        conn.close()
    assert count == len(_DEFAULTS["entries"])


def test_items_keep_order() -> None:
    counts = vocabulary.items("tagger.count_tags")
    assert counts[0] == "1girl"
    assert counts.index("1girl") < counts.index("2girls")


def test_unknown_key() -> None:
    with pytest.raises(KeyError):
        vocabulary.items("nope.nothing")


def test_edit_takes_effect_and_reset_restores() -> None:
    assert has_long_hair("1girl, long hair")
    assert not has_long_hair("1girl, waist-length hair")
    vocabulary.set_value("collection.long_hair_tags", "long hair\nwaist-length hair\n  \nlong hair")
    assert has_long_hair("1girl, waist-length hair")
    assert vocabulary.items("collection.long_hair_tags") == ("long hair", "waist-length hair")
    described = vocabulary.describe_one("collection.long_hair_tags")
    assert described["modified"] and described["updated_at"]
    vocabulary.reset("collection.long_hair_tags")
    assert not has_long_hair("1girl, waist-length hair")
    assert not vocabulary.describe_one("collection.long_hair_tags")["modified"]


def test_edit_is_used_by_callers() -> None:
    assert hair_down_appearance("1girl, ponytail, brown hair") == "1girl, brown hair, hair down, hair hanging down"
    vocabulary.set_value("collection.hair_up_words", ["bun"])
    assert hair_down_appearance("1girl, ponytail, brown hair") is None


def test_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        vocabulary.set_value("tagger.style_tags", [])
    with pytest.raises(ValueError):
        vocabulary.set_value("tagger.style_tags", ["x" * 101])
    with pytest.raises(ValueError):
        vocabulary.set_value("tagger.style_tags", [1, 2])  # type: ignore[list-item]


def test_admin_api() -> None:
    app = FastAPI()
    app.include_router(admin.router)
    client = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    entries = client.get("/api/admin/vocabulary").json()["entries"]
    assert {e["key"] for e in entries} == {e["key"] for e in _DEFAULTS["entries"]}
    res = client.put("/api/admin/vocabulary/story.animal_words", json={"value": ["fox", "tanuki"]})
    assert res.status_code == 200 and res.json()["value"] == ["fox", "tanuki"] and res.json()["modified"]
    assert vocabulary.items("story.animal_words") == ("fox", "tanuki")
    assert client.put("/api/admin/vocabulary/story.animal_words", json={"value": []}).status_code == 422
    assert client.put("/api/admin/vocabulary/nope", json={"value": ["a"]}).status_code == 404
    res = client.post("/api/admin/vocabulary/story.animal_words/reset")
    assert res.status_code == 200 and not res.json()["modified"]
