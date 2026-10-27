"""
衣装(着せ替え)の API(wardrobe.py)。部品(部位ごとの服の種類・色・柄)の編集と、キャラごとの衣装の登録・普段の衣装の選択。
衣装を素体の Aポーズに着せる絵は POST /api/lora-dataset/character/{id}/body-outfit(outfit_id)で描く。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import body_base, wardrobe
from ..character_sheet import blocked_tags, character_minor_tags
from ..db import (
    add_wardrobe_part,
    delete_character_outfit,
    delete_wardrobe_part,
    get_character_outfit,
    get_connection,
    get_wardrobe_part,
    list_all_outfits,
    list_characters,
    list_character_outfits,
    list_wardrobe_parts,
    outfits_using_part,
    reorder_wardrobe_parts,
    save_character_outfit,
    set_character_outfit,
    update_wardrobe_part,
)
from ..models import (
    CharacterOutfitRequest,
    CharacterOutfitSelectRequest,
    WardrobePartOrderRequest,
    WardrobePartRequest,
)
from . import lora_dataset
from .lora_dataset import _age_checks, _character_base, _load_character, _outfit_copy_paths

router = APIRouter(prefix="/api/wardrobe", tags=["wardrobe"])

# 衣装をまだ登録していないキャラに、普段の服装のタグから最初の衣装を作るときの名前
FIRST_OUTFIT_NAME = "普段の服装"


def _catalog() -> wardrobe.Catalog:
    conn = get_connection()
    try:
        return wardrobe.catalog_map(list_wardrobe_parts(conn))
    finally:
        conn.close()


# --- 部品(衣装の設定のページ) ---


@router.get("/parts")
async def get_parts() -> dict[str, Any]:
    """部位・色・柄と、それぞれの部品(DB)。"""
    conn = get_connection()
    try:
        rows = list_wardrobe_parts(conn)
    finally:
        conn.close()
    return {
        "slots": [{"key": k, "label": v} for k, v in wardrobe.SLOTS.items()],
        "modifiers": [{"key": k, "label": v} for k, v in wardrobe.MODIFIERS.items()],
        "parts": {kind: [r for r in rows if r["slot"] == kind] for kind in wardrobe.PART_KINDS},
    }


def _check_part(req: WardrobePartRequest) -> None:
    """部品はどのキャラの衣装にも入るので、全年齢で止めるタグ・性的な場面のタグ・未成年を示すタグは入れない。"""
    if req.slot not in wardrobe.PART_KINDS:
        raise HTTPException(status_code=422, detail=f"衣装の部位に無い項目です: {req.slot}")
    found = sorted(set(blocked_tags(req.tags)) | set(body_base.option_problems(req.tags)))
    if found:
        raise HTTPException(status_code=422, detail=f"部品のタグに入れられないタグです: {', '.join(found)}")


@router.post("/parts")
async def create_part(req: WardrobePartRequest) -> dict[str, Any]:
    """部品を足す。値を省くと part1, part2… を振る(値は衣装が持つので、あとから変えない)。"""
    _check_part(req)
    conn = get_connection()
    try:
        value = (req.value or "").strip()
        if not value:
            taken = {r["value"] for r in list_wardrobe_parts(conn) if r["slot"] == req.slot}
            value = next(f"part{n}" for n in range(1, len(taken) + 2) if f"part{n}" not in taken)
        return add_wardrobe_part(conn, req.slot, value, req.label.strip(), req.tags.strip(), req.colorable)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"同じ値の部品があります: {req.value}")
    finally:
        conn.close()


@router.put("/parts/order")
async def order_parts(req: WardrobePartOrderRequest) -> dict[str, bool]:
    conn = get_connection()
    try:
        reorder_wardrobe_parts(conn, req.slot, req.ids)
    finally:
        conn.close()
    return {"ok": True}


@router.put("/parts/{part_id}")
async def update_part(part_id: int, req: WardrobePartRequest) -> dict[str, Any]:
    """部品の名前・タグ・色を付けられるかを書き換える(部位と値は変えない)。普段の衣装のタグは、次に衣装を保存したときに変わる。"""
    _check_part(req)
    conn = get_connection()
    try:
        if get_wardrobe_part(conn, part_id) is None:
            raise HTTPException(status_code=404, detail="部品が見つかりません。")
        return update_wardrobe_part(conn, part_id, req.label.strip(), req.tags.strip(), req.colorable) or {}
    finally:
        conn.close()


@router.delete("/parts/{part_id}")
async def delete_part(part_id: int) -> dict[str, bool]:
    """部品を消す。使っている衣装があれば止める(衣装を変えてから消す)。"""
    conn = get_connection()
    try:
        part = get_wardrobe_part(conn, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="部品が見つかりません。")
        users = outfits_using_part(conn, part["slot"], part["value"])
        if users:
            raise HTTPException(status_code=409, detail=f"この部品を使っている衣装があります: {', '.join(users[:10])}")
        delete_wardrobe_part(conn, part_id)
    finally:
        conn.close()
    return {"deleted": True}


# --- キャラの衣装 ---


def _dressed(character: dict, outfit_id: int, root_name: str) -> dict[str, Any] | None:
    """その衣装を素体の Aポーズに着せた絵(当てている素体の。無ければ None)。"""
    base = _character_base(character)
    if base is None:
        return None
    image, _ = _outfit_copy_paths(character, base, root_name, outfit_id)
    if not image.is_file():
        return None
    return {"path": image.relative_to(lora_dataset._PROJECT_ROOT).as_posix(), "updated": image.stat().st_mtime}


def _outfits(character_id: int, root_name: str) -> dict[str, Any]:
    """キャラの衣装の一覧(組み立てたタグと、着せた絵)と、普段の衣装。衣装が無く普段の服装があれば、そこから1着目を作る。"""
    character = _load_character(character_id)
    catalog = _catalog()
    conn = get_connection()
    try:
        outfits = list_character_outfits(conn, character_id)
        if not outfits and (character.get("outfit_tags") or "").strip():
            first = save_character_outfit(conn, None, character_id, FIRST_OUTFIT_NAME, [], character["outfit_tags"].strip())
            set_character_outfit(conn, character_id, first["id"], None)
            character["outfit_id"] = first["id"]
            outfits = [first]
    finally:
        conn.close()
    return {
        "outfit_id": character.get("outfit_id"),
        "outfit_tags": character.get("outfit_tags") or "",
        "outfits": [
            {**o, "tags": wardrobe.compose(o["parts"], o["extra_tags"], catalog), "dressed": _dressed(character, o["id"], root_name)}
            for o in outfits
        ],
    }


@router.get("/outfits")
async def get_all_outfits() -> list[dict[str, Any]]:
    """
    全員の衣装(名前と組み立てたタグ)。キャラ一覧の着せ替えと、漫画の場面の服装の候補に使う。衣装が無く普段の服装が
    あるキャラには、そこから1着目を作る(キャラごとの一覧と同じ)。
    """
    catalog = _catalog()
    conn = get_connection()
    try:
        outfits = list_all_outfits(conn)
        dressed = {o["character_id"] for o in outfits}
        for character in list_characters(conn):
            if character["id"] not in dressed and (character.get("outfit_tags") or "").strip():
                first = save_character_outfit(conn, None, character["id"], FIRST_OUTFIT_NAME, [], character["outfit_tags"].strip())
                set_character_outfit(conn, character["id"], first["id"], None)
                outfits.append(first)
    finally:
        conn.close()
    return [
        {"id": o["id"], "character_id": o["character_id"], "name": o["name"], "tags": wardrobe.compose(o["parts"], o["extra_tags"], catalog)}
        for o in outfits
    ]


@router.get("/characters/{character_id}/outfits")
async def get_outfits(character_id: int, root_name: str = "training_data") -> dict[str, Any]:
    return _outfits(character_id, root_name)


def _checked_tags(character: dict, req: CharacterOutfitRequest) -> str:
    """
    衣装のタグを組み立てて検査する。部位・部品が表にあること、性的な場面のタグを入れないこと。未成年の手がかりがある
    キャラは全年齢で止めるタグも入れない。成人キャラは、未成年を示すタグを入れない(キャラシートの保存と同じ)。
    """
    catalog = _catalog()
    for part in req.parts:
        if part.slot not in wardrobe.SLOTS or part.value not in catalog.get(part.slot, {}):
            raise HTTPException(status_code=422, detail=f"部品の表に無い部品です: {part.slot}/{part.value}")
        for kind in ("color", "pattern"):
            value = getattr(part, kind)
            if value and value not in catalog.get(kind, {}):
                raise HTTPException(status_code=422, detail=f"{wardrobe.MODIFIERS[kind]}の表に無い値です: {value}")
    tags = wardrobe.compose([p.model_dump() for p in req.parts], req.extra_tags, catalog)
    found = body_base.clean_extra(tags)
    _, modest = _age_checks(character)
    if modest:
        found = sorted(set(found) | set(blocked_tags(tags)))
    if found:
        raise HTTPException(status_code=422, detail=f"衣装に入れられないタグです: {', '.join(found)}")
    if character.get("is_adult"):
        minors = character_minor_tags({**character, "outfit_tags": tags})
        if minors:
            raise HTTPException(status_code=422, detail=f"成人キャラの衣装に、未成年を示すタグは入れられません: {', '.join(minors)}")
    return tags


@router.post("/characters/{character_id}/outfits")
async def create_outfit(character_id: int, req: CharacterOutfitRequest, root_name: str = "training_data") -> dict[str, Any]:
    character = _load_character(character_id)
    _checked_tags(character, req)
    conn = get_connection()
    try:
        save_character_outfit(conn, None, character_id, req.name.strip(), [p.model_dump() for p in req.parts], req.extra_tags.strip())
    finally:
        conn.close()
    return _outfits(character_id, root_name)


@router.put("/characters/{character_id}/outfits/{outfit_id}")
async def update_outfit(character_id: int, outfit_id: int, req: CharacterOutfitRequest, root_name: str = "training_data") -> dict[str, Any]:
    """衣装を書き換える。普段の衣装なら、普段の服装のタグも書き換える。"""
    character = _load_character(character_id)
    tags = _checked_tags(character, req)
    conn = get_connection()
    try:
        outfit = get_character_outfit(conn, outfit_id)
        if outfit is None or outfit["character_id"] != character_id:
            raise HTTPException(status_code=404, detail="衣装が見つかりません。")
        save_character_outfit(conn, outfit_id, character_id, req.name.strip(), [p.model_dump() for p in req.parts], req.extra_tags.strip())
        if character.get("outfit_id") == outfit_id:
            set_character_outfit(conn, character_id, outfit_id, tags)
    finally:
        conn.close()
    return _outfits(character_id, root_name)


@router.delete("/characters/{character_id}/outfits/{outfit_id}")
async def delete_outfit(character_id: int, outfit_id: int, root_name: str = "training_data") -> dict[str, Any]:
    """衣装を消す(普段の衣装だったら、普段の服装のタグは残し、普段の衣装なしにする)。着せた絵は残す。"""
    conn = get_connection()
    try:
        outfit = get_character_outfit(conn, outfit_id)
        if outfit is None or outfit["character_id"] != character_id:
            raise HTTPException(status_code=404, detail="衣装が見つかりません。")
        delete_character_outfit(conn, outfit_id)
    finally:
        conn.close()
    return _outfits(character_id, root_name)


@router.put("/characters/{character_id}/outfit")
async def select_outfit(character_id: int, req: CharacterOutfitSelectRequest, root_name: str = "training_data") -> dict[str, Any]:
    """普段の衣装を選ぶ(着せ替え)。その衣装のタグを、キャラシートの普段の服装に入れる。"""
    character = _load_character(character_id)
    conn = get_connection()
    try:
        if req.outfit_id is None:
            set_character_outfit(conn, character_id, None, None)
        else:
            outfit = get_character_outfit(conn, req.outfit_id)
            if outfit is None or outfit["character_id"] != character_id:
                raise HTTPException(status_code=404, detail="衣装が見つかりません。")
            tags = wardrobe.compose(outfit["parts"], outfit["extra_tags"], _catalog())
            if character.get("is_adult") and character_minor_tags({**character, "outfit_tags": tags}):
                raise HTTPException(status_code=422, detail="成人キャラの普段の服装に、未成年を示すタグは入れられません。")
            set_character_outfit(conn, character_id, req.outfit_id, tags)
    finally:
        conn.close()
    return _outfits(character_id, root_name)
