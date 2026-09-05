from __future__ import annotations

import json
import random
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
    # ワード選択ルール: プリセットは特定のチャンクID組み合わせを名前付きで保存し、
    # 呼び出すたびに同じ組み合わせを再現する。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS preset_chunks (
            preset_id INTEGER NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
            chunk_id TEXT NOT NULL REFERENCES prompt_chunks(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            PRIMARY KEY (preset_id, chunk_id)
        )
        """
    )
    # ワード選択(/select)経由で生成した画像だけの履歴。プロンプト/設定/使ったチャンクIDと
    # 生成画像(ファイルパス)を紐付けて保存する。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS generation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt TEXT NOT NULL,
            negative_prompt TEXT,
            model TEXT NOT NULL,
            size TEXT NOT NULL,
            steps INTEGER NOT NULL,
            scale REAL NOT NULL,
            seed INTEGER,
            chunk_ids TEXT NOT NULL,
            image_paths TEXT NOT NULL,
            i2i_image_path TEXT,
            i2i_strength REAL,
            i2i_noise REAL,
            character_references TEXT,
            characters TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    _migrate_generation_history(conn)


_GENERATION_HISTORY_EXTRA_COLUMNS = {
    "i2i_image_path": "TEXT",
    "i2i_strength": "REAL",
    "i2i_noise": "REAL",
    "character_references": "TEXT",
    "characters": "TEXT",
}


def _migrate_generation_history(conn: sqlite3.Connection) -> None:
    """
    既存の data/app.db は generation_history に i2i/キャラクター系の列を持たない状態で
    作られている場合があるため、無ければ追加する（CREATE TABLE IF NOT EXISTS は既存
    テーブルには効かないため）。
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(generation_history)")}
    for column, column_type in _GENERATION_HISTORY_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE generation_history ADD COLUMN {column} {column_type}")
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


# ===== ワード選択ルール =====

