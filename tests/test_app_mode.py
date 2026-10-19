"""APP_MODE: multi では .env の運営者のトークンに切り替えない(docs/multi_user_plan_jp.md のステップ 1)。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import client  # noqa: E402
from python.routes.manga_import import _optional_api_key  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.delenv("NOVELAI_API_KEY", raising=False)
    monkeypatch.setenv("NOVELAI_API_TOKEN", "pst-operator")
    monkeypatch.setattr(client, "_fallback_client", None)


def _first(gen):
    return asyncio.run(gen.__anext__())


def test_single_is_the_default_and_keeps_the_fallback() -> None:
    assert client.app_mode() == "single"
    assert client.api_keys_with_fallback("jwt-user") == ["jwt-user", "pst-operator"]
    assert _optional_api_key(None) == "pst-operator"


def test_multi_never_uses_the_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "multi")
    assert client.operator_token() is None
    assert client.api_keys_with_fallback("jwt-user") == ["jwt-user"]
    assert _optional_api_key(None) is None
    assert _optional_api_key("Bearer jwt-user") == "jwt-user"
    # 起動時に共通のクライアントを作らないので、Authorization がなければ 401
    asyncio.run(client.init_client())
    assert client._fallback_client is None
    with pytest.raises(HTTPException) as err:
        _first(client.get_client(None))
    assert err.value.status_code == 401


def test_multi_with_authorization_uses_the_callers_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", " Multi ")
    gen = client.get_client("Bearer jwt-user")
    novelai = _first(gen)
    assert novelai is not client._fallback_client
    asyncio.run(gen.aclose())


def test_unknown_mode_stops_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    # 書き間違い(multi-user など)を single として通さない
    monkeypatch.setenv("APP_MODE", "multi-user")
    with pytest.raises(ValueError):
        client.app_mode()
    with pytest.raises(ValueError):
        asyncio.run(client.init_client())
