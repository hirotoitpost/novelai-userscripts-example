"""APP_MODE=multi のログインとセッション(docs/multi_user_plan_jp.md のステップ 2)。"""

from __future__ import annotations

import base64
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, sessions  # noqa: E402
from python.routes import auth  # noqa: E402

EMAIL = "Someone@Example.com"
ACCESS = "eyJ-access-token"
KEY = b"k" * 32


@pytest.fixture()
def multi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"m" * 32).decode())
    logins: list[str] = []

    async def fake_login(email: str, password: str, recaptcha: str) -> str:
        logins.append(email)
        return ACCESS

    monkeypatch.setattr(auth, "login_with_credentials", fake_login)
    monkeypatch.setattr(auth, "get_encryption_key", lambda email, password: KEY)
    return logins


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(sessions.SessionMiddleware)
    app.include_router(auth.router)

    @app.api_route("/api/echo", methods=["GET", "POST"])
    async def echo(request: Request) -> dict[str, Any]:
        session = sessions.current_session(request)
        return {"authorization": request.headers.get("authorization"), "user": session and session.user_id}

    @app.get("/api/keystore")
    async def keystore(request: Request) -> dict[str, Any]:
        token, key = sessions.keystore_credentials(request, None, request.headers.get("authorization"))
        return {"token": token, "key": key.decode()}

    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://localhost:8000")


def _login(client: TestClient, email: str = EMAIL) -> Any:
    return client.post("/api/auth/login", json={"email": email, "password": "pw", "recaptcha": "r"})


def test_single_changes_nothing(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_MODE", raising=False)
    client = _client(app)
    assert client.get("/api/echo", headers={"Authorization": "Bearer mine"}).json() == {
        "authorization": "Bearer mine",
        "user": None,
    }
    assert client.get("/api/auth/session").json()["mode"] == "single"


def test_uninvited_people_cannot_log_in(app: FastAPI, multi: list[str]) -> None:
    client = _client(app)
    assert _login(client).status_code == 403
    assert multi == []  # NovelAI へログインしに行かない
    assert client.get("/api/echo").status_code == 401


def test_login_sets_a_strict_cookie_and_injects_the_users_token(app: FastAPI, multi: list[str]) -> None:
    sessions.add_invite("  someone@example.COM ")  # 大文字小文字・空白は区別しない
    client = _client(app)
    res = _login(client)
    assert res.status_code == 200 and res.json() == {"access_token": "", "mode": "multi"}
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    # ブラウザから来た Authorization は捨て、セッションの持ち主のトークンを差し込む
    body = client.get("/api/echo", headers={"Authorization": "Bearer someone-else"}).json()
    assert body["authorization"] == f"Bearer {ACCESS}" and body["user"] == 1
    assert client.get("/api/auth/session").json() == {
        "mode": "multi",
        "authenticated": True,
        "has_api_token": False,
        "has_encryption_key": True,
    }


def test_secrets_are_not_stored_in_plain_text(app: FastAPI, multi: list[str], tmp_path: Path) -> None:
    sessions.add_invite(EMAIL)
    client = _client(app)
    _login(client)
    client.put("/api/auth/api-token", json={"token": "pst-persistent"})
    dump = "\n".join(sqlite3.connect(tmp_path / "app.db").iterdump()).lower()
    for secret in (EMAIL.lower(), ACCESS.lower(), "pst-persistent", client.cookies[sessions.COOKIE_NAME].lower()):
        assert secret not in dump


def test_persistent_token_is_preferred_but_keystore_uses_the_login_token(app: FastAPI, multi: list[str]) -> None:
    sessions.add_invite(EMAIL)
    client = _client(app)
    _login(client)
    assert client.put("/api/auth/api-token", json={"token": "eyJ-not-persistent"}).status_code == 422
    assert client.put("/api/auth/api-token", json={"token": " pst-persistent "}).status_code == 204
    assert client.get("/api/echo").json()["authorization"] == "Bearer pst-persistent"
    assert client.get("/api/keystore").json() == {"token": ACCESS, "key": KEY.decode()}
    assert client.delete("/api/auth/api-token").status_code == 204
    assert client.get("/api/echo").json()["authorization"] == f"Bearer {ACCESS}"


def test_writes_from_other_sites_are_refused(app: FastAPI, multi: list[str]) -> None:
    sessions.add_invite(EMAIL)
    client = _client(app)
    _login(client)
    assert client.post("/api/echo", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/echo", headers={"Origin": "http://localhost:8000"}).status_code == 200
    # Vite の中継(changeOrigin で Host が変わる)では、ブラウザが開いていた名前(X-Forwarded-Host)で比べる
    headers = {"Origin": "http://novelai.lan:5173", "X-Forwarded-Host": "novelai.lan:5173"}
    assert client.post("/api/echo", headers=headers).status_code == 200


def test_logout_and_revoking_the_invite_end_the_session(app: FastAPI, multi: list[str]) -> None:
    sessions.add_invite(EMAIL)
    client = _client(app)
    _login(client)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/echo").status_code == 401

    _login(client)
    assert client.get("/api/echo").status_code == 200
    assert sessions.remove_invite(EMAIL)
    assert client.get("/api/echo").status_code == 401


def test_expired_sessions_are_rejected(app: FastAPI, multi: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    sessions.add_invite(EMAIL)
    client = _client(app)
    _login(client)
    later = sessions._now() + sessions.timedelta(days=sessions.SESSION_DAYS, seconds=1)
    monkeypatch.setattr(sessions, "_now", lambda: later)
    assert client.get("/api/echo").status_code == 401


def test_multi_needs_a_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.delenv("APP_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError):
        sessions.check_config()
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(RuntimeError):
        sessions.check_config()
    monkeypatch.setenv("APP_MODE", "single")
    sessions.check_config()  # single では要らない


def test_cors_only_allows_listed_origins_in_multi(multi: list[str]) -> None:
    from python import server

    client = TestClient(server.app, base_url="http://localhost:8000")
    preflight = {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    assert "access-control-allow-origin" not in client.options("/api/auth/login", headers=preflight).headers


def test_allowed_origins_may_write(app: FastAPI, multi: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    # 開発中は画面(:5173)からバックエンド(:8000)へ直接送る呼び出しがある
    monkeypatch.setenv("APP_ALLOWED_ORIGINS", "http://localhost:5173/, http://novelai.lan:5173")
    sessions.add_invite(EMAIL)
    client = _client(app)
    _login(client)
    assert client.post("/api/echo", headers={"Origin": "http://localhost:5173"}).status_code == 200
    assert client.post("/api/echo", headers={"Origin": "http://novelai.lan:5173"}).status_code == 200
    assert client.post("/api/echo", headers={"Origin": "http://localhost:3000"}).status_code == 403


def test_single_reads_the_key_from_a_header(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    # 画面は鍵を URL に入れずヘッダーで送る
    monkeypatch.delenv("APP_MODE", raising=False)
    client = _client(app)
    key = base64.b64encode(KEY).decode()
    headers = {"Authorization": "Bearer mine", "X-NAI-Encryption-Key": key}
    assert client.get("/api/keystore", headers=headers).json() == {"token": "mine", "key": KEY.decode()}
    assert client.get("/api/keystore", headers={"Authorization": "Bearer mine"}).status_code == 422
