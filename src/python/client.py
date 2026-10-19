from __future__ import annotations

import os
from typing import AsyncGenerator

from fastapi import Header, HTTPException
from novelai import AsyncNovelAI

_fallback_client: AsyncNovelAI | None = None

_APP_MODES = ("single", "multi")


def app_mode() -> str:
    """
    .env の APP_MODE(single / multi。既定は single)。multi は複数の利用者で 1 つのサーバーを使うとき
    (docs/multi_user_plan_jp.md)。書き間違いを single として通すと運営者のトークンが他の人に使われるので、
    知らない値ならエラーにする(起動時の init_client で止まる)。
    """
    mode = (os.environ.get("APP_MODE") or "single").strip().lower()
    if mode not in _APP_MODES:
        raise ValueError(f"APP_MODE は single か multi です(今の値: {mode!r})")
    return mode


def operator_token() -> str | None:
    """
    .env の永続 API トークン(運営者のもの)。multi では使わない(None): 他の人のリクエストを
    運営者の NovelAI アカウントと Anlas で処理しないため。
    """
    if app_mode() == "multi":
        return None
    return os.environ.get("NOVELAI_API_TOKEN") or os.environ.get("NOVELAI_API_KEY") or None


async def init_client() -> None:
    global _fallback_client
    api_key = operator_token()
    if api_key:
        _fallback_client = AsyncNovelAI(api_key=api_key)


def api_keys_with_fallback(api_key: str) -> list[str]:
    """
    NovelAI を呼ぶときに試すトークンの順: 渡されたもの(画面のログインのトークン)→ .env の永続 API トークン。
    画面に残ったログインのトークンが期限切れなどで 401 になっても、このアプリの利用者本人の永続トークンで
    続けられるようにする(2026-10 実機: スマホの画面から文章・画像の生成がどちらも 401 になった)。
    APP_MODE=multi では .env のトークンを足さない(渡されたものだけ)。
    """
    keys = [api_key]
    persistent = operator_token()
    if persistent and persistent != api_key:
        keys.append(persistent)
    return keys


async def close_client() -> None:
    global _fallback_client
    if _fallback_client is not None:
        await _fallback_client.close()
        _fallback_client = None


async def get_client(
    authorization: str | None = Header(None),
) -> AsyncGenerator[AsyncNovelAI, None]:
    """
    Authorization: Bearer <token> ヘッダーがあればそれを使う。
    なければ .env の NOVELAI_API_TOKEN をフォールバックに使う(APP_MODE=multi では使わず 401)。
    """
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        client = AsyncNovelAI(api_key=token)
        try:
            yield client
        finally:
            await client.close()
        return

    if _fallback_client is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Provide Authorization header"
            + ("." if app_mode() == "multi" else " or set NOVELAI_API_TOKEN."),
        )
    yield _fallback_client
