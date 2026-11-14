"""
ヘルプのチャット(/api/help/chat)。記事だけを元に答える(help_chat.py)。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .. import app_settings, help_chat
from ..client import get_client

router = APIRouter(prefix="/api/help", tags=["help"])


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    current: str | None = Field(None, max_length=100)  # 今開いている記事
    engine: Literal["default", "excerpt", "auto", "ollama", "novelai"] = "default"


async def novelai_key(authorization: str | None) -> str | None:
    """NovelAI のトークン(画面のログイン。無ければ .env)。無ければ None。"""
    try:
        generator = get_client(authorization)
        client = await generator.__anext__()
    except HTTPException:
        return None
    try:
        return getattr(client, "api_key", None)
    finally:
        await generator.aclose()


async def ask_model(engine: str, messages: list[dict[str, str]], api_key: str | None) -> str:
    if engine == "novelai":
        from ..novelai_text_oa import stream_chat

        if not api_key:
            # 401 にすると画面がログアウトしてしまう(apiFetch)ので 422
            raise HTTPException(status_code=422, detail="NovelAI のトークンがありません(ログインし直すか、Ollama を選んでください)。")
        parts = [delta async for delta in stream_chat(api_key, messages, max_tokens=900, temperature=0.2)]
    else:
        from .llm import stream_llm_text

        parts = [delta async for delta in stream_llm_text(messages, max_tokens=900, temperature=0.1)]
    return "".join(parts)


@router.get("/chat/engines")
async def get_engines(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """画面の選択肢と、既定値(管理ページの設定)。"""
    from ..llm_client import get_text_base_url

    return {
        "default": app_settings.get("help.chat_model"),
        "ollama_ready": bool(get_text_base_url()),
        "novelai_ready": bool(await novelai_key(authorization)),
    }


@router.post("/chat")
async def post_chat(req: ChatRequest, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    articles = help_chat.load_articles()
    picked = help_chat.pick(req.question, articles, req.current)
    api_key = await novelai_key(authorization)
    engine = help_chat.choose_engine(req.engine, app_settings.get("help.chat_model"), bool(api_key))
    if engine == "excerpt":
        return {
            "answer": help_chat.excerpt(req.question, picked),
            "engine": engine,
            "sources": [{"slug": a.slug, "title": a.title} for a in picked],
        }
    messages = help_chat.build_messages(req.question, [t.model_dump() for t in req.history], picked)
    try:
        answer = await ask_model(engine, messages, api_key)
    except HTTPException as exc:
        if engine == "ollama" and exc.status_code == 503:
            raise HTTPException(status_code=503, detail="Ollama が設定されていません(.env の VLLM_BASE_URL)。NovelAI を選んでください。") from exc
        raise
    except Exception as exc:  # noqa: BLE001 文章モデルの失敗は画面に伝える
        label = "NovelAI" if engine == "novelai" else "Ollama"
        raise HTTPException(status_code=502, detail=f"{label} から答えをもらえませんでした: {exc}") from exc
    from .llm import strip_think_tags

    # 考えている途中(<think>…</think>)を出すモデルがある
    answer = help_chat.flag_unknown_names(strip_think_tags(answer).strip(), picked)
    if not answer:
        raise HTTPException(status_code=502, detail="答えが空でした。もう一度聞くか、文章モデルを変えてください。")
    return {
        "answer": answer,
        "engine": engine,
        "sources": [{"slug": a.slug, "title": a.title} for a in picked],
    }

