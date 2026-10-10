"""
NovelAI 公式の「プロンプトチャンク」(promptmacros) の実データ構造を調査するための使い捨てスクリプト。

/user/login は reCAPTCHA 必須になっており script からは再現できないため、
ブラウザで実際にログインして得たセッション用 accessToken (JWT) を
.env の NOVELAI_SESSION_TOKEN に貼り付けて使う。
（NOVELAI_API_TOKEN の persistent access token は keystore で拒否されるため使えない）

暗号鍵の導出には NOVELAI_TEST_EMAIL / NOVELAI_TEST_PASSWORD が必要（ネットワーク不要）。
keystore を復号したうえで /user/objects/promptmacros を取得・復号し、中身をそのまま表示する。
実装予定機能のための一時調査用。動作確認できたら削除してよい。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

load_dotenv(_PROJECT_ROOT / ".env", override=True)

from python.auth_utils import get_encryption_key  # noqa: E402
from python.keystore_crypto import decrypt_keystore, decrypt_object  # noqa: E402

EMAIL = os.environ.get("NOVELAI_TEST_EMAIL")
PASSWORD = os.environ.get("NOVELAI_TEST_PASSWORD")
SESSION_TOKEN = os.environ.get("NOVELAI_SESSION_TOKEN")

# keystore がどちらのホストにあるか未確認のため両方試す
_KEYSTORE_HOSTS = ["https://api.novelai.net", "https://image.novelai.net"]
_OBJECTS_URL = "https://image.novelai.net/user/objects/promptmacros"


async def main() -> None:
    if not EMAIL or not PASSWORD:
        raise RuntimeError("NOVELAI_TEST_EMAIL / NOVELAI_TEST_PASSWORD が .env に設定されていません")
    if not SESSION_TOKEN:
        raise RuntimeError(
            "NOVELAI_SESSION_TOKEN が .env に設定されていません"
            "（ブラウザでログインした際の accessToken(JWT) を貼り付けてください）"
        )

    encryption_key = get_encryption_key(EMAIL, PASSWORD)
    headers = {"Authorization": f"Bearer {SESSION_TOKEN}"}

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        keystore_json = None
        for host in _KEYSTORE_HOSTS:
            resp = await client.get(f"{host}/user/keystore")
            print(f"GET {host}/user/keystore -> {resp.status_code}")
            if resp.status_code == 200:
                keystore_json = resp.json()
                break
            print(f"  body: {resp.text[:500]}")
        if keystore_json is None:
            raise RuntimeError("keystore の取得に失敗しました（両方のホストで失敗）")

        keystore = decrypt_keystore(keystore_json, encryption_key)
        print(f"keystore 復号成功: {len(keystore)} 件のアイテム鍵")

        resp = await client.get(_OBJECTS_URL)
        print(f"GET {_OBJECTS_URL} -> {resp.status_code}")
        resp.raise_for_status()
        body = resp.json()

    items = body.get("objects", body) if isinstance(body, dict) else body
    print(f"promptmacros オブジェクト件数: {len(items)}")

    for item in items:
        try:
            decrypted = decrypt_object(item, keystore)
        except Exception as e:  # noqa: BLE001 調査用に例外内容を見たいので握りつぶす
            print(f"  [id={item.get('id')}] 復号失敗: {e}")
            continue
        print(f"  [id={item.get('id')}] {json.dumps(decrypted, ensure_ascii=False)}")


if __name__ == "__main__":
    asyncio.run(main())
