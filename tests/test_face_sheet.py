"""
キャラシートの顔・表情シート(face_sheet.py と /api/lora-dataset/.../face-sheet)のテスト。NovelAI は呼ばず、
生成の関数を差し替える。DB と出力先は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_face_sheet.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.client import get_client  # noqa: E402
from python.face_sheet import ANGLES, EXPRESSIONS, ITEMS  # noqa: E402
from python.routes import lora_dataset as lora_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


def test_presets_cover_the_requested_items() -> None:
    labels = {i.label for i in ITEMS.values()}
    for wanted in ("正面", "側面", "背面", "見返り", "逆立ち", "テヘペロ", "ウインク", "羞恥(恥ずかしい)", "怒", "哀(悲しみ)"):
        assert wanted in labels
    assert all(i.group == "angle" for i in ANGLES) and all(i.group == "expression" for i in EXPRESSIONS)
    assert len(ITEMS) == len(ANGLES) + len(EXPRESSIONS)  # キーが重ならない


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(lora_routes, "_PROJECT_ROOT", tmp_path)
    prompts: list[str] = []

    async def fake_generate(client, prompt, size, cfg, seed=None, inpaint=None):  # noqa: ANN001
        prompts.append(prompt)
        return Image.new("RGB", size, (200, 200, 200))

    monkeypatch.setattr(lora_routes, "generate_one", fake_generate)
    monkeypatch.setattr(lora_routes.asyncio, "sleep", lambda *_: _noop())
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(lora_routes.router)
    app.dependency_overrides[get_client] = lambda: object()
    test_client = TestClient(app)
    test_client.prompts = prompts  # type: ignore[attr-defined]
    return test_client


async def _noop() -> None:
    return None


def test_face_sheet_is_drawn_saved_and_listed(client: TestClient, tmp_path: Path) -> None:
    character = client.post(
        "/api/story/characters/new", json={"name": "ゆら", "appearance_tags": "1girl, brown hair", "trigger_word": "yura"}
    ).json()
    res = client.post(f"/api/lora-dataset/character/{character['id']}/face-sheet", json={"keys": ["front", "tehepero"]})
    assert res.status_code == 200
    assert res.text.count('"status": "ok"') == 2
    prompts = client.prompts  # type: ignore[attr-defined]
    assert "brown hair" in prompts[0] and "front view" in prompts[0] and "white background" in prompts[0]
    assert "tongue out" in prompts[1]

    entries = {e["key"]: e for e in client.get(f"/api/lora-dataset/character/{character['id']}/face-sheet").json()}
    assert entries["front"]["path"] == "outputs/training_data/yura/face_sheet/front.png"
    assert entries["wink"]["path"] is None
    caption = (tmp_path / "outputs/training_data/yura/face_sheet/tehepero.txt").read_text(encoding="utf-8")
    assert caption.startswith("yura, 1girl, brown hair") and "tongue out" in caption
    # 無い項目は描かない
    assert client.post(f"/api/lora-dataset/character/{character['id']}/face-sheet", json={"keys": ["nope"]}).status_code == 422
