"""
コンテンツガードの定義を見る・変える API(/api/content-guard)。値は DB(content_guard_rules)にある。

  GET    /rules              項目の一覧(今の値・はじめの値・変えてあるか)
  GET    /rules/{key}        1項目
  PUT    /rules/{key}        値を変える(守りを緩める変更はパスワードの合言葉が要る)
  DELETE /rules/{key}        はじめの値に戻す(パスワードは要らない)
  POST   /reset              すべてはじめの値に戻す(パスワードは要らない)
  POST   /check              タグが今の定義でどう扱われるかを確かめる
  GET    /export             すべての項目とガードプロファイルを JSON で書き出す
  POST   /import/preview     取り込むファイルと今の値の違い(項目ごとに 強める / 緩める)
  POST   /import             選んだ項目を取り込む(緩める変更があれば合言葉が要る)
  GET    /password           パスワードを設定してあるか
  POST   /password/setup     はじめの設定
  POST   /password/change    変える(今のパスワードが要る)
  POST   /unlock             パスワードを確かめて、しばらく使える合言葉を返す
  GET    /events             未成年キャラの安全対策で止めた・全年齢にした記録
  GET    /characters         キャラの年齢区分と、未成年の手がかり

緩める変更の判定は content_guard.change_direction、パスワードは guard_password.py。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import content_guard, guard_password, minor_guard
from ..db import get_connection, list_characters, list_guard_profiles, save_guard_profile
from ..guard_password import guard_token

router = APIRouter(prefix="/api/content-guard", tags=["content-guard"])

EXPORT_FORMAT = "novelai-userscripts-example/content-guard"
EXPORT_VERSION = 1


class RuleUpdate(BaseModel):
    # patterns / words は文字列の並び(または1行に1つの文字列)、text は文字列、int は整数、choice は選択肢の値
    value: Any


class CheckRequest(BaseModel):
    tags: str = Field(max_length=4000)


class PasswordSetup(BaseModel):
    password: str = Field(max_length=200)


class PasswordChange(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class ImportRequest(BaseModel):
    # GET /export で書き出したもの
    data: dict[str, Any]
    # 取り込む項目のキーとガードプロファイルの名前(preview では使わない。無ければ全部)
    keys: list[str] | None = None
    profiles: list[str] | None = None


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="その項目はありません。")


@router.get("/rules")
async def get_rules() -> dict[str, Any]:
    return {"rules": content_guard.describe()}


@router.get("/rules/{key}")
async def get_rule(key: str) -> dict[str, Any]:
    try:
        return content_guard.describe_one(key)
    except KeyError:
        raise _not_found()


@router.put("/rules/{key}")
async def put_rule(key: str, req: RuleUpdate, token: str | None = Depends(guard_token)) -> dict[str, Any]:
    try:
        value = content_guard.coerce(content_guard.rule_of(key), req.value)
        if content_guard.change_direction(key, value)["direction"] == "looser":
            guard_password.require_unlocked(token)
        return content_guard.set_value(key, value)
    except KeyError:
        raise _not_found()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/rules/{key}")
async def delete_rule(key: str) -> dict[str, Any]:
    """はじめの値に戻す(項目そのものは消えない)。"""
    try:
        return content_guard.reset(key)
    except KeyError:
        raise _not_found()


@router.post("/reset")
async def post_reset() -> dict[str, Any]:
    return {"rules": content_guard.reset_all()}


@router.post("/check")
async def post_check(req: CheckRequest) -> dict[str, Any]:
    return content_guard.check(req.tags)


# ---- 書き出し・取り込み ----


def _profiles() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_guard_profiles(conn)
    finally:
        conn.close()


@router.get("/export")
async def export_rules() -> dict[str, Any]:
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "rules": {rule["key"]: rule["value"] for rule in content_guard.describe()},
        "profiles": [
            {"name": p["name"], "blocked_tags": p["blocked_tags"], "negative_tags": p["negative_tags"]} for p in _profiles()
        ],
    }


def _profile_direction(current: dict[str, Any] | None, blocked: list[str], negative: str) -> dict[str, Any]:
    """ガードプロファイルの変更の向き(禁止タグ・ネガティブの語が減れば緩める)。"""
    if current is None:
        return {"direction": "stricter", "added": list(blocked), "removed": []}
    before = {t.lower() for t in current["blocked_tags"]} | {
        w.strip().lower() for w in (current["negative_tags"] or "").split(",") if w.strip()
    }
    after = {t.lower() for t in blocked} | {w.strip().lower() for w in negative.split(",") if w.strip()}
    added, removed = sorted(after - before), sorted(before - after)
    direction = "looser" if removed else "stricter" if added else "same"
    return {"direction": direction, "added": added, "removed": removed}


def _plan(data: dict[str, Any]) -> dict[str, Any]:
    """取り込むファイルと今の値の違い。値は coerce 済みにし、読めないものは invalid にする。"""
    if data.get("format") != EXPORT_FORMAT:
        raise HTTPException(status_code=422, detail="このアプリのコンテンツガードを書き出したファイルではありません。")
    rules = data.get("rules")
    if not isinstance(rules, dict):
        raise HTTPException(status_code=422, detail="項目(rules)が読めません。")
    items: list[dict[str, Any]] = []
    for key, raw in rules.items():
        try:
            rule = content_guard.rule_of(key)
        except KeyError:
            items.append({"key": key, "label": key, "direction": "invalid", "reason": "このアプリに無い項目です"})
            continue
        try:
            value = content_guard.coerce(rule, raw)
        except ValueError as exc:
            items.append({"key": key, "label": rule.label, "direction": "invalid", "reason": str(exc)})
            continue
        items.append({"key": key, "label": rule.label, "group": rule.group, "value": value, **content_guard.change_direction(key, value)})
    current = {p["name"]: p for p in _profiles()}
    profiles: list[dict[str, Any]] = []
    for raw in data.get("profiles") or []:
        name = str(raw.get("name") or "").strip() if isinstance(raw, dict) else ""
        blocked = raw.get("blocked_tags") if isinstance(raw, dict) else None
        negative = raw.get("negative_tags") if isinstance(raw, dict) else None
        if not name or not isinstance(blocked, list) or not all(isinstance(t, str) for t in blocked) or not isinstance(negative, str):
            profiles.append({"name": name or "(名前なし)", "direction": "invalid", "reason": "形が読めません"})
            continue
        blocked = [t.strip() for t in blocked if t.strip()]
        profiles.append(
            {"name": name, "blocked_tags": blocked, "negative_tags": negative.strip(),
             **_profile_direction(current.get(name), blocked, negative.strip())}
        )
    return {"exported_at": data.get("exported_at"), "rules": items, "profiles": profiles}


@router.post("/import/preview")
async def preview_import(req: ImportRequest) -> dict[str, Any]:
    return _plan(req.data)


@router.post("/import")
async def import_rules(req: ImportRequest, token: str | None = Depends(guard_token)) -> dict[str, Any]:
    plan = _plan(req.data)
    rules = [r for r in plan["rules"] if r["direction"] not in ("invalid", "same") and (req.keys is None or r["key"] in req.keys)]
    profiles = [
        p for p in plan["profiles"] if p["direction"] not in ("invalid", "same") and (req.profiles is None or p["name"] in req.profiles)
    ]
    if any(item["direction"] == "looser" for item in [*rules, *profiles]):
        guard_password.require_unlocked(token)
    for rule in rules:
        content_guard.set_value(rule["key"], rule["value"])
    conn = get_connection()
    try:
        for profile in profiles:
            save_guard_profile(conn, profile["name"], profile["blocked_tags"], profile["negative_tags"])
    finally:
        conn.close()
    return {"rules": len(rules), "profiles": len(profiles)}


# ---- パスワード ----


@router.get("/password")
async def get_password_state(token: str | None = Depends(guard_token)) -> dict[str, Any]:
    return {"set": guard_password.is_set(), "unlocked": guard_password.token_valid(token), "min_length": guard_password.MIN_LENGTH}


@router.post("/password/setup", status_code=204)
async def setup_password(req: PasswordSetup) -> None:
    guard_password.setup(req.password)


@router.post("/password/change", status_code=204)
async def change_password(req: PasswordChange) -> None:
    guard_password.change(req.current, req.new)


@router.post("/unlock")
async def unlock(req: PasswordSetup) -> dict[str, Any]:
    token, seconds = guard_password.unlock(req.password)
    return {"token": token, "seconds": seconds}


# ---- 未成年キャラの安全対策の記録・キャラの年齢区分 ----


@router.get("/events")
async def get_events(limit: int = 100) -> list[dict[str, Any]]:
    return minor_guard.recent_events(max(1, min(limit, 500)))


@router.get("/characters")
async def get_character_ages() -> list[dict[str, Any]]:
    """キャラの年齢区分(minor / adult / undefined)と、未成年の手がかり(キャラシートとメモから。本文は物語ごとに見る)。"""
    conn = get_connection()
    try:
        characters = list_characters(conn)
    finally:
        conn.close()
    result = []
    for character in characters:
        age = minor_guard.character_age(character)
        result.append(
            {
                "id": character["id"],
                "name": character["name"],
                "state": age.state,
                "defined": "minor" if character.get("is_minor") else "adult" if character.get("is_adult") else "undefined",
                "reasons": age.reasons,
            }
        )
    return result


@router.get("/inspect")
async def inspect_data() -> dict[str, Any]:
    """今あるデータの点検(guard_inspect.py)。今の設定で止まるはずのものの一覧。データは変えない。"""
    import asyncio

    from ..guard_inspect import inspect

    return await asyncio.to_thread(inspect)
