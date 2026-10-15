"""
Docker のコンテナでバックエンドを起動する(Dockerfile の CMD)。

ログは標準エラー(docker compose logs app で見る)と、data/run/container/backend.err.log の両方に出す(管理ページの
ログの欄はこのファイルを読む)。開発中のサーバー(dev-ctl.ps1)は data/run を使うので、混ざらないようにフォルダを分ける。
"""

from __future__ import annotations

import logging.config
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = ROOT / "data" / "run" / "container"


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "backend.scheme").write_text("http", encoding="utf-8")
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"()": "uvicorn.logging.DefaultFormatter", "fmt": "%(levelprefix)s %(message)s", "use_colors": False},
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "use_colors": False,
            },
        },
        "handlers": {
            "stderr": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"},
            "access_stderr": {"class": "logging.StreamHandler", "formatter": "access", "stream": "ext://sys.stderr"},
            "file": {
                "class": "logging.handlers.RotatingFileHandler", "formatter": "default",
                "filename": str(RUN_DIR / "backend.err.log"), "maxBytes": 5_000_000, "backupCount": 2, "encoding": "utf-8",
            },
            "access_file": {
                "class": "logging.handlers.RotatingFileHandler", "formatter": "access",
                "filename": str(RUN_DIR / "backend.err.log"), "maxBytes": 5_000_000, "backupCount": 2, "encoding": "utf-8",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["stderr", "file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access_stderr", "access_file"], "level": "INFO", "propagate": False},
        },
    }
    logging.config.dictConfig(log_config)
    sys.path.insert(0, str(ROOT / "src"))
    uvicorn.run("python.server:app", host="0.0.0.0", port=8000, log_config=log_config)


if __name__ == "__main__":
    main()
