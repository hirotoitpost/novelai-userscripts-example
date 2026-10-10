"""
コンテンツガードの定義を見る・変える API(/api/content-guard)。値は DB(content_guard_rules)にある。

  GET    /rules          項目の一覧(今の値・はじめの値・変えてあるか)
  GET    /rules/{key}    1項目
  PUT    /rules/{key}    値を変える
  DELETE /rules/{key}    はじめの値に戻す
  POST   /reset          すべてはじめの値に戻す
  POST   /check          タグが今の定義でどう扱われるかを確かめる
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import content_guard

router = APIRouter(prefix="/api/content-guard", tags=["content-guard"])


class RuleUpdate(BaseModel):
    # patterns / words は文字列の並び(または1行に1つの文字列)、text は文字列、int は整数
    value: Any


class CheckRequest(BaseModel):
    tags: str = Field(max_length=4000)


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
async def put_rule(key: str, req: RuleUpdate) -> dict[str, Any]:
    try:
        return content_guard.set_value(key, req.value)
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
