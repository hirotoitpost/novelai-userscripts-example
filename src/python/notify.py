"""
長い処理(分割・タグ付け・コマ生成など)の終了をブラウザへプッシュ通知する(Web Push)。

ページ側の通知(new Notification)はページが動いている間しか出せず、スマホは画面を消すと
ページが止まる。Web Push ならサーバーからブラウザのプッシュサービス経由で Service Worker を
起こして通知を出せるので、ブラウザを閉じていても届く。

VAPID の鍵は初回に data/vapid_private.pem として作る(data/ は git 管理外)。購読は DB に持つ。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from .db import delete_push_subscription, get_connection, list_push_subscriptions

logger = logging.getLogger(__name__)

_VAPID_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "vapid_private.pem"
_vapid: Vapid02 | None = None

# 物語のジョブの種類 → 表示名(フロントの JOB_KIND_LABELS と揃える)
JOB_KIND_LABELS = {
    "split": "シーン分割・タグ付け",
    "illustrate": "挿絵の生成",
    "characters": "登場人物の抽出",
    "cast": "登場人物をそろえる(参照画像も)",
    "sfx": "効果音の提案",
    "narration": "ナレーションの作成",
    "panels": "コマの絵の生成",
    "sfx_fonts": "描き文字の選択",
    "manga": "漫画にする(コマの生成と合成)",
}

_STATUS_WORDS = {"done": "✅ 完了", "error": "⚠️ 失敗", "cancelled": "⏹️ キャンセル"}


def _get_vapid() -> Vapid02:
    global _vapid
    if _vapid is None:
        if _VAPID_PATH.exists():
            _vapid = Vapid02.from_file(str(_VAPID_PATH))
        else:
            _VAPID_PATH.parent.mkdir(parents=True, exist_ok=True)
            _vapid = Vapid02()
            _vapid.generate_keys()
            _vapid.save_key(str(_VAPID_PATH))
    return _vapid


def vapid_public_key() -> str:
    """PushManager.subscribe の applicationServerKey に渡す形(非圧縮点の base64url)。"""
    raw = _get_vapid().public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _send_one(subscription: dict[str, Any], payload: str) -> bool:
    """1件送る。購読が失効していたら False(呼び出し側で削除する)。"""
    try:
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=_get_vapid(),
            # webpush() は claims に aud/exp を書き足すので毎回作り直す
            vapid_claims={"sub": os.environ.get("PUSH_CONTACT", "mailto:noreply@example.com")},
            ttl=60 * 60 * 12,
            timeout=10,
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):
            return False
        logger.warning("プッシュ通知の送信に失敗しました: %s", exc)
    return True


async def send_push(title: str, body: str, *, url: str = "/story", tag: str = "nai-task") -> int:
    """購読しているすべてのブラウザへ送る。送れた件数を返す。失敗しても例外にしない。"""
    conn = get_connection()
    try:
        subscriptions = list_push_subscriptions(conn)
    finally:
        conn.close()
    if not subscriptions:
        return 0

    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
    sent = 0
    for sub in subscriptions:
        info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
        try:
            alive = await asyncio.to_thread(_send_one, info, payload)
        except Exception as exc:  # noqa: BLE001 通知の失敗で処理を落とさない
            logger.warning("プッシュ通知の送信に失敗しました: %s", exc)
            continue
        if alive:
            sent += 1
        else:
            conn = get_connection()
            try:
                delete_push_subscription(conn, sub["endpoint"])
            finally:
                conn.close()
    return sent


def _min_seconds() -> float:
    try:
        return float(os.environ.get("PUSH_MIN_SECONDS", "15"))
    except ValueError:
        return 15.0


async def notify_job_finished(story_id: int, kind: str, status: str, message: str, elapsed: float) -> None:
    """物語のジョブの終了を通知する。すぐ終わった処理は通知しない。"""
    if status not in _STATUS_WORDS or elapsed < _min_seconds():
        return
    label = JOB_KIND_LABELS.get(kind, kind)
    minutes, seconds = divmod(int(elapsed), 60)
    duration = f"{minutes}分{seconds}秒" if minutes else f"{seconds}秒"
    await send_push(
        f"{_STATUS_WORDS[status]}: {label}",
        f"物語#{story_id} ・ {message}\n({duration})" if message else f"物語#{story_id}({duration})",
        url=f"/story?story={story_id}",
        # ページ側の通知と同じ tag にして、両方届いても1件にまとめる
        tag=f"nai-job-{story_id}",
    )
