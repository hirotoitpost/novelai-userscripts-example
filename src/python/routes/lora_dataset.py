from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from novelai import AsyncNovelAI

from ..client import get_client
from ..lora_dataset import GenConfig, build_shots, run_dataset
from ..models import LoraDatasetRequest

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/lora-dataset", tags=["lora-dataset"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SAFE_NAME_RE = re.compile(r"[^\w\-]+")


def _safe_name(name: str) -> str:
    return _SAFE_NAME_RE.sub("_", name.strip()) or "character"


@router.post("/generate")
async def generate_lora_dataset(req: LoraDatasetRequest, client: ClientDep) -> StreamingResponse:
    output_root = _PROJECT_ROOT / "outputs" / _safe_name(req.root_name) / _safe_name(req.character_id)

    shots = build_shots(req.trigger_word, req.base_tags, req.extra_tags, req.outfit_tag)
    cfg = GenConfig(
        output_root=output_root,
        model=req.model,
        steps=req.steps,
        scale=req.scale,
        sampler=req.sampler,
        noise_schedule=req.noise_schedule,
        cfg_rescale=req.cfg_rescale,
        negative_prompt=req.negative_prompt,
    )

    async def event_gen():
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        succeeded = failed = 0
        try:
            async for event in run_dataset(client, shots, cfg):
                if event["status"] == "ok":
                    succeeded += 1
                else:
                    failed += 1
                yield _sse("progress", event)
        except Exception as exc:
            yield _sse("error", {"detail": str(exc)})
            return

        yield _sse("complete", {
            "total": succeeded + failed,
            "succeeded": succeeded,
            "failed": failed,
            "output_path": str(output_root),
        })

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
