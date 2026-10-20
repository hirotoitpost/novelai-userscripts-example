"""テスト全体の前置き。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    どのテストも一時フォルダの DB から始める。DB を差し替えないテストは手元の data/app.db を読んでいて、
    開発管理者のページでコンテンツガードのルールを変えると落ちていた(ガードの設定はシステム DB にある)。
    利用者ごとの DB(data/users/{id}/app.db)も _DB_PATH の隣に作られるので、一緒に一時フォルダになる。
    自分で _DB_PATH を差し替えるテストは、そのままそちらが使われる。
    """
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")


@pytest.fixture(autouse=True)
def _default_app_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    どのテストも既定の single から始める。バックエンドを読み込むと手元の .env も読み込まれるので、そこに
    APP_MODE=multi があると(multi を試しているとき)、.env のトークンでやり直すテストなどが落ちていた。
    multi のテストは自分で APP_MODE を設定する。
    """
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.delenv("APP_ALLOWED_ORIGINS", raising=False)
