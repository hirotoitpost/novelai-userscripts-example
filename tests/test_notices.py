"""
お知らせ(notices.py と /api/admin/notices)のテスト。アプリのフォルダと写し先は一時フォルダにする。

実行方法:
  uv run pytest tests/test_notices.py -v
"""

from __future__ import annotations

import json
import re
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import backup, db, notices  # noqa: E402
from python.routes import admin  # noqa: E402

_Usage = namedtuple("_Usage", "total used free")
GIB = notices.GIB


@pytest.fixture()
def app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    monkeypatch.setattr(backup, "_PROJECT_ROOT", root)
    monkeypatch.setattr(db, "_DB_PATH", root / "data" / "app.db")
    # このアプリのフォルダは、写し先とは別のドライブということにする(同じドライブのお知らせは個別に試す)
    monkeypatch.setattr(notices, "_PROJECT_ROOT", tmp_path / "elsewhere")
    image = root / "outputs" / "history" / "a.png"
    image.parent.mkdir(parents=True)
    image.write_text("image a", encoding="utf-8")
    db.get_connection().close()
    usage = lambda path: _Usage(1000 * GIB, 500 * GIB, 500 * GIB)  # noqa: E731
    monkeypatch.setattr(backup, "_disk_usage", usage)
    monkeypatch.setattr(notices, "_disk_usage", usage)
    monkeypatch.setattr(backup, "_job", backup.Job())
    notices.invalidate()
    return root


def _keys(found: list[notices.Notice]) -> set[str]:
    return {n.key.split(":")[0] for n in found}


def _settings(tmp_path: Path, *names: str) -> backup.Settings:
    settings = backup.Settings(disks=[backup.register_disk(backup.Disk(str(tmp_path / n))) for n in names])
    backup.save_settings(settings)
    return settings


def test_days_until_short_follows_the_placement() -> None:
    # 空きの多い2つに置いていくので、小さい F(7GB)は最後まで使われず、D と E が 7GB まで減るまで持つ
    days = notices._days_until_short([7 * GIB, 69 * GIB, 69 * GIB], 2, 2 * GIB)
    assert days is not None and 60 < days < 80
    # 置ける写し先が copies より少なければ、もう足りない
    assert notices._days_until_short([10 * GIB, 0], 2, GIB) == 0
    assert notices._days_until_short([10 * GIB, 10 * GIB], 2, 0) is None


