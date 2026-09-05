from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import blake2b

import argon2.low_level
import httpx

_NOVELAI_API = "https://api.novelai.net"


def _argon_hash(email: str, password: str, size: int, domain: str) -> str:
    pre_salt = (password[:6] + email + domain).encode()
    salt = blake2b(pre_salt, digest_size=16).digest()
    raw = argon2.low_level.hash_secret_raw(
        secret=password.encode(),
        salt=salt,
        time_cost=2,
        memory_cost=int(2_000_000 / 1024),
        parallelism=1,
        hash_len=size,
        type=argon2.low_level.Type.ID,
    )
    return urlsafe_b64encode(raw).decode()


def get_access_key(email: str, password: str) -> str:
    return _argon_hash(email, password, 64, "novelai_data_access_key")[:64]


def get_encryption_key(email: str, password: str) -> bytes:
    """
    keystore/objects の復号に使う鍵（ログイン用 access_key とは別物）。
    argon2id の出力(128byte, base64, パディング除去) を blake2b(32byte) にかけたものが鍵になる。
    """
    pre_key = _argon_hash(email, password, 128, "novelai_data_encryption_key").replace("=", "")
    return blake2b(pre_key.encode(), digest_size=32).digest()


async def login_with_credentials(email: str, password: str, recaptcha: str) -> str:
    """
    NovelAI の /user/login へ access key を送り accessToken を返す。
    key derivation: blake2b(password[:6] + email + domain) → argon2id → base64

    /user/login は reCAPTCHA トークンが必須。フロントエンドで実行して取得したものを
    そのまま中継する（このバックエンドではチャレンジを解けないため）。
    """
    access_key = get_access_key(email, password)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_NOVELAI_API}/user/login",
            json={"key": access_key, "recaptcha": recaptcha},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    if resp.status_code != 201:
        try:
            detail = resp.json().get("message", resp.text)
        except Exception:
            detail = resp.text
        raise ValueError(f"Login failed ({resp.status_code}): {detail}")
    return resp.json()["accessToken"]
