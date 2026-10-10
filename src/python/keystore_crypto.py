from __future__ import annotations

from base64 import b64decode
from json import loads
from typing import Any
from zlib import MAX_WBITS, decompress as _inflate

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox

_COMPRESSION_PREFIX = b"\x00" * 15 + b"\x01"


def _decrypt_data(data: bytes, key: bytes, nonce: bytes | None = None) -> tuple[bytes, bytes] | None:
    """
    NovelAI の keystore/objects で使われている暗号ペイロードを復号する。
    ワイヤーフォーマット: [圧縮マーカー(任意)][nonce(nonce=None時のみ)][ciphertext]
    復号後の平文が圧縮されている場合は raw deflate (wbits=-15) で展開する。
    """
    box = SecretBox(key)

    is_compressed = data.startswith(_COMPRESSION_PREFIX)
    if is_compressed:
        data = data[len(_COMPRESSION_PREFIX):]

    if nonce is None:
        nonce = data[: SecretBox.NONCE_SIZE]
        data = data[SecretBox.NONCE_SIZE:]

    try:
        plaintext = box.decrypt(data, nonce=nonce)
    except CryptoError:
        return None

    if is_compressed:
        plaintext = _inflate(plaintext, -MAX_WBITS)

    return plaintext, nonce


def decrypt_keystore(keystore_response: dict[str, Any], encryption_key: bytes) -> dict[str, bytes]:
    """
    GET /user/keystore のレスポンスを復号し、{meta: 個別アイテム鍵} の dict を返す。
    """
    raw = keystore_response.get("keystore")
    if not raw:
        return {}

    envelope = loads(b64decode(raw).decode())
    nonce = bytes(envelope["nonce"])
    sdata = bytes(envelope["sdata"])

    result = _decrypt_data(sdata, encryption_key, nonce=nonce)
    if result is None:
        raise ValueError("keystore の復号に失敗しました（鍵導出が一致していない可能性があります）")

    plaintext, _ = result
    keys = loads(plaintext)["keys"]
    return {meta: bytes(raw_key) for meta, raw_key in keys.items()}


def decrypt_object(item: dict[str, Any], keystore: dict[str, bytes]) -> Any:
    """
    /user/objects/{type} の1アイテムを復号し、中身のJSONを返す。
    """
    meta = item["meta"]
    if meta not in keystore:
        raise KeyError(f"keystore に meta={meta!r} の鍵が見つかりません")

    result = _decrypt_data(b64decode(item["data"]), keystore[meta])
    if result is None:
        raise ValueError(f"object(id={item.get('id')}) の復号に失敗しました")

    plaintext, _ = result
    return loads(plaintext)
