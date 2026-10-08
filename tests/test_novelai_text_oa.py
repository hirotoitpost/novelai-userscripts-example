"""
NovelAI の文章生成(text.novelai.net/oa)の呼び出しのテスト。画面のログインのトークンで 401 が返ったら、
.env の永続 API トークンで呼び直すこと。NovelAI には接続せず、偽の応答で確かめる。

実行方法:
  uv run pytest tests/test_novelai_text_oa.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import novelai_text_oa  # noqa: E402
from python.novelai_text_oa import stream_chat  # noqa: E402

_SSE = 'data: {"choices": [{"delta": {"content": "こん"}}]}\n\ndata: {"choices": [{"delta": {"content": "にちは"}}]}\n\ndata: [DONE]\n\n'


def _fake_novelai(monkeypatch: pytest.MonkeyPatch, accepted: str) -> list[str]:
    """accepted のトークンだけ受け付ける偽の NovelAI。使われたトークンを順に記録して返す。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        seen.append(token)
        if token != accepted:
            return httpx.Response(401, json={"statusCode": 401, "message": "Unauthorized"})
        return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        novelai_text_oa.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    return seen


async def _chat() -> str:
    return "".join([d async for d in stream_chat("login-token", [{"role": "user", "content": "hi"}])])


def test_retries_with_persistent_token_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVELAI_API_TOKEN", "pst-persistent")
    seen = _fake_novelai(monkeypatch, accepted="pst-persistent")
    assert asyncio.run(_chat()) == "こんにちは"
    assert seen == ["login-token", "pst-persistent"]


def test_uses_the_given_token_when_it_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVELAI_API_TOKEN", "pst-persistent")
    seen = _fake_novelai(monkeypatch, accepted="login-token")
    assert asyncio.run(_chat()) == "こんにちは"
    assert seen == ["login-token"]


def test_reports_401_without_a_persistent_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOVELAI_API_TOKEN", raising=False)
    monkeypatch.delenv("NOVELAI_API_KEY", raising=False)
    seen = _fake_novelai(monkeypatch, accepted="pst-persistent")
    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(_chat())
    assert seen == ["login-token"]
