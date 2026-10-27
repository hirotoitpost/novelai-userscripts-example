"""
R18 のパターン(adult_patterns.py)の API: プレイとシナリオの型の一覧・足す・直す・消す・並べ替えと、プレイの参照画像。
参照画像は判定モデルでタグを読み取り、未成年の手がかりが出たものは受け付けない。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import sqlite3
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from PIL import Image, UnidentifiedImageError

from .. import adult_patterns
from ..db import (
    add_adult_pattern,
    add_adult_pattern_image,
    delete_adult_pattern,
    delete_adult_pattern_image,
    get_adult_pattern,
    get_connection,
    list_adult_patterns,
    reorder_adult_patterns,
    update_adult_pattern,
    update_adult_pattern_image,
    user_files,
)
from ..models import AdultPatternImageRequest, AdultPatternImageTagsRequest, AdultPatternOrderRequest, AdultPatternRequest

router = APIRouter(prefix="/api/adult-patterns", tags=["adult-patterns"])

_ROOT = user_files()
_IMAGE_DIR = "outputs/adult_patterns"
# 参照画像の長い辺(大きすぎる画像は縮めて保存する)
_MAX_SIDE = 1536


@router.get("")
async def get_patterns() -> dict[str, Any]:
    conn = get_connection()
    try:
        rows = list_adult_patterns(conn)
    finally:
        conn.close()
    return {
        "kinds": [{"key": k, "label": v} for k, v in adult_patterns.KINDS.items()],
        "patterns": {kind: [r for r in rows if r["kind"] == kind] for kind in adult_patterns.KINDS},
    }


def _check(req: AdultPatternRequest) -> None:
    if req.kind not in adult_patterns.KINDS:
        raise HTTPException(status_code=422, detail=f"パターンの種類に無い値です: {req.kind}")
    found = adult_patterns.problems(req.name, req.tags, req.description, req.sfx, req.lines)
    if found:
        raise HTTPException(status_code=422, detail=f"未成年を思わせる語は入れられません: {', '.join(found)}")


@router.post("")
async def create_pattern(req: AdultPatternRequest) -> dict[str, Any]:
    _check(req)
    conn = get_connection()
    try:
        return add_adult_pattern(conn, req.model_dump())
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"同じ名前のパターンがあります: {req.name}")
    finally:
        conn.close()


@router.put("/order")
async def order_patterns(req: AdultPatternOrderRequest) -> dict[str, bool]:
    conn = get_connection()
    try:
        reorder_adult_patterns(conn, req.kind, req.ids)
    finally:
        conn.close()
    return {"ok": True}


def _require(conn: Any, pattern_id: int) -> dict[str, Any]:
    pattern = get_adult_pattern(conn, pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="パターンが見つかりません。")
    return pattern


@router.put("/{pattern_id}")
async def update_pattern(pattern_id: int, req: AdultPatternRequest) -> dict[str, Any]:
    """パターンを書き換える(種類は変えない)。"""
    _check(req)
    conn = get_connection()
    try:
        current = _require(conn, pattern_id)
        if current["kind"] != req.kind:
            raise HTTPException(status_code=422, detail="パターンの種類は変えられません。")
        return update_adult_pattern(conn, pattern_id, req.model_dump()) or {}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"同じ名前のパターンがあります: {req.name}")
    finally:
        conn.close()


@router.delete("/{pattern_id}")
async def delete_pattern(pattern_id: int) -> dict[str, bool]:
    """パターンと、その参照画像(ファイルも)を消す。"""
    conn = get_connection()
    try:
        _require(conn, pattern_id)
        paths = delete_adult_pattern(conn, pattern_id)
    finally:
        conn.close()
    for path in paths:
        (_ROOT / path).unlink(missing_ok=True)
    return {"deleted": True}


def _decode(data: str) -> Image.Image:
    payload = data.split(",", 1)[1] if data.startswith("data:") else data
    try:
        image = Image.open(io.BytesIO(base64.b64decode(payload, validate=False)))
        image.load()
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="画像を読み取れませんでした。")
    image = image.convert("RGB")
    image.thumbnail((_MAX_SIDE, _MAX_SIDE))
    return image


@router.post("/{pattern_id}/images")
async def add_image(pattern_id: int, req: AdultPatternImageRequest) -> dict[str, Any]:
    """
    プレイの参照画像を足す。判定モデルで構図・行為のタグを読み取り、プレイを使うたびに1枚ずつ順に足す。
    未成年を思わせるタグが判定に出た画像は受け付けない(保存もしない)。
    """
    from ..image_tagger import tag_image

    conn = get_connection()
    try:
        pattern = _require(conn, pattern_id)
    finally:
        conn.close()
    if pattern["kind"] != "play":
        raise HTTPException(status_code=422, detail="参照画像はプレイにだけ付けられます。")
    image = _decode(req.data)
    scores = await asyncio.to_thread(tag_image, image)
    tags, minors = adult_patterns.image_tags(scores)
    if minors:
        raise HTTPException(status_code=422, detail=f"未成年を思わせる絵は参照画像にできません(判定: {', '.join(minors)})")
    path = f"{_IMAGE_DIR}/{pattern_id}/{uuid4().hex[:12]}.png"
    (_ROOT / path).parent.mkdir(parents=True, exist_ok=True)
    image.save(_ROOT / path, "PNG")
    conn = get_connection()
    try:
        add_adult_pattern_image(conn, pattern_id, path, ", ".join(tags))
        return get_adult_pattern(conn, pattern_id) or {}
    finally:
        conn.close()


@router.put("/{pattern_id}/images/{image_id}")
async def update_image_tags(pattern_id: int, image_id: int, req: AdultPatternImageTagsRequest) -> dict[str, Any]:
    """参照画像から読み取ったタグを手で直す。"""
    found = adult_patterns.problems(req.tags)
    if found:
        raise HTTPException(status_code=422, detail=f"未成年を思わせる語は入れられません: {', '.join(found)}")
    conn = get_connection()
    try:
        pattern = _require(conn, pattern_id)
        if not any(i["id"] == image_id for i in pattern["images"]):
            raise HTTPException(status_code=404, detail="参照画像が見つかりません。")
        update_adult_pattern_image(conn, image_id, req.tags.strip())
        return get_adult_pattern(conn, pattern_id) or {}
    finally:
        conn.close()


@router.delete("/{pattern_id}/images/{image_id}")
async def delete_image(pattern_id: int, image_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        pattern = _require(conn, pattern_id)
        if not any(i["id"] == image_id for i in pattern["images"]):
            raise HTTPException(status_code=404, detail="参照画像が見つかりません。")
        removed = delete_adult_pattern_image(conn, image_id)
        result = get_adult_pattern(conn, pattern_id) or {}
    finally:
        conn.close()
    if removed:
        (_ROOT / removed["path"]).unlink(missing_ok=True)
    return result
