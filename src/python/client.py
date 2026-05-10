from __future__ import annotations

import os
from typing import AsyncGenerator

from fastapi import Header, HTTPException
from novelai import AsyncNovelAI

_fallback_client: AsyncNovelAI | None = None


async def init_client() -> None:
    global _fallback_client
    api_key = os.environ.get("NOVELAI_API_KEY") or os.environ.get("NOVELAI_API_TOKEN")
    if api_key:
        _fallback_client = AsyncNovelAI(api_key=api_key)


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
    なければ .env の NOVELAI_API_TOKEN をフォールバックに使う。
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
            detail="Not authenticated. Provide Authorization header or set NOVELAI_API_TOKEN.",
        )
    yield _fallback_client
