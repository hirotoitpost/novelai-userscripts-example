"""
画像・作品データのバックアップ(backup.py と /api/admin/data-backup)のテスト。アプリのフォルダと写し先は一時フォルダにする。

実行方法:
  uv run pytest tests/test_backup.py -v
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import backup, db  # noqa: E402
from python.routes import admin  # noqa: E402


@pytest.fixture()
def app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    monkeypatch.setattr(backup, "_PROJECT_ROOT", root)
    monkeypatch.setattr(db, "_DB_PATH", root / "data" / "app.db")
    for rel, text in {
        "outputs/history/a.png": "image a",
        "outputs/manga/panels/p.png": "panel",
        "outputs/training_data/mio/1.png": "dataset",
        "data/imports/1/page.png": "import",
        "data/trash/3/0_x.png": "trash",
        "data/models/big.onnx": "model",
        "data/run/backend.log": "log",
        "data/certs/ca.key": "ca private key",
        "data/certs/server.key": "server private key",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    conn = db.get_connection()
    try:
        db.save_character(conn, "みお", "1girl")
    finally:
        conn.close()
    return root


def _settings(tmp_path: Path, **kwargs) -> backup.Settings:
    return backup.Settings(destination=str(tmp_path / "drive"), **kwargs)


def _snapshot_files(tmp_path: Path, name: str) -> dict:
    return json.loads((tmp_path / "drive" / backup.FOLDER / "snapshots" / f"{name}.json").read_text(encoding="utf-8"))["files"]


def test_backup_is_incremental_and_skips_what_can_be_refetched(app_root: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = backup.run_backup(settings)
    files = _snapshot_files(tmp_path, first["name"])
    assert set(files) == {"data/app.db", "outputs/history/a.png", "outputs/manga/panels/p.png", "data/imports/1/page.png", "data/trash/3/0_x.png"}
    assert first["added"] == 5
    time.sleep(1.1)
    # 2回目は、変わっていないファイルを写さない(DB も変わっていなければ同じ中身)
    (app_root / "outputs/history/b.png").write_text("image b", encoding="utf-8")
    second = backup.run_backup(settings)
    assert second["added"] == 1 and "outputs/history/b.png" in _snapshot_files(tmp_path, second["name"])
    # キャラ別データセットは選んだときだけ
    time.sleep(1.1)
    third = backup.run_backup(_settings(tmp_path, include_datasets=True))
    assert "outputs/training_data/mio/1.png" in _snapshot_files(tmp_path, third["name"])


def test_restore_brings_back_files_and_db(app_root: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    taken = backup.run_backup(settings)
    (app_root / "outputs/history/a.png").unlink()
    (app_root / "outputs/manga/panels/p.png").write_text("changed", encoding="utf-8")
    (app_root / "outputs/history/new.png").write_text("new", encoding="utf-8")
    conn = db.get_connection()
    try:
        db.save_character(conn, "ゆら", "1girl")
    finally:
        conn.close()
    time.sleep(1.1)
    result = backup.restore(settings, taken["name"])
    assert result["written"] == 2 and result["before"] != taken["name"]
    assert (app_root / "outputs/history/a.png").read_text(encoding="utf-8") == "image a"
    assert (app_root / "outputs/manga/panels/p.png").read_text(encoding="utf-8") == "panel"
    # その回より後にできたファイルは残す
    assert (app_root / "outputs/history/new.png").is_file()
    conn = db.get_connection()
    try:
        assert [c["name"] for c in db.list_characters(conn)] == ["みお"]
    finally:
        conn.close()
    # 戻す前の状態もバックアップに残っている(ゆら の入った DB)
    assert any(s["reason"].endswith("に戻す前") for s in backup.snapshots(tmp_path / "drive" / backup.FOLDER))


def test_user_dbs_are_snapshotted_and_restored(
    app_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # multi の利用者ごとの DB も、ファイルとしてではなく壊れない写しで入れ、戻すときも入れ替える
    monkeypatch.setenv("APP_MODE", "multi")
    with db.as_user(3):
        conn = db.get_connection()
        try:
            db.save_character(conn, "あおい", "1girl")
        finally:
            conn.close()
    settings = _settings(tmp_path)
    taken = backup.run_backup(settings)
    files = _snapshot_files(tmp_path, taken["name"])
    assert "data/users/3/app.db" in files and files["data/users/3/app.db"]["mtime"] == 0
    with db.as_user(3):
        conn = db.get_connection()
        try:
            db.save_character(conn, "ゆら", "1girl")
        finally:
            conn.close()
    time.sleep(1.1)
    backup.restore(settings, taken["name"])
    with db.as_user(3):
        conn = db.get_connection()
        try:
            assert [c["name"] for c in db.list_characters(conn)] == ["あおい"]
        finally:
            conn.close()


def test_certificate_keys_are_never_backed_up_or_restored(app_root: Path, tmp_path: Path) -> None:
    # 認証局の秘密鍵(ca.key)などは写し先に置かない。前のバックアップに入っていても書き戻さない
    settings = _settings(tmp_path)
    taken = backup.run_backup(settings)
    files = _snapshot_files(tmp_path, taken["name"])
    assert not any(rel.startswith("data/certs") for rel in files)
    objects = {p.read_text(encoding="utf-8", errors="ignore") for p in (tmp_path / "drive" / backup.FOLDER / "objects").glob("*/*")}
    assert "ca private key" not in objects
    snapshot = tmp_path / "drive" / backup.FOLDER / "snapshots" / f"{taken['name']}.json"
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    data["files"]["data/certs/ca.key"] = data["files"]["outputs/history/a.png"]  # 修正の前に取ったバックアップ
    snapshot.write_text(json.dumps(data), encoding="utf-8")
    (app_root / "data/certs/ca.key").write_text("new ca key", encoding="utf-8")
    time.sleep(1.1)
    backup.restore(settings, taken["name"])
    assert (app_root / "data/certs/ca.key").read_text(encoding="utf-8") == "new ca key"


def test_keys_in_backups_taken_before_the_fix_are_removed(app_root: Path, tmp_path: Path) -> None:
    # 修正の前に取った回に入っていた秘密鍵は、次にバックアップを取ったときに記録からも中身からも消える
    settings = _settings(tmp_path)
    store = tmp_path / "drive" / backup.FOLDER
    taken = backup.run_backup(settings)
    snapshot = store / "snapshots" / f"{taken['name']}.json"
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    key_object = store / "objects" / "ke" / "keyhash"
    key_object.parent.mkdir(parents=True)
    key_object.write_text("old ca private key", encoding="utf-8")
    data["files"]["data/certs/ca.key"] = {"hash": "keyhash", "size": 18, "mtime": 0}
    snapshot.write_text(json.dumps(data), encoding="utf-8")
    time.sleep(1.1)
    backup.run_backup(settings)
    assert "data/certs/ca.key" not in json.loads(snapshot.read_text(encoding="utf-8"))["files"]
    assert not key_object.exists()
    assert "data/app.db" in json.loads(snapshot.read_text(encoding="utf-8"))["files"]  # DB は外さない


def test_keeps_seven_and_cleans_unused_objects(app_root: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = tmp_path / "drive" / backup.FOLDER
    for index in range(9):
        (app_root / "outputs/history/a.png").write_text(f"version {index}", encoding="utf-8")
        backup.run_backup(settings)
        time.sleep(1.1)
    listed = backup.snapshots(store)
    assert len(listed) == backup.KEEP
    # 消えた回にしか無い中身(version 0 と 1)は片付いている
    objects = {p.read_text(encoding="utf-8", errors="ignore") for p in (store / "objects").glob("*/*")}
    assert "version 0" not in objects and "version 8" in objects


def test_destination_checks_and_auto_due(app_root: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        backup.check_destination("")
    with pytest.raises(ValueError):
        backup.check_destination("relative/folder")
    with pytest.raises(ValueError):
        backup.check_destination(str(app_root / "outputs"))
    settings = _settings(tmp_path)
    assert backup.due(settings)
    backup.run_backup(settings)
    assert not backup.due(settings)
    assert not backup.due(backup.Settings(destination=settings.destination, auto=False))


def test_admin_api(app_root: Path, tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(admin.router)
    client = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    assert client.post("/api/admin/data-backup/run").status_code == 422
    bad = client.put("/api/admin/data-backup/settings", json={"destination": str(app_root / "x")})
    assert bad.status_code == 422
    state = client.put("/api/admin/data-backup/settings", json={"destination": str(tmp_path / "drive"), "include_datasets": True}).json()
    assert state["settings"]["include_datasets"] is True and state["keep"] == 7
    assert client.post("/api/admin/data-backup/run").status_code == 200
    for _ in range(100):
        state = client.get("/api/admin/data-backup").json()
        if state["job"]["status"] != "running":
            break
        time.sleep(0.05)
    assert state["job"]["status"] == "done", state["job"]
    assert len(state["snapshots"]) == 1 and state["snapshots"][0]["include_datasets"] is True
