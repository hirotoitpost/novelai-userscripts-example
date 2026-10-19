"""
複数ユーザー(APP_MODE=multi)のログインとセッション。docs/multi_user_plan_jp.md のステップ 2。

- 使えるのは招待した人だけ(scripts/manage_users.py で招待する)。
- 利用者はログインのメールアドレスで見分ける。メールアドレスそのものは保存せず、マスター鍵から作った鍵の
  HMAC(email_hash)だけを持つ。
- NovelAI のトークン(ログインで得るアクセストークン、画面で登録する永続トークン)と keystore の復号鍵は、
  ブラウザに置かず、利用者ごとのデータ鍵で暗号化して DB に持つ。データ鍵はマスター鍵(.env の APP_MASTER_KEY。
  ホスティングでは Secret Manager)で包んで users.wrapped_key に置く。
- ブラウザにはセッションの合言葉だけを HttpOnly・SameSite=Strict の Cookie で渡す。DB にはその SHA-256 を置く。
- /api/ へのリクエストは SessionMiddleware がセッションを確かめ、その人の NovelAI トークンを Authorization
  ヘッダーとして差し込む(ブラウザから来た Authorization は捨てる)。今ある各ルーターは、そのまま
  Authorization を読めば利用者本人のトークンで動く。データはその人の DB(data/users/{id}/app.db)を開く(ステップ 3)。

single(既定)では何もしない(今の動きのまま)。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
from base64 import b64decode
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from nacl.secret import SecretBox

from . import db
from .client import app_mode

COOKIE_NAME = "nai_session"
SESSION_DAYS = 30  # NovelAI のアクセストークンの有効期限(30 日)に合わせる

# セッションなしで通す /api/ の道(ログインの前に要るもの)
_PUBLIC_PATHS = frozenset({"/api/auth/login", "/api/auth/session", "/api/auth/logout", "/api/lan-ca.crt"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class NotInvited(Exception):
    """招待されていないメールアドレスでログインしようとした。"""


@dataclass(frozen=True)
class Session:
    user_id: int
    access_token: str  # ログインで得た NovelAI のアクセストークン(keystore を読むのに要る)
    api_token: str | None  # 画面で登録した永続トークン(pst-...)。期限が切れない
    encryption_key: bytes | None  # keystore の復号鍵(ログイン中だけ持つ)
    is_admin: bool = False  # 開発管理者のページを使える(scripts/manage_users.py admin)

    @property
    def novelai_token(self) -> str:
        """NovelAI を呼ぶときのトークン。永続トークンがあればそれ(期限切れの 401 が起きない)。"""
        return self.api_token or self.access_token


# ---- 鍵 ----


def _master_key() -> bytes:
    raw = os.environ.get("APP_MASTER_KEY", "").strip()
    if not raw:
        raise RuntimeError(
            "APP_MODE=multi には .env の APP_MASTER_KEY(32 バイトを base64 にしたもの)が要ります。"
            " 作り方: python -c \"import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
    try:
        key = b64decode(raw, validate=True)
    except ValueError as exc:
        raise RuntimeError("APP_MASTER_KEY が base64 ではありません") from exc
    if len(key) != 32:
        raise RuntimeError(f"APP_MASTER_KEY は 32 バイトです(今は {len(key)} バイト)")
    return key


def _subkey(purpose: bytes) -> bytes:
    """マスター鍵から用途ごとの鍵を作る(1 つの鍵を別々の用途に使い回さない)。"""
    return hashlib.blake2b(b"", key=_master_key(), person=purpose, digest_size=32).digest()


def allowed_origins() -> list[str]:
    """
    multi で、ほかの配信元から呼んでよいもの(.env の APP_ALLOWED_ORIGINS。カンマ区切り)。CORS と、書き込みの
    Origin の確認の両方で使う。開発中は画面(:5173)からバックエンド(:8000)へ直接送る呼び出しがあるので、
    http://localhost:5173 などを書く。
    """
    return [o.strip().rstrip("/") for o in os.environ.get("APP_ALLOWED_ORIGINS", "").split(",") if o.strip()]


def check_config() -> None:
    """起動時に呼ぶ。multi でマスター鍵がなければ止める。"""
    if app_mode() == "multi":
        _master_key()


def email_hash(email: str) -> str:
    """メールアドレスから利用者を見分ける値。大文字小文字と前後の空白は区別しない。"""
    normalized = email.strip().lower().encode()
    return hmac.new(_subkey(b"nai-email"), normalized, hashlib.sha256).hexdigest()


def _wrap(data_key: bytes) -> bytes:
    return bytes(SecretBox(_subkey(b"nai-wrap")).encrypt(data_key))


def _unwrap(wrapped: bytes) -> bytes:
    return SecretBox(_subkey(b"nai-wrap")).decrypt(wrapped)


def _encrypt(data_key: bytes, plain: bytes) -> bytes:
    return bytes(SecretBox(data_key).encrypt(plain))


def _decrypt(data_key: bytes, cipher: bytes) -> bytes:
    return SecretBox(data_key).decrypt(cipher)


def _session_id(cookie: str) -> str:
    return hashlib.sha256(cookie.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = db.get_system_connection()  # 招待・利用者・セッションは全体の DB
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---- 招待 ----


def add_invite(email: str, note: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO user_invites (email_hash, note, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT(email_hash) DO UPDATE SET note = excluded.note",
            (email_hash(email), note, _now().isoformat()),
        )


def set_admin(email: str, admin: bool) -> bool:
    """管理者にする・外す。招待されていなければ False。"""
    with _conn() as conn:
        return (
            conn.execute(
                "UPDATE user_invites SET is_admin = ? WHERE email_hash = ?", (int(admin), email_hash(email))
            ).rowcount
            > 0
        )


def remove_invite(email: str) -> bool:
    """招待を取り消す。その人のセッションも消す(利用者のデータは残す)。"""
    h = email_hash(email)
    with _conn() as conn:
        removed = conn.execute("DELETE FROM user_invites WHERE email_hash = ?", (h,)).rowcount > 0
        conn.execute(
            "DELETE FROM user_sessions WHERE user_id IN (SELECT id FROM users WHERE email_hash = ?)", (h,)
        )
    return removed


def list_invites() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT i.email_hash, i.note, i.created_at, i.is_admin, u.id AS user_id, u.last_login_at"
            " FROM user_invites i LEFT JOIN users u ON u.email_hash = i.email_hash ORDER BY i.created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def is_invited(email: str) -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM user_invites WHERE email_hash = ?", (email_hash(email),)).fetchone()
    return row is not None


# ---- セッション ----


def create_session(email: str, access_token: str, encryption_key: bytes | None) -> str:
    """
    NovelAI へのログインが通った後に呼ぶ。利用者がいなければ作り、セッションを作って Cookie の値を返す。
    招待されていなければ NotInvited。
    """
    h = email_hash(email)
    now = _now()
    cookie = secrets.token_urlsafe(32)
    with _conn() as conn:
        if conn.execute("SELECT 1 FROM user_invites WHERE email_hash = ?", (h,)).fetchone() is None:
            raise NotInvited
        row = conn.execute("SELECT id, wrapped_key FROM users WHERE email_hash = ?", (h,)).fetchone()
        if row is None:
            data_key = secrets.token_bytes(SecretBox.KEY_SIZE)
            user_id = conn.execute(
                "INSERT INTO users (email_hash, wrapped_key, created_at, last_login_at) VALUES (?, ?, ?, ?)",
                (h, _wrap(data_key), now.isoformat(), now.isoformat()),
            ).lastrowid
        else:
            user_id, data_key = row["id"], _unwrap(row["wrapped_key"])
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now.isoformat(), user_id))
        conn.execute("DELETE FROM user_sessions WHERE expires_at <= ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO user_sessions (id, user_id, access_token, encryption_key, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                _session_id(cookie),
                user_id,
                _encrypt(data_key, access_token.encode()),
                _encrypt(data_key, encryption_key) if encryption_key is not None else None,
                now.isoformat(),
                (now + timedelta(days=SESSION_DAYS)).isoformat(),
            ),
        )
    return cookie


def user_id_of(email: str) -> int | None:
    """招待されていて、一度ログインした利用者の ID(MCP サーバーなど、サーバーの PC の中で使う)。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT u.id FROM users u JOIN user_invites i ON i.email_hash = u.email_hash WHERE u.email_hash = ?",
            (email_hash(email),),
        ).fetchone()
    return int(row["id"]) if row else None


