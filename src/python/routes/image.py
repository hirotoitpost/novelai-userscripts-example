from __future__ import annotations

import base64
import io
import json
from typing import Any

from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from novelai import (
    AsyncNovelAI,
    AuthenticationError,
    InvalidRequestError,
    NetworkError,
    RateLimitError,
    ServerError,
)
from novelai.types import (
    Character,
    CharacterReference,
    ControlNet,
    ControlNetImage,
    GenerateImageParams,
    GenerateImageStreamParams,
    I2iParams,
    InpaintParams,
)

from ..client import get_client

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]
from ..models import (
    AnlasEstimateRequest,
    AnlasEstimateResponse,
    GenerateImageRequest,
    GenerateImageResponse,
)

router = APIRouter(prefix="/api/image", tags=["image"])


def _decode_b64(b64: str) -> bytes:
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)


def _pil_to_b64(images: list, fmt: str = "PNG") -> list[str]:
    result = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format=fmt.upper())
        result.append(base64.b64encode(buf.getvalue()).decode())
    return result


def _build_kwargs(req: GenerateImageRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "prompt": req.prompt,
        "model": req.model,
        "quality": req.quality,
        "uc_preset": req.uc_preset,
        "steps": req.steps,
        "scale": req.scale,
        "sampler": req.sampler,
        "noise_schedule": req.noise_schedule,
        "n_samples": req.n_samples,
        "cfg_rescale": req.cfg_rescale,
        "variety_boost": req.variety_boost,
        "size": tuple(req.size) if isinstance(req.size, list) else req.size,
    }

    if req.negative_prompt is not None:
        kwargs["negative_prompt"] = req.negative_prompt
    if req.seed is not None:
        kwargs["seed"] = req.seed
    if req.image_format is not None:
        kwargs["image_format"] = req.image_format

    if req.i2i:
        i2i_kw: dict[str, Any] = {
            "image": _decode_b64(req.i2i.image),
            "strength": req.i2i.strength,
            "noise": req.i2i.noise,
        }
        if req.i2i.seed is not None:
            i2i_kw["seed"] = req.i2i.seed
        kwargs["i2i"] = I2iParams(**i2i_kw)

    if req.inpaint:
        ip_kw: dict[str, Any] = {
            "image": _decode_b64(req.inpaint.image),
            "mask": _decode_b64(req.inpaint.mask),
            "strength": req.inpaint.strength,
        }
        if req.inpaint.seed is not None:
            ip_kw["seed"] = req.inpaint.seed
        kwargs["inpaint"] = InpaintParams(**ip_kw)

    if req.controlnet:
        kwargs["controlnet"] = ControlNet(
            images=[
                ControlNetImage(
                    image=_decode_b64(ci.image),
                    info_extracted=ci.info_extracted,
                    strength=ci.strength,
                    controlnet_model=ci.controlnet_model,
                )
                for ci in req.controlnet.images
            ],
            strength=req.controlnet.strength,
        )

    if req.character_references:
        kwargs["character_references"] = [
            CharacterReference(
                image=_decode_b64(cr.image),
                type=cr.type,
                fidelity=cr.fidelity,
                strength=cr.strength,
            )
            for cr in req.character_references
        ]

    if req.characters:
        kwargs["characters"] = [
            Character(
                prompt=c.prompt,
                negative_prompt=c.negative_prompt,
                position=tuple(c.position) if isinstance(c.position, list) else c.position,
                enabled=c.enabled,
            )
            for c in req.characters
        ]

    return kwargs


def _http_status(exc: Exception) -> int:
    if isinstance(exc, AuthenticationError):
        return 401
    if isinstance(exc, InvalidRequestError):
        return 422
    if isinstance(exc, RateLimitError):
        return 429
    if isinstance(exc, ServerError):
        return 502
    if isinstance(exc, NetworkError):
        return 503
    return 500


@router.post("/generate", response_model=GenerateImageResponse)
async def generate_image(
    req: GenerateImageRequest,
    client: ClientDep,
) -> GenerateImageResponse:
    try:
        params = GenerateImageParams(**_build_kwargs(req))
        images = await client.image.generate(params)
        fmt = req.image_format or "png"
        return GenerateImageResponse(images=_pil_to_b64(images, fmt), format=fmt)
    except Exception as exc:
        raise HTTPException(status_code=_http_status(exc), detail=str(exc))


@router.post("/generate/stream")
async def generate_image_stream(
    req: GenerateImageRequest,
    client: ClientDep,
) -> StreamingResponse:
    async def event_gen():
        try:
            params = GenerateImageStreamParams(**_build_kwargs(req))
            async for chunk in client.image.generate_stream(params):
                payload = json.dumps(
                    {
                        "event_type": chunk.event_type,
                        "samp_ix": chunk.samp_ix,
                        "step_ix": chunk.step_ix,
                        "gen_id": chunk.gen_id,
                        "sigma": chunk.sigma,
                        "image": chunk.image,
                    }
                )
                yield f"event: {chunk.event_type}\ndata: {payload}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/anlas", response_model=AnlasEstimateResponse)
async def estimate_anlas(req: AnlasEstimateRequest) -> AnlasEstimateResponse:
    try:
        params = GenerateImageParams(**_build_kwargs(req.params))
        est = params.calculate_anlas(is_opus=req.is_opus)
        return AnlasEstimateResponse(**est.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
