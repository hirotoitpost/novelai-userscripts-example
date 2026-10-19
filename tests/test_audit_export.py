"""APP_MODE=multi の監査ログと、利用者のデータの書き出し・削除(docs/multi_user_plan_jp.md のステップ 6)。"""

from __future__ import annotations

import base64
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import backup, db, sessions  # noqa: E402
from python.routes import admin, auth  # noqa: E402
from python.routes import content_guard as guard_routes  # noqa: E402

OWNER, GUEST = "owner@example.com", "guest@example.com"


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"m" * 32).decode())

    async def fake_login(email: str, password: str, recaptcha: str) -> str:
        if password != "pw":
            raise ValueError("Login failed (401): wrong password")
        return f"token-of-{email}"

    monkeypatch.setattr(auth, "login_with_credentials", fake_login)
    monkeypatch.setattr(auth, "get_encryption_key", lambda email, password: b"k" * 32)
    app = FastAPI()
    app.add_middleware(sessions.SessionMiddleware)
    for router in (auth.router, admin.router, guard_routes.router):
        app.include_router(router)
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))


def _login(client: TestClient, email: str, password: str = "pw") -> int:
    return client.post("/api/auth/login", json={"email": email, "password": password, "recaptcha": "r"}).status_code


def _actions() -> list[tuple[str, str]]:
    return [(e["action"], e["target"]) for e in reversed(sessions.list_audit(100))]


def test_logins_and_admin_operations_are_recorded(app: FastAPI, tmp_path: Path) -> None:
    sessions.add_invite(OWNER)
    sessions.set_admin(OWNER, True)
    sessions.add_invite(GUEST)
    owner, guest, stranger = _client(app), _client(app), _client(app)
    assert _login(stranger, "stranger@example.com") == 403
    assert _login(owner, OWNER, "wrong") == 401
    assert _login(owner, OWNER) == 200
    assert _login(guest, GUEST) == 200
    owner.put("/api/auth/api-token", json={"token": "pst-secret-token"})
    owner.get("/api/admin/settings")  # 見るだけの操作は残さない
    owner.get("/api/admin/env")  # .env を見たことは残す
    guest.post("/api/content-guard/reset")  # 断られたものも残す
    guest.post("/api/content-guard/check", json={"prompt": "1girl"})  # 判定のチェックは残さない
    guest.post("/api/auth/logout")

    entries = list(reversed(sessions.list_audit(100)))
    actions = [(e["action"], e["target"], e["detail"].get("status")) for e in entries]
    assert actions == [
        ("login_failed", "", 403),
        ("login_failed", "", 401),
        ("login", "", None),
        ("login", "", None),
        ("api_token_set", "", None),
        ("request", "GET /api/admin/env", 200),
        ("request", "POST /api/content-guard/reset", 403),
        ("logout", "", None),
    ]
    assert entries[0]["detail"]["reason"] == "このメールアドレスは招待されていません"
    assert entries[2]["actor"] == sessions.actor_of(OWNER) and entries[2]["ip"] == "127.0.0.1"
    # メールアドレスとトークンは残さない
    rows = json.dumps([list(r) for r in sqlite3.connect(tmp_path / "app.db").execute("SELECT * FROM audit_log")])
    for secret in (OWNER, GUEST, "stranger@example.com", "pst-secret-token"):
        assert secret not in rows
    # 管理ページから見られる(管理者だけ)
    assert owner.get("/api/admin/audit").json()["enabled"] is True
    assert guest.get("/api/admin/audit").status_code in (401, 403)


def test_single_records_nothing(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "single")
    sessions.audit("login", actor="x")
    monkeypatch.setenv("APP_MODE", "multi")
    assert sessions.list_audit() == []


def test_export_and_delete_a_user(app: FastAPI, tmp_path: Path) -> None:
    sessions.add_invite(GUEST)
    guest = _client(app)
    assert _login(guest, GUEST) == 200
    user_id = sessions.user_id_of(GUEST)
    assert user_id is not None
    home = tmp_path / "users" / str(user_id)
    with db.as_user(user_id):
        conn = db.get_connection()
        try:
            db.save_character(conn, "あおい", "1girl")
        finally:
            conn.close()
    (home / "outputs" / "history").mkdir(parents=True)
    (home / "outputs" / "history" / "a.png").write_bytes(b"png")

    path = sessions.export_user(GUEST)
    assert path.parent == tmp_path / "exports"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert {"app.db", "outputs/history/a.png"} <= names
        archive.extract("app.db", tmp_path / "check")
    copy = sqlite3.connect(tmp_path / "check" / "app.db")
    assert [r[0] for r in copy.execute("SELECT name FROM characters")] == ["あおい"]
    # 書き出した ZIP はバックアップに入れない
    assert "data/exports" in backup._SKIP

    assert sessions.delete_user(GUEST) == user_id
    assert not home.exists()
    assert sessions.user_id_of(GUEST) is None
    assert _login(guest, GUEST) == 403  # 招待も消えている
    assert guest.get("/api/admin/audit").status_code == 401  # セッションも消えている
    with pytest.raises(sessions.AdoptError):
        sessions.export_user(GUEST)
