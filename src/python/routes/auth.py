from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from .. import sessions
from ..auth_utils import get_encryption_key, login_with_credentials, verify_token_owner
from ..client import app_mode, operator_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str
    recaptcha: str = ""
    # multi: novelai.net で取ったアクセストークン。あれば reCAPTCHA の代わりに keystore で本人かを確かめる
    access_token: str | None = None


class LoginResponse(BaseModel):
    # single: NovelAI のアクセストークン(画面が持つ)。multi: 空(トークンはサーバーが持ち、画面には Cookie だけ渡す)
    access_token: str
    mode: str = "single"


class SessionResponse(BaseModel):
    mode: str
    # multi: ログインのセッションがあるか。single: 画面がトークンを持つので常に False
    authenticated: bool
    # NovelAI の永続トークンがあるか(multi: 本人が登録したもの。single: .env のもの)
    has_api_token: bool
    # multi: keystore の復号鍵をセッションに持っているか(公式のチャンク・物語の一覧に要る)
    has_encryption_key: bool = False
    # multi: 開発管理者のページを使えるか。single: LAN の中なら誰でも使えるので True
    is_admin: bool = False


class ApiTokenRequest(BaseModel):
    token: str


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request, response: Response) -> LoginResponse:
    multi = app_mode() == "multi"
    # 招待されていない人のために NovelAI へログインしない(reCAPTCHA もパスワードも使わずに断る)
    if multi and not sessions.is_invited(req.email):
        raise HTTPException(status_code=403, detail="このメールアドレスは招待されていません")
    if multi and req.access_token and req.access_token.strip():
        token = req.access_token.strip().strip("\"'")
        try:
            key = await verify_token_owner(req.email, req.password, token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        return _start_session(req.email, token, key, request, response)
    try:
        token = await login_with_credentials(req.email, req.password, req.recaptcha)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")
    if not multi:
        return LoginResponse(access_token=token)
    # keystore の復号鍵はここで作ってセッションに持つ(あとでパスワードを入れ直さなくてよいように)
    key = await asyncio.to_thread(get_encryption_key, req.email, req.password)
    return _start_session(req.email, token, key, request, response)


def _start_session(email: str, token: str, key: bytes, request: Request, response: Response) -> LoginResponse:
    try:
        cookie = sessions.create_session(email, token, key)
    except sessions.NotInvited:
        raise HTTPException(status_code=403, detail="このメールアドレスは招待されていません")
    response.set_cookie(
        sessions.COOKIE_NAME,
        cookie,
        max_age=sessions.SESSION_DAYS * 24 * 3600,
        httponly=True,
        secure=_secure(request),
        samesite="strict",
        path="/",
    )
    return LoginResponse(access_token="", mode="multi")


@router.get("/session", response_model=SessionResponse)
async def get_session(request: Request) -> SessionResponse:
    """画面を開いたときに、運営形態とログインの状態を確かめる。"""
    if app_mode() != "multi":
        return SessionResponse(
            mode="single", authenticated=False, has_api_token=operator_token() is not None, is_admin=True
        )
    session = sessions.get_session(request.cookies.get(sessions.COOKIE_NAME))
    return SessionResponse(
        mode="multi",
        authenticated=session is not None,
        has_api_token=session is not None and session.api_token is not None,
        has_encryption_key=session is not None and session.encryption_key is not None,
        is_admin=session is not None and session.is_admin,
    )


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    sessions.delete_session(request.cookies.get(sessions.COOKIE_NAME))
    response.delete_cookie(sessions.COOKIE_NAME, path="/", httponly=True, secure=_secure(request), samesite="strict")


def _require_session(request: Request) -> sessions.Session:
    session = sessions.current_session(request)
    if session is None:
        raise HTTPException(status_code=404, detail="APP_MODE=multi のときだけ使えます")
    return session


@router.put("/api-token", status_code=204)
async def put_api_token(req: ApiTokenRequest, request: Request) -> None:
    """NovelAI の永続トークン(pst-...)を登録する。登録すると、ログインのトークンの期限が切れても続けられる。"""
    session = _require_session(request)
    token = req.token.strip()
    if not token.startswith("pst-"):
        raise HTTPException(status_code=422, detail="永続トークン(pst- で始まるもの)を入れてください")
    sessions.set_api_token(session.user_id, token)


@router.delete("/api-token", status_code=204)
async def delete_api_token(request: Request) -> None:
    sessions.set_api_token(_require_session(request).user_id, None)
