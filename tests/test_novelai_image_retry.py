"""NovelAI V5 の画像生成: 一時的な失敗(通信の切断・タイムアウト・429・5xx)をやり直す。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import novelai_image_v5  # noqa: E402
from python.routes.story import error_text  # noqa: E402


def _client(answers: list) -> tuple[httpx.AsyncClient, list]:
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Authorization"])
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def test_transient_failures_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(novelai_image_v5, "_RETRY_WAIT", 0.0)
    client, calls = _client([502, httpx.ReadTimeout(""), 429, 200])
    response = asyncio.run(novelai_image_v5._post_retrying(client, {}, "key"))
    assert response.status_code == 200 and len(calls) == 4
    # やり直しても変わらない失敗(401 など)は、すぐ返す(401 は呼ぶ側で .env のトークンに替える)
    client, calls = _client([401])
    assert asyncio.run(novelai_image_v5._post_retrying(client, {}, "key")).status_code == 401 and len(calls) == 1


def test_gives_up_with_a_readable_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(novelai_image_v5, "_RETRY_WAIT", 0.0)
    client, _ = _client([httpx.ReadTimeout("")] * 4)
    with pytest.raises(RuntimeError, match="ReadTimeout"):
        asyncio.run(novelai_image_v5._post_retrying(client, {}, "key"))
    client, _ = _client([503] * 4)
    assert asyncio.run(novelai_image_v5._post_retrying(client, {}, "key")).status_code == 503
    # メッセージが空の例外でも、ジョブの失敗の説明に種類が出る
    assert error_text(httpx.ReadTimeout("")) == "ReadTimeout" and error_text(ValueError("x")) == "ValueError: x"
