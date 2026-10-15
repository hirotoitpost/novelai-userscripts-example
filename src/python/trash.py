"""
ごみ箱: ギャラリー・本棚・キャラで消したものを、RETENTION_DAYS のあいだは元に戻せるようにする。

消すときに、消える DB の行(そのままの値)と画像ファイルを預かる。ファイルは data/trash/{番号}/ へ移し、行は
trash_entries に JSON で残す。戻すときは、行を元の id のまま入れ直し、ファイルを元の場所へ移す。
期限を過ぎたものは、一覧を見たとき・新しく消したときに片付ける(ファイルも消える)。

種類(kind):
- history_image: 生成履歴の画像1枚(履歴の最後の1枚なら履歴の行ごと)
- panel / illustration: 漫画のコマ・挿絵ページの行と画像
- dataset_image: キャラ別データセットの画像とキャプション(ファイルだけ)
- book: 物語(シーン・コマ・ページ・登場キャラの割り当て・シリーズ・作者・関連・ブックマーク)と画像
- character: キャラ(シーンへの割り当て)と参照画像

戻すときに、同じものが新しくできていて入れ直せない(同じ名前のキャラ・同じシーンの新しいコマ など)なら、
何も変えずに理由を返す(TrashConflict)。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db
from .db import get_connection

RETENTION_DAYS = 30
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
KIND_LABELS = {
    "history_image": "生成画像",
    "panel": "漫画のコマ",
    "illustration": "挿絵ページ",
    "dataset_image": "データセットの画像",
    "book": "物語(本)",
    "character": "キャラ",
    "cleanup": "ディスクの整理で片付けたファイル",
    "ugoira": "うごイラ",
}


class TrashConflict(Exception):
    """戻せない(同じものが新しくできている・ファイルの置き場所がふさがっている)。"""


def _trash_dir() -> Path:
    # DB を差し替えるテストでも、ごみ箱のファイルは同じフォルダの下に置く
    return db._DB_PATH.parent / "trash"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """そのままの値の行(JSON の列も文字列のまま。戻すときにそのまま入れ直す)。"""
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _insert(conn: sqlite3.Connection, table: str, row: dict[str, Any], *, ignore: bool = False) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    values = {k: v for k, v in row.items() if k in existing}
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    verb = "INSERT OR IGNORE" if ignore else "INSERT"
    conn.execute(f"{verb} INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))


def _relative(path: str | Path) -> str:
    full = Path(path)
    if not full.is_absolute():
        full = _PROJECT_ROOT / full
    return full.resolve().relative_to(_PROJECT_ROOT.resolve()).as_posix()


def put(kind: str, label: str, payload: dict[str, Any], files: list[str | Path]) -> int:
    """
    預かる。files はリポジトリのルートからのパス(か絶対パス)。無いファイルは飛ばす。ファイルはここで移す
    (DB の行は、呼ぶ側が消す前に集めて payload に入れておく)。戻り値はごみ箱の番号。
    """
    purge_expired()
    now = _now()
    conn = get_connection()
    try:
        entry_id = conn.execute(
            "INSERT INTO trash_entries (kind, label, payload, files, created_at, expires_at) VALUES (?, ?, ?, '[]', ?, ?)",
            (kind, label, json.dumps(payload, ensure_ascii=False), now.isoformat(),
             (now + timedelta(days=RETENTION_DAYS)).isoformat()),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()
    folder = _trash_dir() / str(entry_id)
    moved: list[list[Any]] = []
    for index, path in enumerate(dict.fromkeys(str(f) for f in files if f)):
        try:
            rel = _relative(path)
        except ValueError:
            # このアプリのフォルダの外のファイルは預からない(消しもしない)
            continue
        source = _PROJECT_ROOT / rel
        if not source.is_file():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        stored = folder / f"{index}_{source.name}"
        size = source.stat().st_size
        shutil.move(str(source), stored)
        moved.append([rel, stored.name, size])
    conn = get_connection()
    try:
        conn.execute("UPDATE trash_entries SET files = ? WHERE id = ?", (json.dumps(moved, ensure_ascii=False), entry_id))
        conn.commit()
    finally:
        conn.close()
    return int(entry_id)


def _entry(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM trash_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    entry = dict(row)
    entry["payload"] = json.loads(entry["payload"])
    entry["files"] = json.loads(entry["files"])
    return entry


def list_entries() -> list[dict[str, Any]]:
    purge_expired()
    conn = get_connection()
    try:
        entries = [_entry(conn, r["id"]) for r in conn.execute("SELECT id FROM trash_entries ORDER BY id DESC")]
    finally:
        conn.close()
    return [
        {
            "id": e["id"],
            "kind": e["kind"],
            "kind_label": KIND_LABELS.get(e["kind"], e["kind"]),
            "label": e["label"],
            "created_at": e["created_at"],
            "expires_at": e["expires_at"],
            "files": len(e["files"]),
            "bytes": sum(f[2] for f in e["files"]),
            # 一覧で見せる画像(預かったファイルの最初の画像)
            "preview": next((i for i, f in enumerate(e["files"]) if f[1].lower().endswith((".png", ".jpg", ".jpeg", ".webp"))), None),
        }
        for e in entries
        if e is not None
    ]


def stored_file(entry_id: int, index: int) -> Path | None:
    """預かったファイルの置き場所(一覧の見本の画像に使う)。"""
    conn = get_connection()
    try:
        entry = _entry(conn, entry_id)
    finally:
        conn.close()
    if entry is None or not 0 <= index < len(entry["files"]):
        return None
    path = _trash_dir() / str(entry_id) / entry["files"][index][1]
    return path if path.is_file() else None


def _restore_rows(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    """種類ごとに、行を入れ直す(同じ接続・コミットは呼ぶ側)。"""
    kind, payload = entry["kind"], entry["payload"]
    if kind == "history_image":
        row = payload["entry"]
        current = conn.execute("SELECT image_paths FROM generation_history WHERE id = ?", (row["id"],)).fetchone()
        if current is None:
            _insert(conn, "generation_history", {**row, "image_paths": json.dumps([payload["path"]])})
        else:
            paths = json.loads(current["image_paths"])
            if payload["path"] not in paths:
                conn.execute(
                    "UPDATE generation_history SET image_paths = ? WHERE id = ?", (json.dumps([*paths, payload["path"]]), row["id"])
                )
    elif kind in ("panel", "illustration"):
        table = "manga_panels" if kind == "panel" else "manga_pages"
        try:
            _insert(conn, table, payload["row"])
        except sqlite3.IntegrityError as exc:
            raise TrashConflict("そのシーン(ページ)には、消したあとに新しい絵ができているので戻せません。") from exc
    elif kind in ("book", "character", "ugoira"):
        for table, table_rows in payload["rows"].items():
            for row in table_rows:
                try:
                    _insert(conn, table, row, ignore=table in payload.get("optional", []))
                except sqlite3.IntegrityError as exc:
                    what = "同じ名前のキャラ" if table == "characters" else f"同じもの({table})"
                    raise TrashConflict(f"{what}が新しくできているので戻せません。") from exc
        for draft_id in payload.get("drafts", []):
            conn.execute("UPDATE story_drafts SET story_id = ? WHERE id = ? AND story_id IS NULL", (payload["story_id"], draft_id))


def restore(entry_id: int) -> dict[str, Any]:
    """元に戻す。戻せなければ TrashConflict(何も変えない)。無ければ KeyError。"""
    conn = get_connection()
    try:
        entry = _entry(conn, entry_id)
        if entry is None:
            raise KeyError(entry_id)
        folder = _trash_dir() / str(entry_id)
        for rel, stored, _ in entry["files"]:
            if (_PROJECT_ROOT / rel).exists():
                raise TrashConflict(f"元の場所に同じ名前のファイルがあるので戻せません: {rel}")
            if not (folder / stored).is_file():
                raise TrashConflict(f"預かったファイルが見つかりません: {stored}")
        try:
            _restore_rows(conn, entry)
            conn.execute("DELETE FROM trash_entries WHERE id = ?", (entry_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    for rel, stored, _ in entry["files"]:
        target = _PROJECT_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder / stored), target)
    shutil.rmtree(folder, ignore_errors=True)
    return {"id": entry_id, "kind": entry["kind"], "label": entry["label"], "payload": entry["payload"]}


def purge(entry_id: int) -> bool:
    """完全に消す(元に戻せない)。"""
    conn = get_connection()
    try:
        deleted = conn.execute("DELETE FROM trash_entries WHERE id = ?", (entry_id,)).rowcount
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(_trash_dir() / str(entry_id), ignore_errors=True)
    return bool(deleted)


def purge_expired() -> int:
    conn = get_connection()
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM trash_entries WHERE expires_at < ?", (_now().isoformat(),))]
    finally:
        conn.close()
    for entry_id in ids:
        purge(entry_id)
    return len(ids)


# ---- 種類ごとの預け方(行を集めてから消す) ----


def history_image(entry_id: int, filename: str) -> int | None:
    """生成履歴の画像を1枚預かって外す。履歴の最後の1枚なら、履歴の行ごと(i2i・キャラ参照の元画像も)。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM generation_history WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return None
        row = dict(row)
        paths = json.loads(row["image_paths"])
        target = next((p for p in paths if Path(p).name == filename), None)
        if target is None:
            return None
        rest = [p for p in paths if p != target]
        files: list[str] = [target]
        if rest:
            conn.execute("UPDATE generation_history SET image_paths = ? WHERE id = ?", (json.dumps(rest), entry_id))
        else:
            conn.execute("DELETE FROM generation_history WHERE id = ?", (entry_id,))
            files.append(row.get("i2i_image_path") or "")
            files += [ref.get("image_path") or "" for ref in json.loads(row.get("character_references") or "[]")]
        conn.commit()
    finally:
        conn.close()
    label = f"{(row.get('prompt') or '')[:40]}({Path(target).name})"
    return put("history_image", label, {"entry": row, "path": target}, files)


