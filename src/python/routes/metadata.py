from __future__ import annotations

import base64
import io
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException
from PIL import Image

from novelai._utils.nai_meta import extract_image_metadata

from ..nai_metadata import generation_settings
from ..models import (
    MetadataEraseRequest,
    MetadataEraseResponse,
    MetadataExtractRequest,
    MetadataExtractResponse,
    MetadataGenerationResponse,
)

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


def _decode_b64(b64: str) -> bytes:
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)


def _pil_to_b64(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


def _erase_metadata(img: Image.Image, target: str) -> Image.Image:
    if target in ("alpha", "both"):
        if "A" in img.getbands():
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

    if target in ("png_info", "both"):
        clean = img.copy()
        clean.info = {}
        return clean

    return img


@router.post("/extract", response_model=MetadataExtractResponse)
async def extract_image_metadata_endpoint(
    req: MetadataExtractRequest,
) -> MetadataExtractResponse:
    try:
        image_bytes = _decode_b64(req.image)
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        np_img: Any = np.asarray(img, dtype=np.uint8)
        metadata = extract_image_metadata(np_img)
        # get_fec を渡していないので dict が返る。タプルは FEC も要求したときの形。
        if not isinstance(metadata, dict):
            metadata = metadata[0]
        return MetadataExtractResponse(metadata=metadata)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/generation", response_model=MetadataGenerationResponse)
async def extract_generation_settings(req: MetadataExtractRequest) -> dict[str, Any]:
    """
    NovelAI で生成した画像から、画像生成ページにそのまま入れられる設定(プロンプト・キャラごとのプロンプト・
    モデル・サイズ・ステップ数・サンプラー・シード値など)を取り出す。NovelAI の埋め込みが無ければ 422。
    """
    try:
        image_bytes = _decode_b64(req.image)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"画像を読み取れませんでした: {exc}")
    settings = generation_settings(image_bytes)
    if settings is None:
        raise HTTPException(
            status_code=422,
            detail="この画像には NovelAI の生成設定が入っていません(NovelAI で生成した PNG / WebP をそのまま渡してください)。",
        )
    return settings


@router.post("/erase", response_model=MetadataEraseResponse)
async def erase_image_metadata_endpoint(
    req: MetadataEraseRequest,
) -> MetadataEraseResponse:
    try:
        image_bytes = _decode_b64(req.image)
        img = Image.open(io.BytesIO(image_bytes))
        result = _erase_metadata(img, req.target)
        return MetadataEraseResponse(image=_pil_to_b64(result))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
