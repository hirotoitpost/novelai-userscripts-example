"""
ログの設定と、画像生成の記録。

1. プロセスのログ(setup_logging): data/logs/backend.log に、日付が変わるごとに切り替えて残す(既定 30 日分)。
   アプリの INFO 以上と uvicorn のログ(アクセスログも)を、時刻付きで書く。以前は起動スクリプト(dev-ctl.ps1)が
   標準出力をファイルに向けるだけで、再起動のたびに上書きされ、アプリの INFO も捨てられていた。
2. 生成の記録(record_generation_event): NovelAI に画像を頼んだ1回ごとに、送った内容(プロンプト・設定・
   参照画像のハッシュ)と結果(絵のハッシュ・かかった時間・失敗の理由)を、利用者のフォルダの
   logs/generation-YYYY-MM.jsonl に1行ずつ足す(既定 12 か月分)。同じ設定で描き直しても同じ絵にならなかったとき
   (2026-11 実機)に、何を送ったかを後から確かめるため。プロンプトは利用者の作品なので、その人のフォルダに置く。

保存の日数・月数は環境変数 NAI_LOG_KEEP_DAYS / NAI_GENERATION_LOG_KEEP_MONTHS、置き場所は NAI_LOG_DIR
(プロセスのログ)・NAI_GENERATION_LOG_DIR(生成の記録。利用者に関わらずここにまとめる。テスト用)で変えられる。
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_HANDLER_NAME = "nai-backend-file"
# uvicorn は自分のロガーに直接ハンドラを付け、ルートへは流さない(アクセスログなど)ので、そちらにも付ける
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access")


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "")))
    except ValueError:
        return default


def log_dir() -> Path:
    return Path(os.environ.get("NAI_LOG_DIR") or _PROJECT_ROOT / "data" / "logs")


def setup_logging() -> Path:
    """プロセスのログをファイルにも書く(何度呼んでも1回だけ付ける)。ログのファイルのパスを返す。"""
    folder = log_dir()
    path = folder / "backend.log"
    root = logging.getLogger()
    if any(h.get_name() == _HANDLER_NAME for h in root.handlers):
        return path
    folder.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        path, when="midnight", backupCount=_int_env("NAI_LOG_KEEP_DAYS", 30), encoding="utf-8", delay=True
    )
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    for name in _UVICORN_LOGGERS:
        named = logging.getLogger(name)
        if not named.propagate:
            named.addHandler(handler)
    return path


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64_sha256(image_b64: str) -> str:
    """base64 の画像(data URL の頭があってもよい)の中身のハッシュ。"""
    if "," in image_b64[:100]:
        image_b64 = image_b64.split(",", 1)[1]
    return sha256_hex(base64.b64decode(image_b64))


# 送った内容のうち、画像そのもの(大きい)はハッシュに置き換えて残す
_IMAGE_LIST_KEYS = ("director_reference_images", "reference_image_multiple")
_IMAGE_KEYS = ("image", "mask")


def request_summary(body: dict[str, Any]) -> dict[str, Any]:
    """NovelAI へ送る本文の写し。画像は sha256 にする(何を送ったかは比べられ、記録は小さいまま)。"""
    summary = copy.deepcopy(body)
    parameters = summary.get("parameters") or {}
    for key in _IMAGE_LIST_KEYS:
        if parameters.get(key):
            parameters[key] = [f"sha256:{b64_sha256(v)}" for v in parameters[key]]
    for key in _IMAGE_KEYS:
        if isinstance(parameters.get(key), str):
            parameters[key] = f"sha256:{b64_sha256(parameters[key])}"
    return summary


_write_lock = threading.Lock()
_pruned: set[Path] = set()


def _prune(folder: Path) -> None:
    """保存する月数より古い生成の記録を消す(フォルダごとに、プロセスで1回)。"""
    if folder in _pruned:
        return
    _pruned.add(folder)
    keep = _int_env("NAI_GENERATION_LOG_KEEP_MONTHS", 12)
    now = datetime.now(timezone.utc)
    oldest = now.year * 12 + now.month - 1 - (keep - 1)
    for path in folder.glob("generation-*.jsonl"):
        try:
            year, month = (int(v) for v in path.stem.split("-")[1:3])
        except ValueError:
            continue
        if year * 12 + month - 1 < oldest:
            path.unlink(missing_ok=True)


def generation_log_dir(root: Path | None = None) -> Path:
    """生成の記録のフォルダ: NAI_GENERATION_LOG_DIR があればそこ、無ければ利用者のフォルダ(root。省くと今の利用者)の logs。"""
    override = os.environ.get("NAI_GENERATION_LOG_DIR")
    if override:
        return Path(override)
    if root is None:
        from .db import user_root

        root = user_root()
    return root / "logs"


def record_generation_event(event: dict[str, Any], root: Path | None = None) -> None:
    """
    生成の記録を1行足す。root は利用者のフォルダ(省くと今の利用者)。記録に失敗しても生成は止めない
    (ログに警告を残すだけ)。
    """
    try:
        from .db import current_user_id

        folder = generation_log_dir(root)
        now = datetime.now(timezone.utc)
        record = {"ts": now.isoformat(timespec="milliseconds"), "user_id": current_user_id(), **event}
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _write_lock:
            folder.mkdir(parents=True, exist_ok=True)
            _prune(folder)
            with open(folder / f"generation-{now:%Y-%m}.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:  # noqa: BLE001 - 記録の失敗で生成を止めない
        logger.warning("生成の記録を書けませんでした", exc_info=True)
