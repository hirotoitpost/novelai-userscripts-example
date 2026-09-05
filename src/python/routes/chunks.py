from __future__ import annotations

from base64 import b64decode, b64encode
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query

from ..auth_utils import get_encryption_key
from ..db import (
    create_exclusive_group,
    create_situation,
    delete_exclusive_group,
    delete_situation,
    find_conflicts,
    get_connection,
    list_exclusive_groups,
    list_prompt_chunks,
    list_situations,
    set_chunk_exclusive_groups,
    set_chunk_situations,
    upsert_prompt_chunks,
)
from ..keystore_crypto import decrypt_keystore, decrypt_object
from ..models import (
    ConflictCheckRequest,
    EncryptionKeyRequest,
    EncryptionKeyResponse,
    ExclusiveGroupCreateRequest,
    ExclusiveGroupResponse,
    SetChunkExclusiveGroupsRequest,
    SetChunkSituationsRequest,
    SituationCreateRequest,
    SituationResponse,
)

router = APIRouter(prefix="/api/chunks", tags=["chunks"])

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
