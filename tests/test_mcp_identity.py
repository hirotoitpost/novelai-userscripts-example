"""MCP サーバーを、どの利用者として動かすか(APP_MODE=multi。docs/multi_user_plan_jp.md のステップ 5)。"""

from __future__ import annotations

import base64
import contextvars
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, mcp_identity, sessions  # noqa: E402

EMAIL = "owner@example.com"


@pytest.fixture()
def multi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"m" * 32).decode())
    monkeypatch.setenv("NOVELAI_API_TOKEN", "pst-operator")
    monkeypatch.setattr(mcp_identity, "_identity", None)
    return tmp_path


def _logged_in_user(email: str = EMAIL) -> int:
    sessions.add_invite(email)
    sessions.create_session(email, "eyJ-login", b"k" * 32)  # 一度ログインした(利用者ができる)
    user_id = sessions.user_id_of(email)
    assert user_id is not None
    return user_id


def _in_new_context(fn: Any) -> Any:
    # setup はこのプロセスの「今の利用者」を決める。テストどうしで漏れないように別の文脈で動かす
    return contextvars.copy_context().run(fn)


def test_single_uses_the_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVELAI_API_TOKEN", "pst-operator")
    monkeypatch.setattr(mcp_identity, "_identity", None)

    def run() -> tuple[str, dict[str, str], int | None]:
        mcp_identity.setup()
        return mcp_identity.novelai_token(), mcp_identity.backend_cookies(), db.current_user_id()

    assert _in_new_context(run) == ("pst-operator", {}, None)


def test_multi_needs_a_user_who_has_logged_in(multi: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(mcp_identity.McpUserError, match="APP_MCP_USER"):
        _in_new_context(mcp_identity.setup)
    monkeypatch.setenv("APP_MCP_USER", EMAIL)
    with pytest.raises(mcp_identity.McpUserError, match="ログインしていません"):
        _in_new_context(mcp_identity.setup)  # 招待されていても、一度ログインしないと利用者がいない
    sessions.add_invite(EMAIL)
    with pytest.raises(mcp_identity.McpUserError, match="ログインしていません"):
        _in_new_context(mcp_identity.setup)


def test_multi_runs_as_that_user_with_their_own_token(multi: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = _logged_in_user()
    monkeypatch.setenv("APP_MCP_USER", " Owner@Example.com ")

    def run() -> tuple[Path, Path]:
        mcp_identity.setup()
        # その人の DB とフォルダを開く(運営者の前のデータではなく)
        return db.db_path(), db.user_root()

    assert _in_new_context(run) == (multi / "users" / str(user_id) / "app.db", multi / "users" / str(user_id))
    # 運営者の .env のトークンは使わない。その人が永続トークンを登録するまでは生成できない
    with pytest.raises(mcp_identity.McpUserError, match="永続 API トークン"):
        mcp_identity.novelai_token()
    sessions.set_api_token(user_id, "pst-owner")
    assert mcp_identity.novelai_token() == "pst-owner"


def test_backend_calls_carry_a_short_session_of_that_user(multi: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = _logged_in_user()
    sessions.set_api_token(user_id, "pst-owner")
    monkeypatch.setenv("APP_MCP_USER", EMAIL)
    _in_new_context(mcp_identity.setup)

    app = FastAPI()
    app.add_middleware(sessions.SessionMiddleware)

    @app.get("/api/whoami")
    async def whoami(request: Request) -> dict[str, Any]:
        session = sessions.current_session(request)
        return {"user": db.current_user_id(), "auth": request.headers["authorization"], "key": session.encryption_key}

    client = TestClient(app, base_url="http://localhost:8000")
    cookies = mcp_identity.backend_cookies()
    client.cookies.update(cookies)
    # keystore の鍵は持たない(ログインの画面を通らないので)
    assert client.get("/api/whoami").json() == {"user": user_id, "auth": "Bearer pst-owner", "key": None}
    renewed = mcp_identity.backend_cookies(renew=True)
    assert renewed != cookies


def test_random_id_jobs_are_only_visible_to_their_owner(multi: Path) -> None:
    from python.routes import quick_manga

    with db.as_user(1):
        quick_manga._jobs["abc123"] = "alice's job"  # type: ignore[assignment]
    with db.as_user(2):
        assert quick_manga._jobs.get("abc123") is None and "abc123" not in quick_manga._jobs
    with db.as_user(1):
        assert quick_manga._jobs.pop("abc123") == "alice's job"
