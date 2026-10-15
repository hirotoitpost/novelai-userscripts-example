"""
うごイラの API(/api/ugoira)。中身は ugoira.py。

  GET    /motions                 動き(まばたき など)の一覧
  POST   /motions                 動きを足す
  PUT    /motions/{id}            動きを直す
  DELETE /motions/{id}            動きを消す
  POST   /partial                 A. 部分だけ描き直してうごイラを作る(処理を切り離す。GET /jobs/{id} で進み具合)
  GET    /jobs/{id}               進み具合
  GET    /                        作ったうごイラの一覧
  GET    /{id}                    1つ
  PUT    /{id}/frames             コマの並び・表示時間を変える
  GET    /{id}/export             書き出す(format=zip|gif|webp、max_side=長い辺)
  DELETE /{id}                    ごみ箱へ(30日のあいだは元に戻せる)
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from novelai import AsyncNovelAI
from pydantic import BaseModel, Field

from .. import ugoira
from ..client import get_client

router = APIRouter(prefix="/api/ugoira", tags=["ugoira"])
ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]


class MotionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    region: Literal["eyes", "mouth", "face", "hair"]
    frames: list[str] = Field(min_length=2, max_length=24)
    delays: list[int] = Field(min_length=2, max_length=24)


class PartialRequest(BaseModel):
    key: str = Field(min_length=1, description="ギャラリーの画像のキー")
    motion_id: int
    # インペイントの強さ(弱いほど元の絵に近い)
    strength: float = Field(1.0, ge=0.3, le=1.0)
    seed: int | None = Field(None, ge=0, le=4294967295)


class FramesRequest(BaseModel):
    frames: list[dict[str, Any]] = Field(min_length=1, max_length=200)


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


# ---- 動き ----


@router.get("/motions")
async def get_motions() -> dict[str, Any]:
    return {"motions": ugoira.list_motions(), "regions": ugoira.REGION_LABELS}


@router.post("/motions")
async def post_motion(req: MotionRequest) -> dict[str, Any]:
    try:
        return ugoira.save_motion(None, req.name, req.region, req.frames, req.delays)
    except ugoira.UgoiraError as exc:
        raise _error(exc)


@router.put("/motions/{motion_id}")
async def put_motion(motion_id: int, req: MotionRequest) -> dict[str, Any]:
    if not any(m["id"] == motion_id for m in ugoira.list_motions()):
        raise HTTPException(status_code=404, detail="その動きはありません。")
    try:
        return ugoira.save_motion(motion_id, req.name, req.region, req.frames, req.delays)
    except ugoira.UgoiraError as exc:
        raise _error(exc)


@router.delete("/motions/{motion_id}", status_code=204)
async def remove_motion(motion_id: int) -> None:
    ugoira.delete_motion(motion_id)


# ---- 作る(処理を切り離す) ----


@dataclass
class _Job:
    status: str = "running"
    message: str = "開始しました"
    progress: int = 0
    total: int = 0
    project_id: int | None = None
    task: Any = field(default=None, repr=False)


_jobs: dict[str, _Job] = {}


def _job_response(job_id: str, job: _Job) -> dict[str, Any]:
    return {"id": job_id, **{k: getattr(job, k) for k in ("status", "message", "progress", "total", "project_id")}}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="その処理はありません(バックエンドを起動し直すと消えます)。")
    return _job_response(job_id, job)


def _png_b64(image: Any) -> str:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


@router.post("/partial")
async def start_partial(req: PartialRequest, client: ClientDep) -> dict[str, Any]:
    from .library import _gallery_images

    item = next((i for i in _gallery_images() if i.key == req.key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません。")
    motion = next((m for m in ugoira.list_motions() if m["id"] == req.motion_id), None)
    if motion is None:
        raise HTTPException(status_code=404, detail="その動きはありません。")
    job_id = uuid4().hex
    job = _Job(total=len(motion["frames"]))
    _jobs[job_id] = job
    job.task = asyncio.create_task(_run_partial(job, req, item, motion, getattr(client, "api_key", None)))
    return _job_response(job_id, job)


async def _run_partial(job: _Job, req: PartialRequest, item: Any, motion: dict[str, Any], api_key: str | None) -> None:
    from novelai.types import GenerateImageParams
    from PIL import Image

    from ..manga_v2.detect import detect_heads_in_image
    from ..models import GenerateImageRequest
    from ..nai_metadata import generation_settings
    from ..whole_enhance import whole_characters
    from .image import _build_kwargs, _guard_image, _job_client
    from .library import _served_path

    try:
        data = _served_path(item.path).read_bytes()
        with Image.open(io.BytesIO(data)) as opened:
            original = opened.convert("RGB")
        job.message = "画像を確かめています"
        # 未成年キャラの安全対策(画質向上と同じ判定): 性的な画像に未成年を思わせるタグがあれば作らない
        await asyncio.to_thread(_guard_image, data, item.prompt, original, item.key)
        size = ugoira.fit_size(original.size)
        source = original.resize(size, Image.Resampling.LANCZOS) if size != original.size else original
        head = ugoira.pick_head(await asyncio.to_thread(detect_heads_in_image, source))
        mask = ugoira.region_mask(head, motion["region"], source.size)
        settings = generation_settings(data) or {}
        base_prompt = settings.get("prompt") or item.prompt or ""
        seed = next((s for s in (req.seed, settings.get("seed"), item.seed) if s is not None), 1)
        project_id = ugoira.create_project(f"{motion['name']}({item.label or item.key})", item.key, "partial")
        job.project_id = project_id
        frames: list[dict[str, Any]] = []
        client = None
        try:
            for index, (tags, delay) in enumerate(zip(motion["frames"], motion["delays"])):
                job.message = f"コマ {index + 1}/{len(motion['frames'])} を描いています"
                if not tags.strip():
                    image = source
                else:
                    fields: dict[str, Any] = {
                        "prompt": ugoira.frame_prompt(base_prompt, motion["region"], tags),
                        "quality": not settings,
                        "uc_preset": "none" if settings else "light",
                        "negative_prompt": ", ".join(
                            p for p in (settings.get("negative_prompt"), ugoira.frame_negative(tags)) if p
                        ) or None,
                        "size": list(source.size),
                        "steps": min(settings.get("steps") or 28, 28),
                        "seed": seed,
                        "inpaint": {"image": _png_b64(source), "mask": _png_b64(mask.convert("RGB")), "strength": req.strength, "seed": seed},
                        "characters": whole_characters(settings) or None,
                    }
                    for key in ("model", "scale", "sampler", "noise_schedule"):
                        if settings.get(key):
                            fields[key] = settings[key]
                    try:
                        gen = GenerateImageRequest(**fields)
                    except ValueError:
                        gen = GenerateImageRequest(**{k: v for k, v in fields.items() if k not in ("model", "sampler", "noise_schedule")})
                    if client is None:
                        client = _job_client(api_key)
                    generated = await client.image.generate(GenerateImageParams(**_build_kwargs(gen)))
                    image = ugoira.composite(source, generated[0], mask)
                frames.append({"path": ugoira.save_frame(project_id, index, image), "delay": int(delay), "tags": tags})
                ugoira._store_frames(project_id, frames)
                job.progress = index + 1
        finally:
            if client is not None:
                await client.close()
        job.message = f"できました({len(frames)}コマ)"
        job.status = "done"
    except HTTPException as exc:
        job.status, job.message = "error", str(exc.detail)
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status, job.message = "error", str(exc)


# ---- 作ったもの ----


@router.get("")
async def get_projects() -> list[dict[str, Any]]:
    return ugoira.list_projects()


@router.get("/{project_id}")
async def get_project(project_id: int) -> dict[str, Any]:
    project = ugoira.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="うごイラが見つかりません。")
    return project


@router.put("/{project_id}/frames")
async def put_frames(project_id: int, req: FramesRequest) -> dict[str, Any]:
    try:
        return ugoira.set_frames(project_id, req.frames)
    except ugoira.UgoiraError as exc:
        raise _error(exc)


@router.get("/{project_id}/export")
async def export_project(project_id: int, format: Literal["zip", "gif", "webp"] = "zip", max_side: int | None = None) -> Response:  # noqa: A002
    project = ugoira.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="うごイラが見つかりません。")
    if max_side is not None and not 64 <= max_side <= 4096:
        raise HTTPException(status_code=422, detail="長い辺は 64〜4096 にしてください。")
    try:
        data, ext, mime = await asyncio.to_thread(ugoira.export, project, format, max_side)
    except ugoira.UgoiraError as exc:
        raise _error(exc)
    filename = f"ugoira_{project_id}.{ext}"
    return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int) -> None:
    """ごみ箱へ(trash.py。コマの画像ごと。30日のあいだは元に戻せる)。"""
    from .. import trash
    from ..db import get_connection

    conn = get_connection()
    try:
        rows = trash.rows(conn, "SELECT * FROM ugoira_projects WHERE id = ?", (project_id,))
        if not rows:
            raise HTTPException(status_code=404, detail="うごイラが見つかりません。")
        conn.execute("DELETE FROM ugoira_projects WHERE id = ?", (project_id,))
        conn.commit()
    finally:
        conn.close()
    files = [f["path"] for f in json.loads(rows[0]["frames"])]
    trash.put("ugoira", rows[0]["name"], {"rows": {"ugoira_projects": rows}}, files)
    # コマを預けたあとの空のフォルダは消す(戻すときは作り直される)
    try:
        ugoira.frame_dir(project_id).rmdir()
    except OSError:
        pass