def test_growth_uses_total_size_not_one_time_copies() -> None:
    now = datetime.now(timezone.utc)
    snaps = [
        # 写し先を足した回は「写した分」が大きいが、ファイルの合計はふだんどおり
        {"created_at": now.isoformat(), "bytes": 42 * GIB, "added_bytes": 34 * GIB},
        {"created_at": (now - timedelta(days=4)).isoformat(), "bytes": 40 * GIB, "added_bytes": GIB // 2},
    ]
    assert notices._growth_per_day(snaps, 2) == pytest.approx(1 * GIB)
    assert notices._growth_per_day(snaps[:1], 2) is None


def test_backup_not_configured(app_root: Path) -> None:
    assert _keys(notices.backup_notices()) == {"backup.not_configured"}


def test_backup_capacity_offline_and_stale(app_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, "d1", "d2")
    backup.run_backup(settings)
    assert notices.backup_notices() == []
    # 前の回を 4 日前・いまより 40GB 少なかったことにする(1日 10GB、写し 2 つで 20GB/日 増えている)
    store = tmp_path / "d1" / backup.FOLDER
    for folder in ("d1", "d2"):
        snap = next((tmp_path / folder / backup.FOLDER / "snapshots").glob("*.json"))
        data = json.loads(snap.read_text(encoding="utf-8"))
        old = dict(data, created_at=(datetime.now(timezone.utc) - timedelta(days=4)).isoformat())
        old["files"] = {"x": {"hash": "h", "size": 0, "mtime": 0}}
        (snap.parent / "backup-20000101-000000.json").write_text(json.dumps(old), encoding="utf-8")
        data["files"]["big"] = {"hash": "h2", "size": 40 * GIB, "mtime": 0}
        snap.write_text(json.dumps(data), encoding="utf-8")
    # 残り(閾値 50GB/10% の大きい方 100GB を引いた空き)は 2 つとも 200GB。1台に 10GB/日 ずつ → 約 20 日(30 日以内なので注意)
    monkeypatch.setattr(notices, "_disk_usage", lambda path: _Usage(1000 * GIB, 0, 300 * GIB))
    found = {n.key: n for n in notices.backup_notices()}
    capacity = found["backup.capacity"]
    assert capacity.level == "warning" and re.search(r"あと約 (19|20) 日", capacity.body), capacity.body
    # 写し先が抜けた
    (tmp_path / "d2").rename(tmp_path / "d2-off")
    keys = _keys(notices.backup_notices())
    assert "backup.offline" in keys
    assert store.is_dir()


def test_backup_stale_and_failed(app_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, "d1")
    backup.run_backup(settings)
    snap = next((tmp_path / "d1" / backup.FOLDER / "snapshots").glob("*.json"))
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["created_at"] = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    snap.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(backup, "_job", backup.Job(kind="backup", status="error", message="写し先に書き込めません"))
    found = {n.key: n for n in notices.backup_notices()}
    assert found["backup.stale"].title == "バックアップを 5 日取っていません"
    assert found["backup.failed"].level == "error" and found["backup.failed"].body == "写し先に書き込めません"
    # 写し先が 1 つで置く数が 2 → そろっていない
    assert "backup.short" in found


def test_same_drive_as_app(app_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(tmp_path, "d1")
    monkeypatch.setattr(notices, "_PROJECT_ROOT", app_root)  # 一時フォルダは同じドライブ
    found = [n for n in notices.backup_notices() if n.key.startswith("backup.same_drive")]
    assert len(found) == 1 and found[0].level == "info"


def test_app_drive_space(app_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert notices.storage_notices() == []
    monkeypatch.setattr(notices, "_disk_usage", lambda path: _Usage(1000 * GIB, 990 * GIB, 10 * GIB))
    assert notices.storage_notices()[0].level == "warning"
    monkeypatch.setattr(notices, "_disk_usage", lambda path: _Usage(1000 * GIB, 997 * GIB, 3 * GIB))
    assert notices.storage_notices()[0].level == "error"


def test_snooze_dismiss_and_restore(app_root: Path) -> None:
    listing = notices.listing(force=True)
    assert [n["key"] for n in listing["notices"]] == ["backup.not_configured"]
    notices.snooze("backup.not_configured", 1)
    listing = notices.listing()
    assert listing["notices"] == [] and listing["hidden"][0]["hidden"] == "snoozed"
    notices.restore_all()
    item = notices.listing()["notices"][0]
    notices.dismiss(item["key"], item["fingerprint"])
    assert notices.listing()["hidden"][0]["hidden"] == "dismissed"
    # 重さか題名が変われば出し直す
    notices.dismiss(item["key"], "other")
    assert [n["key"] for n in notices.listing()["notices"]] == ["backup.not_configured"]


def test_failing_check_does_not_hide_others(app_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> list[notices.Notice]:
        raise RuntimeError("boom")

    monkeypatch.setattr(notices, "CHECKS", [broken, notices.backup_notices])
    keys = {n.key for n in notices.collect(force=True)}
    assert keys == {"notices.failed:broken", "backup.not_configured"}


def test_admin_api(app_root: Path, tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(admin.router)
    client = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    listing = client.get("/api/admin/notices?refresh=true").json()
    assert [n["key"] for n in listing["notices"]] == ["backup.not_configured"]
    item = listing["notices"][0]
    assert client.post("/api/admin/notices/snooze", json={"key": item["key"], "days": 7}).json()["notices"] == []
    assert client.post("/api/admin/notices/restore", json={}).json()["notices"][0]["key"] == item["key"]
    hidden = client.post("/api/admin/notices/dismiss", json={"key": item["key"], "fingerprint": item["fingerprint"]}).json()
    assert hidden["notices"] == [] and len(hidden["hidden"]) == 1
    # 写し先を保存したら、すぐ点検し直す(覚えていた結果を捨てる)
    client.put("/api/admin/data-backup/settings", json={"disks": [{"path": str(tmp_path / "drive")}]})
    keys = {n["key"].split(":")[0] for n in client.get("/api/admin/notices").json()["notices"]}
    assert "backup.not_configured" not in keys
