"""チャットのスクリーンショットから会話を読み取る(OCR)。処理本体は chat_ocr.py。"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..chat_ocr import ChatBlock, blocks_to_text, merge_pages, read_screenshot

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

# スマホのスクショは 1〜3 MB ほど。誤って巨大なファイルを送ったときに備える
_MAX_BYTES = 20 * 1024 * 1024


class OcrBlock(BaseModel):
    kind: Literal["character", "narration", "user_action", "user_speech"]
    text: str
    cut_top: bool = False
    cut_bottom: bool = False


class OcrMergeRequest(BaseModel):
    """撮った順のスクショごとの読み取り結果。"""

    pages: list[list[OcrBlock]]


@router.post("/chat-screenshot")
async def read_chat_screenshot(file: UploadFile) -> dict[str, Any]:
    """スクショ1枚を読み、上から順の発言の塊を返す。1枚 2 秒ほどかかる。"""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="画像が空です")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="画像が大きすぎます(20MBまで)")
    try:
        blocks = await asyncio.to_thread(read_screenshot, data)
    except Exception as exc:  # noqa: BLE001 壊れた画像などはそのまま伝える
        raise HTTPException(status_code=400, detail=f"画像を読み取れませんでした: {exc}") from exc
    return {"blocks": [b.to_dict() for b in blocks]}


@router.post("/chat-merge")
async def merge_chat_pages(req: OcrMergeRequest) -> dict[str, Any]:
    """複数枚の読み取り結果から重複を除いてつなぎ、物語の本文にする。"""
    pages = [[ChatBlock(**b.model_dump()) for b in page] for page in req.pages]
    blocks = merge_pages(pages)
    return {"blocks": [b.to_dict() for b in blocks], "text": blocks_to_text(blocks)}
