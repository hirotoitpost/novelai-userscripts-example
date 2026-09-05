from __future__ import annotations

from base64 import b64decode, b64encode
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from novelai import AsyncNovelAI
from novelai.types import GenerateImageParams

from ..auth_utils import get_encryption_key
from ..client import get_client
from ..db import (
    create_exclusive_group,
    create_preset,
    create_situation,
    delete_exclusive_group,
    delete_preset,
    delete_situation,
    find_conflicts,
    find_similar,
    get_connection,
    get_preset,
    list_exclusive_groups,
    list_generation_history,
    list_prompt_chunks,
    list_presets,
    list_situations,
    record_generation,
    select_by_situation,
    select_random,
    set_chunk_exclusive_groups,
    set_chunk_situations,
    update_preset_chunks,
    upsert_prompt_chunks,
)
from ..keystore_crypto import decrypt_keystore, decrypt_object
from ..models import (
    ConflictCheckRequest,
    EncryptionKeyRequest,
    EncryptionKeyResponse,
    ExclusiveGroupCreateRequest,
    ExclusiveGroupResponse,
    PresetCreateRequest,
    PresetSummary,
    PresetUpdateRequest,
    RandomSelectRequest,
    ScenarioSelectRequest,
    SetChunkExclusiveGroupsRequest,
    SetChunkSituationsRequest,
    SimilarSelectRequest,
    SituationCreateRequest,
    SituationResponse,
    WordSelectionGenerateRequest,
)
from .image import _build_kwargs, _decode_b64, _http_status, _pil_to_b64

router = APIRouter(prefix="/api/chunks", tags=["chunks"])

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

_HISTORY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "outputs" / "history"

# promptmacros(プロンプトチャンク)は image.novelai.net 側のユーザーストレージにある。
# persistent access token は拒否されるため、実ログインで発行されたセッショントークンが必要。
_IMAGE_API = "https://image.novelai.net"


@router.post("/encryption-key", response_model=EncryptionKeyResponse)
async def compute_encryption_key(req: EncryptionKeyRequest) -> EncryptionKeyResponse:
    """
    keystore 復号用の鍵をメール+パスワードから計算する。ローカル計算のみでネットワーク送信は無い。
    パスワード自体はレスポンスに含めず、導出された鍵だけを返す。
    """
    key = get_encryption_key(req.email, req.password)
    return EncryptionKeyResponse(encryption_key=b64encode(key).decode())


