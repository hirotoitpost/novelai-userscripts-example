from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException

_NOVELAI_API = "https://api.novelai.net"

# Cloudflare が素のスクリプトをブロックするため、ブラウザライクなヘッダーが必要
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://novelai.net/",
    "Origin": "https://novelai.net",
}

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/subscription")
async def get_subscription(
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """NovelAI の /user/subscription をプロキシして tier・学習ステップ残量などを返す。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization: Bearer <token> required")
    token = authorization[7:]
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_NOVELAI_API}/user/subscription",
            headers={**_BROWSER_HEADERS, "Authorization": f"Bearer {token}"},
            timeout=30,
        )
    if not resp.is_success:
        try:
            detail = resp.json().get("message", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()  # type: ignore[no-any-return]
