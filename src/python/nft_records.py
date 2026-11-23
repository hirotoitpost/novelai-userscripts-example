"""
見えない透かしの台帳(watermark_registry.py)の画像を、Sepolia(イーサリアムのテストネット)の NFT として記録する。

署名・送信はブラウザの MetaMask で行う(秘密鍵はアプリに来ない)。ここで持つのは:
- 記録の契約(contracts/ReleaseRecord.sol)のアドレス。利用者ごとに1つ、画面から MetaMask で作る
- 発行した記録(透かしの番号 = トークン番号、取引の ID)
- まだ発行していない画像の一覧と、発行に渡す値(ファイルの SHA-256・見た目の指紋・出した日時)

テストネットだけを受け付ける(本物のお金がかかるチェーンでは使わない)。記録にお金としての価値は無く、譲渡もできない。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .db import get_connection

SEPOLIA_CHAIN_ID = 11155111
CHAINS = {SEPOLIA_CHAIN_ID: "Sepolia"}
EXPLORER = {SEPOLIA_CHAIN_ID: "https://sepolia.etherscan.io"}
# 1回の発行(取引)に入れる数。20 件で約 145 万ガス(ローカルのチェーンで確かめた)
MAX_BATCH = 20

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TX = re.compile(r"^0x[0-9a-fA-F]{64}$")


class NftError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_chain(chain_id: int) -> None:
    if chain_id not in CHAINS:
        raise NftError("テストネット(Sepolia)だけで使えます。MetaMask のネットワークを Sepolia にしてください。")


def _check_address(value: str, what: str) -> str:
    if not _ADDRESS.match(value or ""):
        raise NftError(f"{what}の形がおかしいです。")
    return value.lower()


def _check_tx(value: str) -> str:
    if not _TX.match(value or ""):
        raise NftError("取引の ID の形がおかしいです。")
    return value.lower()


def get_contract(chain_id: int = SEPOLIA_CHAIN_ID) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nft_contracts WHERE chain_id = ? ORDER BY id DESC LIMIT 1", (chain_id,)
        ).fetchone()
    finally:
        conn.close()
    return _contract_public(dict(row)) if row else None


def _contract_public(item: dict[str, Any]) -> dict[str, Any]:
    base = EXPLORER.get(int(item["chain_id"]), "")
    item["chain_name"] = CHAINS.get(int(item["chain_id"]), str(item["chain_id"]))
    item["url"] = f"{base}/address/{item['address']}" if base else None
    return item


def set_contract(address: str, tx_hash: str, owner: str, chain_id: int = SEPOLIA_CHAIN_ID) -> dict[str, Any]:
    """画面で作った記録の契約を登録する(作り直したら新しいものを使う。前の契約の記録はそのまま残る)。"""
    _check_chain(chain_id)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO nft_contracts (chain_id, address, tx_hash, owner, created_at) VALUES (?, ?, ?, ?, ?)",
            (chain_id, _check_address(address, "契約のアドレス"), _check_tx(tx_hash),
             _check_address(owner, "持ち主のアドレス"), _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return get_contract(chain_id) or {}


def _released_unix(created_at: str) -> int:
    return int(datetime.fromisoformat(created_at).timestamp())


def pending(limit: int = MAX_BATCH, chain_id: int = SEPOLIA_CHAIN_ID) -> dict[str, Any]:
    """
    まだ今の契約で発行していない画像(透かしの番号ごとに1件。古い順)。発行に渡す値をつける。
    戻り値は {total, items: [{mark_id, sha256, phash, released_at, label, purpose_label, source_path}]}。
    """
    contract = get_contract(chain_id)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT w.mark_id, w.sha256, w.phash, w.label, w.purpose, w.source_path,
                   (SELECT MIN(created_at) FROM watermarks f WHERE f.mark_id = w.mark_id) AS first_at
            FROM watermarks w
            WHERE w.id IN (SELECT MAX(id) FROM watermarks GROUP BY mark_id)
              AND NOT EXISTS (
                SELECT 1 FROM nft_tokens t WHERE t.mark_id = w.mark_id AND t.chain_id = ? AND t.contract = ?
              )
            ORDER BY first_at
            """,
            (chain_id, (contract or {}).get("address", "")),
        ).fetchall()
    finally:
        conn.close()
    from .watermark_registry import PURPOSE_LABEL

    items = [
        {
            "mark_id": int(row["mark_id"]),
            "sha256": f"0x{row['sha256']}",
            "phash": f"0x{row['phash']}",
            "released_at": _released_unix(row["first_at"]),
            "label": row["label"],
            "purpose_label": PURPOSE_LABEL.get(row["purpose"], row["purpose"]),
            "source_path": row["source_path"],
        }
        for row in rows
    ]
    return {"total": len(items), "items": items[: max(1, min(limit, MAX_BATCH))]}


def record_minted(contract: str, tx_hash: str, mark_ids: list[int], chain_id: int = SEPOLIA_CHAIN_ID) -> int:
    """発行できた記録を残す(今の契約で、台帳にある番号だけ)。残した数を返す。"""
    _check_chain(chain_id)
    address = _check_address(contract, "契約のアドレス")
    current = get_contract(chain_id)
    if current is None or current["address"] != address:
        raise NftError("登録している契約と違います。")
    if not mark_ids or len(mark_ids) > MAX_BATCH:
        raise NftError(f"1回に記録できるのは 1〜{MAX_BATCH} 件です。")
    tx = _check_tx(tx_hash)
    conn = get_connection()
    try:
        known = {
            int(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT mark_id FROM watermarks WHERE mark_id IN ({','.join('?' * len(mark_ids))})",
                mark_ids,
            )
        }
        unknown = [m for m in mark_ids if m not in known]
        if unknown:
            raise NftError(f"台帳に無い番号です: {', '.join(f'{m:08X}' for m in unknown[:5])}")
        count = 0
        for mark_id in dict.fromkeys(mark_ids):
            cur = conn.execute(
                "INSERT OR IGNORE INTO nft_tokens (mark_id, chain_id, contract, tx_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (mark_id, chain_id, address, tx, _now()),
            )
            count += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return count


def token_for(conn: sqlite3.Connection, mark_id: int) -> dict[str, Any] | None:
    """その番号の発行記録(いちばん新しい契約のもの)と、エクスプローラーの URL。"""
    row = conn.execute(
        "SELECT * FROM nft_tokens WHERE mark_id = ? ORDER BY id DESC LIMIT 1", (mark_id,)
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    base = EXPLORER.get(int(item["chain_id"]), "")
    item["chain_name"] = CHAINS.get(int(item["chain_id"]), str(item["chain_id"]))
    item["token_url"] = f"{base}/nft/{item['contract']}/{mark_id}" if base else None
    item["tx_url"] = f"{base}/tx/{item['tx_hash']}" if base else None
    return item
