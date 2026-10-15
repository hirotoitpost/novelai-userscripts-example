"""
コンテンツガードを緩めるときのパスワード。

- 守りを強める変更・はじめの値に戻すことにはパスワードは要らない。緩める変更(止める語を減らす、年齢区分が未定義の
  キャラの扱いを変える、未成年と定義したキャラを外す、ガードプロファイルを消す・減らす など)だけに要る。
- パスワードそのものは保存しない。塩と scrypt の値を DB(guard_secret)に置く。.env には置かない(管理ページから
  .env を書き換えられるので、そこから差し替えられてしまう)。
- パスワードがまだ無いあいだは、緩める変更はできない(止める側には働くので、設定を後回しにしても安全側)。
- 一度パスワードを入れると、UNLOCK_SECONDS のあいだ使える合言葉(トークン)を返す。緩める API は、それを
  X-Guard-Token ヘッダーで受け取る。
- 何度も間違えたら、しばらく受け付けない。
- 忘れたときは、この PC で scripts/reset_guard_password.py を実行して消し、設定し直す(画面からは消せない)。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timezone

from fastapi import Header, HTTPException

from .db import get_connection

MIN_LENGTH = 8
UNLOCK_SECONDS = 10 * 60
# この回数続けて間違えたら、LOCKOUT_SECONDS のあいだ受け付けない
MAX_FAILURES = 5
LOCKOUT_SECONDS = 5 * 60
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

_lock = threading.Lock()
_tokens: dict[str, float] = {}
_failures = 0
_locked_until = 0.0


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT).hex()


def _stored() -> tuple[bytes, str] | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT salt, hash FROM guard_secret WHERE id = 1").fetchone()
    finally:
        conn.close()
    return (bytes.fromhex(row["salt"]), row["hash"]) if row else None


def _save(password: str) -> None:
    salt = secrets.token_bytes(16)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO guard_secret (id, salt, hash, updated_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET salt = excluded.salt, hash = excluded.hash, updated_at = excluded.updated_at",
            (salt.hex(), _hash(password, salt), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _check_length(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise HTTPException(status_code=422, detail=f"パスワードは {MIN_LENGTH} 文字以上にしてください。")


def is_set() -> bool:
    return _stored() is not None


def setup(password: str) -> None:
    """はじめの設定。もう設定してあれば 409(変えるときは change)。"""
    with _lock:
        if is_set():
            raise HTTPException(status_code=409, detail="パスワードはもう設定してあります。変えるときは今のパスワードが要ります。")
        _check_length(password)
        _save(password)


def _verify(password: str) -> bool:
    """パスワードが合っているか。続けて間違えたら、しばらく受け付けない(429)。"""
    global _failures, _locked_until
    now = time.monotonic()
    if now < _locked_until:
        raise HTTPException(status_code=429, detail=f"パスワードを続けて間違えたので、{int(_locked_until - now) + 1} 秒待ってください。")
    stored = _stored()
    if stored is None:
        raise HTTPException(status_code=409, detail="パスワードがまだ設定されていません。管理ページで設定してください。")
    salt, expected = stored
    if hmac.compare_digest(_hash(password, salt), expected):
        _failures = 0
        return True
    _failures += 1
    if _failures >= MAX_FAILURES:
        _failures = 0
        _locked_until = now + LOCKOUT_SECONDS
    return False


def change(current: str, new: str) -> None:
    with _lock:
        if not _verify(current):
            raise HTTPException(status_code=403, detail="今のパスワードが違います。")
        _check_length(new)
        _save(new)
        # 古いパスワードで開けた合言葉は使えなくする
        _tokens.clear()


def unlock(password: str) -> tuple[str, int]:
    """パスワードが合えば、緩める変更に使う合言葉と、使える秒数。"""
    with _lock:
        if not _verify(password):
            raise HTTPException(status_code=403, detail="パスワードが違います。")
        now = time.monotonic()
        for token, until in list(_tokens.items()):
            if until <= now:
                del _tokens[token]
        token = secrets.token_urlsafe(24)
        _tokens[token] = now + UNLOCK_SECONDS
        return token, UNLOCK_SECONDS


def token_valid(token: str | None) -> bool:
    if not token:
        return False
    with _lock:
        until = _tokens.get(token)
        return until is not None and until > time.monotonic()


def require_unlocked(token: str | None) -> None:
    """緩める変更の前に呼ぶ。合言葉が無いか切れていれば 403(画面はこれを見てパスワードを聞く)。"""
    if not token_valid(token):
        detail = (
            "守りを緩める変更には、管理ページで設定したパスワードが要ります。"
            if is_set()
            else "守りを緩める変更には、先に管理ページでパスワードを設定してください。"
        )
        raise HTTPException(status_code=403, detail={"code": "guard_locked", "message": detail, "password_set": is_set()})


def guard_token(x_guard_token: str | None = Header(default=None)) -> str | None:
    """API の引数で X-Guard-Token ヘッダーを受け取るためのもの。"""
    return x_guard_token


def clear() -> None:
    """パスワードを消す(この PC で scripts/reset_guard_password.py から使う。画面からは呼べない)。"""
    with _lock:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM guard_secret")
            conn.commit()
        finally:
            conn.close()
        _tokens.clear()


def reset_state() -> None:
    """テスト用: 合言葉と間違えた回数を忘れる。"""
    global _failures, _locked_until
    with _lock:
        _tokens.clear()
        _failures = 0
        _locked_until = 0.0
