"""
下着・水着のコレクションと、からかいコレクション・ヌード写真集を、好きなキャラで作る(collection_templates.py の台本を
/api/story/scripted と同じ手順で物語にする)。成人キャラ(年齢を入れた 18 歳以上)だけで作れる。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from ..collection_templates import CollectionOptions, build_collection, catalog
from ..content_guard import is_defined_adult
from ..db import get_character, get_connection
from ..models import ScriptedStoryRequest, StoryResponse
from .story import create_scripted_story

router = APIRouter(prefix="/api/collections", tags=["collections"])


class CollectionRequest(BaseModel):
    kind: Literal["lingerie", "swimsuit", "tease_lingerie", "tease_swimsuit", "nude"]
    character_ids: list[int] = Field(min_length=1, max_length=4)
    title: Optional[str] = Field(None, max_length=200)
    # 体つき: sheet(キャラシートのまま)/ slender / curvy。省略時はコレクションが slender、からかいが curvy、ヌードが sheet
    body: Optional[Literal["sheet", "slender", "curvy"]] = None
    style_tags: str = Field("", max_length=1000)
    explicit: bool = True
    poses: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    lines: bool = True
    panels_per_page: int = Field(4, ge=1, le=12)
    series_id: Optional[int] = None
    volume_no: Optional[int] = Field(None, ge=1)


@router.get("")
async def get_catalog() -> dict[str, Any]:
    """作れるコレクションの種類・テーマ・からかいのポーズ・体つき。"""
    return catalog()


@router.post("", response_model=StoryResponse)
async def create_collection(req: CollectionRequest) -> StoryResponse:
    """選んだキャラでコレクションの台本を組み立て、物語にする。絵は漫画v2のコマ生成で描く。"""
    conn = get_connection()
    try:
        characters = [get_character(conn, cid) for cid in req.character_ids]
    finally:
        conn.close()
    missing = [cid for cid, c in zip(req.character_ids, characters) if c is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"キャラが見つかりません: {missing}")
    minors = [c["name"] for c in characters if c and not is_defined_adult(c)]
    if minors:
        raise HTTPException(
            status_code=422,
            detail=f"コレクションは成人キャラ(年齢を入れた 18 歳以上)だけで作れます: {'・'.join(minors)}",
        )
    options = CollectionOptions(
        kind=req.kind, title=req.title, body=req.body, style_tags=req.style_tags, explicit=req.explicit,
        poses=req.poses, lines=req.lines, themes=req.themes,
    )
    try:
        script = build_collection([c for c in characters if c], options)
        scripted = ScriptedStoryRequest(
            title=script["title"], premise=script["premise"], panels_per_page=req.panels_per_page,
            scenes=script["scenes"], series_id=req.series_id, volume_no=req.volume_no,
        )
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return await create_scripted_story(scripted)
