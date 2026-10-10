"""処理終了のプッシュ通知(Web Push)の購読。送信は notify.py。"""

from __future__ import annotations

from fastapi import APIRouter, Header
from pydantic import BaseModel

from ..db import delete_push_subscription, get_connection, upsert_push_subscription
from ..notify import send_push, vapid_public_key

router = APIRouter(prefix="/api/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscription(BaseModel):
    """PushSubscription.toJSON() の形。"""

    endpoint: str
    keys: PushKeys


class PushUnsubscribe(BaseModel):
    endpoint: str


@router.get("/public-key")
async def get_public_key() -> dict[str, str]:
    return {"public_key": vapid_public_key()}


@router.post("/subscribe")
async def subscribe(sub: PushSubscription, user_agent: str = Header(default="")) -> dict[str, bool]:
    conn = get_connection()
    try:
        upsert_push_subscription(conn, sub.endpoint, sub.keys.p256dh, sub.keys.auth, user_agent)
    finally:
        conn.close()
    return {"ok": True}


@router.post("/unsubscribe")
async def unsubscribe(req: PushUnsubscribe) -> dict[str, bool]:
    conn = get_connection()
    try:
        delete_push_subscription(conn, req.endpoint)
    finally:
        conn.close()
    return {"ok": True}


@router.post("/test")
async def send_test() -> dict[str, int]:
    """通知が届くかの確認用。購読しているすべてのブラウザへ送る。"""
    sent = await send_push("🔔 テスト通知", "処理が終わるとこのように通知します。", tag="nai-test")
    return {"sent": sent}
