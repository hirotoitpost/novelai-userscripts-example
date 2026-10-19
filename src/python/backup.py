"""
画像・作品データのバックアップ(管理ページで写し先を指定する)。

写し先のフォルダの下に novelai-backup/ を作り、その中に:
- objects/ab/abcdef…: ファイルの中身(SHA-256 の名前で1回だけ置く。同じ中身は何回取っても増えない)
- snapshots/backup-YYYYmmdd-HHMMSS.json: その回のファイルの一覧(パス → 中身の名前・大きさ・更新日時)

2回目からは、前の回と大きさ・更新日時が同じファイルは読み直さない(増えた・変わったファイルだけ写す)。
外付けドライブ(exFAT など、ハードリンクが使えないもの)でも同じように動く。

対象:
- DB(data/app.db。書き込み中でも壊れない写しを作ってから入れる)
- outputs/ の画像(生成画像・コマ・ページ・出版物 など)と、data/ の取り込んだ作品・素材・ごみ箱
- キャラ別データセット(outputs/training_data* など)は、設定で入れるか選ぶ
入れないもの: モデル・フォント・評価データ(取り直せる)、.env(トークン)、サーバーのログ、DB のバックアップ

決めた数(KEEP)より古い回は消し、どの回からも使われなくなった中身も片付ける。
戻すときは、先に今の状態を1回バックアップしてから、選んだ回の DB を戻し、その回のファイルのうち無い・違うものを
書き戻す(その回より後にできたファイルは消さない)。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db
from .db import get_system_connection

KEEP = 7
AUTO_INTERVAL = timedelta(days=1)
FOLDER = "novelai-backup"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_NAME = "data/app.db"
# 入れないもの(リポジトリのルートからのパスの頭)
_SKIP = ("data/models", "data/fonts", "data/eval", "data/backups", "data/run", "data/app.db")
# multi の利用者ごとの DB(db.user_db_path)。全体の DB と同じく、壊れない写しを作ってから入れる
_USER_DB = re.compile(r"data/users/\d+/app\.db")


def _is_db(rel: str) -> bool:
    return rel == DB_NAME or _USER_DB.fullmatch(rel) is not None


def _db_files() -> list[str]:
    """写す DB(全体の DB と、multi の利用者ごとの DB)。リポジトリのルートからのパス。"""
    users = sorted(
        path.relative_to(_PROJECT_ROOT).as_posix() for path in (_PROJECT_ROOT / "data" / "users").glob("*/app.db")
    )
    return [DB_NAME, *[rel for rel in users if _is_db(rel)]]


def _db_path(rel: str) -> Path:
    # 全体の DB は db._DB_PATH(テストで差し替える)、利用者の DB はそのとなりの users/ の下
    return db._DB_PATH if rel == DB_NAME else db._DB_PATH.parent / Path(rel).relative_to("data")
_DATASET_PREFIXES = ("outputs/training_data", "outputs/lora_dataset")
_CHUNK = 1 << 20


@dataclass
class Settings:
    destination: str = ""
    include_datasets: bool = False
    auto: bool = True


def load_settings() -> Settings:
    # バックアップはサーバー全体のもの(運営者の設定)。multi でも全体の DB に 1 つだけ置く
    conn = get_system_connection()
    try:
        row = conn.execute("SELECT value FROM backup_config WHERE id = 1").fetchone()
    finally:
        conn.close()
    data = json.loads(row["value"]) if row else {}
    return Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})


def save_settings(settings: Settings) -> None:
    conn = get_system_connection()
    try:
        conn.execute(
            "INSERT INTO backup_config (id, value) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET value = excluded.value",
            (json.dumps(settings.__dict__, ensure_ascii=False),),
        )
        conn.commit()
    finally:
        conn.close()


def check_destination(path: str) -> Path:
    """写し先のフォルダ。無ければ作る。書けなければ ValueError。"""
    if not path.strip():
        raise ValueError("写し先のフォルダを指定してください。")
    base = Path(path.strip()).expanduser()
    if not base.is_absolute():
        raise ValueError("写し先は、ドライブから始まるフォルダの場所で指定してください(例: E:\\backup)。")
    root = _PROJECT_ROOT.resolve()
    if base.resolve() == root or root in base.resolve().parents:
        raise ValueError("このアプリのフォルダの中には写せません(外付けドライブなど、別の場所を指定してください)。")
    try:
        (base / FOLDER).mkdir(parents=True, exist_ok=True)
        probe = base / FOLDER / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"写し先に書き込めません: {exc}") from exc
    return base / FOLDER


def _targets(include_datasets: bool) -> list[Path]:
    """バックアップするファイル(DB は別に扱う)。"""
    files: list[Path] = []
    for top in ("outputs", "data"):
        folder = _PROJECT_ROOT / top
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(_PROJECT_ROOT).as_posix()
            if rel.startswith(_SKIP) or _is_db(rel) or rel.endswith((".db-wal", ".db-shm", ".part")):
                continue
            if not include_datasets and rel.startswith(_DATASET_PREFIXES):
                continue
            files.append(path)
    return files


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _store(store: Path, source: Path, digest: str) -> bool:
    """中身を objects/ に置く(もうあれば何もしない)。置いたら True。"""
    target = store / "objects" / digest[:2] / digest
    if target.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    shutil.copyfile(source, tmp)
    tmp.replace(target)
    return True


def snapshots(store: Path) -> list[dict[str, Any]]:
    """取った回の一覧(新しい順)。"""
    folder = store / "snapshots"
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.glob("backup-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result.append({
            "name": path.stem,
            "created_at": data["created_at"],
            "files": len(data["files"]),
            "bytes": sum(f["size"] for f in data["files"].values()),
            "added": data.get("added", 0),
            "added_bytes": data.get("added_bytes", 0),
            "include_datasets": data.get("include_datasets", False),
            "reason": data.get("reason", ""),
        })
    return result


def _previous(store: Path) -> dict[str, Any]:
    latest = snapshots(store)
    if not latest:
        return {}
    data = json.loads((store / "snapshots" / f"{latest[0]['name']}.json").read_text(encoding="utf-8"))
    return data["files"]


Progress = Callable[[str, int, int], None]


def run_backup(settings: Settings, reason: str = "手動", progress: Progress | None = None) -> dict[str, Any]:
    """1回分のバックアップを取る。戻り値はその回の要約。"""
    store = check_destination(settings.destination)
    report = progress or (lambda message, done, total: None)
    previous = _previous(store)
    files: dict[str, dict[str, Any]] = {}
    added = 0
    added_bytes = 0

    # DB は書き込み中でも壊れない写しを作ってから入れる
    report("DB を写しています", 0, 1)
    with tempfile.TemporaryDirectory() as tmp:
        for index, rel in enumerate(_db_files()):
            copy = Path(tmp) / f"{index}.db"
            source = sqlite3.connect(_db_path(rel))
            try:
                target = sqlite3.connect(copy)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
            digest = _hash_file(copy)
            size = copy.stat().st_size
            if _store(store, copy, digest):
                added += 1
                added_bytes += size
            files[rel] = {"hash": digest, "size": size, "mtime": 0}

    targets = _targets(settings.include_datasets)
    for index, path in enumerate(targets, start=1):
        rel = path.relative_to(_PROJECT_ROOT).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        old = previous.get(rel)
        if old and old["size"] == stat.st_size and old["mtime"] == stat.st_mtime_ns and (
            store / "objects" / old["hash"][:2] / old["hash"]
        ).is_file():
            digest = old["hash"]
        else:
            try:
                digest = _hash_file(path)
                if _store(store, path, digest):
                    added += 1
                    added_bytes += stat.st_size
            except OSError:
                # 写しているあいだに消えたファイルなどは飛ばす
                continue
        files[rel] = {"hash": digest, "size": stat.st_size, "mtime": stat.st_mtime_ns}
        if index % 50 == 0 or index == len(targets):
            report("ファイルを写しています", index, len(targets))

    now = datetime.now()
    name = f"backup-{now.strftime('%Y%m%d-%H%M%S')}"
    (store / "snapshots").mkdir(parents=True, exist_ok=True)
    # 同じ秒に2回取ったとき(戻す直前の自動バックアップなど)は、名前に番号を足す
    suffix = 2
    while (store / "snapshots" / f"{name}.json").exists():
        name = f"backup-{now.strftime('%Y%m%d-%H%M%S')}-{suffix}"
        suffix += 1
    data = {
        "created_at": now.astimezone(timezone.utc).isoformat(),
        "reason": reason,
        "include_datasets": settings.include_datasets,
        "added": added,
        "added_bytes": added_bytes,
        "files": files,
    }
    tmp_path = store / "snapshots" / f"{name}.json.part"
    tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(store / "snapshots" / f"{name}.json")
    report("古いバックアップを片付けています", 0, 1)
    removed = prune(store)
    return {"name": name, "files": len(files), "added": added, "added_bytes": added_bytes, "removed": removed}


def prune(store: Path, keep: int = KEEP) -> int:
    """KEEP より古い回を消し、どの回からも使われていない中身を片付ける。消した回の数。"""
    listed = snapshots(store)
    old = listed[keep:]
    for snap in old:
        (store / "snapshots" / f"{snap['name']}.json").unlink(missing_ok=True)
    used: set[str] = set()
    for snap in listed[:keep]:
        data = json.loads((store / "snapshots" / f"{snap['name']}.json").read_text(encoding="utf-8"))
        used.update(f["hash"] for f in data["files"].values())
    objects = store / "objects"
    if objects.is_dir():
        for path in objects.glob("*/*"):
            if path.is_file() and path.name not in used:
                path.unlink()
    return len(old)


def restore(settings: Settings, name: str, progress: Progress | None = None) -> dict[str, Any]:
    """
    選んだ回に戻す。先に今の状態を1回バックアップする。DB(multi の利用者ごとの DB も)は SQLite のバックアップ機能で入れ替え(動いている
    バックエンドのままでよい)、ファイルは無い・中身が違うものだけ書き戻す(その回より後にできたファイルは残す)。
    """
    store = check_destination(settings.destination)
    path = store / "snapshots" / f"{name}.json"
    if not name.startswith("backup-") or not path.is_file():
        raise ValueError("そのバックアップはありません。")
    data = json.loads(path.read_text(encoding="utf-8"))
    report = progress or (lambda message, done, total: None)
    report("戻す前に、今の状態をバックアップしています", 0, 1)
    before = run_backup(settings, reason=f"{name} に戻す前", progress=progress)

    missing = [rel for rel, f in data["files"].items() if not (store / "objects" / f["hash"][:2] / f["hash"]).is_file()]
    if missing:
        raise ValueError(f"バックアップの中身が欠けています({len(missing)}件。例: {missing[0]})")
    db_entries = [(rel, f) for rel, f in data["files"].items() if _is_db(rel)]
    for rel, entry in db_entries:
        report("DB を戻しています", 0, 1)
        target_path = _db_path(rel)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(store / "objects" / entry["hash"][:2] / entry["hash"])
        try:
            target = sqlite3.connect(target_path)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
    written = 0
    items = [(rel, f) for rel, f in data["files"].items() if not _is_db(rel)]
    for index, (rel, f) in enumerate(items, start=1):
        target_path = _PROJECT_ROOT / rel
        if target_path.is_file() and target_path.stat().st_size == f["size"] and _hash_file(target_path) == f["hash"]:
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(store / "objects" / f["hash"][:2] / f["hash"], target_path)
        written += 1
        if index % 50 == 0 or index == len(items):
            report("ファイルを戻しています", index, len(items))
    return {"name": name, "written": written, "before": before["name"]}


# ---- 処理の切り離しと、1日1回の自動バックアップ ----


@dataclass
class Job:
    kind: str = ""
    status: str = "idle"
    message: str = ""
    done: int = 0
    total: int = 0
    result: dict[str, Any] | None = None
    started_at: str | None = None
    finished_at: str | None = None
    thread: Any = field(default=None, repr=False)


_job = Job()
_lock = threading.Lock()


def job() -> Job:
    return _job


def start(kind: str, work: Callable[[Progress], dict[str, Any]]) -> Job:
    """バックアップ・戻すを、切り離したスレッドで1つずつ進める。動いていれば RuntimeError。"""
    global _job
    with _lock:
        if _job.status == "running":
            raise RuntimeError("バックアップ(または戻す処理)が動いています。終わってからにしてください。")
        _job = Job(kind=kind, status="running", message="開始しました", started_at=datetime.now(timezone.utc).isoformat())
        current = _job

    def progress(message: str, done: int, total: int) -> None:
        current.message, current.done, current.total = message, done, total

    def runner() -> None:
        try:
            current.result = work(progress)
            current.status = "done"
            current.message = "終わりました"
        except Exception as exc:  # noqa: BLE001 失敗も状態として返す
            current.status = "error"
            current.message = str(exc)
        finally:
            current.finished_at = datetime.now(timezone.utc).isoformat()

    current.thread = threading.Thread(target=runner, name=f"backup-{kind}", daemon=True)
    current.thread.start()
    return current


def due(settings: Settings, now: datetime | None = None) -> bool:
    """自動バックアップを取る頃か(写し先があり、前の回から1日たった)。"""
    if not settings.auto or not settings.destination:
        return False
    try:
        store = check_destination(settings.destination)
    except ValueError:
        return False
    listed = snapshots(store)
    if not listed:
        return True
    last = datetime.fromisoformat(listed[0]["created_at"])
    return (now or datetime.now(timezone.utc)) - last >= AUTO_INTERVAL


def auto_tick() -> bool:
    """自動バックアップの見回り(バックエンドが1時間ごとに呼ぶ)。取り始めたら True。"""
    settings = load_settings()
    if _job.status == "running" or not due(settings):
        return False
    try:
        start("backup", lambda progress: run_backup(settings, reason="自動(1日1回)", progress=progress))
    except RuntimeError:
        return False
    return True
