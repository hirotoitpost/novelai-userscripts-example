"""
作業アシスタント(/api/assistant)。読むだけ(案内を返す。操作はしない)。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .. import app_settings, assistant, help_chat
from . import help as help_route

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

# 管理者だけに出す案内(サーバー全体のバックアップなど)
_ADMIN_PAGES = {"admin"}


def _is_admin(request: Request) -> bool:
    """LAN の中の管理者か(開発管理者のページと同じ条件)。"""
    from ..sessions import require_admin
    from .admin import require_lan

    try:
        require_lan(request)
        require_admin(request)
    except HTTPException:
        return False
    return True


def _visible_page(request: Request, page: str) -> str:
    """管理者だけの案内は、LAN の中の管理者にだけ出す(それ以外は、そのほかのページの案内)。"""
    if page in _ADMIN_PAGES and not _is_admin(request):
        return ""
    return page


def _params(request: Request, page: str, story_id: int, character_id: int) -> dict[str, str]:
    return {
        "page": page,
        "story_id": str(story_id),
        "character_id": str(character_id),
        # ホームの案内に、バックアップ(サーバー全体)を入れてよいか
        "is_admin": "1" if _is_admin(request) else "0",
    }


@router.get("/guide")
async def get_guide(request: Request, page: str = "", story_id: int = 0, character_id: int = 0) -> dict[str, Any]:
    """今のページ(と開いている物語・キャラ)の「次の操作」。"""
    page = _visible_page(request, page)
    return await asyncio.to_thread(assistant.guide, page, _params(request, page, story_id, character_id))


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[help_route.ChatTurn] = Field(default_factory=list, max_length=12)
    page: str = Field("", max_length=50)
    story_id: int = 0
    character_id: int = 0
    engine: Literal["default", "auto", "ollama", "novelai"] = "default"


@router.post("/chat")
async def post_chat(req: ChatRequest, request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """汎用のチャット(読むだけ)。今のページの状況とヘルプの記事を渡して、文章モデルに答えさせる。"""
    page = _visible_page(request, req.page)
    guide_data = await asyncio.to_thread(assistant.guide, page, _params(request, page, req.story_id, req.character_id))
    articles = help_chat.pick(req.message, help_chat.load_articles(), guide_data.get("help") or None, limit=2)
    api_key = await help_route.novelai_key(authorization)
    engine = help_chat.choose_engine(req.engine, app_settings.get("assistant.chat_model"), bool(api_key))
    if engine == "excerpt":  # このチャットは文章モデルで答える
        engine = "novelai" if api_key else "ollama"
    messages = assistant.chat_messages(req.message, [t.model_dump() for t in req.history], guide_data, articles)
    try:
        answer = await help_route.ask_model(engine, messages, api_key)
    except HTTPException as exc:
        if engine == "ollama" and exc.status_code == 503:
            raise HTTPException(status_code=503, detail="Ollama が設定されていません(.env の VLLM_BASE_URL)。NovelAI を選んでください。") from exc
        raise
    except Exception as exc:  # noqa: BLE001 文章モデルの失敗は画面に伝える
        label = "NovelAI" if engine == "novelai" else "Ollama"
        raise HTTPException(status_code=502, detail=f"{label} から答えをもらえませんでした: {exc}") from exc
    from .llm import strip_think_tags

    answer = strip_think_tags(answer).strip()
    if not answer:
        raise HTTPException(status_code=502, detail="答えが空でした。もう一度聞くか、文章モデルを変えてください。")
    return {"answer": answer, "engine": engine, "sources": [{"slug": a.slug, "title": a.title} for a in articles]}


@router.get("/chat/engines")
async def get_chat_engines(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from ..llm_client import get_text_base_url

    return {
        "default": app_settings.get("assistant.chat_model"),
        "ollama_ready": bool(get_text_base_url()),
        "novelai_ready": bool(await help_route.novelai_key(authorization)),
    }
