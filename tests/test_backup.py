"""
画像・作品データのバックアップ(backup.py と /api/admin/data-backup)のテスト。アプリのフォルダと写し先は一時フォルダにする。

実行方法:
  uv run pytest tests/test_backup.py -v
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections import namedtuple
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
    # 空きは、どの写し先も 500GB あることにする(閾値のテストでは写し先ごとに変える)
    monkeypatch.setattr(backup, "_disk_usage", lambda path: _Usage(1000 * backup.GIB, 500 * backup.GIB, 500 * backup.GIB))
    return root


_Usage = namedtuple("_Usage", "total used free")


def _settings(tmp_path: Path, **kwargs) -> backup.Settings:
    return backup.Settings(disks=[backup.Disk(str(tmp_path / "drive"))], **kwargs)


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
    assert not backup.due(backup.Settings(disks=settings.disks, auto=False))


def test_admin_api(app_root: Path, tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(admin.router)
    client = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    assert client.post("/api/admin/data-backup/run").status_code == 422
    bad = client.put("/api/admin/data-backup/settings", json={"disks": [{"path": str(app_root / "x")}]})
    assert bad.status_code == 422
    twice = [{"path": str(tmp_path / "drive")}, {"path": str(tmp_path / "drive")}]
    assert client.put("/api/admin/data-backup/settings", json={"disks": twice}).status_code == 422
    state = client.put(
        "/api/admin/data-backup/settings",
        json={"disks": [{"path": str(tmp_path / "drive")}, {"path": str(tmp_path / "drive2"), "reserve_gb": 10}], "include_datasets": True},
    ).json()
    assert state["settings"]["include_datasets"] is True and state["keep"] == 7 and state["settings"]["copies"] == 2
    assert all(d["id"] for d in state["settings"]["disks"]) and state["settings"]["disks"][1]["reserve_gb"] == 10
    assert all(d["available"] and d["accepting"] for d in state["disks"])
    assert client.post("/api/admin/data-backup/run").status_code == 200
    for _ in range(100):
        state = client.get("/api/admin/data-backup").json()
        if state["job"]["status"] != "running":
            break
        time.sleep(0.05)
    assert state["job"]["status"] == "done", state["job"]
    assert len(state["snapshots"]) == 1 and state["snapshots"][0]["include_datasets"] is True
    assert state["health"] is not None
    assert state["health"]["short"] == 0 and state["health"]["copies"] == 2


# ---- 複数の写し先 ----


def _disks(tmp_path: Path, *names: str, **kwargs) -> backup.Settings:
    disks = [backup.register_disk(backup.Disk(str(tmp_path / name))) for name in names]
    return backup.Settings(disks=disks, **kwargs)


def _objects(tmp_path: Path, name: str) -> set[str]:
    return {p.name for p in (tmp_path / name / backup.FOLDER / "objects").glob("*/*")}


def _snapshot_names(tmp_path: Path, name: str) -> set[str]:
    return {p.stem for p in (tmp_path / name / backup.FOLDER / "snapshots").glob("*.json")}


def _snapshot_files_on(tmp_path: Path, disk: str, name: str) -> dict:
    return json.loads((tmp_path / disk / backup.FOLDER / "snapshots" / f"{name}.json").read_text(encoding="utf-8"))["files"]


def _unplug(tmp_path: Path, name: str) -> None:
    (tmp_path / name).rename(tmp_path / f"{name}-off")


def _plug(tmp_path: Path, name: str) -> None:
    # 抜いているあいだに、同じ場所へ空のフォルダが作られていても消して戻す
    shutil.rmtree(tmp_path / name, ignore_errors=True)
    (tmp_path / f"{name}-off").rename(tmp_path / name)


def test_each_object_goes_to_two_of_three_disks(app_root: Path, tmp_path: Path) -> None:
    settings = _disks(tmp_path, "d1", "d2", "d3")
    taken = backup.run_backup(settings)
    assert taken["short"] == 0 and taken["missing"] == 0
    held = [_objects(tmp_path, n) for n in ("d1", "d2", "d3")]
    for rel, entry in _snapshot_files_on(tmp_path, "d1", taken["name"]).items():
        count = sum(entry["hash"] in h for h in held)
        # DB は全部の写し先、ほかは 2 か所
        assert count == (3 if rel == "data/app.db" else 2), rel
    # 一覧はどの写し先にもある
    assert all(taken["name"] in _snapshot_names(tmp_path, n) for n in ("d1", "d2", "d3"))


def test_disk_below_threshold_gets_no_new_objects(app_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _disks(tmp_path, "d1", "d2", "d3")

    # d3 は空きが 60GB(閾値は 50GB と 10% の大きい方 = 100GB)
    def usage(path: Path) -> _Usage:
        free = 60 if "d3" in str(path) else 500
        return _Usage(1000 * backup.GIB, (1000 - free) * backup.GIB, free * backup.GIB)

    monkeypatch.setattr(backup, "_disk_usage", usage)
    taken = backup.run_backup(settings)
    assert taken["short"] == 0
    files = _snapshot_files_on(tmp_path, "d1", taken["name"])
    # d3 には DB と一覧だけ
    assert _objects(tmp_path, "d3") == {files["data/app.db"]["hash"]}
    assert taken["name"] in _snapshot_names(tmp_path, "d3")
    states = {Path(d["path"]).name: d for d in backup.disk_states(settings)}
    assert states["d3"]["accepting"] is False and states["d1"]["accepting"] is True
    # 閾値を下げれば、次の回から置く先に戻る(3つのどれに置くかは空き次第なので、d3 を一番空いていることにする)
    settings.disks[2].reserve_gb = 10
    settings.disks[2].reserve_percent = 1
    monkeypatch.setattr(
        backup, "_disk_usage",
        lambda path: _Usage(1000 * backup.GIB, 0, (900 if "d3" in str(path) else 500) * backup.GIB),
    )
    (app_root / "outputs/history/c.png").write_text("image c", encoding="utf-8")
    time.sleep(1.1)
    backup.run_backup(settings)
    assert len(_objects(tmp_path, "d3")) > 1


def test_only_one_disk_with_room_keeps_going_and_reports_short(
    app_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _disks(tmp_path, "d1", "d2")
    monkeypatch.setattr(
        backup, "_disk_usage",
        lambda path: _Usage(1000 * backup.GIB, 0, (10 if "d2" in str(path) else 500) * backup.GIB),
    )
    taken = backup.run_backup(settings)
    assert taken["short"] == 4  # DB 以外の 4 つは 1 か所だけ
    assert backup.health(settings)["short"] == 4
    # どこにも置けなければ止める
    monkeypatch.setattr(backup, "_disk_usage", lambda path: _Usage(1000 * backup.GIB, 0, 10 * backup.GIB))
    (app_root / "outputs/history/z.png").write_text("image z", encoding="utf-8")
    with pytest.raises(ValueError, match="置ける写し先がありません"):
        backup.run_backup(settings)


def test_adding_a_disk_later_fills_second_copies(app_root: Path, tmp_path: Path) -> None:
    one = _disks(tmp_path, "d1")
    first = backup.run_backup(one)
    assert first["short"] == 5  # 写し先が1つなので 2 か所にはそろわない(DB も 1 か所)
    # 消したファイルの中身(古い回だけが使う)も、写し先を足せば 2 か所にそろう
    (app_root / "outputs/history/a.png").unlink()
    two = backup.Settings(disks=[*one.disks, backup.register_disk(backup.Disk(str(tmp_path / "d2")))])
    time.sleep(1.1)
    second = backup.run_backup(two)
    assert second["short"] == 0 and second["repaired"] > 0
    assert _objects(tmp_path, "d1") == _objects(tmp_path, "d2")
    assert first["name"] in _snapshot_names(tmp_path, "d2")


def test_missing_disk_is_skipped_and_restore_brings_back_what_it_can(app_root: Path, tmp_path: Path) -> None:
    settings = _disks(tmp_path, "d1", "d2", copies=1)
    taken = backup.run_backup(settings)
    _unplug(tmp_path, "d2")
    states = {Path(d["path"]).name: d for d in backup.disk_states(settings)}
    assert states["d2"]["available"] is False and "別のディスク" in states["d2"]["error"]
    lost = {rel for rel, f in _snapshot_files_on(tmp_path, "d1", taken["name"]).items() if f["hash"] not in _objects(tmp_path, "d1")}
    assert "data/app.db" not in lost  # DB はどの写し先にもある
    for rel in ("outputs/history/a.png", "outputs/manga/panels/p.png", "data/imports/1/page.png", "data/trash/3/0_x.png"):
        (app_root / rel).unlink()
    time.sleep(1.1)
    result = backup.restore(settings, taken["name"])
    assert result["missing"] == len(lost) and set(result["missing_examples"]) == lost
    assert result["written"] == 4 - len(lost)
    # 抜けていた写し先が戻れば、一覧がそろい、残りも戻せる
    _plug(tmp_path, "d2")
    time.sleep(1.1)
    result = backup.restore(settings, taken["name"])
    assert result["missing"] == 0
    assert (app_root / "outputs/history/a.png").read_text(encoding="utf-8") == "image a"
    assert _snapshot_names(tmp_path, "d1") == _snapshot_names(tmp_path, "d2")


def test_two_copies_survive_any_one_disk(app_root: Path, tmp_path: Path) -> None:
    settings = _disks(tmp_path, "d1", "d2", "d3")
    taken = backup.run_backup(settings)
    files = _snapshot_files_on(tmp_path, "d1", taken["name"])
    for name in ("d1", "d2", "d3"):
        _unplug(tmp_path, name)
        lives, problems = backup.open_disks(settings)
        assert len(lives) == 2 and len(problems) == 1
        held = set().union(*(live.objects for live in lives))
        assert all(f["hash"] in held for f in files.values()), name
        _plug(tmp_path, name)


def test_retiring_disk_is_emptied_onto_others(app_root: Path, tmp_path: Path) -> None:
    settings = _disks(tmp_path, "d1", "d2")
    backup.run_backup(settings)
    settings = backup.Settings(disks=[*settings.disks, backup.register_disk(backup.Disk(str(tmp_path / "d3")))])
    settings.disks[0].retiring = True
    # 外す準備にしただけ(まだ取っていない)では「外せます」(0)にしない
    states = {Path(d["path"]).name: d for d in backup.disk_states(settings)}
    assert states["d1"]["pending"] is None
    time.sleep(1.1)
    taken = backup.run_backup(settings)
    assert taken["short"] == 0
    assert _objects(tmp_path, "d2") == _objects(tmp_path, "d3")
    states = {Path(d["path"]).name: d for d in backup.disk_states(settings)}
    assert states["d1"]["pending"] == 0 and states["d1"]["accepting"] is False


def test_old_single_destination_settings_are_read_as_one_disk() -> None:
    settings = backup.settings_from_dict({"destination": "E:\\backup", "include_datasets": True, "auto": False})
    assert [d.path for d in settings.disks] == ["E:\\backup"] and settings.copies == 2
    assert settings.include_datasets is True and settings.auto is False
