"""APP_MODE=multi のコンテンツガードは、運営者が全員分を決める(docs/multi_user_plan_jp.md のステップ 4b)。"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import content_guard, db, guard_password, sessions  # noqa: E402
from python.routes import auth  # noqa: E402
from python.routes import content_guard as guard_routes  # noqa: E402
from python.routes import lora_dataset  # noqa: E402

KEY = "general.safe_negative"


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"m" * 32).decode())

    async def fake_login(email: str, password: str, recaptcha: str) -> str:
        return f"token-of-{email}"

    monkeypatch.setattr(auth, "login_with_credentials", fake_login)
    monkeypatch.setattr(auth, "get_encryption_key", lambda email, password: b"k" * 32)
    content_guard.reset_cache()
    guard_password.reset_state()
    app = FastAPI()
    app.add_middleware(sessions.SessionMiddleware)
    for router in (auth.router, guard_routes.router, lora_dataset.router):
        app.include_router(router)
    return app


def _login(app: FastAPI, email: str, *, admin: bool = False) -> TestClient:
    sessions.add_invite(email)
    sessions.set_admin(email, admin)
    client = TestClient(app, base_url="http://localhost:8000")
    assert client.post("/api/auth/login", json={"email": email, "password": "pw", "recaptcha": "r"}).status_code == 200
    return client


def _value(client: TestClient) -> str:
    return client.get(f"/api/content-guard/rules/{KEY}").json()["value"]


def test_the_operator_decides_the_rules_for_everyone(app: FastAPI) -> None:
    owner, guest = _login(app, "owner@example.com", admin=True), _login(app, "guest@example.com")
    stricter = f"{_value(owner)}, operator_only_tag"
    # 守りを固くする変更も、管理者だけ(全員に効くので)
    assert guest.put(f"/api/content-guard/rules/{KEY}", json={"value": stricter}).status_code == 403
    assert owner.put(f"/api/content-guard/rules/{KEY}", json={"value": stricter}).status_code == 200
    assert _value(guest) == stricter  # ほかの人にも同じ値が効く
    assert guest.delete(f"/api/content-guard/rules/{KEY}").status_code == 403
    assert guest.post("/api/content-guard/reset").status_code == 403
    assert _value(guest) == stricter


def test_the_password_is_the_operators(app: FastAPI) -> None:
    owner, guest = _login(app, "owner@example.com", admin=True), _login(app, "guest@example.com")
    assert guest.post("/api/content-guard/password/setup", json={"password": "guest-password"}).status_code == 403
    assert owner.post("/api/content-guard/password/setup", json={"password": "owner-password"}).status_code == 204
    assert guest.get("/api/content-guard/password").json()["set"] is True
    # 緩める操作に要る合言葉は、運営者のパスワードからしか得られない
    assert guest.post("/api/content-guard/unlock", json={"password": "guest-password"}).status_code != 200
    assert guest.post("/api/content-guard/unlock", json={"password": "owner-password"}).status_code == 200


def test_guard_profiles_are_shared_and_admin_only(app: FastAPI, tmp_path: Path) -> None:
    owner, guest = _login(app, "owner@example.com", admin=True), _login(app, "guest@example.com")
    profile = {"name": "school", "blocked_tags": ["operator_blocked"], "negative_tags": ""}
    assert guest.post("/api/lora-dataset/guards", json=profile).status_code == 403
    saved = owner.post("/api/lora-dataset/guards", json=profile)
    assert saved.status_code == 200
    assert [p["name"] for p in guest.get("/api/lora-dataset/guards").json()] == ["school"]
    assert guest.delete(f"/api/lora-dataset/guards/{saved.json()['id']}").status_code == 403
    # 置き場所は全体の DB(利用者の DB には入らない)
    import sqlite3

    assert sqlite3.connect(tmp_path / "app.db").execute("SELECT count(*) FROM guard_profiles").fetchone()[0] == 1
    with db.as_user(1):
        conn = db.get_connection()
        try:
            assert conn.execute("SELECT count(*) FROM guard_profiles").fetchone()[0] == 0
        finally:
            conn.close()
