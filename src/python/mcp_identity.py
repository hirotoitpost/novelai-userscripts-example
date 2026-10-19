"""
MCP サーバー(mcp_server.py)を、どの利用者として動かすか。docs/multi_user_plan_jp.md のステップ 5。

MCP サーバーはサーバーの PC の中で、MCP クライアント(Claude Code など)から stdio で起動される別のプロセス。
- single: 今までどおり。データは data/app.db と outputs/、NovelAI は .env の NOVELAI_API_TOKEN。
- multi: .env の APP_MCP_USER(メールアドレス)の利用者として動く。その人の DB とフォルダを開き、NovelAI はその人が
  ホームで登録した永続トークンで呼ぶ(運営者の .env のトークンは使わない)。漫画のツールがバックエンドを呼ぶときは、
  その人の短い期限のセッション(sessions.create_service_session)の Cookie を付ける。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import db, sessions
from .client import app_mode


class McpUserError(RuntimeError):
    """multi で、MCP サーバーをどの利用者として動かすかが決まらない。"""


@dataclass
class _Identity:
    user_id: int
    cookie: str | None = None


_identity: _Identity | None = None


def setup() -> None:
    """MCP サーバーの起動時に呼ぶ。multi では利用者を決めて、このプロセスで開く DB とフォルダをその人のものにする。"""
    global _identity
    if app_mode() != "multi":
        _identity = None
        return
    sessions.check_config()
    email = os.environ.get("APP_MCP_USER", "").strip()
    if not email:
        raise McpUserError(
            "APP_MODE=multi では、MCP サーバーをどの利用者として動かすかを .env の APP_MCP_USER(メールアドレス)に書いてください"
        )
    user_id = sessions.user_id_of(email)
    if user_id is None:
        raise McpUserError(
            f"APP_MCP_USER の人({email})は、招待されていないか、まだ一度もログインしていません"
        )
    _identity = _Identity(user_id)
    # このプロセスのツール(切り離したタスク・スレッドも)は、その人の DB とフォルダを開く
    db._current_user.set(user_id)


def user_id() -> int | None:
    return _identity.user_id if _identity else None


def novelai_token() -> str:
    """NovelAI を呼ぶトークン。multi はその人の永続トークン、single は .env のもの。"""
    if _identity is not None:
        token = sessions.api_token_of(_identity.user_id)
        if not token:
            raise McpUserError("APP_MCP_USER の人は、ホームで NovelAI の永続 API トークンを登録してください")
        return token
    token = os.environ.get("NOVELAI_API_TOKEN") or os.environ.get("NOVELAI_API_KEY")
    if not token:
        raise RuntimeError("NOVELAI_API_TOKEN が .env に設定されていません")
    return token


def backend_cookies(*, renew: bool = False) -> dict[str, str]:
    """バックエンドを呼ぶときの Cookie(multi だけ)。renew で作り直す(期限が切れて 401 になったとき)。"""
    if _identity is None:
        return {}
    if _identity.cookie is None or renew:
        _identity.cookie = sessions.create_service_session(_identity.user_id, novelai_token())
    return {sessions.COOKIE_NAME: _identity.cookie}
