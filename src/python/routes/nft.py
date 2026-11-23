"""
台帳の画像の NFT 記録の API(/api/nft。Sepolia テストネットだけ)。中身は nft_records.py。
署名・送信は画面の MetaMask で行い、ここには結果(契約のアドレス・取引の ID)だけが来る。

  GET  /api/nft/config      記録の契約(無ければ null)と、使えるチェーン
  PUT  /api/nft/contract    画面で作った記録の契約を登録する
  GET  /api/nft/pending     まだ発行していない画像と、発行に渡す値(20 件まで)
  POST /api/nft/minted      発行できた番号を記録する
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import nft_records

router = APIRouter(prefix="/api/nft", tags=["nft"])


class ContractBody(BaseModel):
    address: str
    tx_hash: str
    owner: str
    chain_id: int = nft_records.SEPOLIA_CHAIN_ID


class MintedBody(BaseModel):
    contract: str
    tx_hash: str
    mark_ids: list[int] = Field(min_length=1, max_length=nft_records.MAX_BATCH)
    chain_id: int = nft_records.SEPOLIA_CHAIN_ID


@router.get("/config")
def get_config() -> dict[str, Any]:
    return {
        "chain_id": nft_records.SEPOLIA_CHAIN_ID,
        "chain_name": nft_records.CHAINS[nft_records.SEPOLIA_CHAIN_ID],
        "max_batch": nft_records.MAX_BATCH,
        "contract": nft_records.get_contract(),
    }


@router.put("/contract")
def put_contract(body: ContractBody) -> dict[str, Any]:
    try:
        return nft_records.set_contract(body.address, body.tx_hash, body.owner, body.chain_id)
    except nft_records.NftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/pending")
def get_pending(limit: int = Query(nft_records.MAX_BATCH, ge=1, le=nft_records.MAX_BATCH)) -> dict[str, Any]:
    return nft_records.pending(limit)


@router.post("/minted")
def post_minted(body: MintedBody) -> dict[str, Any]:
    try:
        return {"recorded": nft_records.record_minted(body.contract, body.tx_hash, body.mark_ids, body.chain_id)}
    except nft_records.NftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
