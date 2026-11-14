"""
読み上げ(/api/voice。voice.py)。VOICEVOX のエンジンへの中継。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from .. import voice

router = APIRouter(prefix="/api/voice", tags=["voice"])


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    speaker: int = Field(8, ge=0, le=10000)
    speed: float = Field(1.0, ge=0.5, le=2.0)


class SplitRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)


@router.get("/status")
async def get_status() -> dict[str, Any]:
    return await asyncio.to_thread(voice.status)


@router.get("/speakers")
async def get_speakers() -> dict[str, Any]:
    try:
        return {"speakers": await asyncio.to_thread(voice.speakers)}
    except voice.VoiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/split")
async def post_split(req: SplitRequest) -> dict[str, Any]:
    """読む文に分ける(Markdown の記号を外し、文ごとに)。画面はこれを1つずつ /tts に送る。"""
    return {"sentences": voice.split_sentences(req.text)}


@router.post("/tts")
async def post_tts(req: TtsRequest) -> Response:
    try:
        audio = await asyncio.to_thread(voice.synthesize, req.text, req.speaker, req.speed)
    except voice.VoiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})
