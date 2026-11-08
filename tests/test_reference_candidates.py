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


def test_illustration_candidates_use_the_dataset_prompt(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    async def fake_sdk(novelai, prompt, size, cfg, *, seed=None):
        calls.append({"prompt": prompt, "size": size, "cfg": cfg, "seed": seed})
        return Image.new("RGB", (8, 12), "white")

    monkeypatch.setattr(manga_v2, "generate_one", fake_sdk)
    character_id = _character()
    conn = db.get_connection()
    try:
        db.update_character_sheet(conn, character_id, {"style_tags": "flat color"})
    finally:
        conn.close()
    res = client.post(
        f"/api/manga-v2/characters/{character_id}/reference-candidates",
        json={"count": 1, "style": "illustration"},
        headers=AUTH,
    )
    assert res.status_code == 200, res.text
    candidate = res.json()[0]
    assert (tmp_path / candidate["path"]).is_file()
    # 漫画用の組み立て(manga style・キャラの欄)は使わず、キャラシートの画風と候補の構図を全体のプロンプトに入れる
    assert client.calls == []  # type: ignore[attr-defined]
    call = calls[0]
    assert "manga style" not in call["prompt"]
    assert call["prompt"].startswith("1boy, black hair, glasses, white shirt, flat color")
    assert "white background" in call["prompt"]
    assert "beard" in call["cfg"].negative_prompt and call["cfg"].model == "nai-diffusion-4-5-full"
    assert call["seed"] == candidate["seed"] and call["size"] == (832, 1216)


def test_back_view_candidates_drop_the_face_and_register_as_back(client: TestClient, tmp_path: Path) -> None:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "あかね", "1girl, silver hair, high ponytail, yellow eyes, beautiful face, face visible")
        db.update_character_sheet(conn, character["id"], {"outfit_tags": "white coat", "seed": 123})
    finally:
        conn.close()
    character_id = character["id"]
    # 正面の参照画像があれば、弱めて使う
    front = tmp_path / "outputs" / "manga" / "refs" / "front.png"
    front.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 12), "red").save(front)
    conn = db.get_connection()
    try:
        db.update_character_reference(conn, character_id, "outputs/manga/refs/front.png")
    finally:
        conn.close()

    res = client.post(
        f"/api/manga-v2/characters/{character_id}/reference-candidates",
        json={"count": 1, "view": "back", "back_reference_strength": 0.3},
        headers=AUTH,
    )
    assert res.status_code == 200, res.text
    call = client.calls[0]  # type: ignore[attr-defined]
    assert call["character_tags"] == ["1girl, silver hair, high ponytail, white coat"]
    assert "from behind" in call["prompt"] and "looking at viewer" not in call["prompt"]
    assert "looking back" in call["negative"]
    assert call["character_reference"].strength == 0.3

    # 選ぶと後ろ姿の参照になり、基準シードは変えない
    chosen = res.json()[0]
    res = client.put(
        f"/api/manga-v2/characters/{character_id}/reference",
        json={"candidate_path": chosen["path"], "view": "back"},
    )
    assert res.status_code == 204, res.text
    conn = db.get_connection()
    try:
        saved = db.get_character(conn, character_id)
    finally:
        conn.close()
    assert saved["back_reference_image_path"].startswith("outputs/manga/refs/char")
    assert saved["reference_image_path"] == "outputs/manga/refs/front.png" and saved["seed"] == 123
