"""テスト全体の前置き。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_app_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    どのテストも既定の single から始める。バックエンドを読み込むと手元の .env も読み込まれるので、そこに
    APP_MODE=multi があると(multi を試しているとき)、.env のトークンでやり直すテストなどが落ちていた。
    multi のテストは自分で APP_MODE を設定する。
    """
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.delenv("APP_ALLOWED_ORIGINS", raising=False)
