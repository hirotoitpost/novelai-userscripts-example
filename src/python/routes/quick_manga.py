"""
おまかせ漫画(中身は quick_manga.py): テーマ1行から、登場人物 → 大枠シナリオ → 台本 → 物語 → コマの生成と合成まで
1つの処理で作る。題材の候補も出す。時間がかかるので、処理はリクエストと切り離して進め、GET /jobs/{id} で進み具合を返す。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from novelai import AsyncNovelAI

from ..client import get_client
from ..db import (
    get_character,
    get_connection,
    list_characters,
    list_story_scenes,
    save_character,
    set_manga_v2_page_layouts,
    update_character_sheet,
)
from ..manga_v2.layout import dynamic_layout
from ..models import QuickMangaRequest
from ..quick_manga import propose_cast, suggest_themes, unique_name

router = APIRouter(prefix="/api/quick-manga", tags=["quick-manga"])
ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

# 新しく作るキャラの画風(キャラ別データセットとそろえる)
_STYLE = "best quality, very aesthetic"


@dataclass
class _QuickJob:
    status: str = "running"
    message: str = "開始しました"
    progress: int = 0
    total: int = 0
    detail: str | None = None
    story_id: int | None = None
    # 作ったキャラ(新しく作ったときだけ)と、選んだ大枠シナリオ
    characters: list[dict[str, Any]] = field(default_factory=list)
    title: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


_jobs: dict[str, _QuickJob] = {}


def _job_response(job_id: str, job: _QuickJob) -> dict[str, Any]:
    keys = ("status", "message", "progress", "total", "detail", "story_id", "characters", "title")
    return {"id": job_id, **{k: getattr(job, k) for k in keys}}


async def _prepare_characters(req: QuickMangaRequest, api_key: str, job: _QuickJob) -> list[dict[str, Any]]:
    """使うキャラ: 選んだもの。無ければ、テーマに合う登場人物を考えて、キャラシートを作る(参照画像も)。"""
    from ..cast import auto_reference

    conn = get_connection()
    try:
        if req.character_ids:
            chosen = [get_character(conn, i) for i in req.character_ids]
            if any(c is None for c in chosen):
                raise RuntimeError("選んだキャラが見つかりません(消された可能性があります)。")
            characters = [c for c in chosen if c]
        else:
            characters = []
    finally:
        conn.close()
    if not characters:
        job.message = f"テーマに合う登場人物を考えています({req.cast_count}人)"
        proposals = await propose_cast(api_key, req.theme, req.genre, req.cast_count)
        conn = get_connection()
        try:
            taken = {c["name"] for c in list_characters(conn)}
            for item in proposals:
                name = unique_name(item["name"], taken)
                taken.add(name)
                created = save_character(conn, name, item["appearance_tags"], item["notes"] or None)
                updated = update_character_sheet(
                    conn, created["id"], {"body_tags": item["body_tags"], "outfit_tags": item["outfit_tags"], "style_tags": _STYLE}
                )
                characters.append(updated or created)
        finally:
            conn.close()
        job.characters = [{"id": c["id"], "name": c["name"]} for c in characters]
    if req.references:
        missing = [c for c in characters if not c.get("reference_image_path") and (c.get("appearance_tags") or "").strip()]
        for index, character in enumerate(missing, start=1):
            job.message = f"参照画像を作っています {index}/{len(missing)}: {character['name']}"
            await auto_reference(api_key, character, req.reference_candidates)
        conn = get_connection()
        try:
            characters = [get_character(conn, c["id"]) or c for c in characters]
        finally:
            conn.close()
    return characters


async def _make(req: QuickMangaRequest, api_key: str, job: _QuickJob) -> None:
    from ..manga_draft import draft_characters, generate_episode, generate_outlines
    from ..models import MangaDraftCreateRequest, MangaDraftPanel, MangaV2ComposeRequest, MangaV2PanelsRequest
    from ..panel_check import review_script
    from .manga_draft import post_create
    from .manga_v2 import _check_and_redraw, _redraw_states, _run_panels, check_summary_text, compose, redraw_summary_text

    try:
        characters = await _prepare_characters(req, api_key, job)
        cast = draft_characters(characters)
        job.message = "大枠シナリオを考えています"
        panel_range = (min(req.min_panels, req.max_panels), max(req.min_panels, req.max_panels)) if req.dynamic_panels else None
        outlines = await generate_outlines(
            api_key, req.theme, req.genre, cast, req.episodes, notes=req.notes, panel_range=panel_range
        )
        outline = outlines[0]
        counts: list[int] | None = outline.get("panel_counts")
        job.title = outline["title"]
        episodes: list[list[dict[str, Any]]] = []
        job.total = len(outline["episodes"])
        for index in range(len(outline["episodes"])):
            job.progress = index
            job.message = f"台本を書いています {index + 1}/{len(outline['episodes'])}話: {outline['title']}"
            panels = await generate_episode(
                api_key, outline, index, cast, notes=req.notes,
                # ページごとにコマ数を変えるときは全ページで1つの話なので、それまでのページを全部渡す(繰り返さないように)
                previous_panels=[p for e in episodes for p in e] if counts else (episodes[-1] if episodes else None),
                count=counts[index] if counts else None,
            )
            if req.review_script:
                job.message = f"台本を見直しています {index + 1}/{len(outline['episodes'])}話(絵に要る物・状態を足す)"
                panels = await review_script(api_key, outline["episodes"][index], panels)
            episodes.append(panels)
        story = await post_create(
            MangaDraftCreateRequest(
                title=outline["title"][:200],
                character_ids=[c["id"] for c in characters],
                episodes=[[MangaDraftPanel.model_validate(p) for p in episode] for episode in episodes],
                panels_per_page=4,
            )
        )
        job.story_id = story.id
        if counts:
            # ページごとのコマ割りを、そのページのコマ数に合わせる(取り込んだ作品のコマ割りと同じ仕組みで合成する)
            conn = get_connection()
            try:
                set_manga_v2_page_layouts(conn, story.id, [dynamic_layout(len(e), i) for i, e in enumerate(episodes)])
            finally:
                conn.close()
        count = sum(len(e) for e in episodes)
        if req.stop_after_script:
            job.message = f"台本ができました: {outline['title']}({len(episodes)}話・{count}コマ。コマの絵はまだ生成していません)"
            job.status = "done"
            return
        conn = get_connection()
        try:
            targets = list_story_scenes(conn, story.id)
        finally:
            conn.close()
        panels = MangaV2PanelsRequest(
            template=req.template, color=req.color, use_character_reference=req.use_character_reference, vary_seed=True
        )
        job.progress = 0
        await _run_panels(job, story.id, panels, api_key, targets)
        checked = ""
        if req.check_panels:
            checked = "。" + check_summary_text(await _check_and_redraw(job, story.id, panels, api_key, 2))
        redrawn = redraw_summary_text(await _redraw_states(job, story.id, panels, api_key)) if req.redraw_states else ""
        if redrawn:
            checked += f"。{redrawn}"
        job.message = "ページに合成しています"
        result = await asyncio.to_thread(compose, story.id, MangaV2ComposeRequest(template=req.template))
        job.message = f"漫画ができました: {outline['title']}({len(result['pages'])}ページ・{count}コマ){checked}" + (f"。{job.guard_note}" if getattr(job, "guard_note", "") else "")
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status = "error"
        job.detail = str(exc)


@router.get("/themes")
async def get_themes(client: ClientDep, genre: str = "日常コメディ", trends: bool = True) -> dict[str, Any]:
    """
    題材の候補(テーマの文)。trends なら、なろう・AniList のそのジャンルの今の傾向(キーワード・タグ)を手がかりにする
    (特定の作品の筋は使わない)。手がかりにしたものと、問い合わせられなかったものも返す。
    """
    genre = genre.strip()[:100] or "日常コメディ"
    try:
        return await suggest_themes(client.api_key, genre, trends)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=f"題材を考えられませんでした: {exc}")


@router.post("")
async def post_quick_manga(req: QuickMangaRequest, client: ClientDep) -> dict[str, Any]:
    """テーマから漫画まで一気に作る(ジョブ)。進み具合は GET /jobs/{id}。"""
    job_id = uuid4().hex[:12]
    job = _QuickJob()
    _jobs[job_id] = job
    job.task = asyncio.create_task(_make(req, client.api_key, job))
    return _job_response(job_id, job)


@router.get("/jobs/{job_id}")
async def get_quick_manga_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_response(job_id, job)
