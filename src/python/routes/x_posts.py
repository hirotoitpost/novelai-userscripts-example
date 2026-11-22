"""
X への投稿の API(/api/x)。中身は x_posts.py(投稿・予約・安全の確認)と x_api.py(X の API)。

  GET    /api/x/status                 キーがあるか・使える利用者か(アカウントの確認はしない。料金がかかるので)
  POST   /api/x/account                つながっているアカウントを X に聞く(読み取り1回)
  POST   /api/x/resolve                画像の指定を、表示用の情報(ファイル・成人向けの印)に
  POST   /api/x/check                  投稿できるかを確かめる(登録はしない)
  GET    /api/x/posts                  投稿の一覧(予約中が先、あとは新しい順)
  POST   /api/x/posts                  投稿を登録する(scheduled_at が無ければ今すぐ投稿)
  PUT    /api/x/posts/{id}             予約中の投稿を書き換える
  POST   /api/x/posts/{id}/cancel      予約を取り消す
  POST   /api/x/posts/{id}/retry       失敗・見送り・取り消しを、今すぐ(または指定の時刻に)やり直す
  POST   /api/x/posts/{id}/delete-remote   X から投稿を消す(記録は残す)
  POST   /api/x/metrics                投稿済みの反応の数を X から読む
  DELETE /api/x/posts/{id}             記録を消す(X の投稿は消えない)
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import x_api, x_posts
from ..db import current_user_id


def require_owner() -> None:
    """multi では、X のキーの持ち主の利用者だけ(ほかの人が運営者のアカウントに投稿しないように)。"""
    if not x_posts.is_owner():
        raise HTTPException(status_code=403, detail="X への投稿は、X のキーを設定した利用者だけが使えます。")


router = APIRouter(prefix="/api/x", tags=["x"], dependencies=[Depends(require_owner)])


class PartBody(BaseModel):
    text: str = Field("", max_length=1000)
    refs: list[str] = Field(default_factory=list, max_length=x_posts.MAX_IMAGES_PER_PART + 4)


class PostBody(BaseModel):
    title: str = Field("", max_length=120)
    parts: list[PartBody] = Field(min_length=1, max_length=x_posts.MAX_PARTS)
    sensitive: bool = False
    # 予約の時刻(タイムゾーンつきの ISO 形式)。無ければ今すぐ
    scheduled_at: datetime | None = None


class RefsBody(BaseModel):
    refs: list[str] = Field(min_length=1, max_length=x_posts.MAX_PARTS * x_posts.MAX_IMAGES_PER_PART)


class RetryBody(BaseModel):
    scheduled_at: datetime | None = None


class MetricsBody(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


def _require_keys() -> None:
    if not x_api.configured():
        raise HTTPException(status_code=409, detail="X の API キーが .env にありません(X_API_KEY など4つ)。")


def _when(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if when.astimezone(timezone.utc) < datetime.now(timezone.utc) - x_posts.MISS_AFTER:
        raise HTTPException(status_code=422, detail="予約の時刻が過ぎています。")
    return when


def _parts(body: PostBody) -> list[x_posts.PartInput]:
    return [x_posts.PartInput(part.text, list(part.refs)) for part in body.parts]


def _kick() -> None:
    """時刻になった投稿を、裏で今すぐ投稿する(見回りを待たない)。"""
    threading.Thread(target=x_posts.run_tick, name="x-post", daemon=True).start()


def _get(post_id: int) -> dict[str, Any]:
    post = x_posts.get_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="投稿が見つかりません。")
    return post


@router.get("/status")
def get_status() -> dict[str, Any]:
    return {"configured": x_api.configured(), "owner": True, "user_id": current_user_id()}


@router.post("/account")
def get_account() -> dict[str, Any]:
    _require_keys()
    try:
        return x_api.XClient().me()
    except x_api.XApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/resolve")
def resolve_refs(body: RefsBody) -> list[dict[str, Any]]:
    """画像の指定を表示用に。本1冊(book:{ID})は全ページに広げる。"""
    refs = x_posts.expand_refs(body.refs)
    found = x_posts.resolve(refs)
    return [found[ref].public() for ref in refs if ref in found]


@router.post("/check")
def check_post(body: PostBody) -> dict[str, Any]:
    """登録せずに、投稿できるかだけを確かめる(画面の「確認」)。"""
    try:
        _, adult, warnings = x_posts.build_parts(_parts(body))
    except x_posts.PostRejected as exc:
        return {"ok": False, "reasons": exc.reasons, "adult": False, "warnings": []}
    return {"ok": True, "reasons": [], "adult": adult, "warnings": warnings}


@router.get("/posts")
def get_posts() -> list[dict[str, Any]]:
    return x_posts.list_posts()


@router.post("/posts")
def post_post(body: PostBody) -> dict[str, Any]:
    _require_keys()
    try:
        post = x_posts.create_post(
            body.title, _parts(body), sensitive=body.sensitive, scheduled_at=_when(body.scheduled_at)
        )
    except x_posts.PostRejected as exc:
        raise HTTPException(status_code=422, detail={"message": "投稿できません。", "reasons": exc.reasons}) from exc
    if body.scheduled_at is None:
        _kick()
    return post


@router.put("/posts/{post_id}")
def put_post(post_id: int, body: PostBody) -> dict[str, Any]:
    _get(post_id)
    try:
        post = x_posts.update_scheduled(
            post_id, body.title, _parts(body), sensitive=body.sensitive, scheduled_at=_when(body.scheduled_at)
        )
    except x_posts.PostRejected as exc:
        raise HTTPException(status_code=422, detail={"message": "投稿できません。", "reasons": exc.reasons}) from exc
    if body.scheduled_at is None:
        _kick()
    return post


@router.post("/posts/{post_id}/cancel")
def cancel_post(post_id: int) -> dict[str, Any]:
    post = _get(post_id)
    if post["status"] != x_posts.SCHEDULED:
        raise HTTPException(status_code=409, detail="予約中の投稿だけ取り消せます。")
    return x_posts.set_status(post_id, x_posts.CANCELLED) or {}


@router.post("/posts/{post_id}/retry")
def retry_post(post_id: int, body: RetryBody) -> dict[str, Any]:
    _require_keys()
    post = _get(post_id)
    if post["status"] not in (x_posts.FAILED, x_posts.MISSED, x_posts.CANCELLED):
        raise HTTPException(status_code=409, detail="失敗・見送り・取り消しの投稿だけやり直せます。")
    when = _when(body.scheduled_at) or datetime.now(timezone.utc)
    updated = x_posts.set_status(post_id, x_posts.SCHEDULED, scheduled_at=when) or {}
    if body.scheduled_at is None:
        _kick()
    return updated


@router.post("/posts/{post_id}/delete-remote")
def delete_remote(post_id: int) -> dict[str, Any]:
    _require_keys()
    post = _get(post_id)
    if not post["tweet_ids"]:
        raise HTTPException(status_code=409, detail="X に投稿されていません。")
    try:
        return x_posts.delete_remote(post_id) or {}
    except x_api.XApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/metrics")
def refresh_metrics(body: MetricsBody) -> list[dict[str, Any]]:
    _require_keys()
    try:
        return x_posts.refresh_metrics(body.ids)
    except x_api.XApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: int) -> None:
    post = _get(post_id)
    if post["status"] == x_posts.POSTING:
        raise HTTPException(status_code=409, detail="投稿している途中です。")
    x_posts.delete_record(post_id)
