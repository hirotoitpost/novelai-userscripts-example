"""台帳の画像の NFT 記録(nft_records.py)。チェーンには つながない。"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from python import nft_records, watermark_registry

ADDR = "0x" + "a" * 40
OWNER = "0x" + "b" * 40
TX = "0x" + "c" * 64


def _png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    pixels = (rng.random((512, 512, 3)) * 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buffer, "PNG")
    return buffer.getvalue()


def _mark(seed: int) -> int:
    watermark_registry.mark(_png(seed), purpose=watermark_registry.PUBLICATION, source_path=f"{seed}.png")
    return int(watermark_registry.list_marks(limit=1)[0]["mark_id"])


def test_only_testnet_and_valid_values() -> None:
    with pytest.raises(nft_records.NftError):
        nft_records.set_contract(ADDR, TX, OWNER, chain_id=1)  # 本物のイーサリアム
    with pytest.raises(nft_records.NftError):
        nft_records.set_contract("0x123", TX, OWNER)
    with pytest.raises(nft_records.NftError):
        nft_records.set_contract(ADDR, "0xabc", OWNER)
    contract = nft_records.set_contract(ADDR.upper().replace("0X", "0x"), TX, OWNER)
    assert contract["address"] == ADDR and contract["url"].startswith("https://sepolia.etherscan.io/address/")


def test_pending_and_minted() -> None:
    first, second = _mark(1), _mark(2)
    nft_records.set_contract(ADDR, TX, OWNER)
    todo = nft_records.pending()
    assert todo["total"] == 2
    item = todo["items"][0]
    assert item["mark_id"] == first and item["sha256"].startswith("0x") and len(item["sha256"]) == 66
    assert len(item["phash"]) == 18 and item["released_at"] > 1_700_000_000

    assert nft_records.record_minted(ADDR, TX, [first]) == 1
    assert nft_records.record_minted(ADDR, TX, [first]) == 0  # 2回目は増えない
    assert [i["mark_id"] for i in nft_records.pending()["items"]] == [second]
    marks = {m["mark_id"]: m for m in watermark_registry.list_marks()}
    assert marks[first]["nft"]["token_url"].endswith(f"/nft/{ADDR}/{first}")
    assert marks[second]["nft"] is None

    with pytest.raises(nft_records.NftError):
        nft_records.record_minted(ADDR, TX, [123])  # 台帳に無い番号
    with pytest.raises(nft_records.NftError):
        nft_records.record_minted("0x" + "d" * 40, TX, [second])  # 登録していない契約

    # 契約を作り直すと、また全部が未発行になる
    nft_records.set_contract("0x" + "e" * 40, TX, OWNER)
    assert nft_records.pending()["total"] == 2
