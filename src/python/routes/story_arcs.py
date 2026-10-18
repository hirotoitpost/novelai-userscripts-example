"""
話の流れのセット(/api/story-arcs)。夏休み(星座観測 → 水着の買い物 → プール → 花火大会)のような、アニメでよくある
巻ごとの流れを名前を付けて残し、おまかせ漫画で段階(1巻分)を選んでテーマにする。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import delete_story_arc, get_connection, get_story_arc, list_story_arcs, save_story_arc

router = APIRouter(prefix="/api/story-arcs", tags=["story-arcs"])


class StoryArcRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # セットの説明(セットの後に続く流れなども。おまかせ漫画では補足に入れる)
    description: str = Field("", max_length=1000)
    # 巻ごとの流れ(1つが1巻)
    beats: list[str] = Field(min_length=1, max_length=20)


def _save(arc_id: int | None, req: StoryArcRequest) -> dict[str, Any]:
    beats = [b.strip()[:500] for b in req.beats if b.strip()]
    if not beats:
        raise HTTPException(status_code=422, detail="流れを1つ以上書いてください。")
    conn = get_connection()
    try:
        if arc_id is not None and get_story_arc(conn, arc_id) is None:
            raise HTTPException(status_code=404, detail="そのセットはありません。")
        return save_story_arc(conn, arc_id, req.name.strip(), req.description.strip(), beats)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="同じ名前のセットがあります。") from exc
    finally:
        conn.close()


@router.get("")
async def get_story_arcs() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_story_arcs(conn)
    finally:
        conn.close()


@router.post("")
async def post_story_arc(req: StoryArcRequest) -> dict[str, Any]:
    return _save(None, req)


@router.put("/{arc_id}")
async def put_story_arc(arc_id: int, req: StoryArcRequest) -> dict[str, Any]:
    return _save(arc_id, req)


@router.delete("/{arc_id}", status_code=204)
async def remove_story_arc(arc_id: int) -> None:
    conn = get_connection()
    try:
        if not delete_story_arc(conn, arc_id):
            raise HTTPException(status_code=404, detail="そのセットはありません。")
    finally:
        conn.close()
