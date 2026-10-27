"""
キャラごとの生成した画像の API(/api/character-images): 一覧・参照画像に選び直す・古い画像の整理。中身は character_images.py。
画像はライブラリの配信(/api/library/file・/api/library/thumb)で見せる。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import character_images
from ..db import get_character, get_connection, update_character_reference, user_files
from ..models import CharacterImageCleanupRequest, CharacterImageReferenceRequest

router = APIRouter(prefix="/api/character-images", tags=["character-images"])

_ROOT = user_files()


def _character(character_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        character = get_character(conn, character_id)
    finally:
        conn.close()
    if character is None:
        raise HTTPException(status_code=404, detail="キャラが見つかりません。")
    return character


def _kinds() -> dict[str, Any]:
    return {
        "kinds": [{"key": k, "label": v} for k, v in character_images.KINDS.items()],
        "cleanable": list(character_images.CLEANABLE),
    }


@router.get("/kinds")
async def get_kinds() -> dict[str, Any]:
    return _kinds()


@router.get("/{character_id}")
async def get_images(character_id: int) -> dict[str, Any]:
    """そのキャラの画像の一覧(新しい順)。"""
    character = _character(character_id)
    items = await asyncio.to_thread(character_images.collect, character)
    return {**_kinds(), "reference_image_path": character.get("reference_image_path"), "items": items}


@router.put("/{character_id}/reference")
async def choose_reference(character_id: int, req: CharacterImageReferenceRequest) -> dict[str, Any]:
    """
    一覧の画像を参照画像にする。前の参照画像はそのまま指し直し、ほかの画像は参照画像の置き場所へ写す(元の画像を
    整理しても参照画像が消えないように)。候補は、その候補のシードを基準シードにもする(候補から選ぶときと同じ)。
    """
    from .manga_v2 import register_reference

    character = _character(character_id)
    item = await asyncio.to_thread(character_images.find, character, req.path)
    if item is None:
        raise HTTPException(status_code=404, detail="このキャラの画像の一覧にありません。")
    if item["kind"] == "reference":
        conn = get_connection()
        try:
            update_character_reference(conn, character_id, item["path"])
        finally:
            conn.close()
        path = item["path"]
    else:
        seed = item["seed"] if item["kind"] == "candidate" else None
        path = register_reference(character_id, (_ROOT / item["path"]).read_bytes(), seed)
    return {"reference_image_path": path}


@router.post("/cleanup/preview")
async def preview_cleanup(req: CharacterImageCleanupRequest) -> dict[str, Any]:
    """整理できる画像の一覧(古い順。画面で見本を見て選ぶ)。何も変えない。"""
    character = _character(req.character_id) if req.character_id is not None else None
    targets = await asyncio.to_thread(character_images.cleanup_targets, req.kinds, req.older_than_days, character)
    return {"items": targets, "bytes": sum(t["bytes"] for t in targets)}


@router.post("/cleanup")
async def run_cleanup(req: CharacterImageCleanupRequest) -> dict[str, Any]:
    """
    選んだ画像をごみ箱へ移す(30日のあいだは元に戻せる)。paths のうち、いま整理してよいもの(同じ条件で数え直した
    一覧にあるもの)だけを移す。
    """
    if not req.paths:
        raise HTTPException(status_code=422, detail="片付ける画像を選んでください。")
    character = _character(req.character_id) if req.character_id is not None else None
    targets = await asyncio.to_thread(character_images.cleanup_targets, req.kinds, req.older_than_days, character)
    wanted = set(req.paths)
    chosen = [t for t in targets if t["path"] in wanted]
    if not chosen:
        return {"deleted": 0, "bytes": 0, "trash_ids": []}
    label = f"{character['name']}の画像の整理" if character else "古い画像の整理"
    return await asyncio.to_thread(character_images.move_to_trash, chosen, label)
