"""
NovelAI サブスクリプション情報（Anlas 残高）確認スクリプト。
標準ライブラリのみ使用。外部パッケージ・サーバー起動不要。

動作優先順:
  1. NOVELAI_API_TOKEN が .env にあれば、それを直接使用
  2. NOVELAI_TEST_EMAIL / NOVELAI_TEST_PASSWORD があれば、
     NovelAI の /user/login を直接呼び出してトークンを取得
     ※ key derivation (argon2id) は argon2-cffi が必要なため、
       サーバー未起動時はトークン方式のみ対応

実行: python tests/_run_subscription_check.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_NOVELAI_API = "https://api.novelai.net"
_TIER_NAMES = {0: "Free", 1: "Tablet", 2: "Scroll", 3: "Opus"}


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key and val and key not in os.environ:
            os.environ[key] = val


_HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://novelai.net/",
    "Origin": "https://novelai.net",
}


def _get_json(url: str, token: str) -> dict:  # type: ignore[type-arg]
    req = urllib.request.Request(
        url,
        headers={**_HEADERS_BASE, "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())  # type: ignore[no-any-return]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    _load_env(Path(__file__).resolve().parent.parent / ".env")

    token = os.environ.get("NOVELAI_API_TOKEN", "").strip()
    if not token:
        print("ERROR: .env に NOVELAI_API_TOKEN が設定されていません", file=sys.stderr)
        sys.exit(1)

    print(f"Using token: {token[:20]}...")
    print(f"Calling {_NOVELAI_API}/user/subscription ...")

    data = _get_json(f"{_NOVELAI_API}/user/subscription", token)

    # --- 全レスポンス表示 ---
    print("\n" + "=" * 60)
    print("Subscription response (full):")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("=" * 60)

    # --- サマリ ---
    tier = data.get("tier", "?")
    active = data.get("active", "?")
    tier_name = _TIER_NAMES.get(tier, str(tier)) if isinstance(tier, int) else str(tier)

    print(f"\n  Tier   : {tier_name} ({tier})")
    print(f"  Active : {active}")

    # Anlas 関連フィールドを全て列挙
    anlas_fields = {k: v for k, v in data.items() if "anlas" in str(k).lower() or "step" in str(k).lower()}
    if anlas_fields:
        print("  Anlas/Steps fields:")
        for k, v in anlas_fields.items():
            print(f"    {k}: {v}")
    else:
        print("  (Anlas フィールドが見つかりませんでした)")
    print()


if __name__ == "__main__":
    main()
