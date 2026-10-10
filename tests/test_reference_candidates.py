"""
キャラシートから参照画像の候補を生成し、選んだ1枚を参照画像と基準シードに登録する仕組みのテスト。
NovelAI には接続せず、画像生成を差し替える。DB と画像の保存先は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_reference_candidates.py -v
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes import manga_v2  # noqa: E402

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    refs = tmp_path / "outputs" / "manga" / "refs"
    monkeypatch.setattr(manga_v2, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manga_v2, "_REFERENCE_DIR", refs)
    monkeypatch.setattr(manga_v2, "_CANDIDATE_DIR", refs / "candidates")
    calls: list[dict] = []

    async def fake_generate(
        api_key: str, prompt: str, negative: str, **kwargs: object
    ) -> bytes:
        calls.append({"prompt": prompt, "negative": negative, **kwargs})
        buf = io.BytesIO()
        Image.new("RGB", (8, 12), "white").save(buf, format="PNG")
        return buf.getvalue()

    monkeypatch.setattr(manga_v2, "generate_image_v5", fake_generate)
    app = FastAPI()
    app.include_router(manga_v2.router)
    with TestClient(app) as test_client:
        test_client.calls = calls  # type: ignore[attr-defined]
        yield test_client


def _character() -> int:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "矢野先生", "1boy, black hair, glasses")
        db.update_character_sheet(
            conn,
            character["id"],
            {"outfit_tags": "white shirt", "negative_tags": "beard"},
        )
    finally:
        conn.close()
    return character["id"]


def test_candidates_then_choose(client: TestClient, tmp_path: Path) -> None:
    character_id = _character()
    res = client.post(
        f"/api/manga-v2/characters/{character_id}/reference-candidates",
        json={"count": 2},
        headers=AUTH,
    )
    assert res.status_code == 200, res.text
    candidates = res.json()
    assert len(candidates) == 2 and candidates[0]["seed"] != candidates[1]["seed"]
    call = client.calls[0]  # type: ignore[attr-defined]
    # キャラの欄に容姿+服装、全体には候補の構図、キャラのネガティブも付く
    assert call["character_tags"] == ["1boy, black hair, glasses, white shirt"]
    assert "white background" in call["prompt"] and "beard" in call["negative"]
    assert call["seed"] == candidates[0]["seed"]

    chosen = candidates[1]
    res = client.put(
        f"/api/manga-v2/characters/{character_id}/reference",
        json={"candidate_path": chosen["path"], "seed": chosen["seed"]},
    )
    assert res.status_code == 204, res.text
    conn = db.get_connection()
    try:
        character = db.get_character(conn, character_id)
    finally:
        conn.close()
    assert character is not None and character["seed"] == chosen["seed"]
    assert (tmp_path / character["reference_image_path"]).is_file()


def test_candidate_path_must_be_a_candidate(client: TestClient, tmp_path: Path) -> None:
    character_id = _character()
    (tmp_path / "secret.png").write_bytes(b"x")
    res = client.put(
        f"/api/manga-v2/characters/{character_id}/reference",
        json={"candidate_path": "secret.png"},
    )
    assert res.status_code == 404


def test_candidates_need_a_character(client: TestClient) -> None:
    assert (
        client.post(
            "/api/manga-v2/characters/999/reference-candidates", json={}, headers=AUTH
        ).status_code
        == 404
    )
