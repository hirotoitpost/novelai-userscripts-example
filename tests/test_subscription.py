"""
Integration test: NovelAI アカウントの Anlas 残高・サブスクリプション情報を確認する。

実行要件:
  .env に NOVELAI_TEST_EMAIL と NOVELAI_TEST_PASSWORD が設定されていること。
  未設定の場合は自動的にスキップされます。

実行方法:
  uv run pytest tests/test_subscription.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

# プロジェクトルートの .env を読み込む
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

_NOVELAI_API = "https://api.novelai.net"

_TIER_NAMES = {0: "Free", 1: "Tablet", 2: "Scroll", 3: "Opus"}


def _creds() -> tuple[str, str] | None:
    email = os.environ.get("NOVELAI_TEST_EMAIL", "").strip()
    password = os.environ.get("NOVELAI_TEST_PASSWORD", "").strip()
    return (email, password) if email and password else None


# 認証情報が未設定ならスキップ
_skip = pytest.mark.skipif(
    _creds() is None,
    reason="NOVELAI_TEST_EMAIL / NOVELAI_TEST_PASSWORD が .env に未設定",
)


@_skip
def test_subscription() -> None:
    """ログインして /user/subscription を取得し、Anlas 残高を表示・検証する。"""
    creds = _creds()
    assert creds is not None
    email, password = creds

    async def _run() -> dict:  # type: ignore[type-arg]
        from python.auth_utils import login_with_credentials

        token = await login_with_credentials(email, password)

        async with httpx.AsyncClient() as http:
            resp = await http.get(
                f"{_NOVELAI_API}/user/subscription",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    data: dict = asyncio.run(_run())  # type: ignore[type-arg]

    # --- レスポンス全体を表示 ---
    print("\n" + "=" * 60)
    print("Subscription response (raw):")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("=" * 60)

    # --- 主要フィールドのサマリ表示 ---
    tier = data.get("tier", "?")
    active = data.get("active", "?")
    anlas = data.get("anlas", data.get("trainingStepsLeft", {}).get("purchasedTrainingSteps", "?"))
    tier_name = _TIER_NAMES.get(tier, str(tier)) if isinstance(tier, int) else str(tier)

    print(f"\n  Tier   : {tier_name} ({tier})")
    print(f"  Active : {active}")
    print(f"  Anlas  : {anlas}")
    print()

    assert isinstance(data, dict), "レスポンスが JSON オブジェクトではありません"