@router.get("/promptmacros")
async def get_prompt_macros(
    encryption_key: str = Query(..., description="POST /encryption-key で取得した base64 鍵"),
    authorization: str | None = Header(None),
) -> list[dict[str, Any]]:
    """
    NovelAI 公式のプロンプトチャンクを取得・復号し、ローカルDB(prompt_chunks)へ
    upsert してから返す。取得＝インポートであり、通常のアプリ利用には不要な任意操作。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization ヘッダーが必要です")
    token = authorization[7:]

    try:
        key = b64decode(encryption_key)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"encryption_key が不正です: {exc}")

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(f"{_IMAGE_API}/user/keystore")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        try:
            keystore = decrypt_keystore(resp.json(), key)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"keystore の復号に失敗しました: {exc}")

        resp = await client.get(f"{_IMAGE_API}/user/objects/promptmacros")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        body = resp.json()

    items = body.get("objects", body) if isinstance(body, dict) else body

    result: list[dict[str, Any]] = []
    for item in items:
        try:
            decrypted = decrypt_object(item, keystore)
        except Exception:  # noqa: BLE001 一部アイテムの復号失敗はスキップして続行する
            continue
        decrypted["remote_object_id"] = item.get("id")
        result.append(decrypted)

    conn = get_connection()
    try:
        upsert_prompt_chunks(conn, result)
    finally:
        conn.close()

    return result


@router.get("/imported")
async def get_imported_chunks() -> list[dict[str, Any]]:
    """NovelAIへは問い合わせず、ローカルDBに既にインポート済みのチャンクだけを返す。"""
    conn = get_connection()
    try:
        return list_prompt_chunks(conn)
    finally:
        conn.close()


@router.get("/situations", response_model=list[SituationResponse])
async def get_situations() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_situations(conn)
    finally:
        conn.close()


@router.post("/situations", response_model=SituationResponse)
async def create_situation_endpoint(req: SituationCreateRequest) -> dict[str, Any]:
    conn = get_connection()
    try:
        return create_situation(conn, req.name)
    finally:
        conn.close()


@router.delete("/situations/{situation_id}", status_code=204)
async def delete_situation_endpoint(situation_id: int) -> None:
    conn = get_connection()
    try:
        delete_situation(conn, situation_id)
    finally:
        conn.close()


@router.put("/{chunk_id}/situations", status_code=204)
async def set_chunk_situations_endpoint(chunk_id: str, req: SetChunkSituationsRequest) -> None:
    conn = get_connection()
    try:
        set_chunk_situations(conn, chunk_id, req.situation_ids)
    finally:
        conn.close()


@router.get("/exclusive-groups", response_model=list[ExclusiveGroupResponse])
async def get_exclusive_groups() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_exclusive_groups(conn)
    finally:
        conn.close()


@router.post("/exclusive-groups", response_model=ExclusiveGroupResponse)
async def create_exclusive_group_endpoint(req: ExclusiveGroupCreateRequest) -> dict[str, Any]:
    conn = get_connection()
    try:
        return create_exclusive_group(conn, req.name)
    finally:
        conn.close()


@router.delete("/exclusive-groups/{group_id}", status_code=204)
async def delete_exclusive_group_endpoint(group_id: int) -> None:
    conn = get_connection()
    try:
        delete_exclusive_group(conn, group_id)
    finally:
        conn.close()


@router.put("/{chunk_id}/exclusive-groups", status_code=204)
async def set_chunk_exclusive_groups_endpoint(chunk_id: str, req: SetChunkExclusiveGroupsRequest) -> None:
    conn = get_connection()
    try:
        set_chunk_exclusive_groups(conn, chunk_id, req.group_ids)
    finally:
        conn.close()


@router.post("/check-conflicts")
async def check_conflicts_endpoint(req: ConflictCheckRequest) -> list[dict[str, Any]]:
    """
    渡したチャンクID群の中に、同じ排他グループのものが複数含まれていないか確認する。
    ワード選択ルール実装時に流用する想定の検証エンドポイント。
    """
    conn = get_connection()
    try:
        return find_conflicts(conn, req.chunk_ids)
    finally:
        conn.close()


# ===== ワード選択ルール =====

@router.post("/select/scenario")
async def select_scenario_endpoint(req: ScenarioSelectRequest) -> list[dict[str, Any]]:
    """指定シチュエーションのチャンクを、排他グループの重複を除いて返す。"""
    conn = get_connection()
    try:
        return select_by_situation(conn, req.situation_id)
    finally:
        conn.close()


@router.post("/select/random")
async def select_random_endpoint(req: RandomSelectRequest) -> list[dict[str, Any]]:
    """(任意でシチュエーション絞り込み後)排他グループの重複を除いてランダムに選ぶ。"""
    conn = get_connection()
    try:
        return select_random(conn, req.situation_id, req.count)
    finally:
        conn.close()


@router.post("/select/similar")
async def select_similar_endpoint(req: SimilarSelectRequest) -> list[dict[str, Any]]:
    """指定チャンクとタグの重なりが大きい順にランキングする。"""
    conn = get_connection()
    try:
        return find_similar(conn, req.chunk_id, req.limit)
    finally:
        conn.close()


@router.get("/presets", response_model=list[PresetSummary])
async def get_presets() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_presets(conn)
    finally:
        conn.close()


@router.post("/presets", response_model=PresetSummary)
async def create_preset_endpoint(req: PresetCreateRequest) -> dict[str, Any]:
    conn = get_connection()
    try:
        return create_preset(conn, req.name, req.chunk_ids)
    finally:
        conn.close()


@router.get("/presets/{preset_id}")
async def get_preset_endpoint(preset_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        preset = get_preset(conn, preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="preset not found")
        return preset
    finally:
        conn.close()


@router.put("/presets/{preset_id}", status_code=204)
async def update_preset_endpoint(preset_id: int, req: PresetUpdateRequest) -> None:
    conn = get_connection()
    try:
        update_preset_chunks(conn, preset_id, req.chunk_ids)
    finally:
        conn.close()


@router.delete("/presets/{preset_id}", status_code=204)
async def delete_preset_endpoint(preset_id: int) -> None:
    conn = get_connection()
    try:
        delete_preset(conn, preset_id)
    finally:
        conn.close()


# ===== 生成履歴(/select 経由の生成のみ対象) =====

@router.post("/select/generate")
async def select_generate_endpoint(
    req: WordSelectionGenerateRequest,
    client: ClientDep,
) -> dict[str, Any]:
    """
    ワード選択(/select)からの画像生成専用エンドポイント。通常の /api/image/generate と同じ
    生成処理を行うが、使ったチャンクIDと合わせて generation_history に記録する点だけが違う。
    """
    try:
        params = GenerateImageParams(**_build_kwargs(req.generation))
        images = await client.image.generate(params)
    except Exception as exc:
        raise HTTPException(status_code=_http_status(exc), detail=str(exc))

    b64_images = _pil_to_b64(images, "png")

    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    for b64 in b64_images:
        filename = f"{uuid4().hex}.png"
        (_HISTORY_DIR / filename).write_bytes(b64decode(b64))
        image_paths.append(f"outputs/history/{filename}")

    i2i_image_path = None
    if req.generation.i2i:
        i2i_image_path = _save_reference_image(req.generation.i2i.image)

    character_references: list[dict[str, Any]] | None = None
    if req.generation.character_references:
        character_references = [
            {
                "image_path": _save_reference_image(cr.image),
                "type": cr.type,
                "fidelity": cr.fidelity,
                "strength": cr.strength,
            }
            for cr in req.generation.character_references
        ]

    characters: list[dict[str, Any]] | None = None
    if req.generation.characters:
        characters = [
            {
                "prompt": c.prompt,
                "negative_prompt": c.negative_prompt,
                "position": c.position,
                "enabled": c.enabled,
            }
            for c in req.generation.characters
        ]

    conn = get_connection()
    try:
        entry = record_generation(
            conn,
            prompt=req.generation.prompt,
            negative_prompt=req.generation.negative_prompt,
            model=req.generation.model,
            size=str(req.generation.size),
            steps=req.generation.steps,
            scale=req.generation.scale,
            seed=req.generation.seed,
            chunk_ids=req.chunk_ids,
            image_paths=image_paths,
            i2i_image_path=i2i_image_path,
            i2i_strength=req.generation.i2i.strength if req.generation.i2i else None,
            i2i_noise=req.generation.i2i.noise if req.generation.i2i else None,
            character_references=character_references,
            characters=characters,
        )
    finally:
        conn.close()

    return {"images": b64_images, "format": "png", "history_id": entry["id"]}


def _save_reference_image(image_b64: str) -> str:
    """i2i/キャラクター参照画像を outputs/history/ に保存し、リポジトリルート相対パスを返す。"""
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}_ref.png"
    (_HISTORY_DIR / filename).write_bytes(_decode_b64(image_b64))
    return f"outputs/history/{filename}"


@router.get("/history")
async def get_generation_history(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        entries = list_generation_history(conn, limit)
    finally:
        conn.close()

    for entry in entries:
        images: list[str] = []
        for rel_path in entry.pop("image_paths"):
            b64 = _read_ref_image(rel_path)
            if b64:
                images.append(b64)
        entry["images"] = images

        i2i_image_path = entry.pop("i2i_image_path", None)
        entry["i2i_image"] = _read_ref_image(i2i_image_path) if i2i_image_path else None

        for cr in entry.get("character_references", []):
            cr["image"] = _read_ref_image(cr.pop("image_path", None))
    return entries


def _read_ref_image(rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    full_path = _HISTORY_DIR.parent.parent / rel_path
    if not full_path.exists():
        return None
    return b64encode(full_path.read_bytes()).decode()
