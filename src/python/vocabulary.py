"""
タグの分類・整理に使う語彙(絵柄のタグ・人物の語・構図のタグ・敬称 など。コンテンツガード以外)。

値はすべて DB(全体の DB の vocabulary)に持ち、開発管理者のページ(/api/admin/vocabulary)で編集できる。
このファイルには値を書かない。はじめの値は vocabulary_defaults.json にあり、DB に無い項目だけをそこから入れる
(「既定に戻す」もそのファイルの値に戻す)。使う側は words(key)(集合)か items(key)(並び順を保つ)で読む。
変更は次の呼び出しから効く(再起動は要らない)。

未成年・性的な内容の判定はここではなく content_guard(パスワードで守る)と safety_floor(コードの最低線)にある。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import db

_DEFAULTS_FILE = Path(__file__).with_name("vocabulary_defaults.json")
_MAX_ITEMS = 1000
_MAX_ITEM_CHARS = 100


@dataclass(frozen=True)
class Entry:
    key: str
    group: str
    label: str
    description: str
    default: tuple[str, ...]


@lru_cache(maxsize=1)
def _entries() -> dict[str, Entry]:
    data = json.loads(_DEFAULTS_FILE.read_text(encoding="utf-8"))
    return {
        e["key"]: Entry(e["key"], e["group"], e["label"], e["description"], tuple(e["value"]))
        for e in data["entries"]
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Loaded:
    path: Any
    values: dict[str, tuple[str, ...]]
    updated: dict[str, str]
    sets: dict[str, frozenset[str]]


_cache: _Loaded | None = None


def _load() -> _Loaded:
    global _cache
    if _cache is None or _cache.path != db._DB_PATH:
        entries = _entries()
        conn = db.get_system_connection()
        try:
            rows = conn.execute("SELECT key, value, updated_at FROM vocabulary").fetchall()
            values: dict[str, tuple[str, ...]] = {}
            updated: dict[str, str] = {}
            for row in rows:
                if row["key"] not in entries:
                    continue
                try:
                    value = json.loads(row["value"])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, list) and all(isinstance(v, str) for v in value):
                    values[row["key"]] = tuple(value)
                    updated[row["key"]] = row["updated_at"]
            # DB に無い項目(はじめての起動・あとから増えた項目)は、はじめの値を入れる
            missing = [entry for key, entry in entries.items() if key not in values]
            for entry in missing:
                now = _now()
                conn.execute(
                    "INSERT INTO vocabulary (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (entry.key, json.dumps(list(entry.default), ensure_ascii=False), now),
                )
                values[entry.key] = entry.default
                updated[entry.key] = now
            if missing:
                conn.commit()
        finally:
            conn.close()
        _cache = _Loaded(db._DB_PATH, values, updated, {})
    return _cache


def reset_cache() -> None:
    """DB を差し替えたとき(テストなど)に、読み込んだ値を捨てる。"""
    global _cache
    _cache = None


# ---- 読む ----


def items(key: str) -> tuple[str, ...]:
    """今の値(並び順を保つ)。"""
    if key not in _entries():
        raise KeyError(key)
    return _load().values[key]


def words(key: str) -> frozenset[str]:
    """今の値を集合で。"""
    loaded = _load()
    if key not in loaded.sets:
        loaded.sets[key] = frozenset(items(key))
    return loaded.sets[key]


# ---- 変える ----


def coerce(value: Any) -> tuple[str, ...]:
    """受け取った値を語の並びにする(文字列なら1行1語)。合わなければ ValueError。"""
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        raise ValueError("文字列の並びで指定してください")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("文字列の並びで指定してください")
        item = raw.strip()
        if not item or item in seen:
            continue
        if len(item) > _MAX_ITEM_CHARS:
            raise ValueError(f"長すぎます({_MAX_ITEM_CHARS}文字まで): {item[:30]}…")
        seen.add(item)
        result.append(item)
    if not result:
        raise ValueError("空にはできません(はじめの値に戻すなら「既定に戻す」)")
    if len(result) > _MAX_ITEMS:
        raise ValueError(f"多すぎます({_MAX_ITEMS}件まで)")
    return tuple(result)


def _store(values: dict[str, tuple[str, ...]]) -> None:
    conn = db.get_system_connection()
    try:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO vocabulary (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(list(value), ensure_ascii=False), _now()),
            )
        conn.commit()
    finally:
        conn.close()
    reset_cache()


def set_value(key: str, value: Any) -> dict[str, Any]:
    """値を変える。変えた後の項目を返す。知らない項目は KeyError、型が合わなければ ValueError。"""
    if key not in _entries():
        raise KeyError(key)
    _store({key: coerce(value)})
    return describe_one(key)


def reset(key: str) -> dict[str, Any]:
    """はじめの値(vocabulary_defaults.json)に戻す。"""
    entry = _entries().get(key)
    if entry is None:
        raise KeyError(key)
    _store({key: entry.default})
    return describe_one(key)


def describe_one(key: str) -> dict[str, Any]:
    entry = _entries().get(key)
    if entry is None:
        raise KeyError(key)
    loaded = _load()
    value = loaded.values[key]
    return {
        "key": entry.key,
        "group": entry.group,
        "label": entry.label,
        "description": entry.description,
        "value": list(value),
        "default": list(entry.default),
        "modified": value != entry.default,
        "updated_at": loaded.updated.get(key),
    }


def describe() -> list[dict[str, Any]]:
    """項目の一覧(今の値・はじめの値・変えてあるか)。"""
    return [describe_one(key) for key in _entries()]
