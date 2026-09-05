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
    result = []
    for row in rows:
        d = dict(row)
        d["is_category"] = bool(d["is_category"])
        d["child_order"] = json.loads(d["child_order"]) if d["child_order"] else None
        result.append(d)
    return result
