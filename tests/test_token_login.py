"""APP_MODE=multi のログイン: reCAPTCHA の代わりに、アクセストークンで keystore を開けるかで本人かを確かめる。"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nacl.secret import SecretBox
from nacl.utils import random

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import auth_utils, db, sessions  # noqa: E402
from python.routes import auth  # noqa: E402

EMAIL, PASSWORD, TOKEN = "owner@example.com", "correct horse", "eyJ-real-access-token"


def _keystore_for(email: str, password: str) -> dict[str, Any]:
    """NovelAI の GET /user/keystore と同じ形(メール+パスワードの鍵で暗号化した keystore)。"""
    key = auth_utils.get_encryption_key(email, password)
    nonce = random(SecretBox.NONCE_SIZE)
    sdata = SecretBox(key).encrypt(json.dumps({"keys": {"meta-1": list(b"x" * 32)}}).encode(), nonce).ciphertext
    envelope = {"nonce": list(nonce), "sdata": list(sdata)}
    return {"keystore": base64.b64encode(json.dumps(envelope).encode()).decode()}


@pytest.fixture()
def novelai(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """NovelAI の keystore の代わり。state["keystore"]・state["status"] で答えを変える。"""
    state: dict[str, Any] = {"keystore": _keystore_for(EMAIL, PASSWORD), "status": 200, "calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request.headers["authorization"])
        if request.headers["authorization"] != f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(state["status"], json=state["keystore"])

    real = httpx.AsyncClient
    monkeypatch.setattr(auth_utils.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def _verify(email: str = EMAIL, password: str = PASSWORD, token: str = TOKEN) -> bytes:
    return asyncio.run(auth_utils.verify_token_owner(email, password, token))


def test_the_owner_is_verified(novelai: dict[str, Any]) -> None:
    assert _verify() == auth_utils.get_encryption_key(EMAIL, PASSWORD)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"password": "wrong"}, "合いません"),
        ({"email": "someone-else@example.com"}, "合いません"),
        ({"token": "eyJ-other"}, "受け付けません"),
        ({"token": "pst-persistent"}, "永続トークン"),
    ],
)
def test_others_are_refused(novelai: dict[str, Any], kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _verify(**kwargs)


def test_an_empty_keystore_cannot_prove_anything(novelai: dict[str, Any]) -> None:
    novelai["keystore"] = {"keystore": ""}
    with pytest.raises(ValueError, match="keystore がない"):
        _verify()


def test_login_route_uses_the_token_instead_of_recaptcha(
    novelai: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"m" * 32).decode())

    async def no_recaptcha_login(*args: Any) -> str:
        raise AssertionError("トークンがあるときは /user/login を呼ばない")

    monkeypatch.setattr(auth, "login_with_credentials", no_recaptcha_login)
    app = FastAPI()
    app.add_middleware(sessions.SessionMiddleware)
    app.include_router(auth.router)
    client = TestClient(app, base_url="http://localhost:8000")
    sessions.add_invite(EMAIL)

    bad = client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong", "access_token": TOKEN})
    assert bad.status_code == 401 and "合いません" in bad.json()["detail"]
    ok = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD, "access_token": f' "{TOKEN}" '})
    assert ok.status_code == 200 and ok.json()["mode"] == "multi"
    assert client.get("/api/auth/session").json()["has_encryption_key"] is True
    session = sessions.get_session(client.cookies[sessions.COOKIE_NAME])
    assert session is not None and session.access_token == TOKEN
