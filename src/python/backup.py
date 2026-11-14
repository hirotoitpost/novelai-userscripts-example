"""
画像・作品データのバックアップ(管理ページで写し先を指定する)。写し先(ディスク)はいくつでも足せる。

各写し先のフォルダの下に novelai-backup/ を作り、その中に:
- objects/ab/abcdef…: ファイルの中身(SHA-256 の名前で置く。同じ中身は何回取っても増えない)
- snapshots/backup-YYYYmmdd-HHMMSS.json: その回のファイルの一覧(パス → 中身の名前・大きさ・更新日時)
- disk.json: そのディスクの印(id。ドライブ文字が変わっても同じディスクと分かる)と、前の回のときの中身の数

複数の写し先への置き方(分散ストレージの「複製数」と同じ考え方):
- 一覧(snapshots)と DB は、つながっている全部の写し先に置く(小さく、どれか1台あれば戻せるように)
- 画像などの中身は、COPIES(既定 2)か所の写し先に置く。置く先は「残しておく空き」を引いた空きの多い順。
  空きが閾値(reserve_gb と reserve_percent の大きい方)を下回った写し先には新しい中身を置かない(読むのには使う)
- 足りない写し(写し先を足したとき・抜けていたとき・壊れたとき)は、毎回の最後にほかの写し先から写してそろえる
- 「外す準備」(retiring)の写し先には置かず、そこにしか無い中身をほかへ写す。そろったら外せる
- つながっていない写し先は飛ばす。戻すときは見つかった中身だけ書き戻し、見つからないものを知らせる

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
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db
from .db import get_system_connection

KEEP = 7
AUTO_INTERVAL = timedelta(days=1)
FOLDER = "novelai-backup"
GIB = 1 << 30
MAX_COPIES = 3
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_NAME = "data/app.db"
# 入れないもの(リポジトリのルートからのパスの頭)
# data/certs: LAN 用の認証局と、サーバーの証明書の秘密鍵(ca.key があれば、スマホが信頼する証明書をいくらでも作れる)。
# 写し先(外付けドライブ・クラウド)に置かない。scripts/make_lan_cert.py で作り直せる
_SKIP = ("data/models", "data/fonts", "data/eval", "data/backups", "data/run", "data/app.db", "data/exports", "data/certs")
# multi の利用者ごとの DB(db.user_db_path)。全体の DB と同じく、壊れない写しを作ってから入れる
_USER_DB = re.compile(r"data/users/\d+/app\.db")
_DATASET_PREFIXES = ("outputs/training_data", "outputs/lora_dataset")
_CHUNK = 1 << 20
# 空きの大きさ(テストで差し替える)
_disk_usage = shutil.disk_usage


def _device_of(path: Path) -> int:
    """そのフォルダのあるドライブの番号(同じドライブか見分ける。テストとヘルプの見本の環境で差し替える)。"""
    return os.stat(path).st_dev


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


# ---- 設定 ----


@dataclass
class Disk:
    """写し先の1つ。reserve_gb と reserve_percent の大きい方の空きを残す(下回ったら新しい中身を置かない)。"""

    path: str
    id: str = ""
    reserve_gb: float = 50.0
    reserve_percent: float = 10.0
    retiring: bool = False


@dataclass
class Settings:
    disks: list[Disk] = field(default_factory=list)
    copies: int = 2
    include_datasets: bool = False
    auto: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def settings_from_dict(data: dict[str, Any]) -> Settings:
    # 写し先が1つだった頃の設定({"destination": "E:\\backup"})は、その写し先1つとして読む
    disks_data = data.get("disks")
    if disks_data is None and data.get("destination"):
        disks_data = [{"path": data["destination"]}]
    disks = [Disk(**{k: v for k, v in d.items() if k in Disk.__dataclass_fields__}) for d in disks_data or []]
    copies = int(data.get("copies", 2))
    return Settings(
        disks=disks,
        copies=max(1, min(MAX_COPIES, copies)),
        include_datasets=bool(data.get("include_datasets", False)),
        auto=bool(data.get("auto", True)),
    )


def load_settings() -> Settings:
    # バックアップはサーバー全体のもの(運営者の設定)。multi でも全体の DB に 1 つだけ置く
    conn = get_system_connection()
    try:
        row = conn.execute("SELECT value FROM backup_config WHERE id = 1").fetchone()
    finally:
        conn.close()
    return settings_from_dict(json.loads(row["value"]) if row else {})


def save_settings(settings: Settings) -> None:
    conn = get_system_connection()
    try:
        conn.execute(
            "INSERT INTO backup_config (id, value) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET value = excluded.value",
            (json.dumps(settings.to_dict(), ensure_ascii=False),),
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


def _read_disk_json(store: Path) -> dict[str, Any]:
    try:
        return json.loads((store / "disk.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_disk_json(store: Path, data: dict[str, Any]) -> None:
    tmp = store / "disk.json.part"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _replace(tmp, store / "disk.json")


def _replace(tmp: Path, target: Path, tries: int = 20) -> None:
    """tmp を target に置き換える。Windows では、ほかのスレッド(管理ページの見回り)が target を読んでいると失敗するので、少し待って何回か試す。"""
    for attempt in range(tries):
        try:
            tmp.replace(target)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.05)


def register_disk(disk: Disk) -> Disk:
    """設定を保存するとき: 写し先を確かめ、ディスクの印(id)を読む(無ければ作る)。書けなければ ValueError。"""
    store = check_destination(disk.path)
    info = _read_disk_json(store)
    if not info.get("id"):
        info = {"id": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat()}
        _write_disk_json(store, info)
    return Disk(disk.path.strip(), info["id"], disk.reserve_gb, disk.reserve_percent, disk.retiring)


# ---- つながっている写し先 ----


@dataclass
class _Live:
    disk: Disk
    store: Path
    objects: set[str]
    full: bool = False  # この回で書き込みに失敗した(満杯など)。この回はもう置かない


def _scan_objects(store: Path) -> set[str]:
    folder = store / "objects"
    if not folder.is_dir():
        return set()
    return {p.name for p in folder.glob("*/*") if p.is_file() and not p.name.endswith(".part")}


def open_disks(settings: Settings, scan: bool = True) -> tuple[list[_Live], dict[str, str]]:
    """つながっている写し先と、使えない写し先(パス → 理由)。"""
    lives: list[_Live] = []
    problems: dict[str, str] = {}
    for disk in settings.disks:
        try:
            store = check_destination(disk.path)
        except ValueError as exc:
            problems[disk.path] = f"つながっていません: {exc}"
            continue
        found = _read_disk_json(store).get("id", "")
        if disk.id and found != disk.id:
            # ドライブ文字が入れ替わって、別のディスクが同じ場所に来たとき。まちがって片付けないよう使わない
            problems[disk.path] = "登録したときと別のディスクです(ドライブ文字が変わっていないか確かめてください)。"
            continue
        lives.append(_Live(disk, store, _scan_objects(store) if scan else set()))
    return lives, problems


def _object_path(store: Path, digest: str) -> Path:
    return store / "objects" / digest[:2] / digest


def _reserve(disk: Disk, total: int) -> int:
    return int(max(disk.reserve_gb * GIB, total * disk.reserve_percent / 100))


def _room(live: _Live) -> int:
    """閾値を引いた空き(マイナスなら新しい中身は置かない)。"""
    usage = _disk_usage(live.store)
    return usage.free - _reserve(live.disk, usage.total)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _store(store: Path, source: Path, digest: str) -> bool:
    """中身を objects/ に置く(もうあれば何もしない)。置いたら True。"""
    target = _object_path(store, digest)
    if target.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    try:
        shutil.copyfile(source, tmp)
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return True


def _holders(lives: list[_Live], digest: str, *, include_retiring: bool = False) -> list[_Live]:
    return [lv for lv in lives if digest in lv.objects and (include_retiring or not lv.disk.retiring)]


def _place(lives: list[_Live], digest: str, source: Path, size: int, copies: int) -> tuple[int, int]:
    """
    中身を、外す準備でない写し先 copies か所にそろえる(空きの多い順。閾値を下回った写し先には置かない)。
    戻り値は (そろった数, 新しく置いた数)。元のファイルが読めなければ OSError。
    """
    holders = _holders(lives, digest)
    placed = 0
    while len(holders) < copies:
        candidates = [
            (room, lv) for lv in lives
            if not lv.disk.retiring and not lv.full and digest not in lv.objects and (room := _room(lv)) >= size
        ]
        if not candidates:
            break
        best = max(candidates, key=lambda c: c[0])[1]
        try:
            _store(best.store, source, digest)
        except OSError:
            if not source.is_file():
                raise
            best.full = True
            continue
        best.objects.add(digest)
        holders.append(best)
        placed += 1
    return len(holders), placed


# ---- 一覧(snapshots) ----


def _summary(name: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "created_at": data["created_at"],
        "files": len(data["files"]),
        "bytes": sum(f["size"] for f in data["files"].values()),
        "added": data.get("added", 0),
        "added_bytes": data.get("added_bytes", 0),
        "include_datasets": data.get("include_datasets", False),
        "reason": data.get("reason", ""),
    }


def snapshots(store: Path) -> list[dict[str, Any]]:
    """1つの写し先にある回の一覧(新しい順)。"""
    folder = store / "snapshots"
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.glob("backup-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result.append(_summary(path.stem, data))
    return result


def _snapshot_paths(lives: list[_Live]) -> dict[str, list[Path]]:
    """回の名前 → それを持っている写し先の一覧ファイル。"""
    found: dict[str, list[Path]] = {}
    for live in lives:
        folder = live.store / "snapshots"
        if folder.is_dir():
            for path in folder.glob("backup-*.json"):
                found.setdefault(path.stem, []).append(path)
    return found


def _read_snapshot(paths: list[Path]) -> dict[str, Any] | None:
    for path in paths:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def list_snapshots(lives: list[_Live]) -> list[dict[str, Any]]:
    """つながっている写し先全部の回の一覧(新しい順)。"""
    result = []
    for name, paths in sorted(_snapshot_paths(lives).items(), reverse=True):
        data = _read_snapshot(paths)
        if data is not None:
            result.append(_summary(name, data))
    return result


def _sync_snapshots(lives: list[_Live]) -> None:
    """どの写し先にも同じ一覧を置く(抜けていた写し先が戻ったとき・写し先を足したとき)。"""
    for name, paths in _snapshot_paths(lives).items():
        have = {p.parent.parent for p in paths}
        for live in lives:
            if live.store in have:
                continue
            try:
                (live.store / "snapshots").mkdir(parents=True, exist_ok=True)
                shutil.copyfile(paths[0], live.store / "snapshots" / f"{name}.json")
            except OSError:
                continue


def _previous(lives: list[_Live]) -> dict[str, Any]:
    found = _snapshot_paths(lives)
    for name in sorted(found, reverse=True):
        data = _read_snapshot(found[name])
        if data is not None:
            return data["files"]
    return {}


# ---- バックアップを取る ----


Progress = Callable[[str, int, int], None]


def _need_disks(settings: Settings) -> None:
    if not settings.disks:
        raise ValueError("先に写し先のフォルダを指定してください。")


def run_backup(settings: Settings, reason: str = "手動", progress: Progress | None = None) -> dict[str, Any]:
    """1回分のバックアップを取る。戻り値はその回の要約。"""
    _need_disks(settings)
    lives, problems = open_disks(settings)
    if not lives:
        raise ValueError("つながっている写し先がありません。" + " ".join(f"{p}: {m}" for p, m in problems.items()))
    report = progress or (lambda message, done, total: None)
    for live in lives:
        _forget_skipped(live.store)
    _sync_snapshots(lives)
    previous = _previous(lives)
    copies = settings.copies
    files: dict[str, dict[str, Any]] = {}
    added = 0
    added_bytes = 0

    # DB は書き込み中でも壊れない写しを作ってから、つながっている全部の写し先に入れる
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
            stored = 0
            for live in lives:
                if live.disk.retiring:
                    continue
                try:
                    if _store(live.store, copy, digest):
                        added += 1
                        added_bytes += size
                    live.objects.add(digest)
                    stored += 1
                except OSError:
                    live.full = True
            if not stored:
                raise ValueError("DB をどの写し先にも書き込めませんでした(空きが無いか、外す準備の写し先だけです)。")
            files[rel] = {"hash": digest, "size": size, "mtime": 0}

    targets = _targets(settings.include_datasets)
    for index, path in enumerate(targets, start=1):
        rel = path.relative_to(_PROJECT_ROOT).as_posix()
        try:
            stat = path.stat()
            old = previous.get(rel)
            if old and old["size"] == stat.st_size and old["mtime"] == stat.st_mtime_ns and _holders(
                lives, old["hash"], include_retiring=True
            ):
                digest = old["hash"]
            else:
                digest = _hash_file(path)
            have, placed = _place(lives, digest, path, stat.st_size, copies)
        except OSError:
            # 写しているあいだに消えたファイルなどは飛ばす
            continue
        if placed:
            added += placed
            added_bytes += stat.st_size * placed
        if not have and not _holders(lives, digest, include_retiring=True):
            raise ValueError(
                f"置ける写し先がありません(どの写し先も空きが閾値を下回っています)。{rel} で止めました。"
                "写し先を足すか、閾値を下げてください。"
            )
        files[rel] = {"hash": digest, "size": stat.st_size, "mtime": stat.st_mtime_ns}
        if index % 50 == 0 or index == len(targets):
            report("ファイルを写しています", index, len(targets))

    now = datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    taken = _snapshot_paths(lives)
    name = f"backup-{stamp}"
    # 同じ秒に2回取ったとき(戻す直前の自動バックアップなど)は、名前に番号を足す
    suffix = 2
    while name in taken:
        name = f"backup-{stamp}-{suffix}"
        suffix += 1
    data = {
        "created_at": now.astimezone(timezone.utc).isoformat(),
        "reason": reason,
        "include_datasets": settings.include_datasets,
        "copies": copies,
        "added": added,
        "added_bytes": added_bytes,
        "files": files,
    }
    text = json.dumps(data, ensure_ascii=False)
    written = 0
    for live in lives:
        try:
            (live.store / "snapshots").mkdir(parents=True, exist_ok=True)
            tmp_path = live.store / "snapshots" / f"{name}.json.part"
            tmp_path.write_text(text, encoding="utf-8")
            _replace(tmp_path, live.store / "snapshots" / f"{name}.json")
            written += 1
        except OSError:
            continue
    if not written:
        raise ValueError("この回の一覧をどの写し先にも書き込めませんでした。")
    report("古いバックアップを片付けています", 0, 1)
    removed = _prune(lives)
    health = _repair(lives, copies, report)
    return {
        "name": name, "files": len(files), "added": added + health["repaired"],
        "added_bytes": added_bytes + health["repaired_bytes"], "removed": removed,
        "short": health["short"], "repaired": health["repaired"], "missing": health["missing"],
        "disks": len(lives), "skipped_disks": problems,
    }


def _referenced(lives: list[_Live]) -> dict[str, int]:
    """残っている回のどれかが使う中身(名前 → 大きさ)。"""
    used: dict[str, int] = {}
    for paths in _snapshot_paths(lives).values():
        data = _read_snapshot(paths)
        if data is None:
            continue
        for f in data["files"].values():
            used[f["hash"]] = f["size"]
    return used


def _repair(lives: list[_Live], copies: int, report: Progress) -> dict[str, Any]:
    """
    残っている回が使う中身を、copies か所にそろえる(ほかの写し先から写す)。そのあと、写し先ごとの数を disk.json に残す。
    short: copies か所にそろわなかった中身の数。missing: つながっている写し先のどこにも無い中身の数。
    """
    used = _referenced(lives)
    short = missing = repaired = repaired_bytes = 0
    total = len(used)
    for index, (digest, size) in enumerate(used.items(), start=1):
        sources = _holders(lives, digest, include_retiring=True)
        if not sources:
            missing += 1
            continue
        have = len(_holders(lives, digest))
        if have < copies:
            try:
                have, placed = _place(lives, digest, _object_path(sources[0].store, digest), size, copies)
            except OSError:
                placed = 0
            repaired += placed
            repaired_bytes += size * placed
        if have < copies:
            short += 1
        if index % 500 == 0 or index == total:
            report(f"{copies} か所にそろえています", index, total)

    active = [lv for lv in lives if not lv.disk.retiring]
    target = max(1, min(copies, len(active)))
    checked_at = datetime.now(timezone.utc).isoformat()
    for live in lives:
        mine = [d for d in used if d in live.objects]
        info = _read_disk_json(live.store)
        info.update({
            "objects": len(mine),
            "bytes": sum(used[d] for d in mine),
            "checked_at": checked_at,
            "copies": copies,
            "short": short,
            "missing": missing,
            # 外す準備の写し先: ここにしか無い(ほかで target か所にそろっていない)中身の数。0 なら外せる。
            # 外す準備でないときは数えていない(None)。0 にすると、あとで外す準備にした直後(まだ写していない)に「外せます」と出てしまう
            "pending": sum(1 for d in mine if len(_holders(lives, d)) < target) if live.disk.retiring else None,
        })
        try:
            _write_disk_json(live.store, info)
        except OSError:
            continue
    return {"short": short, "missing": missing, "repaired": repaired, "repaired_bytes": repaired_bytes}


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


def _forget_skipped(store: Path) -> int:
    """
    前の回の記録から、今は入れないもの(_SKIP。DB は除く)を外す。中身はこの回の最後の片付けで消える
    (data/certs を入れないようにする前の回に入っていた秘密鍵を、写し先に残さないため)。外した数。
    """
    forgotten = 0
    folder = store / "snapshots"
    if not folder.is_dir():
        return 0
    for path in folder.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        drop = [rel for rel in data["files"] if rel.startswith(_SKIP) and not _is_db(rel)]
        if not drop:
            continue
        for rel in drop:
            del data["files"][rel]
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _replace(tmp, path)
        forgotten += len(drop)
    return forgotten


def _prune(lives: list[_Live], keep: int = KEEP) -> int:
    """KEEP より古い回を(つながっている全部の写し先から)消し、どの回からも使われていない中身を片付ける。消した回の数。"""
    found = _snapshot_paths(lives)
    names = sorted(found, reverse=True)
    for name in names[keep:]:
        for path in found[name]:
            path.unlink(missing_ok=True)
    used: set[str] = set()
    for name in names[:keep]:
        data = _read_snapshot(found[name])
        if data is None:
            # 読めない一覧があるときは、まちがって中身を消さないよう片付けをやめる
            return len(names[keep:])
        used.update(f["hash"] for f in data["files"].values())
    for live in lives:
        for digest in list(live.objects - used):
            _object_path(live.store, digest).unlink(missing_ok=True)
            live.objects.discard(digest)
    return len(names[keep:])


def prune(store: Path, keep: int = KEEP) -> int:
    """1つの写し先だけを片付ける(古い呼び方)。"""
    return _prune([_Live(Disk(str(store.parent)), store, _scan_objects(store))], keep)


# ---- 戻す ----


def restore(settings: Settings, name: str, progress: Progress | None = None) -> dict[str, Any]:
    """
    選んだ回に戻す。先に今の状態を1回バックアップする。DB(multi の利用者ごとの DB も)は SQLite のバックアップ機能で入れ替え(動いている
    バックエンドのままでよい)、ファイルは無い・中身が違うものだけ書き戻す(その回より後にできたファイルは残す)。
    つながっている写し先のどこにも無い中身は飛ばして、その数と例を返す。
    """
    _need_disks(settings)
    if not name.startswith("backup-"):
        raise ValueError("そのバックアップはありません。")
    lives, _ = open_disks(settings)
    data = _read_snapshot(_snapshot_paths(lives).get(name, []))
    if data is None:
        raise ValueError("そのバックアップはありません(その回を持つ写し先がつながっていません)。")
    report = progress or (lambda message, done, total: None)
    report("戻す前に、今の状態をバックアップしています", 0, 1)
    before = run_backup(settings, reason=f"{name} に戻す前", progress=progress)
    lives, _ = open_disks(settings)

    def locate(digest: str) -> Path | None:
        holders = _holders(lives, digest, include_retiring=True)
        return _object_path(holders[0].store, digest) if holders else None

    missing: list[str] = []
    for rel, entry in [(rel, f) for rel, f in data["files"].items() if _is_db(rel)]:
        report("DB を戻しています", 0, 1)
        found = locate(entry["hash"])
        if found is None:
            missing.append(rel)
            continue
        target_path = _db_path(rel)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(found)
        try:
            target = sqlite3.connect(target_path)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
    written = 0
    # 前のバックアップに入っていても、今は入れないもの(data/certs など)は書き戻さない
    items = [(rel, f) for rel, f in data["files"].items() if not _is_db(rel) and not rel.startswith(_SKIP)]
    for index, (rel, f) in enumerate(items, start=1):
        target_path = _PROJECT_ROOT / rel
        if index % 50 == 0 or index == len(items):
            report("ファイルを戻しています", index, len(items))
        if target_path.is_file() and target_path.stat().st_size == f["size"] and _hash_file(target_path) == f["hash"]:
            continue
        found = locate(f["hash"])
        if found is None:
            missing.append(rel)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(found, target_path)
        written += 1
    return {"name": name, "written": written, "before": before["name"], "missing": len(missing), "missing_examples": missing[:10]}


# ---- 管理ページに見せる様子 ----


def disk_states(settings: Settings) -> list[dict[str, Any]]:
    """写し先ごとの様子(つながっているか・空き・閾値・前の回のときの中身の数)。中身は数えない(速く返す)。"""
    lives, problems = open_disks(settings, scan=False)
    by_path = {lv.disk.path: lv for lv in lives}
    devices: dict[int, int] = {}
    for live in lives:
        try:
            dev = _device_of(live.store)
        except OSError:
            continue
        devices[dev] = devices.get(dev, 0) + 1
    result = []
    for disk in settings.disks:
        row: dict[str, Any] = {**asdict(disk), "available": False, "error": problems.get(disk.path)}
        live = by_path.get(disk.path)
        if live is not None:
            usage = _disk_usage(live.store)
            reserve = _reserve(disk, usage.total)
            info = _read_disk_json(live.store)
            try:
                shared = devices.get(_device_of(live.store), 0) > 1
            except OSError:
                shared = False
            row.update({
                "available": True,
                "total": usage.total,
                "free": usage.free,
                "reserve_bytes": reserve,
                "accepting": not disk.retiring and usage.free > reserve,
                "objects": info.get("objects"),
                "bytes": info.get("bytes"),
                "pending": info.get("pending"),
                "checked_at": info.get("checked_at"),
                "same_drive": shared,
            })
        result.append(row)
    return result


def health(settings: Settings) -> dict[str, Any] | None:
    """前の回のときの、そろい具合(つながっている写し先のどれかの disk.json から)。"""
    lives, _ = open_disks(settings, scan=False)
    infos = [i for i in (_read_disk_json(lv.store) for lv in lives) if i.get("checked_at")]
    if not infos:
        return None
    latest = max(infos, key=lambda i: i["checked_at"])
    return {k: latest.get(k) for k in ("checked_at", "copies", "short", "missing")}


def all_snapshots(settings: Settings) -> list[dict[str, Any]]:
    lives, _ = open_disks(settings, scan=False)
    return list_snapshots(lives)


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
            # お知らせ(空きの見込み・失敗)を出し直す
            from . import notices

            notices.invalidate()

    current.thread = threading.Thread(target=runner, name=f"backup-{kind}", daemon=True)
    current.thread.start()
    return current


def due(settings: Settings, now: datetime | None = None) -> bool:
    """自動バックアップを取る頃か(つながっている写し先があり、前の回から1日たった)。"""
    if not settings.auto or not settings.disks:
        return False
    lives, _ = open_disks(settings, scan=False)
    if not lives:
        return False
    listed = list_snapshots(lives)
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
