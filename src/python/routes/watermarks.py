"""
見えない透かしの台帳と照合の API(/api/watermarks)。中身は watermark_registry.py。

  GET  /api/watermarks           台帳(外に出した画像。新しい順)
  POST /api/watermarks/verify    画像(multipart の file)が、自分の出したものかを照合する
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile
from PIL import UnidentifiedImageError

from .. import watermark_registry

router = APIRouter(prefix="/api/watermarks", tags=["watermarks"])

_MAX_BYTES = 30 * 1024 * 1024


@router.get("")
def get_marks(limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)) -> list[dict[str, Any]]:
    return watermark_registry.list_marks(limit, offset)


@router.post("/verify")
async def verify_image(file: UploadFile) -> dict[str, Any]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="画像が空です。")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="画像が大きすぎます(30MB まで)。")
    try:
        return watermark_registry.verify(data)
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail=f"画像として読めませんでした: {exc}") from exc
