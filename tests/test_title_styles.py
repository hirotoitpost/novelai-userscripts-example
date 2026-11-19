"""
作品風テンプレート(title_styles.py)と一覧の API(/api/title-styles)のテスト。NovelAI には接続しない。

実行方法:
  uv run pytest tests/test_title_styles.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import title_styles as ts  # noqa: E402
from python.routes import title_styles as title_style_routes  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(title_style_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def test_keys_unique_and_fields_filled() -> None:
    keys = [s.key for s in ts.TITLE_STYLES]
    assert len(keys) == len(set(keys))
    for s in ts.TITLE_STYLES:
        assert s.kind in ("game", "anime")
        assert s.title_tag and s.look and s.outfit and s.label and s.framing


def test_style_tags_leave_out_framing_and_outfit() -> None:
    """漫画のコマ・コレクション用は作品タグと絵柄だけ(構図・背景・衣装は場面が決める)。"""
    ba = ts.require("blue_archive")
    tags = ba.style_tags()
    assert tags.startswith("blue archive")
    assert "white background" not in tags and "full body" not in tags
    assert "tactical vest" not in tags


def test_prompt_tags_include_outfit_and_framing() -> None:
    ba = ts.require("blue_archive")
    assert "tactical vest" in ba.prompt_tags()
    assert "white background" in ba.prompt_tags()
    assert "tactical vest" not in ba.prompt_tags(outfit=False)


def test_apply_adds_tags_and_negative_without_duplicates() -> None:
    prompt, negative = ts.apply("1girl, white background", "lowres", "kimetsu")
    assert prompt.startswith("1girl, white background")
    assert prompt.count("white background") == 1  # 元のタグと重ねない
    assert "kimetsu no yaiba" in prompt
    assert negative is not None and negative.startswith("lowres") and "demon slayer uniform" in negative


def test_apply_without_key_is_noop() -> None:
    assert ts.apply("1girl", None, None) == ("1girl", None)


def test_unknown_key_raises_with_choices() -> None:
    with pytest.raises(KeyError, match="blue_archive"):
        ts.require("no_such_title")


def test_join_tags_dedupes_case_insensitive() -> None:
    assert ts.join_tags("a, B", "b, c", "") == "a, B, c"


def test_catalog_route(client: TestClient) -> None:
    res = client.get("/api/title-styles")
    assert res.status_code == 200
    data = res.json()
    assert {d["key"] for d in data} >= {"blue_archive", "nikke", "genshin", "umamusume", "frieren"}
    ba = next(d for d in data if d["key"] == "blue_archive")
    assert ba["style_tags"] and ba["prompt_tags"]
