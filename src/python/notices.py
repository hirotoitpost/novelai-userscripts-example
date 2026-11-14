"""
お知らせ(画面右下のベル)。各機能の「作業が要るか」を点検して、管理者に知らせる。

- 点検(check)は、Notice の並びを返す関数。CHECKS に足せば、ほかの機能も同じ仕組みに乗る
- 結果は少しのあいだ覚えておく(TTL)。設定を変えた・バックアップが終わった などのときは invalidate() で捨てる
- 「あとで」(snooze): その日時まで出さない。「今後知らせない」(dismiss): 中身の印(fingerprint。重さと題名)が
  変わるまで出さない(数字だけ変わる本文では出し直さない。重さが上がったら出し直す)
- 状態は全体の DB の notice_state に置く(サーバー全体のお知らせ。multi でも1つ)
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .db import get_system_connection

Level = Literal["info", "warning", "error"]
LEVELS: dict[str, int] = {"info": 0, "warning": 1, "error": 2}
GIB = 1 << 30
TTL = 600.0
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# 空きの大きさ(テストで差し替える)
_disk_usage = shutil.disk_usage


@dataclass
class Notice:
    key: str  # 同じ事がらなら同じ(例: backup.capacity、backup.offline:<id>)
    level: Level
    title: str
    body: str
    link: str = ""  # 画面の中の行き先(例: /admin?tab=ops#data-backup)
    link_label: str = "開く"
    help: str = ""  # ヘルプの記事(あとで足す /help/<slug>)
    feature: str = ""  # どの機能のお知らせか(表示の見出し)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.level}\n{self.title}".encode()).hexdigest()[:16]


Check = Callable[[], list[Notice]]
CHECKS: list[Check] = []


def check(fn: Check) -> Check:
    """点検を登録する(デコレーター)。"""
    CHECKS.append(fn)
    return fn


# ---- 状態(あとで・今後知らせない) ----


def _conn():
    conn = get_system_connection()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS notice_state (key TEXT PRIMARY KEY, snoozed_until TEXT, dismissed TEXT)"
    )
    return conn


def _states() -> dict[str, dict[str, Any]]:
    conn = _conn()
    try:
        return {row["key"]: dict(row) for row in conn.execute("SELECT * FROM notice_state")}
    finally:
        conn.close()


def snooze(key: str, days: float) -> None:
    until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO notice_state (key, snoozed_until) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET snoozed_until = excluded.snoozed_until",
            (key, until),
        )
        conn.commit()
    finally:
        conn.close()


def dismiss(key: str, fingerprint: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO notice_state (key, dismissed) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET dismissed = excluded.dismissed",
            (key, fingerprint),
        )
        conn.commit()
    finally:
        conn.close()


def restore_all() -> None:
    """「あとで」「今後知らせない」を全部やめる(全部また出す)。"""
    conn = _conn()
    try:
        conn.execute("DELETE FROM notice_state")
        conn.commit()
    finally:
        conn.close()


# ---- 集める ----

_cache: tuple[float, list[Notice]] | None = None
_lock = threading.Lock()


def invalidate() -> None:
    global _cache
    _cache = None


def _collect() -> list[Notice]:
    found: list[Notice] = []
    for fn in CHECKS:
        try:
            found.extend(fn())
        except Exception as exc:  # noqa: BLE001 点検の1つが失敗しても、ほかは出す
            found.append(Notice(
                key=f"notices.failed:{fn.__name__}", level="info", title="お知らせの点検の1つが失敗しました",
                body=f"{fn.__name__}: {exc}", feature="お知らせ",
            ))
    return found


def collect(force: bool = False) -> list[Notice]:
    """今のお知らせ(あとで・今後知らせない も含む全部)。少しのあいだ覚えておく。"""
    global _cache
    with _lock:
        if not force and _cache is not None and time.monotonic() - _cache[0] < TTL:
            return _cache[1]
        found = _collect()
        _cache = (time.monotonic(), found)
        return found


def listing(force: bool = False) -> dict[str, Any]:
    """画面に返す形: 出すもの(重い順)と、隠しているもの。"""
    states = _states()
    now = datetime.now(timezone.utc)
    active: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for notice in collect(force):
        row = {**asdict(notice), "fingerprint": notice.fingerprint}
        state = states.get(notice.key, {})
        until = state.get("snoozed_until")
        if state.get("dismissed") == notice.fingerprint:
            hidden.append({**row, "hidden": "dismissed"})
        elif until and datetime.fromisoformat(until) > now:
            hidden.append({**row, "hidden": "snoozed", "snoozed_until": until})
        else:
            active.append(row)
    active.sort(key=lambda n: -LEVELS[n["level"]])
    return {"notices": active, "hidden": hidden}


# ---- バックアップの点検 ----

BACKUP_LINK = "/admin?tab=ops#data-backup"
STALE_DAYS = 3
WARN_DAYS = 30
ERROR_DAYS = 7


def _days_until_short(rooms: list[float], copies: int, per_day: float, horizon: int = 3650) -> float | None:
    """
    写し先ごとの残り(閾値を引いた空き)と1日に増える量(写しの数ぶん込み)から、copies か所に置けなくなるまでの日数。
    置き方(空きの多い順に copies か所)をまねて、少しずつ減らしていく。増えていなければ None。
    """
    if per_day <= 0:
        return None
    rooms = sorted((max(0.0, r) for r in rooms), reverse=True)
    if len([r for r in rooms if r > 0]) < copies:
        return 0.0
    step = per_day / 20  # 1日を20回に分ける
    piece = step / copies
    for tick in range(horizon * 20):
        rooms.sort(reverse=True)
        if rooms[copies - 1] < piece:
            return tick / 20
        for i in range(copies):
            rooms[i] -= piece
    return None


def _growth_per_day(snapshots: list[dict[str, Any]], copies: int) -> float | None:
    """
    1日に増える量(写しの数ぶん込みのバイト/日)。その回に入っていたファイルの大きさの合計の、いちばん古い回からの増え方で見る
    (「その回に写した分」は、写し先を足したときの写し直しも入るので使わない)。回が2つ未満か、1日に満たなければ None。
    """
    if len(snapshots) < 2:
        return None
    newest, oldest = snapshots[0], snapshots[-1]
    days = (datetime.fromisoformat(newest["created_at"]) - datetime.fromisoformat(oldest["created_at"])).total_seconds() / 86400
    if days < 1:
        return None
    return max(0.0, (newest["bytes"] - oldest["bytes"]) / days * copies)


def _gb(n: float) -> str:
    return f"{n / GIB:.1f} GB" if n < 100 * GIB else f"{n / GIB:.0f} GB"


@check
def backup_notices() -> list[Notice]:
    from . import backup

    settings = backup.load_settings()
    feature = "バックアップ"
    if not settings.disks:
        return [Notice(
            key="backup.not_configured", level="info", title="画像・作品データのバックアップが設定されていません",
            body="外付けドライブなどを写し先にすると、1日1回自動で取ります。",
            link=BACKUP_LINK, link_label="設定する", help="backup", feature=feature,
        )]
    found: list[Notice] = []
    lives, problems = backup.open_disks(settings, scan=False)
    for path, message in problems.items():
        disk = next((d for d in settings.disks if d.path == path), None)
        found.append(Notice(
            key=f"backup.offline:{disk.id if disk and disk.id else path}", level="warning",
            title=f"バックアップの写し先 {path} が使えません",
            body=f"{message} ほかの写し先だけで取り続けます。つなぎ直すと、次の回でそろえます。",
            link=BACKUP_LINK, help="backup", feature=feature,
        ))
    if not lives:
        return found

    job = backup.job()
    if job.status == "error":
        found.append(Notice(
            key="backup.failed", level="error", title=f"前回の{'戻す処理' if job.kind == 'restore' else 'バックアップ'}が失敗しました",
            body=job.message, link=BACKUP_LINK, help="backup", feature=feature,
        ))

    listed = backup.list_snapshots(lives)
    if listed and settings.auto:
        last = datetime.fromisoformat(listed[0]["created_at"])
        days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        if days >= STALE_DAYS:
            found.append(Notice(
                key="backup.stale", level="warning", title=f"バックアップを {int(days)} 日取っていません",
                body="自動バックアップは、バックエンドが動いているあいだ1日1回取ります。失敗していないか確かめてください。",
                link=BACKUP_LINK, link_label="今すぐ取る", help="backup", feature=feature,
            ))

    health = backup.health(settings)
    if health and health.get("missing"):
        found.append(Notice(
            key="backup.missing", level="error", title="バックアップの中身が見つからないものがあります",
            body=f"{health['missing']:,} 個の中身が、つながっている写し先のどこにもありません(抜けている写し先にあるかもしれません)。",
            link=BACKUP_LINK, help="backup", feature=feature,
        ))
    if health and health.get("short"):
        found.append(Notice(
            key="backup.short", level="warning", title=f"{health['copies']} か所にそろっていない中身があります",
            body=f"{health['short']:,} 個。置ける写し先が足りません。写し先を足すか、閾値を見直してください。",
            link=BACKUP_LINK, help="backup-add-disk", feature=feature,
        ))

    # 空きの見込み(置く先として使っている写し先の、閾値を引いた空き)
    active = [lv for lv in lives if not lv.disk.retiring]
    rooms: list[float] = []
    for live in active:
        usage = _disk_usage(live.store)
        rooms.append(usage.free - backup._reserve(live.disk, usage.total))
    copies = min(settings.copies, max(1, len(active)))
    per_day = _growth_per_day(listed, copies)
    days_left = _days_until_short(rooms, copies, per_day) if per_day else None
    ranked = sorted(rooms, reverse=True)
    room_label = "、".join(f"{lv.disk.path}: {_gb(max(0, r))}" for lv, r in zip(active, rooms))
    if days_left is not None and days_left <= WARN_DAYS:
        found.append(Notice(
            key="backup.capacity", level="error" if days_left <= ERROR_DAYS else "warning",
            title="バックアップの写し先の空きが足りなくなりそうです",
            body=(
                f"今の増え方(1日あたり約 {_gb(per_day)}、写しの数ぶん込み)だと、あと約 {max(0, int(days_left))} 日で "
                f"{settings.copies} か所に置けなくなります。置ける残り: {room_label}。写し先を足すか、閾値を見直してください。"
            ),
            link=BACKUP_LINK, link_label="写し先を足す", help="backup-add-disk", feature=feature,
        ))
    elif per_day is None and len(ranked) >= copies and ranked[copies - 1] < 5 * GIB:
        found.append(Notice(
            key="backup.capacity", level="warning", title="バックアップの写し先の空きが足りなくなりそうです",
            body=f"{settings.copies} か所に置ける残りが少なくなっています。置ける残り: {room_label}。",
            link=BACKUP_LINK, link_label="写し先を足す", help="backup-add-disk", feature=feature,
        ))

    # 写し先がアプリと同じドライブ(ドライブが壊れると、元のデータとその写し先を一緒に失う)
    try:
        app_dev = backup._device_of(_PROJECT_ROOT)
    except OSError:
        app_dev = None
    for live in lives:
        try:
            same = app_dev is not None and backup._device_of(live.store) == app_dev
        except OSError:
            same = False
        if same:
            found.append(Notice(
                key=f"backup.same_drive:{live.disk.id or live.disk.path}", level="info",
                title=f"写し先 {live.disk.path} は、このアプリと同じドライブです",
                body=(
                    "そのドライブが壊れると、元のデータとその写し先を一緒に失います(置く数が 2 以上なら、ほかの写し先から戻せます)。"
                    "承知のうえで使うときは「今後知らせない」を押してください。"
                ),
                link=BACKUP_LINK, help="backup-remove-disk", feature=feature,
            ))
    return found


# ---- このアプリのドライブの空き ----

APP_MIN_FREE = 20 * GIB


@check
def storage_notices() -> list[Notice]:
    usage = _disk_usage(_PROJECT_ROOT)
    if usage.free >= APP_MIN_FREE:
        return []
    return [Notice(
        key="storage.app_drive", level="error" if usage.free < 5 * GIB else "warning",
        title="このアプリのドライブの空きが少なくなっています",
        body=(
            f"空き {_gb(usage.free)} / {_gb(usage.total)}。満杯になると画像を保存できなくなります。"
            "使っていない画像やデータセットを片付けるか、ごみ箱を空にしてください。"
        ),
        link="/admin?tab=ops#storage", link_label="保存領域を見る", help="storage", feature="保存領域",
    )]

