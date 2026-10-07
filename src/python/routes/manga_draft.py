"""
漫画ドラフト: テーマ → 大枠シナリオ案 → 1話ずつの台本 → 物語(→ コマ生成・合成は漫画v2の API)。
文章は NovelAI の GLM-4.6 に書かせる(manga_draft.py)。シリーズを選ぶと、シリーズのメモリ(人物設定)と
既刊のあらすじを前提に渡し、続編として作る。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from novelai import AsyncNovelAI

from ..client import get_client
from ..db import get_character, get_connection, get_manga_import, get_series, list_series_volumes
from ..manga_draft import (
    PANELS_PER_EPISODE,
    DraftCharacter,
    draft_characters,
    generate_episode,
    generate_outlines,
    panel_to_scene,
)
from ..manga_import import structure_lines
from ..models import (
    MangaDraftCreateRequest,
    MangaDraftEpisodeRequest,
    MangaDraftOutlineRequest,
    ScriptedStoryRequest,
    StoryResponse,
)
from .story import create_scripted_story

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/manga-draft", tags=["manga-draft"])


def _characters(character_ids: list[int], profiles: dict[int, str]) -> list[DraftCharacter]:
    conn = get_connection()
    try:
        rows = [get_character(conn, i) for i in character_ids]
    finally:
        conn.close()
    missing = [i for i, row in zip(character_ids, rows) if row is None]
    if missing:
        raise HTTPException(status_code=422, detail=f"存在しないキャラIDがあります: {missing}")
    return draft_characters([row for row in rows if row is not None], profiles)


def _series_context(series_id: int | None) -> tuple[str, list[str], int | None]:
    """(シリーズのメモリ, 既刊のあらすじ, 次の巻の番号)。シリーズ未指定なら空。"""
    if series_id is None:
        return "", [], None
    conn = get_connection()
    try:
        series = get_series(conn, series_id)
        if series is None:
            raise HTTPException(status_code=404, detail="series not found")
        volumes = list_series_volumes(conn, series_id)
    finally:
        conn.close()
    recaps = [f"{v['volume_no']}巻: {v['recap']}" for v in volumes if v.get("recap")]
    next_volume = max((v["volume_no"] for v in volumes), default=0) + 1
    return series.get("memory") or "", recaps, next_volume


# 取り込みから作れる話数の上限(大枠の話数の上限と同じ)
_MAX_EPISODES = 10


def _import_structure(import_id: int | None) -> list[list[str]]:
    """取り込んだ作品のコマ運びを、読む順に4コマずつ(1話ずつ)の説明にする。未指定なら空。"""
    if import_id is None:
        return []
    conn = get_connection()
    try:
        item = get_manga_import(conn, import_id)
    finally:
        conn.close()
    if item is None:
        raise HTTPException(status_code=404, detail="import not found")
    panels = [p for page in (item.get("analysis") or {}).get("pages") or [] for p in page["panels"]]
    if not panels:
        raise HTTPException(status_code=409, detail="取り込んだ作品の構成をまだ読み取っていません。")
    lines = structure_lines(panels)
    chunks = [lines[i : i + PANELS_PER_EPISODE] for i in range(0, len(lines), PANELS_PER_EPISODE)]
    return chunks[:_MAX_EPISODES]


def _notes(series_memory: str, notes: str) -> str:
    return "\n".join(part for part in (series_memory.strip(), notes.strip()) if part)


@router.post("/outlines")
async def post_outlines(req: MangaDraftOutlineRequest, client: ClientDep) -> dict[str, Any]:
    """大枠シナリオ案を3つ作る(GLM-4.6 で数十秒)。"""
    characters = _characters(req.character_ids, req.profiles)
    memory, recaps, next_volume = _series_context(req.series_id)
    structure = _import_structure(req.import_id)
    # 取り込んだ作品の構成を使うときは、話数をそのコマ数に合わせる
    episodes = len(structure) or req.episodes
    try:
        outlines = await generate_outlines(
            client.api_key,
            req.theme,
            req.genre,
            characters,
            episodes,
            notes=_notes(memory, req.notes),
            previous=recaps,
            structure=structure,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "outlines": outlines,
        "characters": [{"id": c.id, "name": c.name} for c in characters],
        "next_volume": next_volume,
        "episodes": episodes,
    }


@router.post("/episode")
async def post_episode(req: MangaDraftEpisodeRequest, client: ClientDep) -> dict[str, Any]:
    """大枠の1話分を4コマの台本にする(GLM-4.6 で十数秒)。直前の話のコマを渡すと流れをつなげる。"""
    if req.episode_index >= len(req.outline.episodes):
        raise HTTPException(status_code=400, detail="その話は大枠にありません。")
    characters = _characters(req.character_ids, req.profiles)
    memory, _, _ = _series_context(req.series_id)
    structure = _import_structure(req.import_id)
    try:
        panels = await generate_episode(
            client.api_key,
            req.outline.model_dump(),
            req.episode_index,
            characters,
            notes=_notes(memory, req.notes),
            previous_panels=[p.model_dump() for p in req.previous_panels],
            structure=structure[req.episode_index] if req.episode_index < len(structure) else None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"panels": panels}


@router.post("/create", response_model=StoryResponse)
async def post_create(req: MangaDraftCreateRequest) -> StoryResponse:
    """
    編集した台本から物語を作る(シリーズを選んでいればその次の巻)。コマの生成と合成は
    漫画v2の API(/api/manga-v2/{id}/panels, /compose)で行う。
    """
    characters = _characters(req.character_ids, req.profiles)
    scenes = [
        panel_to_scene(panel.model_dump(), characters, f"{e + 1}話{i + 1}")
        for e, episode in enumerate(req.episodes)
        for i, panel in enumerate(episode)
    ]
    if not scenes:
        raise HTTPException(status_code=400, detail="台本が空です。")
    volume_no = req.volume_no
    if req.series_id is not None and volume_no is None:
        volume_no = _series_context(req.series_id)[2]
    script = ScriptedStoryRequest.model_validate(
        {
            "title": req.title,
            "panels_per_page": req.panels_per_page,
            "scenes": scenes,
            "series_id": req.series_id,
            "volume_no": volume_no,
        }
    )
    return await create_scripted_story(script)
