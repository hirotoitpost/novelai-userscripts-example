"""
APP_MODE=multi でバックエンドを読み込めるか(利用者が決まっていない読み込みの時点で、利用者のフォルダを開かない)。

実行方法:
  uv run pytest tests/test_multi_startup.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_server_imports_in_multi_mode() -> None:
    # 別のプロセスで読み込む(このプロセスはほかのテストがもう読み込んでいるので)
    env = {**os.environ, "APP_MODE": "multi"}
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import python.server"],
        cwd=_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
