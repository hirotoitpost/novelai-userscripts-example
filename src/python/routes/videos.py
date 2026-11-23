"""
ギャラリーの絵から Grok で動画を作る API(/api/videos)。中身は grok_video.py。

  GET  /api/videos/config          使えるか・モデルと料金の目安・既定値
  POST /api/videos/generate        動画づくりを始める(裏で数分。job の ID を返す)
  GET  /api/videos/jobs/{job_id}   進み具合(done なら video_key にギャラリーのキー)

XAI_API_KEY は運営者のキーなので、multi では X 投稿と同じく、キーの持ち主の利用者だけが使える。
成人向けの絵からは作らない(xAI の決まりと、未成年に見える絵の安全のため)。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import grok_video, x_posts
from ..db import user_files


def require_owner() -> None:
    if not x_posts.is_owner():
        raise HTTPException(status_code=403, detail="動画づくりは、xAI のキーを設定した利用者だけが使えます。")


router = APIRouter(prefix="/api/videos", tags=["videos"], dependencies=[Depends(require_owner)])


class GenerateBody(BaseModel):
    key: str = Field(min_length=1)
    prompt: str = Field("", max_length=2000)
    keep_style: bool = True
    model: str = grok_video.DEFAULT_MODEL
    seconds: int = Field(6, ge=grok_video.MIN_SECONDS, le=grok_video.MAX_SECONDS)
    resolution: str = "720p"
    audio: bool = True


@router.get("/config")
def get_config() -> dict[str, Any]:
    return {
        "configured": grok_video.configured(),
        "models": [{"id": m, "usd_per_second": p} for m, p in grok_video.MODELS.items()],
        "default_model": grok_video.DEFAULT_MODEL,
        "resolutions": list(grok_video.RESOLUTIONS),
        "min_seconds": grok_video.MIN_SECONDS,
        "max_seconds": grok_video.MAX_SECONDS,
        "keep_style": grok_video.KEEP_STYLE,
    }


@router.post("/generate")
def post_generate(body: GenerateBody) -> dict[str, Any]:
    from .library import find_image

    if not grok_video.configured():
        raise HTTPException(status_code=409, detail="XAI_API_KEY が .env にありません。")
    item = find_image(body.key)
    if item is None or item.source == "video":
        raise HTTPException(status_code=404, detail="動画にする絵が見つかりません。")
    if item.adult:
        raise HTTPException(status_code=422, detail="成人向けの絵からは動画を作れません。")
    prompt = " ".join(p for p in (body.prompt.strip(), grok_video.KEEP_STYLE if body.keep_style else "") if p)
    if not prompt:
        raise HTTPException(status_code=422, detail="どう動かすかを書いてください。")
    label = item.label or (f"{item.story_title} {item.index + 1}" if item.story_title and item.index is not None else "")
    try:
        job = grok_video.start(
            source_key=item.key, image=user_files() / item.path, prompt=prompt, model=body.model,
            seconds=body.seconds, resolution=body.resolution, audio=body.audio,
            label=f"{label or '動画'}(動画)", adult=False,
        )
    except grok_video.VideoJobError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**job.public(), "estimate_usd": grok_video.estimate(body.model, body.seconds)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = grok_video.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="その動画づくりは見つかりません(バックエンドを再起動すると消えます)。")
    return job.public()