def _resolve_conflicts(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同じ排他グループに属するチャンクが複数あれば、先に出てきたものだけを残す。"""
    used_groups: set[int] = set()
    result: list[dict[str, Any]] = []
    for chunk in chunks:
        group_ids = [g["id"] for g in chunk.get("exclusive_groups", [])]
        if any(gid in used_groups for gid in group_ids):
            continue
        used_groups.update(group_ids)
        result.append(chunk)
    return result


def select_by_situation(conn: sqlite3.Connection, situation_id: int) -> list[dict[str, Any]]:
    """シナリオベース選択: 指定シチュエーションが付いたチャンクを、排他グループの重複を除いて全て返す。"""
    candidates = [
        c
        for c in list_prompt_chunks(conn)
        if not c["is_category"] and any(s["id"] == situation_id for s in c["situations"])
    ]
    return _resolve_conflicts(candidates)


def select_random(
    conn: sqlite3.Connection, situation_id: int | None, count: int | None
) -> list[dict[str, Any]]:
    """ランダム選択: (任意でシチュエーション絞り込み後)排他グループの重複を除いてランダムに選ぶ。"""
    pool = [c for c in list_prompt_chunks(conn) if not c["is_category"]]
    if situation_id is not None:
        pool = [c for c in pool if any(s["id"] == situation_id for s in c["situations"])]
    random.shuffle(pool)
    resolved = _resolve_conflicts(pool)
    return resolved[:count] if count is not None else resolved


def _tag_set(expansion: str) -> set[str]:
    return {t.strip().lower() for t in expansion.split(",") if t.strip()}


def find_similar(conn: sqlite3.Connection, chunk_id: str, limit: int) -> list[dict[str, Any]]:
    """類似選択: expansion のタグ集合の Jaccard 係数でランキングする(追加インフラ不要)。"""
    all_chunks = list_prompt_chunks(conn)
    by_id = {c["id"]: c for c in all_chunks}
    ref = by_id.get(chunk_id)
    if ref is None:
        return []

    ref_tags = _tag_set(ref["expansion"])
    scored: list[dict[str, Any]] = []
    for c in all_chunks:
        if c["id"] == chunk_id or c["is_category"]:
            continue
        tags = _tag_set(c["expansion"])
        union = ref_tags | tags
        if not union:
            continue
        score = len(ref_tags & tags) / len(union)
        if score > 0:
            scored.append({**c, "score": score})

    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:limit]


def list_presets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, created_at FROM presets ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def _set_preset_chunks(conn: sqlite3.Connection, preset_id: int, chunk_ids: list[str]) -> None:
    conn.execute("DELETE FROM preset_chunks WHERE preset_id = ?", (preset_id,))
    conn.executemany(
        "INSERT INTO preset_chunks (preset_id, chunk_id, position) VALUES (?, ?, ?)",
        [(preset_id, cid, i) for i, cid in enumerate(chunk_ids)],
    )


def create_preset(conn: sqlite3.Connection, name: str, chunk_ids: list[str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "INSERT INTO presets (name, created_at) VALUES (?, ?) RETURNING id, name, created_at",
        (name.strip(), now),
    ).fetchone()
    preset = dict(row)
    _set_preset_chunks(conn, preset["id"], chunk_ids)
    conn.commit()
    return preset


def update_preset_chunks(conn: sqlite3.Connection, preset_id: int, chunk_ids: list[str]) -> None:
    _set_preset_chunks(conn, preset_id, chunk_ids)
    conn.commit()


def delete_preset(conn: sqlite3.Connection, preset_id: int) -> None:
    conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
    conn.commit()


def get_preset(conn: sqlite3.Connection, preset_id: int) -> dict[str, Any] | None:
    preset_row = conn.execute(
        "SELECT id, name, created_at FROM presets WHERE id = ?", (preset_id,)
    ).fetchone()
    if preset_row is None:
        return None

    rows = conn.execute(
        """
        SELECT pc.id AS id, pc.label AS label, pc.expansion AS expansion, pc.color AS color
        FROM preset_chunks p
        JOIN prompt_chunks pc ON pc.id = p.chunk_id
        WHERE p.preset_id = ?
        ORDER BY p.position
        """,
        (preset_id,),
    ).fetchall()

    result = dict(preset_row)
    result["chunks"] = [dict(row) for row in rows]
    return result


def record_generation(
    conn: sqlite3.Connection,
    prompt: str,
    negative_prompt: str | None,
    model: str,
    size: str,
    steps: int,
    scale: float,
    seed: int | None,
    chunk_ids: list[str],
    image_paths: list[str],
    i2i_image_path: str | None = None,
    i2i_strength: float | None = None,
    i2i_noise: float | None = None,
    character_references: list[dict[str, Any]] | None = None,
    characters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    /select 経由の画像生成だけを対象にした履歴保存。image_paths / i2i_image_path /
    character_references[].image_path はリポジトリルートからの相対パス(outputs/history/...)で、
    画像データ自体はDBに入れずファイルのまま置く。
    """
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        INSERT INTO generation_history
            (prompt, negative_prompt, model, size, steps, scale, seed, chunk_ids, image_paths,
             i2i_image_path, i2i_strength, i2i_noise, character_references, characters, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, created_at
        """,
        (
            prompt,
            negative_prompt,
            model,
            size,
            steps,
            scale,
            seed,
            json.dumps(chunk_ids),
            json.dumps(image_paths),
            i2i_image_path,
            i2i_strength,
            i2i_noise,
            json.dumps(character_references) if character_references else None,
            json.dumps(characters) if characters else None,
            now,
        ),
    ).fetchone()
    conn.commit()
    return dict(row)


def list_generation_history(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM generation_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["chunk_ids"] = json.loads(d["chunk_ids"])
        d["image_paths"] = json.loads(d["image_paths"])
        d["character_references"] = json.loads(d["character_references"]) if d["character_references"] else []
        d["characters"] = json.loads(d["characters"]) if d["characters"] else []
        result.append(d)
    return result