def api_token_of(user_id: int) -> str | None:
    """その人が画面で登録した永続トークン(無ければ None)。"""
    with _conn() as conn:
        row = conn.execute("SELECT wrapped_key, novelai_api_token FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not row["novelai_api_token"]:
        return None
    return _decrypt(_unwrap(row["wrapped_key"]), row["novelai_api_token"]).decode()


def create_service_session(user_id: int, access_token: str, *, days: int = 1) -> str:
    """
    サーバーの PC の中のプロセス(MCP サーバー)が、その人としてバックエンドを呼ぶためのセッション。ログインの画面を
    通らないので、keystore の鍵は持たない。期限は短くする(切れたら作り直す)。Cookie の値を返す。
    """
    now = _now()
    cookie = secrets.token_urlsafe(32)
    with _conn() as conn:
        row = conn.execute("SELECT wrapped_key FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(user_id)
        conn.execute(
            "INSERT INTO user_sessions (id, user_id, access_token, encryption_key, created_at, expires_at)"
            " VALUES (?, ?, ?, NULL, ?, ?)",
            (
                _session_id(cookie),
                user_id,
                _encrypt(_unwrap(row["wrapped_key"]), access_token.encode()),
                now.isoformat(),
                (now + timedelta(days=days)).isoformat(),
            ),
        )
    return cookie


def get_session(cookie: str | None) -> Session | None:
    """Cookie の値からセッションを引く。ない・期限切れ・招待を取り消された なら None。"""
    if not cookie:
        return None
    sid = _session_id(cookie)
    with _conn() as conn:
        row = conn.execute(
            "SELECT s.user_id, s.access_token, s.encryption_key, s.expires_at, u.wrapped_key,"
            " u.novelai_api_token, (i.email_hash IS NOT NULL) AS invited, COALESCE(i.is_admin, 0) AS is_admin"
            " FROM user_sessions s JOIN users u ON u.id = s.user_id"
            " LEFT JOIN user_invites i ON i.email_hash = u.email_hash WHERE s.id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= _now().isoformat() or not row["invited"]:
            conn.execute("DELETE FROM user_sessions WHERE id = ?", (sid,))
            return None
    data_key = _unwrap(row["wrapped_key"])
    return Session(
        user_id=row["user_id"],
        access_token=_decrypt(data_key, row["access_token"]).decode(),
        api_token=_decrypt(data_key, row["novelai_api_token"]).decode() if row["novelai_api_token"] else None,
        encryption_key=_decrypt(data_key, row["encryption_key"]) if row["encryption_key"] else None,
        is_admin=bool(row["is_admin"]),
    )


def delete_session(cookie: str | None) -> None:
    if not cookie:
        return
    with _conn() as conn:
        conn.execute("DELETE FROM user_sessions WHERE id = ?", (_session_id(cookie),))


def set_api_token(user_id: int, token: str | None) -> None:
    """画面で登録した永続トークンを保存する(None で消す)。"""
    with _conn() as conn:
        row = conn.execute("SELECT wrapped_key FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(user_id)
        cipher = _encrypt(_unwrap(row["wrapped_key"]), token.encode()) if token else None
        conn.execute("UPDATE users SET novelai_api_token = ? WHERE id = ?", (cipher, user_id))


# ---- 前のデータ(single のもの)を利用者に渡す ----


class AdoptError(Exception):
    """前のデータを渡せない(利用者がまだいない・もう渡してある など)。"""


# 利用者のファイルを置くフォルダ(プロジェクトのフォルダからのパス。db.user_root の下に同じ形で置く)
LEGACY_FILE_DIRS = ("outputs", "data/imports", "data/stamps")


def adopt_legacy_data(email: str, *, force: bool = False, files_only: bool = False) -> int:
    """
    single で使っていたデータを、その利用者のデータとしてコピーする(ステップ 3)。
    - DB(data/app.db)とごみ箱(data/trash): その人の DB があれば、force でなければ断る(上書きすると、その人が
      multi で作ったものが消える)。files_only なら DB は写さない(DB を先に写したあと、ファイルだけ写すとき)
    - ファイル(LEGACY_FILE_DIRS): その人のフォルダの同じ場所へ(同じ名前のファイルは上書き)
    元のデータは消さない(single に戻せば今までどおり使える)。その人が一度ログインして、利用者ができている必要が
    ある。渡した利用者の ID を返す。
    """
    with _conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE email_hash = ?", (email_hash(email),)).fetchone()
    if row is None:
        raise AdoptError("その人はまだログインしていません(一度ログインすると利用者ができます)")
    user_id = int(row["id"])
    target = db.user_db_path(user_id)
    if not files_only:
        if target.exists() and not force:
            raise AdoptError(f"その人のデータはもうあります({target})。上書きするなら --force")
        _copy_legacy_db(target)
    project = db._PROJECT_ROOT
    for rel in LEGACY_FILE_DIRS:
        source = project / rel
        if source.is_dir():
            shutil.copytree(source, target.parent / rel, dirs_exist_ok=True)
    return user_id


def _copy_legacy_db(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # 書き込み中でもそろった状態で写せるよう、sqlite のバックアップの仕組みでコピーする。ファイルを置き換えず
    # 中身を書き換えるので、バックエンドがその人の DB を開いていても上書きできる(Windows はファイルを置き換えられない)
    source = sqlite3.connect(db._DB_PATH)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
        # 招待・利用者・セッションは全体の DB だけに置く(写しからは消す)
        for table in ("user_sessions", "users", "user_invites"):
            copy.execute(f"DELETE FROM {table}")  # noqa: S608 決まった名前だけ
        copy.commit()
    finally:
        copy.close()
        source.close()
    legacy_trash = db._DB_PATH.parent / "trash"
    if legacy_trash.is_dir():
        shutil.copytree(legacy_trash, target.parent / "trash", dirs_exist_ok=True)


# ---- ミドルウェア ----


def _cookie_from(headers: list[tuple[bytes, bytes]]) -> str | None:
    for name, value in headers:
        if name == b"cookie":
            for part in value.decode("latin-1").split(";"):
                key, _, val = part.strip().partition("=")
                if key == COOKIE_NAME:
                    return val
    return None


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key == name:
            return value.decode("latin-1")
    return None


async def _reply(send: Any, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SessionMiddleware:
    """
    multi のとき、/api/ へのリクエストにログインのセッションを求める(ログインの道は除く)。
    - Cookie がない・無効なら 401。
    - 書き込みのリクエスト(GET 以外)で Origin がこのサーバーとも APP_ALLOWED_ORIGINS とも違えば 403
      (よそのサイトからの操作を防ぐ)。
    - ブラウザから来た Authorization は捨て、その人の NovelAI トークンを差し込む。
      セッションは request.state.session で読める。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or app_mode() != "multi":
            await self.app(scope, receive, send)
            return
        headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
        method = scope.get("method", "GET")
        if method not in _SAFE_METHODS:
            origin = _header(headers, b"origin")
            host = _header(headers, b"x-forwarded-host") or _header(headers, b"host") or ""
            if (
                origin
                and urlsplit(origin).netloc.lower() != host.lower()
                and origin.rstrip("/") not in allowed_origins()
            ):
                await _reply(send, 403, "ほかのサイトからの操作は受け付けません")
                return
        if scope["path"] in _PUBLIC_PATHS or method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        session = get_session(_cookie_from(headers))
        if session is None:
            await _reply(send, 401, "ログインしてください")
            return
        headers = [(k, v) for k, v in headers if k != b"authorization"]
        headers.append((b"authorization", f"Bearer {session.novelai_token}".encode()))
        scope = {**scope, "headers": headers, "state": {**scope.get("state", {}), "session": session}}
        # このリクエスト(と、そこから切り離した処理)では、その人の DB を開く(db.get_connection)
        with db.as_user(session.user_id):
            await self.app(scope, receive, send)


def current_session(request: Request) -> Session | None:
    """ミドルウェアが付けたセッション(single では None)。"""
    return getattr(request.state, "session", None)


def require_admin(request: Request) -> None:
    """
    multi では管理者だけ(開発管理者のページ・コンテンツガードの設定の変更。ステップ 4)。single は何もしない
    (開発管理者のページは LAN の中なら誰でも。今までどおり)。
    """
    if app_mode() != "multi":
        return
    session = current_session(request)
    if session is None or not session.is_admin:
        raise HTTPException(status_code=403, detail="管理者だけが使えます。")


def keystore_credentials(request: Request, encryption_key: str | None, authorization: str | None) -> tuple[str, bytes]:
    """
    keystore を読む 2 つの機能(公式のプロンプトのチャンクの取り込み・公式サイトの物語の一覧)で使う
    トークンと復号鍵。multi ではセッションに持っている(永続トークンでは keystore を読めないので、
    ログインで得たアクセストークンを使う)。single では今までどおり Authorization と画面から渡された鍵
    (X-NAI-Encryption-Key ヘッダー。前の画面のクエリ encryption_key も受け付ける)。
    """
    session = current_session(request)
    if session is not None:
        if session.encryption_key is None:
            raise HTTPException(status_code=401, detail="もう一度ログインしてください(復号の鍵がありません)")
        return session.access_token, session.encryption_key
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization ヘッダーが必要です")
    # 画面は鍵を URL に入れずヘッダーで送る(アクセスログなどに残らないように)。クエリは前の画面のために残す
    encryption_key = encryption_key or request.headers.get("x-nai-encryption-key")
    if not encryption_key:
        raise HTTPException(status_code=422, detail="encryption_key が必要です")
    try:
        key = b64decode(encryption_key)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"encryption_key が不正です: {exc}")
    return authorization[7:], key
