"""テスト全体の前置き。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# バックエンドを読み込むとプロセスのログ(data/logs/backend.log)を付けるので、テストのログは一時フォルダへ
os.environ["NAI_LOG_DIR"] = tempfile.mkdtemp(prefix="nai-test-logs-")

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


@pytest.fixture(autouse=True)
def _isolated_generation_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """生成の記録(app_logging.record_generation_event)は一時フォルダへ(手元の利用者のフォルダに書かない)。"""
    monkeypatch.setenv("NAI_GENERATION_LOG_DIR", str(tmp_path / "generation-logs"))
