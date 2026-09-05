from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_chunks (
            id TEXT PRIMARY KEY,
            remote_object_id TEXT,
            container_id TEXT,
            label TEXT NOT NULL,
            expansion TEXT NOT NULL DEFAULT '',
            color TEXT,
            is_category INTEGER NOT NULL DEFAULT 0,
            child_order TEXT,
            version INTEGER,
            synced_at TEXT NOT NULL
        )
        """
    )
    # 「カテゴリ」はNovelAI公式のフォルダ構造(is_category/container_id)をそのまま流用し、
    # 「シチュエーション」はそれとは独立した自前のタグ分類として追加する。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS situations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_chunk_situations (
            chunk_id TEXT NOT NULL REFERENCES prompt_chunks(id) ON DELETE CASCADE,
            situation_id INTEGER NOT NULL REFERENCES situations(id) ON DELETE CASCADE,
            PRIMARY KEY (chunk_id, situation_id)
        )
        """
    )
    # ワードの関係(競合/排他): 「髪の長さ」のような排他グループを作り、
    # 同じグループに属するチャンクは同時に選ばれてはいけない、という形でモデル化する。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exclusive_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_chunk_exclusive_groups (
            chunk_id TEXT NOT NULL REFERENCES prompt_chunks(id) ON DELETE CASCADE,
            group_id INTEGER NOT NULL REFERENCES exclusive_groups(id) ON DELETE CASCADE,
            PRIMARY KEY (chunk_id, group_id)
        )
        """
    )
    conn.commit()


def upsert_prompt_chunks(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """
    NovelAI 公式から取得・復号したチャンクをDBへ upsert する（同一 id は上書き）。
    items は routes/chunks.py の decrypt_object() 結果に、取得元の object id を
    remote_object_id として付加したもの。
    """
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        conn.execute(
            """
            INSERT INTO prompt_chunks
                (id, remote_object_id, container_id, label, expansion, color, is_category, child_order, version, synced_at)
            VALUES
                (:id, :remote_object_id, :container_id, :label, :expansion, :color, :is_category, :child_order, :version, :synced_at)
            ON CONFLICT(id) DO UPDATE SET
                remote_object_id = excluded.remote_object_id,
                container_id     = excluded.container_id,
                label            = excluded.label,
                expansion        = excluded.expansion,
                color            = excluded.color,
                is_category      = excluded.is_category,
                child_order      = excluded.child_order,
                version          = excluded.version,
                synced_at        = excluded.synced_at
            """,
            {
                "id": item["id"],
                "remote_object_id": item.get("remote_object_id"),
                "container_id": item.get("containerId") or None,
                "label": item.get("label", ""),
                "expansion": item.get("expansion", ""),
                "color": item.get("color"),
                "is_category": 1 if item.get("isCategory") else 0,
                "child_order": json.dumps(item["childOrder"]) if item.get("childOrder") else None,
                "version": item.get("version"),
                "synced_at": now,
            },
        )
    conn.commit()
    return len(items)


def list_prompt_chunks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM prompt_chunks ORDER BY is_category DESC, label"
    ).fetchall()
    situations_by_chunk = _situations_by_chunk(conn)
    exclusive_groups_by_chunk = _exclusive_groups_by_chunk(conn)

    result = []
    for row in rows:
        d = dict(row)
        d["is_category"] = bool(d["is_category"])
        d["child_order"] = json.loads(d["child_order"]) if d["child_order"] else None
        d["situations"] = situations_by_chunk.get(d["id"], [])
        d["exclusive_groups"] = exclusive_groups_by_chunk.get(d["id"], [])
        result.append(d)
    return result


def _situations_by_chunk(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT pcs.chunk_id, s.id, s.name
        FROM prompt_chunk_situations pcs
        JOIN situations s ON s.id = pcs.situation_id
        ORDER BY s.name
        """
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["chunk_id"], []).append({"id": row["id"], "name": row["name"]})
    return result


def list_situations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name FROM situations ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def create_situation(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    name = name.strip()
    row = conn.execute(
        "INSERT INTO situations (name) VALUES (?) ON CONFLICT(name) DO UPDATE SET name = name RETURNING id, name",
        (name,),
    ).fetchone()
    conn.commit()
    return dict(row)


def delete_situation(conn: sqlite3.Connection, situation_id: int) -> None:
    conn.execute("DELETE FROM situations WHERE id = ?", (situation_id,))
    conn.commit()


def set_chunk_situations(conn: sqlite3.Connection, chunk_id: str, situation_ids: list[int]) -> None:
    conn.execute("DELETE FROM prompt_chunk_situations WHERE chunk_id = ?", (chunk_id,))
    conn.executemany(
        "INSERT INTO prompt_chunk_situations (chunk_id, situation_id) VALUES (?, ?)",
        [(chunk_id, sid) for sid in situation_ids],
    )
    conn.commit()


def _exclusive_groups_by_chunk(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT pceg.chunk_id, g.id, g.name
        FROM prompt_chunk_exclusive_groups pceg
        JOIN exclusive_groups g ON g.id = pceg.group_id
        ORDER BY g.name
        """
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["chunk_id"], []).append({"id": row["id"], "name": row["name"]})
    return result


def list_exclusive_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name FROM exclusive_groups ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def create_exclusive_group(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    name = name.strip()
    row = conn.execute(
        "INSERT INTO exclusive_groups (name) VALUES (?) ON CONFLICT(name) DO UPDATE SET name = name RETURNING id, name",
        (name,),
    ).fetchone()
    conn.commit()
    return dict(row)


def delete_exclusive_group(conn: sqlite3.Connection, group_id: int) -> None:
    conn.execute("DELETE FROM exclusive_groups WHERE id = ?", (group_id,))
    conn.commit()


def set_chunk_exclusive_groups(conn: sqlite3.Connection, chunk_id: str, group_ids: list[int]) -> None:
    conn.execute("DELETE FROM prompt_chunk_exclusive_groups WHERE chunk_id = ?", (chunk_id,))
    conn.executemany(
        "INSERT INTO prompt_chunk_exclusive_groups (chunk_id, group_id) VALUES (?, ?)",
        [(chunk_id, gid) for gid in group_ids],
    )
    conn.commit()


def find_conflicts(conn: sqlite3.Connection, chunk_ids: list[str]) -> list[dict[str, Any]]:
    """
    指定したチャンク群の中に、同じ排他グループに属するものが2件以上あれば報告する。
    ワード選択ルール実装時にこのまま流用する想定。
    """
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"""
        SELECT g.id AS group_id, g.name AS group_name, pceg.chunk_id, pc.label
        FROM prompt_chunk_exclusive_groups pceg
        JOIN exclusive_groups g ON g.id = pceg.group_id
        JOIN prompt_chunks pc ON pc.id = pceg.chunk_id
        WHERE pceg.chunk_id IN ({placeholders})
        ORDER BY g.name
        """,
        chunk_ids,
    ).fetchall()

    by_group: dict[int, dict[str, Any]] = {}
    for row in rows:
        group = by_group.setdefault(
            row["group_id"], {"group_id": row["group_id"], "group_name": row["group_name"], "members": []}
        )
        group["members"].append({"id": row["chunk_id"], "label": row["label"]})

    return [g for g in by_group.values() if len(g["members"]) > 1]
