"""
表情の変更・2倍の拡大(NovelAI のディレクターツール)の API のテスト。NovelAI へは送らず、クライアントを差し替える。

実行方法:
  uv run pytest tests/test_image_tools.py -v
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.client import get_client  # noqa: E402
from python.routes import image as image_routes  # noqa: E402


def png(size: tuple[int, int] = (64, 96), comment: dict[str, Any] | None = None) -> bytes:
    info = PngInfo()
    if comment is not None:
        info.add_text("Comment", json.dumps(comment))
        info.add_text("Source", "NovelAI Diffusion V4.5 4BDE2A90")
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def emotion(self, image: bytes, emotion: str, *, prompt: str = "", defry: int = 0) -> list[Image.Image]:
        self.calls.append(("emotion", emotion, prompt, defry, len(image)))
        return [Image.open(io.BytesIO(image)).convert("RGB")]

    async def upscale(self, params: Any) -> list[Image.Image]:
        self.calls.append(("upscale", len(params.image)))
        source = Image.open(io.BytesIO(params.image))
        return [source.resize((source.width * 2, source.height * 2))]


class FakeUser:
    def __init__(self, tier: int = 3, active: bool = True, fail: bool = False) -> None:
        self.tier, self.active, self.fail = tier, active, fail

    async def get_subscription(self) -> Any:
        if self.fail:
            raise RuntimeError("no network")
        return SimpleNamespace(tier=self.tier, active=self.active)


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, SimpleNamespace]:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(image_routes, "_HISTORY_DIR", tmp_path / "history")
    fake = SimpleNamespace(tools=FakeTools(), user=FakeUser())
    app = FastAPI()
    app.include_router(image_routes.router)
    app.dependency_overrides[get_client] = lambda: fake
    return TestClient(app), fake


def _history(tmp_path: Path) -> list[dict[str, Any]]:
    conn = db.get_connection()
    try:
        return [dict(row) for row in conn.execute("SELECT prompt, model, seed FROM generation_history ORDER BY id")]
    finally:
        conn.close()


def test_emotion_changes_the_image_and_keeps_it_in_the_gallery(
    api: tuple[TestClient, SimpleNamespace], tmp_path: Path
) -> None:
    client, fake = api
    comment = {
        "prompt": "1girl, cafe",
        "uc": "blurry",
        "steps": 23,
        "scale": 5.0,
        "seed": 7,
        "width": 832,
        "height": 1216,
    }
    source = base64.b64encode(png(comment=comment)).decode()
    res = client.post(
        "/api/image/tools/emotion", json={"image": f"data:image/png;base64,{source}", "emotion": "happy", "defry": 1}
    )
    assert res.status_code == 200, res.text
    assert Image.open(io.BytesIO(base64.b64decode(res.json()["images"][0]))).size == (64, 96)
    assert fake.tools.calls[0][:4] == ("emotion", "happy", "", 1)
    # ギャラリーには、元の画像のプロンプトに「何をしたか」を付けて残す
    assert _history(tmp_path) == [{"prompt": "[表情: happy] 1girl, cafe", "model": "nai-diffusion-4-5-full", "seed": 7}]
    assert len(list((tmp_path / "history").glob("*.png"))) == 1
    # 知らない表情・範囲の外の値は受け付けない
    assert client.post("/api/image/tools/emotion", json={"image": source, "emotion": "sleepy"}).status_code == 422
    assert (
        client.post("/api/image/tools/emotion", json={"image": source, "emotion": "sad", "defry": 9}).status_code == 422
    )


def test_upscale_doubles_the_image(api: tuple[TestClient, SimpleNamespace], tmp_path: Path) -> None:
    client, fake = api
    res = client.post("/api/image/tools/upscale", json={"image": base64.b64encode(png()).decode()})
    assert res.status_code == 200, res.text
    assert Image.open(io.BytesIO(base64.b64decode(res.json()["images"][0]))).size == (128, 192)
    assert fake.tools.calls[0][0] == "upscale"
    # NovelAI の設定が入っていない画像でも、記録は残す
    assert _history(tmp_path)[0]["prompt"] == "[2倍に拡大]"


def test_tool_errors_are_reported_and_nothing_is_saved(api: tuple[TestClient, SimpleNamespace], tmp_path: Path) -> None:
    client, fake = api

    async def boom(*args: Any, **kwargs: Any) -> list[Image.Image]:
        raise RuntimeError("image too large")

    fake.tools.upscale = boom
    res = client.post("/api/image/tools/upscale", json={"image": base64.b64encode(png()).decode()})
    assert res.status_code == 500 and "image too large" in res.json()["detail"]
    assert _history(tmp_path) == []


def test_anlas_estimate_depends_on_the_subscription(api: tuple[TestClient, SimpleNamespace]) -> None:
    client, fake = api
    opus = client.get("/api/image/tools/anlas", params={"tool": "emotion", "width": 832, "height": 1216}).json()
    assert opus == {"tool": "emotion", "total_anlas": 0, "is_opus": True}
    fake.user.tier = 2
    paid = client.get("/api/image/tools/anlas", params={"tool": "emotion", "width": 832, "height": 1216}).json()
    assert paid["total_anlas"] > 0 and paid["is_opus"] is False
    # 契約が分からないときは、高い方の見積もりを出す
    fake.user.fail = True
    unknown = client.get("/api/image/tools/anlas", params={"tool": "emotion", "width": 832, "height": 1216}).json()
    assert unknown == paid
    assert (
        client.get("/api/image/tools/anlas", params={"tool": "nope", "width": 832, "height": 1216}).status_code == 422
    )
    assert client.get("/api/image/tools/anlas", params={"tool": "emotion", "width": 8, "height": 8}).status_code == 422
