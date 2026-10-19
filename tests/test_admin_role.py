"""APP_MODE=multi の開発管理者のページは、管理者だけ(docs/multi_user_plan_jp.md のステップ 4)。"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, sessions  # noqa: E402
from python.routes import admin, auth  # noqa: E402


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(admin, "_ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"m" * 32).decode())

    async def fake_login(email: str, password: str, recaptcha: str) -> str:
        return f"token-of-{email}"

    monkeypatch.setattr(auth, "login_with_credentials", fake_login)
    monkeypatch.setattr(auth, "get_encryption_key", lambda email, password: b"k" * 32)
    app = FastAPI()
    app.add_middleware(sessions.SessionMiddleware)
    app.include_router(auth.router)
    app.include_router(admin.router)
    return app


def _login(app: FastAPI, email: str) -> TestClient:
    sessions.add_invite(email)
    # 開発管理者のページは LAN の中からだけ(require_lan)なので、この PC からの接続にする
    client = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    assert client.post("/api/auth/login", json={"email": email, "password": "pw", "recaptcha": "r"}).status_code == 200
    return client


def test_only_admins_can_use_the_admin_page(app: FastAPI) -> None:
    owner, guest = _login(app, "owner@example.com"), _login(app, "guest@example.com")
    assert owner.get("/api/admin/settings").status_code == 403
    assert owner.get("/api/auth/session").json()["is_admin"] is False
    # 管理者にすれば、ログインし直さなくても効く
    assert sessions.set_admin("Owner@Example.com", True)
    assert owner.get("/api/admin/settings").status_code == 200
    assert owner.get("/api/auth/session").json()["is_admin"] is True
    assert guest.get("/api/admin/settings").status_code == 403
    assert guest.get("/api/admin/env").status_code == 403
    # 外せば使えなくなる
    sessions.set_admin("owner@example.com", False)
    assert owner.get("/api/admin/settings").status_code == 403
    assert not sessions.set_admin("stranger@example.com", True)  # 招待されていない人は管理者にできない


def test_env_cannot_be_edited_from_the_page_in_multi(app: FastAPI, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_MODE=multi\n", encoding="utf-8")
    owner = _login(app, "owner@example.com")
    sessions.set_admin("owner@example.com", True)
    res = owner.put("/api/admin/env", json={"key": "APP_MODE", "value": "single"})
    assert res.status_code == 403
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "APP_MODE=multi\n"


def test_single_keeps_the_lan_rule(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "single")
    client = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    assert client.get("/api/admin/settings").status_code == 200
    assert client.get("/api/auth/session").json()["is_admin"] is True