def manga_row(kind: str, row_id: int) -> int | None:
    """漫画のコマ(panel)か挿絵ページ(illustration)を預かって消す。"""
    table = "manga_panels" if kind == "panel" else "manga_pages"
    conn = get_connection()
    try:
        found = rows(conn, f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        if not found:
            return None
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        conn.commit()
    finally:
        conn.close()
    row = found[0]
    label = f"物語{row['story_id']}の{'コマ' if kind == 'panel' else '挿絵'}({Path(row['image_path']).name})"
    return put(kind, label, {"row": row}, [row["image_path"]])


def dataset_image(full: Path) -> int:
    """データセットの画像とキャプションを預かる(行は無い)。"""
    return put("dataset_image", _relative(full), {}, [full, full.with_suffix(".txt")])


def book(story_id: int, extra_files: list[Path]) -> int | None:
    """
    物語を預かって消す。消える行(シーン・コマ・ページ・登場キャラの割り当て)と、外れるもの(下書きとの結び付き・
    作者・関連・ブックマーク・成人向けの手動指定・最後の巻ならシリーズ)を集める。extra_files は合成したページなど。
    """
    from .db import delete_series_if_empty, forget_library_items, forget_work, get_series

    conn = get_connection()
    try:
        story = rows(conn, "SELECT * FROM stories WHERE id = ?", (story_id,))
        if not story:
            return None
        series_id = story[0].get("series_id")
        scene_ids = [r["id"] for r in conn.execute("SELECT id FROM story_scenes WHERE story_id = ?", (story_id,))]
        marks = [f"panel:{r['id']}" for r in conn.execute("SELECT id FROM manga_panels WHERE story_id = ?", (story_id,))]
        marks += [f"illustration:{r['id']}" for r in conn.execute("SELECT id FROM manga_pages WHERE story_id = ?", (story_id,))]
        # 外れる作者・関連: 単巻ならその物語の、シリーズの最後の巻ならシリーズの(削除と同じ)
        work_keys = [("story", story_id)] if series_id is None else []
        series_rows = []
        if series_id is not None:
            others = conn.execute("SELECT COUNT(*) FROM stories WHERE series_id = ? AND id != ?", (series_id, story_id)).fetchone()[0]
            if others == 0:
                series_rows = rows(conn, "SELECT * FROM series WHERE id = ?", (series_id,))
                work_keys.append(("series", series_id))
        placeholders = ",".join("?" for _ in scene_ids) or "NULL"
        payload_rows = {
            "series": series_rows,
            "stories": story,
            "story_scenes": rows(conn, "SELECT * FROM story_scenes WHERE story_id = ?", (story_id,)),
            "manga_panels": rows(conn, "SELECT * FROM manga_panels WHERE story_id = ?", (story_id,)),
            "manga_pages": rows(conn, "SELECT * FROM manga_pages WHERE story_id = ?", (story_id,)),
            "scene_characters": rows(conn, f"SELECT * FROM scene_characters WHERE scene_id IN ({placeholders})", tuple(scene_ids)),
            "work_authors": [r for kind, wid in work_keys for r in rows(
                conn, "SELECT * FROM work_authors WHERE work_kind = ? AND work_id = ?", (kind, wid))],
            "work_relations": [r for kind, wid in work_keys for r in rows(
                conn, "SELECT * FROM work_relations WHERE (from_kind = ? AND from_id = ?) OR (to_kind = ? AND to_id = ?)",
                (kind, wid, kind, wid))],
            "bookmarks": rows(conn, "SELECT * FROM bookmarks WHERE (kind = 'book' AND item_key = ?) OR (kind = 'image' AND item_key IN ("
                              + (",".join("?" for _ in marks) or "NULL") + "))", (str(story_id), *marks)),
            "adult_marks": rows(conn, "SELECT * FROM adult_marks WHERE (kind = 'book' AND item_key = ?) OR (kind = 'image' AND item_key IN ("
                                + (",".join("?" for _ in marks) or "NULL") + "))", (str(story_id), *marks)),
        }
        drafts = [r["id"] for r in conn.execute("SELECT id FROM story_drafts WHERE story_id = ?", (story_id,))]
        files = [r["image_path"] for r in payload_rows["manga_panels"] + payload_rows["manga_pages"]]
        title = story[0].get("title") or story[0].get("premise") or ""
        conn.execute("DELETE FROM stories WHERE id = ?", (story_id,))
        conn.commit()
        if series_id is not None:
            delete_series_if_empty(conn, series_id)
            if get_series(conn, series_id) is None:
                forget_work(conn, "series", series_id)
        else:
            forget_work(conn, "story", story_id)
        forget_library_items(conn, "image", marks)
        forget_library_items(conn, "book", [str(story_id)])
    finally:
        conn.close()
    payload = {
        "story_id": story_id,
        "rows": payload_rows,
        "drafts": drafts,
        # 相手が無くなっていたら入れない行(作者が消えた・関連の相手が消えた・キャラが消えた など)
        "optional": ["work_authors", "work_relations", "bookmarks", "adult_marks", "scene_characters", "series"],
    }
    return put("book", str(title).splitlines()[0][:60] if title else f"物語{story_id}", payload, [*files, *extra_files])


def character(character_id: int) -> int | None:
    """キャラを預かって消す(シーンへの割り当てと参照画像も)。"""
    conn = get_connection()
    try:
        found = rows(conn, "SELECT * FROM characters WHERE id = ?", (character_id,))
        if not found:
            return None
        scenes = rows(conn, "SELECT * FROM scene_characters WHERE character_id = ?", (character_id,))
        conn.execute("DELETE FROM characters WHERE id = ?", (character_id,))
        conn.commit()
    finally:
        conn.close()
    row = found[0]
    reference = row.get("reference_image_path") or ""
    conn = get_connection()
    try:
        # 別名保存したキャラが同じ参照画像を使っていれば、画像は預からない(そのキャラの画像が消えないように)
        shared = reference and conn.execute(
            "SELECT 1 FROM characters WHERE reference_image_path = ?", (reference,)
        ).fetchone()
    finally:
        conn.close()
    payload = {"rows": {"characters": [row], "scene_characters": scenes}, "optional": ["scene_characters"]}
    return put("character", row["name"], payload, [] if shared else [reference])
