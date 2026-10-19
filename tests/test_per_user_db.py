"""APP_MODE=multi で、利用者ごとの DB を開く(docs/multi_user_plan_jp.md のステップ 3)。"""

from __future__ import annotations

import asyncio
import base64
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import app_settings, db, sessions, trash  # noqa: E402
from python.routes import auth  # noqa: E402


@pytest.fixture()
def multi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)  # adopt が本物の outputs/ を写さないように
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"m" * 32).decode())

    async def fake_login(email: str, password: str, recaptcha: str) -> str:
        return f"token-of-{email}"

    monkeypatch.setattr(auth, "login_with_credentials", fake_login)
    monkeypatch.setattr(auth, "get_encryption_key", lambda email, password: b"k" * 32)
    app_settings.reset_cache()
    return tmp_path


def _names() -> list[str]:
    conn = db.get_connection()
    try:
        return [r["name"] for r in conn.execute("SELECT name FROM situations ORDER BY name")]
    finally:
        conn.close()


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(sessions.SessionMiddleware)
    app.include_router(auth.router)

    @app.post("/api/situations/{name}")
    async def add(name: str) -> dict[str, Any]:
        conn = db.get_connection()
        try:
            conn.execute("INSERT INTO situations (name) VALUES (?)", (name,))
            conn.commit()
        finally:
            conn.close()
        return {"db": str(db.db_path())}

    @app.get("/api/situations")
    def names() -> list[str]:  # 同期の関数(スレッドで動く)からも同じ利用者の DB を開く
        return _names()

    @app.get("/api/later")
    async def later() -> list[str]:
        # 切り離した処理(タスク)も、作ったときの利用者の DB を開く
        return await asyncio.create_task(asyncio.to_thread(_names))

    return app


def _client_for(app: FastAPI, email: str) -> TestClient:
    sessions.add_invite(email)
    client = TestClient(app, base_url="http://localhost:8000")
    res = client.post("/api/auth/login", json={"email": email, "password": "pw", "recaptcha": "r"})
    assert res.status_code == 200
    return client


def test_single_uses_the_one_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    assert db.db_path() == tmp_path / "app.db"


def test_multi_refuses_without_a_user(multi: Path) -> None:
    with pytest.raises(db.NoUserError):
        db.get_connection()
    with db.as_user(7):
        assert db.db_path() == multi / "users" / "7" / "app.db"


def test_each_user_sees_only_their_own_data(app: FastAPI, multi: Path) -> None:
    alice, bob = _client_for(app, "alice@example.com"), _client_for(app, "bob@example.com")
    assert alice.post("/api/situations/alice-only").json()["db"].endswith(str(Path("users/1/app.db")))
    bob.post("/api/situations/bob-only")
    assert alice.get("/api/situations").json() == ["alice-only"]
    assert bob.get("/api/situations").json() == ["bob-only"]
    assert alice.get("/api/later").json() == ["alice-only"]
    # 招待・利用者・セッションは全体の DB だけにある
    system = sqlite3.connect(multi / "app.db")
    assert system.execute("SELECT count(*) FROM users").fetchone()[0] == 2
    assert system.execute("SELECT count(*) FROM situations").fetchone()[0] == 0


def test_settings_and_trash_are_per_user(multi: Path) -> None:
    with db.as_user(1):
        app_settings.set_value("reference.candidate_tags", "solo, alice")
        assert app_settings.get("reference.candidate_tags") == "solo, alice"
        assert trash._trash_dir() == multi / "users" / "1" / "trash"
    with db.as_user(2):
        # キャッシュに残った前の利用者の値ではなく、その人の DB の値(変えていなければ既定値)
        assert app_settings.get("reference.candidate_tags") != "solo, alice"
    with db.as_user(1):
        assert app_settings.get("reference.candidate_tags") == "solo, alice"


