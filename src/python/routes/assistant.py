"""
作業アシスタント(/api/assistant)。読むだけ(案内を返す。操作はしない)。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .. import assistant

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

# 管理者だけに出す案内(サーバー全体のバックアップなど)
_ADMIN_PAGES = {"admin"}


@router.get("/guide")
async def get_guide(request: Request, page: str = "", story_id: int = 0) -> dict[str, Any]:
    """今のページ(と開いている物語)の「次の操作」。"""
    if page in _ADMIN_PAGES:
        from ..sessions import require_admin
        from .admin import require_lan

        try:
            require_lan(request)
            require_admin(request)
        except HTTPException:
            page = ""
    params = {"page": page, "story_id": str(story_id)}
    return await asyncio.to_thread(assistant.guide, page, params)
