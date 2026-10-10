"""
コンテンツガードの定義(止めるタグ・取り除くタグ・足すネガティブ・LLM への指示・成人向けの判定の語)。

値はすべて DB(content_guard_rules)に持ち、API(/api/content-guard)と開発管理者のページで編集できる。
このファイルには値を書かない。はじめの値は content_guard_defaults.json にあり、DB に無い項目だけをそこから入れる
(「既定に戻す」もそのファイルの値に戻す)。使う側は get(key) / find(key, text) などで読む。変更は次の呼び出しから
効く(再起動は要らない)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import db
from .db import get_connection

_DEFAULTS_FILE = Path(__file__).with_name("content_guard_defaults.json")
_MAX_ITEMS = 500
_MAX_ITEM_CHARS = 200
_MAX_TEXT_CHARS = 4000
# 照合のしかた: word=単語として / tag=タグ全体が一致 / substring=文中のどこでも
_MATCH_WRAP = {"word": r"\b({})\b", "tag": r"^({})$", "substring": r"({})"}


@dataclass(frozen=True)
class Rule:
    key: str
    group: str
    label: str
    description: str
    # "patterns"(正規表現の並び)| "words"(語の並び)| "text" | "int"
    kind: str
    default: Any
    match: str | None = None
    minimum: int | None = None
    maximum: int | None = None


@lru_cache(maxsize=1)
def _rules() -> dict[str, Rule]:
    data = json.loads(_DEFAULTS_FILE.read_text(encoding="utf-8"))
    return {
        r["key"]: Rule(
            key=r["key"],
            group=r["group"],
            label=r["label"],
            description=r["description"],
            kind=r["kind"],
            default=r["value"],
            match=r.get("match"),
            minimum=r.get("minimum"),
            maximum=r.get("maximum"),
        )
        for r in data["rules"]
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Loaded:
    path: Any
    values: dict[str, Any]
    updated: dict[str, str]
    compiled: dict[str, Any]


_cache: _Loaded | None = None


def _load() -> _Loaded:
    global _cache
    if _cache is None or _cache.path != db._DB_PATH:
        rules = _rules()
        conn = get_connection()
        try:
            rows = conn.execute("SELECT key, value, updated_at FROM content_guard_rules").fetchall()
            values: dict[str, Any] = {}
            updated: dict[str, str] = {}
            for row in rows:
                if row["key"] not in rules:
                    continue
                try:
                    values[row["key"]] = json.loads(row["value"])
                    updated[row["key"]] = row["updated_at"]
                except json.JSONDecodeError:
                    continue
            # DB に無い項目(はじめての起動・あとから増えた項目)は、はじめの値を入れる
            missing = [rule for key, rule in rules.items() if key not in values]
            for rule in missing:
                now = _now()
                conn.execute(
                    "INSERT INTO content_guard_rules (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (rule.key, json.dumps(rule.default, ensure_ascii=False), now),
                )
                values[rule.key] = rule.default
                updated[rule.key] = now
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


def get(key: str) -> Any:
    """今の値(DB の値)。"""
    if key not in _rules():
        raise KeyError(key)
    return _load().values[key]


def text(key: str) -> str:
    return str(get(key)).strip()


def _matcher(key: str) -> re.Pattern[str] | None:
    """patterns の項目をまとめた正規表現。並びが空なら None(何にも当たらない)。"""
    loaded = _load()
    if key not in loaded.compiled:
        rule = _rules()[key]
        patterns = [p for p in loaded.values[key] if p]
        wrap = _MATCH_WRAP[rule.match or "word"]
        loaded.compiled[key] = re.compile(wrap.format("|".join(patterns)), re.IGNORECASE) if patterns else None
    return loaded.compiled[key]


def search(key: str, value: str) -> bool:
    matcher = _matcher(key)
    return bool(matcher and matcher.search(value or ""))


def find(key: str, value: str) -> list[str]:
    """当たった語(小文字・重複なし・並べ替え済み)。"""
    matcher = _matcher(key)
    return sorted({m.group(0).lower() for m in matcher.finditer(value or "")}) if matcher else []


def count(key: str, value: str) -> int:
    matcher = _matcher(key)
    return len(matcher.findall(value or "")) if matcher else 0


def words(key: str) -> frozenset[str]:
    """words の項目を集合で。"""
    loaded = _load()
    cache_key = f"words:{key}"
    if cache_key not in loaded.compiled:
        loaded.compiled[cache_key] = frozenset(get(key))
    return loaded.compiled[cache_key]


# ---- 場面のタグ ----


def is_sexual(tags: str) -> bool:
    return search("scene.sexual_tags", tags)


def tags_adult(tags: str | None) -> bool:
    """本棚・ギャラリーで成人向けと自動判定するタグか。"""
    return bool(tags) and (is_sexual(tags or "") or search("library.adult_tags", tags or ""))


def sanitize_scene_tags(tags: str, *, adult: bool) -> str:
    """
    成人向け、または性的な語を含むタグから未成年を思わせるタグを除く。成人向けなら先頭に nsfw を付け、
    性器が関わる場面には explicit, uncensored(女性器なら pussy も)を足す(足さないと布や構図で隠されがち)。
    """
    items = [t.strip() for t in tags.split(",") if t.strip()]
    sexual = adult or any(is_sexual(t) for t in items)
    if sexual:
        items = [t for t in items if not search("scene.minor_tags", t)]
    lower = [t.lower() for t in items]
    if adult and any(search("scene.genital_tags", t) for t in items):
        extra = [t for t in ("explicit", "uncensored") if t not in lower]
        if any(search("scene.female_genital_tags", t) for t in items) and "pussy" not in lower:
            extra.append("pussy")
        items = [items[0], *extra, *items[1:]] if lower[0] == "nsfw" else [*extra, *items]
        lower = [t.lower() for t in items]
    if adult and items and "nsfw" not in lower and any(is_sexual(t) for t in items):
        items.insert(0, "nsfw")
    return ", ".join(dict.fromkeys(items))


# ---- 変える ----


def coerce(rule: Rule, value: Any) -> Any:
    """受け取った値を、その項目の型に合わせる。合わなければ ValueError。"""
    if rule.kind in ("patterns", "words"):
        if isinstance(value, str):
            value = value.splitlines()
        if not isinstance(value, list):
            raise ValueError("文字列の並びで指定してください")
        items: list[str] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError("文字列の並びで指定してください")
            item = raw.strip()
            # 完全一致の項目(pixiv のタグなど)は大文字・小文字を区別する
            dedupe = item if rule.match == "exact" else item.lower()
            if not item or dedupe in seen:
                continue
            if len(item) > _MAX_ITEM_CHARS:
                raise ValueError(f"長すぎます({_MAX_ITEM_CHARS}文字まで): {item[:30]}…")
            if rule.kind == "patterns":
                try:
                    re.compile(item)
                except re.error as exc:
                    raise ValueError(f"正規表現として読めません: {item}({exc})") from exc
            seen.add(dedupe)
            items.append(item)
        if len(items) > _MAX_ITEMS:
            raise ValueError(f"多すぎます({_MAX_ITEMS}件まで)")
        return items
    if rule.kind == "text":
        if not isinstance(value, str):
            raise ValueError("文字列で指定してください")
        result = value.strip()
        if len(result) > _MAX_TEXT_CHARS:
            raise ValueError(f"長すぎます({_MAX_TEXT_CHARS}文字まで)")
        return result
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError("整数で指定してください")
    number = int(value)
    if rule.minimum is not None and number < rule.minimum:
        raise ValueError(f"{rule.minimum} 以上にしてください")
    if rule.maximum is not None and number > rule.maximum:
        raise ValueError(f"{rule.maximum} 以下にしてください")
    return number


def _store(items: dict[str, Any]) -> None:
    conn = get_connection()
    try:
        for key, value in items.items():
            conn.execute(
                "INSERT INTO content_guard_rules (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), _now()),
            )
        conn.commit()
    finally:
        conn.close()
    reset_cache()


def set_value(key: str, value: Any) -> dict[str, Any]:
    """値を変える。変えた後の項目を返す。知らない項目は KeyError、型が合わなければ ValueError。"""
    rule = _rules().get(key)
    if rule is None:
        raise KeyError(key)
    _store({key: coerce(rule, value)})
    return describe_one(key)


def reset(key: str) -> dict[str, Any]:
    """はじめの値(content_guard_defaults.json)に戻す。"""
    rule = _rules().get(key)
    if rule is None:
        raise KeyError(key)
    _store({key: rule.default})
    return describe_one(key)


def reset_all() -> list[dict[str, Any]]:
    _store({key: rule.default for key, rule in _rules().items()})
    return describe()


def describe_one(key: str) -> dict[str, Any]:
    rule = _rules().get(key)
    if rule is None:
        raise KeyError(key)
    loaded = _load()
    value = loaded.values[key]
    return {
        "key": rule.key,
        "group": rule.group,
        "label": rule.label,
        "description": rule.description,
        "kind": rule.kind,
        "match": rule.match,
        "value": value,
        "default": rule.default,
        "modified": value != rule.default,
        "minimum": rule.minimum,
        "maximum": rule.maximum,
        "updated_at": loaded.updated.get(key),
    }


def describe() -> list[dict[str, Any]]:
    """項目の一覧(今の値・はじめの値・変えてあるか)。"""
    return [describe_one(key) for key in _rules()]


def check(tags: str) -> dict[str, Any]:
    """タグが今の定義でどう扱われるか(編集した結果を確かめるためのもの)。"""
    return {
        "dataset_blocked": find("dataset.blocked_tags", tags),
        "dataset_minor": find("dataset.minor_tags", tags),
        "sexual": is_sexual(tags),
        "library_adult": tags_adult(tags),
        "scene_tags": sanitize_scene_tags(tags, adult=False),
        "scene_tags_adult": sanitize_scene_tags(tags, adult=True),
    }
