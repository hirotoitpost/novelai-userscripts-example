"""
物語エディタ: NovelAI の文章生成モデルと対話しながら物語を書き、書き上げたら
「物語 → 漫画」(stories)へ送る。

公式エディタと同じく、本文の末尾から続きを生成して書き足す使い方。本文は自由に
書き換えられ、生成の取り消し・やり直しは画面側で行う(サーバーは続きを返すだけ)。
"""

from __future__ import annotations

from typing import Annotated, Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from novelai import AsyncNovelAI

from ..client import get_client
from ..db import (
    create_draft,
    create_story,
    delete_draft,
    duplicate_draft,
    get_connection,
    get_draft,
    get_story,
    list_drafts,
    set_story_memory,
    set_story_series,
    set_story_title,
    update_draft,
)
from ..models import (
    WriterDraft,
    WriterDraftSummary,
    WriterDraftUpdate,
    WriterDuplicateRequest,
    WriterGenerateRequest,
    WriterModel,
    WriterToStoryRequest,
)
from ..novelai_text_oa import DEFAULT_TEXT_MODEL, TEXT_MODELS, build_prompt, stream_completion
from .llm import sse_event

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/writer", tags=["writer"])

_MODEL_IDS = {m.id for m in TEXT_MODELS}


@router.get("/models", response_model=list[WriterModel])
async def get_models() -> list[dict[str, Any]]:
    return [{"id": m.id, "label": m.label, "note": m.note, "default": m.id == DEFAULT_TEXT_MODEL} for m in TEXT_MODELS]


@router.get("/drafts", response_model=list[WriterDraftSummary])
async def get_drafts() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_drafts(conn)
    finally:
        conn.close()


@router.post("/drafts", response_model=WriterDraft)
async def post_draft() -> dict[str, Any]:
    conn = get_connection()
    try:
        return create_draft(conn)
    finally:
        conn.close()


def _require_draft(draft_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        draft = get_draft(conn, draft_id)
    finally:
        conn.close()
    if draft is None:
        raise HTTPException(status_code=404, detail="下書きが見つかりません。")
    return draft


@router.get("/drafts/{draft_id}", response_model=WriterDraft)
async def get_draft_route(draft_id: int) -> dict[str, Any]:
    return _require_draft(draft_id)


@router.put("/drafts/{draft_id}", response_model=WriterDraft)
async def put_draft(draft_id: int, req: WriterDraftUpdate) -> dict[str, Any]:
    _require_draft(draft_id)
    fields = req.model_dump(exclude_none=True)
    conn = get_connection()
    try:
        return update_draft(conn, draft_id, fields)  # type: ignore[return-value]
    finally:
        conn.close()


@router.post("/drafts/{draft_id}/duplicate", response_model=WriterDraft)
async def post_duplicate(draft_id: int, req: WriterDuplicateRequest) -> dict[str, Any]:
    """別名で保存: 今の下書きを複製する(加筆前を残しておき、複製の方に書き足す使い方)。"""
    source = _require_draft(draft_id)
    title = (req.title or "").strip() or f"{source['title'] or '(無題)'} (コピー)"
    conn = get_connection()
    try:
        return duplicate_draft(conn, draft_id, title)  # type: ignore[return-value]
    finally:
        conn.close()


@router.delete("/drafts/{draft_id}", status_code=204)
async def delete_draft_route(draft_id: int) -> None:
    conn = get_connection()
    try:
        delete_draft(conn, draft_id)
    finally:
        conn.close()


@router.post("/generate")
async def generate(req: WriterGenerateRequest, client: ClientDep, request: Request) -> StreamingResponse:
    """
    本文の続きを SSE で返す(event: delta / done / error)。画面は受け取った断片を本文の
    末尾に足していく。保存前の入力でも生成できるよう、本文はリクエストに含めて送る。
    """
    model = req.settings.model if req.settings.model in _MODEL_IDS else DEFAULT_TEXT_MODEL
    prompt = build_prompt(req.memory, req.text, req.author_note)
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="書き出しを入力してください。")
    # get_client はリクエスト終了時にクライアントを閉じるので、トークンだけ持ち出す
    api_key = client.api_key

    async def events() -> AsyncGenerator[str, None]:
        produced = 0
        try:
            async for delta in stream_completion(
                api_key,
                prompt,
                model=model,
                max_tokens=req.settings.max_tokens,
                temperature=req.settings.temperature,
                top_p=req.settings.top_p,
            ):
                if await request.is_disconnected():
                    return
                produced += len(delta)
                yield sse_event("delta", {"text": delta})
            yield sse_event("done", {"model": model, "chars": produced})
        except Exception as exc:  # noqa: BLE001 生成の失敗は画面に伝える
            yield sse_event("error", {"detail": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _premise_label(title: str, text: str) -> str:
    """物語一覧に出す見出し。取り込み(/api/story/import)と同じく先頭の一文を使う。"""
    head = title.strip() or text.strip().split("\n", 1)[0][:40]
    return f"[エディタ] {head}"


def _attach_to_series(conn: Any, story_id: int, draft: dict[str, Any], previous: int | None) -> None:
    """
    シリーズの次の巻として書いた下書きの物語を、その巻にする。本文を直して作り直した場合は、
    同じ下書きから前に作った物語が巻の席にいるので、それを単巻に戻して入れ替える(絵は前の物語に残る)。
    """
    holder = conn.execute(
        "SELECT id FROM stories WHERE series_id = ? AND volume_no = ?", (draft["series_id"], draft["volume_no"])
    ).fetchone()
    if holder is not None:
        if holder["id"] != previous:
            raise HTTPException(
                status_code=409,
                detail=f"このシリーズの{draft['volume_no']}巻は既に別の物語です。",
            )
        set_story_series(conn, holder["id"], None, None)
    set_story_series(conn, story_id, draft["series_id"], draft["volume_no"])
    if draft["title"].strip():
        set_story_title(conn, story_id, draft["title"].strip())


@router.post("/drafts/{draft_id}/to-story")
async def draft_to_story(draft_id: int, req: WriterToStoryRequest) -> dict[str, Any]:
    """
    書き上げた本文を「物語 → 漫画」へ送る。取り込み(/api/story/import)と同じく本文を
    そのまま raw_text として保存するので、物語ページでシーン分割から先に進められる。

    この下書きから作った物語が既にあり、本文が変わっていなければ、その物語を開くだけにする
    (押すたびに新しい物語ができると、生成済みのコマの絵が別の物語に取り残される)。
    本文が変わっていれば新しい物語を作る(前の物語と絵はそのまま残る)。
    """
    draft = _require_draft(draft_id)
    if not draft["text"].strip():
        raise HTTPException(status_code=400, detail="本文がありません。")
    conn = get_connection()
    try:
        if draft.get("story_id"):
            existing = get_story(conn, draft["story_id"])
            if existing is not None and (existing.get("raw_text") or "") == draft["text"]:
                return {"story_id": existing["id"], "reused": True}
        story = create_story(
            conn, _premise_label(draft["title"], draft["text"]), 4, req.panels_per_page, raw_text=draft["text"]
        )
        update_draft(conn, draft_id, {"story_id": story["id"]})
        # メモリ(登場人物の容姿など)は本文に書かれていないことが多いので、登場人物の抽出用に渡す
        set_story_memory(conn, story["id"], draft["memory"])
        if draft.get("series_id") and draft.get("volume_no"):
            _attach_to_series(conn, story["id"], draft, previous=draft.get("story_id"))
    finally:
        conn.close()
    return {"story_id": story["id"], "reused": False}