def test_adopt_copies_the_single_data_to_the_operator(app: FastAPI, multi: Path) -> None:
    # single のときのデータ(全体の DB と同じファイル)とごみ箱
    legacy = db.get_system_connection()  # multi の全体の DB = single のときの DB
    legacy.execute("INSERT INTO situations (name) VALUES ('from-single')")
    legacy.commit()
    legacy.close()
    (multi / "trash" / "1").mkdir(parents=True)
    (multi / "trash" / "1" / "a.png").write_bytes(b"png")

    with pytest.raises(sessions.AdoptError, match="ログインしていません"):
        sessions.adopt_legacy_data("owner@example.com")
    owner = _client_for(app, "owner@example.com")
    user_id = sessions.adopt_legacy_data("Owner@Example.com ")
    assert owner.get("/api/situations").json() == ["from-single"]
    assert (multi / "users" / str(user_id) / "trash" / "1" / "a.png").read_bytes() == b"png"
    copy = sqlite3.connect(multi / "users" / str(user_id) / "app.db")
    assert copy.execute("SELECT count(*) FROM users").fetchone()[0] == 0  # ログインの情報は写さない
    # 開いたままでも上書きできる(バックエンドが開いているとき)
    # 元のデータは残す。2 回目は断る(その人が multi で作ったものを消さない)
    assert sqlite3.connect(multi / "app.db").execute("SELECT count(*) FROM situations").fetchone()[0] == 1
    owner.post("/api/situations/made-in-multi")
    with pytest.raises(sessions.AdoptError, match="もうあります"):
        sessions.adopt_legacy_data("owner@example.com")
    sessions.adopt_legacy_data("owner@example.com", force=True)
    assert owner.get("/api/situations").json() == ["from-single"]


def test_jobs_keyed_by_db_ids_do_not_mix_between_users(multi: Path) -> None:
    # 利用者ごとに DB が分かれると、物語の ID は人ごとに 1 から振られる
    jobs: db.PerUser[str] = db.PerUser()
    with db.as_user(1):
        jobs[1] = "alice's story 1"
    with db.as_user(2):
        assert jobs.get(1) is None and 1 not in jobs and jobs.values() == []
        jobs[1] = "bob's story 1"
    with db.as_user(1):
        assert jobs.get(1) == "alice's story 1" and jobs.items() == [(1, "alice's story 1")]
        assert jobs.pop(1) == "alice's story 1"
    with db.as_user(2):
        assert jobs[1] == "bob's story 1"


def test_cleanup_only_looks_at_the_users_own_folder(multi: Path) -> None:
    # 片付けの対象は、その人のフォルダの中の、その人の DB から使われていないものだけ(ほかの人の画像は見ない)
    from python.routes import admin

    for user_id in (1, 2):
        panel = multi / "users" / str(user_id) / "outputs" / "manga" / "panels" / f"user{user_id}.png"
        panel.parent.mkdir(parents=True)
        panel.write_bytes(b"png")
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin.require_admin] = lambda: None  # 管理者の確認は test_admin_role.py で見る
    client = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    with db.as_user(1):
        files = client.get("/api/admin/cleanup/panels/files").json()["files"]
    assert [f["name"] for f in files] == ["user1.png"]
    assert files[0]["path"] == "outputs/manga/panels/user1.png"


def test_adopt_copies_files_and_paths_resolve_in_the_users_folder(
    app: FastAPI, multi: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from python.routes import story

    for rel in ("outputs/manga/panels/p.png", "data/imports/3/page.png", "data/stamps/src/1.png", "data/models/m.onnx"):
        (multi / rel).parent.mkdir(parents=True, exist_ok=True)
        (multi / rel).write_bytes(rel.encode())
    _client_for(app, "owner@example.com")
    user_id = sessions.adopt_legacy_data("owner@example.com")
    home = multi / "users" / str(user_id)
    assert (home / "outputs/manga/panels/p.png").read_bytes() == b"outputs/manga/panels/p.png"
    assert (home / "data/imports/3/page.png").is_file() and (home / "data/stamps/src/1.png").is_file()
    assert not (home / "data/models").exists()  # モデルはサーバー全体のもの(写さない)
    assert (multi / "outputs/manga/panels/p.png").is_file()  # 元は残す
    # 各モジュールの _PROJECT_ROOT(db.UserPath)は、その人のフォルダを指す。DB の相対パスもそこから読む
    with db.as_user(user_id):
        assert (story._PROJECT_ROOT / "outputs/manga/panels/p.png").read_bytes() == b"outputs/manga/panels/p.png"
        assert story._MANGA_DIR.resolve() == (home / "outputs" / "manga").resolve()
    with db.as_user(user_id + 1):
        assert not (story._PROJECT_ROOT / "outputs/manga/panels/p.png").exists()
    with pytest.raises(db.NoUserError):
        story._MANGA_DIR.exists()  # multi で利用者が決まっていなければ、共有の場所を見ない
    # DB を写したあとでも、ファイルだけなら写せる
    (multi / "outputs/history/new.png").parent.mkdir(parents=True)
    (multi / "outputs/history/new.png").write_bytes(b"new")
    sessions.adopt_legacy_data("owner@example.com", files_only=True)
    assert (home / "outputs/history/new.png").read_bytes() == b"new"
