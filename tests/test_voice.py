"""
読み上げ(voice.py と /api/voice)のテスト。VOICEVOX のエンジンは差し替える(動いていなくてよい)。

実行方法:
  uv run pytest tests/test_voice.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import voice  # noqa: E402
from python.routes import voice as voice_route  # noqa: E402


class FakeEngine:
    """VOICEVOX のエンジンの代わり(呼ばれた内容を残す)。"""

    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self) -> FakeEngine:
        return self

    def __enter__(self) -> FakeEngine:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def _check(self) -> None:
        if self.down:
            raise httpx.ConnectError("refused")

    def get(self, path: str) -> httpx.Response:
        self._check()
        request = httpx.Request("GET", f"http://engine{path}")
        if path == "/version":
            return httpx.Response(200, json="0.25.1", request=request)
        return httpx.Response(200, request=request, json=[
            {"name": "春日部つむぎ", "styles": [{"name": "ノーマル", "id": 8}]},
            {"name": "ずんだもん", "styles": [{"name": "ノーマル", "id": 3}, {"name": "ささやき", "id": 22, "type": "talk"},
                                              {"name": "歌", "id": 3001, "type": "sing"}]},
        ])

    def post(self, path: str, params: dict[str, Any] | None = None, json: Any = None) -> httpx.Response:
        self._check()
        self.calls.append((path, {"params": params, "json": json}))
        request = httpx.Request("POST", f"http://engine{path}")
        if path == "/audio_query":
            return httpx.Response(200, json={"speedScale": 1.0, "text": params["text"]}, request=request)
        return httpx.Response(200, content=b"RIFF" + json["text"].encode(), request=request)


@pytest.fixture()
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    fake = FakeEngine()
    monkeypatch.setattr(voice, "_client", fake)
    monkeypatch.setattr(voice, "_speakers", None)
    voice._cache.clear()
    return fake


def test_clean_text_drops_markdown() -> None:
    text = "1. [漫画の欄へ](/story?story=3#manga-v2) 移動します。\n> ※ **注意** です `F:\\backup`\n![動画](/help-media/a.mp4)"
    assert voice.clean_text(text) == "漫画の欄へ 移動します。\n※ 注意 です F:\\backup"


def test_split_sentences() -> None:
    assert voice.split_sentences("次はこれです。押してください!\n- 2つめ") == ["次はこれです。", "押してください!", "2つめ"]
    long = "あ" * 50 + "、" + "い" * 100
    parts = voice.split_sentences(long, limit=120)
    assert parts[0].endswith("、") and "".join(parts) == long


def test_synthesize_sets_speed_and_caches(engine: FakeEngine) -> None:
    audio = voice.synthesize("**読む**文です。", 8, 1.3)
    assert audio == b"RIFF" + "読む文です。".encode()
    query, synthesis = engine.calls
    assert query[1]["params"] == {"text": "読む文です。", "speaker": 8}
    assert synthesis[1]["json"]["speedScale"] == 1.3
    voice.synthesize("**読む**文です。", 8, 1.3)
    assert len(engine.calls) == 2  # 同じ文・同じ声は使い回す
    with pytest.raises(ValueError):
        voice.synthesize("[](/x)", 8)


def test_speakers_only_talk_styles(engine: FakeEngine) -> None:
    names = [(s["name"], s["style"]) for s in voice.speakers()]
    assert names == [("春日部つむぎ", "ノーマル"), ("ずんだもん", "ノーマル"), ("ずんだもん", "ささやき")]


def test_engine_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice, "_client", FakeEngine(down=True))
    monkeypatch.setattr(voice, "_speakers", None)
    voice._cache.clear()
    assert voice.status()["ready"] is False
    app = FastAPI()
    app.include_router(voice_route.router)
    client = TestClient(app)
    assert client.get("/api/voice/speakers").status_code == 503
    assert client.post("/api/voice/tts", json={"text": "こんにちは。"}).status_code == 503


def test_api(engine: FakeEngine) -> None:
    app = FastAPI()
    app.include_router(voice_route.router)
    client = TestClient(app)
    assert client.get("/api/voice/status").json()["ready"] is True
    split = client.post("/api/voice/split", json={"text": "1. **押す**。\n2. 待つ。"}).json()
    assert split["sentences"] == ["押す。", "待つ。"]
    res = client.post("/api/voice/tts", json={"text": "押す。", "speaker": 3, "speed": 1.1})
    assert res.status_code == 200 and res.headers["content-type"] == "audio/wav" and res.content.startswith(b"RIFF")
    assert client.post("/api/voice/tts", json={"text": "x", "speed": 5}).status_code == 422
