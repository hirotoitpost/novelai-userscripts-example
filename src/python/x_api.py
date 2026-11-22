"""
X(旧 Twitter)の API を、自分のアカウントとして呼ぶ(OAuth 1.0a のユーザーのトークン。期限が無い)。

キーは .env に置く(画面・DB には入れない):
  X_API_KEY / X_API_SECRET                    アプリのコンシューマーキー
  X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET      自分のアカウントのアクセストークン(読み取りと書き込み)

使う API(どれも 2026-11 に実機で確かめた。v2 の画像のアップロードも OAuth 1.0a で通る):
  GET  /2/users/me                 つながっているアカウント
  POST /2/media/upload             画像を上げる(multipart。1枚 5MB まで)
  POST /2/media/metadata           上げた画像にセンシティブの警告(adult_content)・代替テキストを付ける
  POST /2/tweets                   投稿する(画像は4枚まで。reply で前の投稿へのスレッドにする)
  DELETE /2/tweets/{id}            投稿を消す
  GET  /2/tweets?ids=...           投稿の反応(いいね・リポストなどの数)

料金は使った分だけの前払い(クレジット)。呼ぶたびに少しかかるので、読み取りは画面のボタンを押したときだけにする。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

API = "https://api.x.com"
_KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
_TIMEOUT = 60.0
# 1枚の画像の上限(X の決まり)
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class XApiError(RuntimeError):
    """X の API がエラーを返した(status は HTTP の状態。0 は通信できなかった)。"""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str
    access_token: str
    access_secret: str


def credentials() -> Credentials | None:
    """.env のキー(4つそろっていなければ None)。"""
    values = [os.environ.get(name, "").strip() for name in _KEYS]
    if not all(values):
        return None
    return Credentials(*values)


def configured() -> bool:
    return credentials() is not None


def _quote(value: object) -> str:
    return urllib.parse.quote(str(value), safe="~")


def authorization(creds: Credentials, method: str, url: str, params: dict[str, str] | None = None) -> str:
    """
    OAuth 1.0a の署名つき Authorization ヘッダー(HMAC-SHA1)。params はクエリの値(署名に入れる)。
    JSON や multipart の本文は署名に入れない(OAuth 1.0a の決まり)。
    """
    oauth = {
        "oauth_consumer_key": creds.api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": creds.access_token,
        "oauth_version": "1.0",
    }
    signed = {**oauth, **(params or {})}
    normalized = "&".join(f"{_quote(k)}={_quote(v)}" for k, v in sorted(signed.items()))
    base = "&".join([method.upper(), _quote(url), _quote(normalized)])
    key = f"{_quote(creds.api_secret)}&{_quote(creds.access_secret)}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_quote(k)}="{_quote(v)}"' for k, v in sorted(oauth.items()))


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        parts = [str(body.get(k)) for k in ("title", "detail") if body.get(k)]
        for error in body.get("errors") or []:
            if isinstance(error, dict):
                parts.append(str(error.get("message") or error.get("detail") or error))
        if parts:
            return " / ".join(dict.fromkeys(parts))[:300]
    return json.dumps(body, ensure_ascii=False)[:300]


class XClient:
    """自分のアカウントとして X の API を呼ぶ(同期。バックエンドではスレッドで呼ぶ)。"""

    def __init__(self, creds: Credentials | None = None, http: httpx.Client | None = None) -> None:
        found = creds or credentials()
        if found is None:
            raise XApiError("X の API キーが .env にありません(X_API_KEY など4つ)。")
        self.creds = found
        self.http = http or httpx.Client(timeout=_TIMEOUT)

    def _request(self, method: str, path: str, *, params: dict[str, str] | None = None, **kwargs: Any) -> Any:
        url = f"{API}{path}"
        headers = {"Authorization": authorization(self.creds, method, url, params), **kwargs.pop("headers", {})}
        try:
            response = self.http.request(method, url, params=params, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise XApiError(f"X に接続できませんでした: {exc}") from exc
        if response.status_code >= 400:
            raise XApiError(f"X の API のエラー({response.status_code}): {_error_text(response)}", response.status_code)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def me(self) -> dict[str, Any]:
        """つながっているアカウント({id, name, username})。"""
        return dict(self._request("GET", "/2/users/me").get("data") or {})

    def upload_image(self, data: bytes, mime: str, filename: str = "image") -> str:
        """画像を上げて、メディアの ID を返す(24 時間以内に投稿に付けないと消える)。"""
        if len(data) > MAX_IMAGE_BYTES:
            raise XApiError(f"画像が大きすぎます({len(data) // 1024}KB。5MB まで)")
        body = self._request(
            "POST",
            "/2/media/upload",
            files={"media": (filename, data, mime)},
            data={"media_category": "tweet_image", "media_type": mime},
        )
        media_id = (body.get("data") or {}).get("id")
        if not media_id:
            raise XApiError("画像を上げましたが、メディアの ID が返ってきませんでした。")
        return str(media_id)

    def set_media_metadata(self, media_id: str, *, sensitive: bool = False, alt_text: str | None = None) -> None:
        """上げた画像に、センシティブの警告(成人向け)と代替テキストを付ける。"""
        metadata: dict[str, Any] = {}
        if alt_text:
            metadata["alt_text"] = {"text": alt_text[:1000]}
        if sensitive:
            metadata["sensitive_media_warning"] = {"adult_content": True}
        if not metadata:
            return
        self._request(
            "POST",
            "/2/media/metadata",
            headers={"Content-Type": "application/json"},
            content=json.dumps({"id": media_id, "metadata": metadata}),
        )

    def create_post(self, text: str, media_ids: list[str] | None = None, reply_to: str | None = None) -> str:
        """投稿して、投稿の ID を返す。reply_to を渡すと、その投稿への返信(スレッドの続き)にする。"""
        payload: dict[str, Any] = {}
        if text:
            payload["text"] = text
        if media_ids:
            payload["media"] = {"media_ids": media_ids}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}
        body = self._request(
            "POST", "/2/tweets", headers={"Content-Type": "application/json"}, content=json.dumps(payload)
        )
        post_id = (body.get("data") or {}).get("id")
        if not post_id:
            raise XApiError("投稿しましたが、投稿の ID が返ってきませんでした。")
        return str(post_id)

    def delete_post(self, post_id: str) -> None:
        self._request("DELETE", f"/2/tweets/{post_id}")

    def metrics(self, post_ids: list[str]) -> dict[str, dict[str, int]]:
        """投稿ごとの反応の数(public_metrics: like_count, retweet_count, reply_count, quote_count など)。"""
        if not post_ids:
            return {}
        body = self._request(
            "GET", "/2/tweets", params={"ids": ",".join(post_ids[:100]), "tweet.fields": "public_metrics"}
        )
        return {
            str(item["id"]): dict(item.get("public_metrics") or {})
            for item in body.get("data") or []
            if isinstance(item, dict) and item.get("id")
        }


def post_url(username: str | None, post_id: str) -> str:
    return f"https://x.com/{username or 'i/web'}/status/{post_id}"


def weighted_length(text: str) -> int:
    """
    X の文字数の数え方の近似: 半角の英数字・ラテン文字などは 1、日本語などは 2、URL は 23。上限は 280。
    """
    total = 0
    for word in text.split(" "):
        if word.startswith(("http://", "https://")) and len(word) > 8:
            total += 23 + 1
            continue
        for char in word:
            code = ord(char)
            light = (
                code <= 0x10FF
                or 0x2000 <= code <= 0x200D
                or 0x2010 <= code <= 0x201F
                or 0x2032 <= code <= 0x2037
            )
            total += 1 if light else 2
        total += 1
    return max(0, total - 1)


MAX_WEIGHTED = 280
