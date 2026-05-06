from __future__ import annotations

import base64
import io
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException
from PIL import Image

from novelai._utils.nai_meta import extract_image_metadata

from ..models import (
    MetadataEraseRequest,
    MetadataEraseResponse,
    MetadataExtractRequest,
    MetadataExtractResponse,
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
        return MetadataExtractResponse(metadata=metadata)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
        raise HTTPException(status_code=400, detail=str(exc))
