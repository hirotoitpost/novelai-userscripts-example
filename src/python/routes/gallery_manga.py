"""
ギャラリーで選んだ画像から漫画を作る(1枚=1コマ)。画像のプロンプトから似た絵を生成し直し、指定のジャンルに
合うセリフ・描き文字を付けて、物語(漫画)にする。中身は gallery_manga.py。

時間がかかるので、処理はリクエストと切り離して進め、GET /jobs/{id} で進み具合を返す。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from novelai import AsyncNovelAI

from ..client import get_client
from ..db import get_connection, list_story_scenes
from ..gallery_manga import MAX_IMAGES, panel_scene, settings_tags, write_script
from ..models import (
    GalleryMangaRequest,
    MangaV2ComposeRequest,
    MangaV2PanelsRequest,
    ScriptedStoryRequest,
)
from ..nai_metadata import generation_settings

router = APIRouter(prefix="/api/gallery-manga", tags=["gallery-manga"])
ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

# 1ページのコマ数(テンプレート grid4)
_PANELS_PER_PAGE = 4
_DEFAULT_GENRE = "日常コメディ"


@dataclass
class _GalleryJob:
    status: str = "running"
    message: str = "開始しました"
    progress: int = 0
    total: int = 0
    detail: str | None = None
    story_id: int | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


_jobs: dict[str, _GalleryJob] = {}


def _job_response(job_id: str, job: _GalleryJob) -> dict[str, Any]:
    return {"id": job_id, **{k: getattr(job, k) for k in ("status", "message", "progress", "total", "detail", "story_id")}}


def _source_tags(keys: list[str]) -> list[str]:
    """選んだ画像ごとのコマのプロンプト(選んだ順)。見つからない画像は飛ばす。"""
    from .library import _gallery_images, _served_path

    by_key = {item.key: item for item in _gallery_images()}
    sources = []
    for key in dict.fromkeys(keys):
        item = by_key.get(key)
        if item is None:
            continue
        try:
            settings = generation_settings(_served_path(item.path).read_bytes())
        except (HTTPException, OSError):
            settings = None
        sources.append(settings_tags(settings, item.prompt))
    return sources


async def _make(req: GalleryMangaRequest, api_key: str, job: _GalleryJob) -> None:
    from .manga_v2 import _run_panels, compose
    from .story import create_scripted_story

    try:
        job.message = "選んだ画像のプロンプトを読み取っています"
        sources = await asyncio.to_thread(_source_tags, req.image_keys)
        if not sources:
            raise RuntimeError("選んだ画像が見つかりません。")
        # プロンプトの無い画像(拡大しただけの画像など)は、何を描くか分からないので外す
        skipped = len([s for s in sources if not s])
        sources = [s for s in sources if s]
        if not sources:
            raise RuntimeError("選んだ画像にプロンプトがありません(生成設定の入った画像を選んでください)。")
        genre = req.genre.strip() or _DEFAULT_GENRE
        job.message = f"「{genre}」に合う話とセリフを考えています({len(sources)}コマ)"
        script, written = await write_script(api_key, genre, req.theme, sources)
        title = (req.title.strip() or script["title"] or f"ギャラリーから作った漫画({genre})")[:200]
        story = await create_scripted_story(
            ScriptedStoryRequest.model_validate(
                {
                    "title": title,
                    "panels_per_page": _PANELS_PER_PAGE,
                    "scenes": [
                        panel_scene(panel, f"{i // _PANELS_PER_PAGE + 1}ページ{i % _PANELS_PER_PAGE + 1}")
                        for i, panel in enumerate(script["panels"])
                    ],
                }
            )
        )
        job.story_id = story.id
        note = "" if written else "(セリフは考えられませんでした。物語のページで足せます)"
        if skipped:
            note += f"(プロンプトの無い画像 {skipped} 枚は外しました)"
        if not req.make_images:
            job.message = f"台本ができました: {title}({len(sources)}コマ。コマの絵はまだ生成していません){note}"
            job.status = "done"
            return
        conn = get_connection()
        try:
            targets = list_story_scenes(conn, story.id)
        finally:
            conn.close()
        await _run_panels(job, story.id, MangaV2PanelsRequest(template="grid4", color=req.color), api_key, targets)
        job.message = "ページに合成しています"
        result = await asyncio.to_thread(compose, story.id, MangaV2ComposeRequest(template="grid4"))
        job.message = f"漫画ができました: {title}({len(result['pages'])}ページ・{len(targets)}コマ){note}"
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status = "error"
        job.detail = str(exc)


@router.post("")
async def post_gallery_manga(req: GalleryMangaRequest, client: ClientDep) -> dict[str, Any]:
    """
    ギャラリーで選んだ画像(image_keys。選んだ順に1枚=1コマ)から漫画を作る。ジョブを返すので、進み具合は
    GET /jobs/{id} で見る。make_images を False にすると台本(物語)を作るところまでで、コマの絵は生成しない。
    """
    keys = list(dict.fromkeys(req.image_keys))
    if len(keys) > MAX_IMAGES:
        raise HTTPException(status_code=422, detail=f"一度に漫画にできるのは {MAX_IMAGES} 枚までです。")
    job_id = uuid4().hex[:12]
    job = _GalleryJob(total=len(keys))
    _jobs[job_id] = job
    job.task = asyncio.create_task(_make(req, client.api_key, job))
    return _job_response(job_id, job)


@router.get("/jobs/{job_id}")
async def get_gallery_manga_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_response(job_id, job)
