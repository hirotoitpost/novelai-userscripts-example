"""
コンテンツガード(content_guard)と、その編集用 API(/api/content-guard)のテスト: はじめの値が DB に入ること、
はじめの値での判定がこれまでどおりであること、API で変えた値がすぐ各機能に効くこと、戻せること。
DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_content_guard.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import content_guard, db  # noqa: E402
from python.character_sheet import blocked_tags, minor_tags, sheet_negative, sheet_prompt  # noqa: E402
from python.manga_draft import _OUTLINE_SYSTEM, _content_rule  # noqa: E402
from python.manga_similar import _is_safe, _naming_system  # noqa: E402
from python.manga_v2.prompt import build_panel_negative, build_panel_prompt, is_sexual  # noqa: E402
from python.routes import content_guard as content_guard_routes  # noqa: E402
from python.routes.story import _adult_tags_system_prompt, sanitize_scene_tags  # noqa: E402

CHARACTER = {"appearance_tags": "1girl, black hair", "outfit_tags": "blouse", "negative_tags": "short hair"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    content_guard.reset_cache()
    app = FastAPI()
    app.include_router(content_guard_routes.router)
    yield TestClient(app)
    content_guard.reset_cache()


def put(client: TestClient, key: str, value: object) -> dict:
    res = client.put(f"/api/content-guard/rules/{key}", json={"value": value})
    assert res.status_code == 200, res.text
    return res.json()


def test_defaults_are_seeded_into_the_db(client: TestClient, tmp_path: Path) -> None:
    rules = client.get("/api/content-guard/rules").json()["rules"]
    assert len(rules) == 20 and not any(r["modified"] for r in rules)
    conn = sqlite3.connect(tmp_path / "app.db")
    try:
        stored = {row[0] for row in conn.execute("SELECT key FROM content_guard_rules")}
    finally:
        conn.close()
    assert stored == {r["key"] for r in rules}
    by_key = {r["key"]: r for r in rules}
    assert len(by_key["dataset.blocked_tags"]["value"]) == 20
    assert len(by_key["dataset.minor_tags"]["value"]) == 23
    assert len(by_key["scene.sexual_tags"]["value"]) == 19
    assert by_key["library.adult_text_min_hits"]["value"] == 3


def test_default_behaviour_is_unchanged(client: TestClient) -> None:
    # キャラ別データセット
    assert blocked_tags("one-piece swimsuit, sports bra, {{nude}}, nipple") == ["bra", "nipple", "nude", "swimsuit"]
    assert blocked_tags("sexy, cumulonimbus, swimwear, nude_body") == []
    assert blocked_tags("maid outfit, mermaid", ["maid"], include_core=False) == ["maid"]
    assert minor_tags("JK, student council, young woman") == ["jk", "student", "young"]
    assert minor_tags("lolita fashion, petite, adult") == []
    assert sheet_negative(CHARACTER).endswith("short hair, nsfw, nude, underwear, swimsuit, cleavage")
    assert "child, loli, shota, young, teenage" in sheet_negative(CHARACTER, r18=True)
    assert sheet_prompt(CHARACTER, r18=True) == "1girl, black hair, adult, mature female, blouse"
    # 場面のタグ
    assert sanitize_scene_tags("1girl, school uniform, classroom, smile", adult=False) == (
        "1girl, school uniform, classroom, smile"
    )
    assert sanitize_scene_tags("1girl, nude, school uniform, classroom, bed", adult=False) == "1girl, nude, bed"
    assert sanitize_scene_tags("1girl, school uniform, classroom, bed", adult=True) == "1girl, bed"
    assert sanitize_scene_tags("1girl, sex, pussy, bed", adult=True) == (
        "nsfw, explicit, uncensored, 1girl, sex, pussy, bed"
    )
    assert sanitize_scene_tags("nsfw, 1girl, penis, fellatio", adult=True) == (
        "nsfw, explicit, uncensored, 1girl, penis, fellatio"
    )
    assert sanitize_scene_tags("1girl, kiss, bed", adult=True) == "1girl, kiss, bed"
    assert is_sexual("1girl, nude") and not is_sexual("1girl, bikini") and not is_sexual("sexy pose")
    assert build_panel_negative(None, color=True, sexual=True).endswith(
        ", child, loli, shota, young, petite, flat chest, school uniform, student"
    )
    assert "loli" not in build_panel_negative(None, color=True)
    assert build_panel_prompt("1girl, nude, school uniform, bed", color=True, complexity=None) == (
        "manga style, 1girl, nude, bed, very aesthetic, masterpiece"
    )
    # 本棚・ギャラリー
    assert content_guard.tags_adult("explicit, 1girl") and content_guard.tags_adult("r-18")
    assert not content_guard.tags_adult("1girl, underwear") and not content_guard.tags_adult(None)
    assert content_guard.count("library.adult_text", "全裸の全裸で絶頂、イクっ") == 4
    # 似た漫画
    assert not _is_safe("large breasts") and not _is_safe("wet hair") and not _is_safe("sex")
    assert _is_safe("sweatdrop") and _is_safe("black thighhighs")
    # LLM への指示
    prompt = _adult_tags_system_prompt(2)
    assert "\nAll characters are adults. Read each scene" in prompt
    assert (
        "- never use tags implying minors (child, loli, shota, school uniform, student, classroom) and do not add"
        in prompt
    )
    assert _content_rule() == "\n- 全年齢向け。露出・性的な描写はしない"
    assert _OUTLINE_SYSTEM.format(options=3, episodes=4, content_rule=_content_rule()).endswith("性的な描写はしない")
    assert "- 全年齢向けの日常の話に出せる人物にする\n- look:" in _naming_system()
    assert content_guard.words("stamps.adult_tags") == {"R-18", "R18", "R-18G"}


def test_edits_take_effect_everywhere(client: TestClient) -> None:
    # 止めるタグを足す
    current = client.get("/api/content-guard/rules/dataset.blocked_tags").json()["value"]
    saved = put(client, "dataset.blocked_tags", [*current, "swimwear", "see[- ]through"])
    assert saved["modified"] and saved["updated_at"]
    assert blocked_tags("swimwear, see through") == ["see through", "swimwear"]
    # 1行に1つの文字列でも渡せる(空行・重複は捨てる)
    put(client, "scene.sexual_tags", "nude\nbikini\n\nNude\n")
    assert content_guard.get("scene.sexual_tags") == ["nude", "bikini"]
    assert is_sexual("1girl, bikini") and not is_sexual("1girl, sex")
    assert sanitize_scene_tags("1girl, bikini, student", adult=False) == "1girl, bikini"
    assert content_guard.tags_adult("1girl, bikini")
    assert not _is_safe("bikini armor")
    # ネガティブと決まり
    put(client, "general.safe_negative", "nsfw, nude, see-through")
    assert sheet_negative(CHARACTER).endswith("short hair, nsfw, nude, see-through")
    put(client, "scene.adult_safety_negative", "child, aged down")
    assert build_panel_negative(None, color=True, sexual=True).endswith(", child, aged down")
    put(client, "draft.content_rule", "")
    assert _content_rule() == ""
    put(client, "scene.adult_tagging_rule", "")
    assert "- do not add places or clothes not in the text\n" in _adult_tags_system_prompt(1)
    put(client, "library.adult_text_min_hits", 1)
    assert content_guard.get("library.adult_text_min_hits") == 1
    # 並びを空にすると、何にも当たらなくなる
    put(client, "dataset.minor_tags", [])
    assert minor_tags("school uniform, loli") == []


def test_validation(client: TestClient) -> None:
    def status(key: str, value: object) -> int:
        return client.put(f"/api/content-guard/rules/{key}", json={"value": value}).status_code

    assert status("nope", ["x"]) == 404
    assert status("dataset.blocked_tags", ["(unclosed"]) == 422
    assert status("dataset.blocked_tags", "x" * 300) == 422
    assert status("dataset.blocked_tags", 3) == 422
    assert status("dataset.blocked_tags", [1, 2]) == 422
    assert status("general.safe_negative", ["nsfw"]) == 422
    assert status("library.adult_text_min_hits", 0) == 422
    assert status("library.adult_text_min_hits", "3") == 422
    assert status("library.adult_text_min_hits", 2.5) == 422
    # 失敗した変更は残らない
    assert not any(r["modified"] for r in client.get("/api/content-guard/rules").json()["rules"])
    assert client.get("/api/content-guard/rules/nope").status_code == 404
    assert client.delete("/api/content-guard/rules/nope").status_code == 404


def test_reset(client: TestClient) -> None:
    put(client, "dataset.blocked_tags", ["nude"])
    put(client, "general.safe_negative", "nsfw")
    assert blocked_tags("bikini") == []
    restored = client.delete("/api/content-guard/rules/dataset.blocked_tags").json()
    assert not restored["modified"] and len(restored["value"]) == 20
    assert blocked_tags("bikini") == ["bikini"]
    assert content_guard.get("general.safe_negative") == "nsfw"
    rules = client.post("/api/content-guard/reset").json()["rules"]
    assert not any(r["modified"] for r in rules)
    assert content_guard.get("general.safe_negative") == "nsfw, nude, underwear, swimsuit, cleavage"


def test_check(client: TestClient) -> None:
    result = client.post("/api/content-guard/check", json={"tags": "1girl, nude, school uniform, bed"}).json()
    assert result == {
        "dataset_blocked": ["nude"],
        "dataset_minor": ["school uniform"],
        "sexual": True,
        "library_adult": True,
        "scene_tags": "1girl, nude, bed",
        "scene_tags_adult": "nsfw, 1girl, nude, bed",
    }
