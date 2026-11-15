"""
絵柄セットの API(style_sets.py)。絵柄だけの参照画像と強さ・忠実度・絵柄タグ・ネガティブを名前を付けて保存し、
キャラに選ばせる。画像はアップロードか、ギャラリーの画像(キー)から作る。
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import style_sets
from ..db import (
    create_style_set,
    delete_style_set,
    get_connection,
    get_style_set,
    list_style_sets,
    set_characters_style_set,
    update_style_set,
)
from ..models import StyleSetCharactersRequest, StyleSetRequest, StyleSetUpdateRequest

router = APIRouter(prefix="/api/style-sets", tags=["style-sets"])


def _image_bytes(image: str | None, gallery_key: str | None) -> bytes | None:
    """送られた画像(base64 / data URL)か、ギャラリーの画像のキーから画像を読む。どちらも無ければ None。"""
    if image:
        data = image.split(",", 1)[1] if image.startswith("data:") else image
        try:
            return base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="画像を読み取れませんでした。")
    if gallery_key:
        from .library import _served_path, find_image

        item = find_image(gallery_key)
        if item is None:
            raise HTTPException(status_code=404, detail="ギャラリーの画像が見つかりません。")
        return _served_path(item.path).read_bytes()
    return None


def _saved_image(image: str | None, gallery_key: str | None) -> str | None:
    data = _image_bytes(image, gallery_key)
    if data is None:
        return None
    try:
        return style_sets.save_image(data)
    except OSError:
        raise HTTPException(status_code=422, detail="画像として読めませんでした。")


def _one(conn: sqlite3.Connection, style_set_id: int) -> dict[str, Any]:
    """一覧と同じ形(members 付き)の1件。"""
    for item in list_style_sets(conn):
        if item["id"] == style_set_id:
            return item
    raise HTTPException(status_code=404, detail="絵柄セットが見つかりません。")


@router.get("")
async def get_style_sets() -> list[dict[str, Any]]:
    """絵柄セットの一覧(名前順)。members は、そのセットを選んでいるキャラ(参照画像はプレビューのサンプルに使う)。"""
    conn = get_connection()
    try:
        return list_style_sets(conn)
    finally:
        conn.close()


@router.post("")
async def post_style_set(req: StyleSetRequest) -> dict[str, Any]:
    image_path = _saved_image(req.image, req.gallery_key)
    if image_path is None:
        raise HTTPException(status_code=422, detail="絵柄の参照にする画像を選んでください。")
    values = req.model_dump(exclude={"image", "gallery_key", "character_ids"})
    values["name"] = req.name.strip()
    conn = get_connection()
    try:
        try:
            created = create_style_set(conn, {**values, "image_path": image_path})
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"「{values['name']}」という絵柄セットは既にあります。")
        set_characters_style_set(conn, req.character_ids, created["id"])
        return _one(conn, created["id"])
    finally:
        conn.close()


@router.put("/{style_set_id}")
async def put_style_set(style_set_id: int, req: StyleSetUpdateRequest) -> dict[str, Any]:
    values = req.model_dump(exclude_unset=True, exclude={"image", "gallery_key"})
    if "name" in values:
        values["name"] = values["name"].strip()
    conn = get_connection()
    try:
        if get_style_set(conn, style_set_id) is None:
            raise HTTPException(status_code=404, detail="絵柄セットが見つかりません。")
        image_path = _saved_image(req.image, req.gallery_key)
        if image_path is not None:
            values["image_path"] = image_path
        try:
            update_style_set(conn, style_set_id, values)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"「{values.get('name')}」という絵柄セットは既にあります。")
        return _one(conn, style_set_id)
    finally:
        conn.close()


@router.delete("/{style_set_id}", status_code=204)
async def remove_style_set(style_set_id: int) -> None:
    """絵柄セットを消す。選んでいたキャラはセットなしに戻る(キャラ・画像ファイルは消さない)。"""
    conn = get_connection()
    try:
        delete_style_set(conn, style_set_id)
    finally:
        conn.close()


@router.put("/{style_set_id}/characters")
async def add_characters(style_set_id: int, req: StyleSetCharactersRequest) -> dict[str, Any]:
    """キャラをこの絵柄セットに入れる(別のセットにいれば移す)。"""
    conn = get_connection()
    try:
        if get_style_set(conn, style_set_id) is None:
            raise HTTPException(status_code=404, detail="絵柄セットが見つかりません。")
        set_characters_style_set(conn, req.character_ids, style_set_id)
        return _one(conn, style_set_id)
    finally:
        conn.close()


@router.delete("/{style_set_id}/characters/{character_id}")
async def remove_character(style_set_id: int, character_id: int) -> dict[str, Any]:
    """キャラをこの絵柄セットから外す(セットなしに戻す)。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE characters SET style_set_id = NULL WHERE id = ? AND style_set_id = ?", (character_id, style_set_id)
        )
        conn.commit()
        return _one(conn, style_set_id)
    finally:
        conn.close()
