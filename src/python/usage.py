"""
GPU の利用上限(APP_MODE=multi)。docs/multi_user_plan_jp.md のステップ 6b。

運営者の GPU を使うもの(Ollama / vLLM の文章生成)と、外部の GPU を借りるもの(ComfyUI の動画生成。お金がかかる)に、
利用者ごとの 1 日の回数と「同時に 1 つ」をかける。NovelAI の生成は各自の Anlas を使うので対象外。手元の画像のモデル
(拡大・タグの推定など)は CPU で動くので、今は対象外。

- 回数: .env の APP_LIMIT_LLM_PER_DAY(既定 500。Ollama / vLLM への通信 1 回ごと)・APP_LIMIT_VIDEO_PER_DAY(既定 20。
  動画 1 本ごと)。0 なら上限なし。日付はサーバーの PC の日付で区切る。
- 同時に 1 つ: 断らずに、その人の前の処理が終わるまで待たせる(1 つのジョブの中でまとめて呼ぶ処理もあるので)。
- 上限をかけない: single、利用者が決まっていないとき(起動時の処理など)、管理者。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx

from . import db
from .client import app_mode

KINDS: dict[str, tuple[str, int, str]] = {
    "llm": ("APP_LIMIT_LLM_PER_DAY", 500, "文章生成(Ollama / vLLM)"),
    "video": ("APP_LIMIT_VIDEO_PER_DAY", 20, "動画の生成"),
}


class LimitExceeded(Exception):
    """今日の上限に達した(429 で返す。server.py)。"""


def limit_of(kind: str) -> int | None:
    """1 日の上限(None は上限なし)。"""
    env, default, _ = KINDS[kind]
    raw = os.environ.get(env, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return value if value > 0 else None


def _today() -> str:
    return datetime.now().date().isoformat()


def _limited_user() -> int | None:
    """上限をかける利用者(かけないなら None)。"""
    if app_mode() != "multi":
        return None
    user_id = db.current_user_id()
    if user_id is None:
        return None
    conn = db.get_system_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(i.is_admin, 0) AS is_admin FROM users u"
            " LEFT JOIN user_invites i ON i.email_hash = u.email_hash WHERE u.id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    return None if row is not None and row["is_admin"] else user_id


def used_today(user_id: int, kind: str) -> int:
    conn = db.get_system_connection()
    try:
        row = conn.execute(
            "SELECT count FROM usage_counts WHERE user_id = ? AND day = ? AND kind = ?", (user_id, _today(), kind)
        ).fetchone()
    finally:
        conn.close()
    return int(row["count"]) if row else 0


def _take(user_id: int, kind: str) -> None:
    """1 回分を数える。上限に達していれば LimitExceeded(数えない)。"""
    limit = limit_of(kind)
    conn = db.get_system_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")  # 同時に数えても、上限を超えないように
        row = conn.execute(
            "SELECT count FROM usage_counts WHERE user_id = ? AND day = ? AND kind = ?", (user_id, _today(), kind)
        ).fetchone()
        used = int(row["count"]) if row else 0
        if limit is not None and used >= limit:
            conn.rollback()
            raise LimitExceeded(f"{KINDS[kind][2]}は、今日の上限({limit} 回)に達しました。明日また使えます。")
        conn.execute(
            "INSERT INTO usage_counts (user_id, day, kind, count) VALUES (?, ?, ?, 1)"
            " ON CONFLICT(user_id, day, kind) DO UPDATE SET count = count + 1",
            (user_id, _today(), kind),
        )
        conn.commit()
    finally:
        conn.close()


# 「同時に 1 つ」の鍵。asyncio.Lock はイベントループごとなので、ループも鍵に入れる(テストではループが別になる)
_locks: dict[tuple[int, int, str], asyncio.Lock] = {}


def _lock(user_id: int, kind: str) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), user_id, kind)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


@asynccontextmanager
async def slot(kind: str) -> AsyncIterator[None]:
    """GPU を使う処理を 1 回分。上限なら LimitExceeded、同じ人の前の処理があれば終わるまで待つ。"""
    user_id = _limited_user()
    if user_id is None:
        yield
        return
    async with _lock(user_id, kind):
        _take(user_id, kind)
        yield


class _ReleasingStream(httpx.AsyncByteStream):
    """応答の本文を読み終えた(閉じた)ときに、「同時に 1 つ」の鍵を返す(流れてくる応答でも生成の間は持つ)。"""

    def __init__(self, stream: Any, release: Any) -> None:
        self._stream = stream
        self._release = release

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        try:
            await self._stream.aclose()
        finally:
            await self._release()


class LimitedTransport(httpx.AsyncBaseTransport):
    """
    Ollama / vLLM への httpx の通信に、文章生成の上限をかける(llm_client と、Ollama を直接呼ぶところで使う)。
    上限のとき: 直接呼ぶところには LimitExceeded をそのまま投げる(httpx は包み直さないので、呼ぶ側の
    except にそのままのメッセージが届く)。openai のクライアント(as_response)には 429 の応答を返す。
    """

    def __init__(self, kind: str = "llm", *, as_response: bool = False) -> None:
        self._kind = kind
        self._as_response = as_response
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        manager = slot(self._kind)
        try:
            await manager.__aenter__()
        except LimitExceeded as exc:
            if not self._as_response:
                raise
            # 例外にすると openai のクライアントが「接続エラー」に包んでやり直すので、429 の応答にして理由を伝える
            # (本文は OpenAI と同じ形。x-should-retry: false でやり直しを止める)
            return httpx.Response(
                429,
                headers={"x-should-retry": "false"},
                json={"error": {"message": str(exc), "type": "usage_limit"}, "detail": str(exc)},
                request=request,
            )
        released = False

        async def release() -> None:
            nonlocal released
            if not released:
                released = True
                await manager.__aexit__(None, None, None)

        try:
            response = await self._inner.handle_async_request(request)
        except BaseException:
            await release()
            raise
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_ReleasingStream(response.stream, release),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def llm_http_client(*, for_openai: bool = False, **kwargs: Any) -> httpx.AsyncClient:
    """文章生成の上限をかけた httpx のクライアント(Ollama / vLLM を呼ぶとき)。openai のクライアントに渡すなら for_openai。"""
    return httpx.AsyncClient(transport=LimitedTransport("llm", as_response=for_openai), **kwargs)


def today_summary(user_id: int) -> dict[str, dict[str, int | None]]:
    """その人の今日の回数と上限(画面・管理ページ用)。"""
    return {kind: {"used": used_today(user_id, kind), "limit": limit_of(kind)} for kind in KINDS}
