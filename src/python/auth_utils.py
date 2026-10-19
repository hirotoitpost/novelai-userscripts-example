from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import blake2b

import asyncio

import argon2.low_level
import httpx

from .keystore_crypto import decrypt_keystore

_NOVELAI_API = "https://api.novelai.net"
_IMAGE_API = "https://image.novelai.net"


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


async def verify_token_owner(email: str, password: str, access_token: str) -> bytes:
    """
    reCAPTCHA を使わずに、アクセストークンがそのメールアドレスの持ち主のものかを確かめる(APP_MODE=multi のログイン)。

    このアプリから /user/login を呼んでも、reCAPTCHA の鍵は novelai.net でしか使えないので通らない。代わりに、
    渡されたトークンで NovelAI の keystore を読み、メールアドレス+パスワードから作った鍵で開けるかを見る。
    keystore はその鍵でしか開けないので、開ければトークン・メールアドレス・パスワードがそろって本人のもの。
    確かめられたら keystore の復号鍵を返す(セッションに持つ)。確かめられなければ ValueError(理由つき)。
    """
    if access_token.startswith("pst-"):
        raise ValueError(
            "永続トークン(pst-)では確かめられません(keystore を読めないため)。novelai.net にログインしたときの"
            "アクセストークンを貼ってください。永続トークンはログインの後にホームで登録できます"
        )
    key = await asyncio.to_thread(get_encryption_key, email, password)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{_IMAGE_API}/user/keystore", headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code == 401:
        raise ValueError("NovelAI がトークンを受け付けません(期限切れか、貼り間違い)")
    if resp.status_code != 200:
        raise ValueError(f"NovelAI の keystore を読めませんでした ({resp.status_code})")
    body = resp.json()
    if not body.get("keystore"):
        # 空の keystore では、パスワードと合うかを確かめられない
        raise ValueError(
            "このアカウントにはまだ keystore がないので、本人かを確かめられません。novelai.net で物語を 1 つ"
            "保存してから、もう一度ログインしてください"
        )
    try:
        decrypt_keystore(body, key)
    except ValueError:
        raise ValueError("メールアドレスかパスワードが、このトークンのアカウントと合いません") from None
    return key
