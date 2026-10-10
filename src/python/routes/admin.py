"""
開発管理者のページの API(/api/admin)。サーバーの状態・ログ・動いている処理・外部サービス・設定(.env)・
データの管理・サーバーの操作・アプリの既定値を扱う。

この PC 自身から開いたときだけ使える(require_local)。.env の秘密やサーバーの再起動を扱うので、LAN のほかの
端末や、この PC のブラウザで開いたほかのサイトのページからは使えないようにする:
- 接続元がこの PC(127.0.0.1 / ::1)であること
- フロント(Vite)の中継を通っていないこと(中継を通ると、スマホからでも接続元がこの PC に見える。
  vite.config.ts でも /api/admin は中継しない)
- Host と Origin が localhost / 127.0.0.1 であること(バックエンドの CORS は全部許可なので、これが無いと
  ほかのサイトのページから読めてしまう。名前を 127.0.0.1 に向ける手口(DNS rebinding)も Host で防ぐ)
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import app_settings, db
from ..db import get_connection

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_RUN_DIR = _PROJECT_ROOT / "data" / "run"
_ENV_FILE = _PROJECT_ROOT / ".env"
_ENV_EXAMPLE = _PROJECT_ROOT / ".env.example"
_BACKUP_DIR = _PROJECT_ROOT / "data" / "backups"
_CERT_DIR = _PROJECT_ROOT / "data" / "certs"
_MODEL_DIR = _PROJECT_ROOT / "data" / "models"
_STARTED_AT = time.time()

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _hostname(value: str) -> str:
    """Host ヘッダーや Origin からホスト名(ポート・[] を除く)。"""
    host = urlsplit(value if "//" in value else f"//{value}").hostname
    return (host or "").lower()


def require_local(request: Request) -> None:
    """この PC 自身から、直接開いたときだけ通す(モジュールの説明を参照)。"""
    denied = HTTPException(
        status_code=403, detail="開発管理者のページは、この PC から(localhost で)開いたときだけ使えます。"
    )
    if request.client is None or not _is_loopback(request.client.host):
        raise denied
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("forwarded")
    if forwarded:
        raise denied
    if _hostname(request.headers.get("host", "")) not in _LOCAL_HOSTS:
        raise denied
    origin = request.headers.get("origin")
    if origin and _hostname(origin) not in _LOCAL_HOSTS:
        raise denied
    if request.headers.get("sec-fetch-site") == "cross-site" and not origin:
        raise denied


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_local)])


# ---- 状態とログ ----


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=_PROJECT_ROOT, capture_output=True, text=True, timeout=5, encoding="utf-8"
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


async def _frontend_alive(scheme: str) -> bool:
    try:
        async with httpx.AsyncClient(verify=False, timeout=1.5) as client:
            return (await client.get(f"{scheme}://127.0.0.1:5173/")).status_code < 500
    except httpx.HTTPError:
        return False


@router.get("/status")
async def get_status() -> dict[str, Any]:
    backend_scheme = _read(_RUN_DIR / "backend.scheme") or "http"
    frontend_scheme = _read(_RUN_DIR / "frontend.scheme") or backend_scheme
    return {
        "backend": {
            "pid": os.getpid(),
            "scheme": backend_scheme,
            "started_at": _STARTED_AT,
            "uptime_seconds": time.time() - _STARTED_AT,
            "python": sys.version.split()[0],
        },
        "frontend": {
            "pid": _read(_RUN_DIR / "frontend.pid") or None,
            "scheme": frontend_scheme,
            "alive": await _frontend_alive(frontend_scheme),
        },
        "git": {
            "commit": _git("rev-parse", "--short", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "subject": _git("log", "-1", "--format=%s"),
            "date": _git("log", "-1", "--format=%cI"),
            "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        },
        "database": {"path": str(db._DB_PATH), "bytes": db._DB_PATH.stat().st_size if db._DB_PATH.exists() else 0},
    }


_LOGS = {
    "backend": "backend.log",
    "backend.err": "backend.err.log",
    "frontend": "frontend.log",
    "frontend.err": "frontend.err.log",
}
# 色などの端末の制御文字(Vite のログに入る)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@router.get("/logs")
async def get_logs(name: str = "backend.err", lines: int = 200) -> dict[str, Any]:
    """ログの末尾。uvicorn の起動・リクエスト・エラーは backend.err(標準エラー)に出る。"""
    if name not in _LOGS:
        raise HTTPException(status_code=404, detail="そのログはありません。")
    path = _RUN_DIR / _LOGS[name]
    lines = min(max(lines, 10), 2000)
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return {"name": name, "lines": [], "bytes": 0, "updated_at": None}
    tail = [_ANSI.sub("", line) for line in text.splitlines()[-lines:]]
    return {"name": name, "lines": tail, "bytes": size, "updated_at": path.stat().st_mtime}


# ---- 動いている処理 ----


@router.get("/jobs")
async def get_jobs() -> dict[str, Any]:
    """物語のジョブ(動いているもの・最近終わったもの)と、漫画の取り込みのジョブ。"""
    from .manga_import import _jobs as import_jobs
    from .story import _job_response, _jobs as story_jobs

    conn = get_connection()
    try:
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM stories")}
        import_titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM manga_imports")}
    finally:
        conn.close()
    stories = [
        {**_job_response(job), "title": titles.get(job.story_id) or f"物語 {job.story_id}"}
        for job in sorted(story_jobs.values(), key=lambda j: j.started_at, reverse=True)
    ]
    imports = [
        {
            "import_id": import_id,
            "title": import_titles.get(import_id) or f"取り込み {import_id}",
            "status": job.status,
            "message": job.message,
            "progress": job.progress,
            "total": job.total,
            "detail": job.detail,
            "story_id": job.story_id,
        }
        for import_id, job in import_jobs.items()
    ]
    return {"stories": stories, "imports": imports}


@router.post("/jobs/story/{story_id}/cancel")
async def cancel_story_job(story_id: int) -> dict[str, Any]:
    from .story import _jobs as story_jobs

    job = story_jobs.get(story_id)
    if job is None or job.status != "running" or job.task is None:
        raise HTTPException(status_code=409, detail="動いているジョブがありません。")
    job.task.cancel()
    return {"ok": True}


@router.post("/jobs/import/{import_id}/cancel")
async def cancel_import_job(import_id: int) -> dict[str, Any]:
    from .manga_import import _jobs as import_jobs

    job = import_jobs.get(import_id)
    if job is None or job.status != "running" or job.task is None:
        raise HTTPException(status_code=409, detail="動いているジョブがありません。")
    job.task.cancel()
    job.status = "error"
    job.detail = "キャンセルしました"
    return {"ok": True}


# ---- 外部サービス ----


def _dir_bytes(path: Path) -> tuple[int, int]:
    """(合計バイト, ファイル数)。"""
    total = count = 0
    if path.is_dir():
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                    count += 1
                except OSError:
                    continue
    return total, count


async def _novelai() -> dict[str, Any]:
    from .user import _BROWSER_HEADERS, _NOVELAI_API

    token = os.environ.get("NOVELAI_API_TOKEN")
    if not token:
        return {
            "configured": False,
            "ok": False,
            "detail": "NOVELAI_API_TOKEN が .env にありません(ログインして使う場合は不要)",
        }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_NOVELAI_API}/user/subscription", headers={**_BROWSER_HEADERS, "Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError as exc:
        return {"configured": True, "ok": False, "detail": f"つながりません: {exc}"}
    if not resp.is_success:
        return {"configured": True, "ok": False, "detail": f"トークンが使えません(HTTP {resp.status_code})"}
    data = resp.json()
    steps = data.get("trainingStepsLeft") or {}
    return {
        "configured": True,
        "ok": True,
        "tier": data.get("tier"),
        "active": data.get("active"),
        "expires_at": data.get("expiresAt"),
        "anlas": (steps.get("fixedTrainingStepsLeft") or 0) + (steps.get("purchasedTrainingSteps") or 0),
    }


async def _ollama() -> dict[str, Any]:
    from ..llm_client import get_text_base_url, get_text_model, get_vision_base_url, get_vision_model

    base = get_text_base_url()
    info: dict[str, Any] = {
        "text_url": base,
        "text_model": get_text_model(),
        "vision_url": get_vision_base_url(),
        "vision_model": get_vision_model(),
        "ok": False,
        "models": [],
    }
    root = re.sub(r"/v1/?$", "", base or "")
    if not root:
        info["detail"] = "VLLM_BASE_URL が設定されていません"
        return info
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{root}/api/tags")
        if resp.is_success:
            info["models"] = [m.get("name") for m in resp.json().get("models", [])]
            info["ok"] = True
            if info["text_model"] and info["text_model"] not in info["models"]:
                info["detail"] = f"文章のモデル {info['text_model']} が Ollama にありません"
        else:
            info["detail"] = f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        info["detail"] = f"つながりません: {exc}"
    return info


def _certificate() -> dict[str, Any]:
    path = _CERT_DIR / "server.crt"
    if not path.is_file():
        return {"exists": False}
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(path.read_bytes())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    expires = cert.not_valid_after_utc
    return {
        "exists": True,
        "expires_at": expires.isoformat(),
        "days_left": (expires - dt.datetime.now(dt.timezone.utc)).days,
        "names": [str(n.value) for n in names],
    }


@router.get("/services")
async def get_services() -> dict[str, Any]:
    models = []
    if _MODEL_DIR.is_dir():
        for folder in sorted(p for p in _MODEL_DIR.iterdir() if p.is_dir()):
            size, count = _dir_bytes(folder)
            models.append({"name": folder.name, "bytes": size, "files": count})
    return {"novelai": await _novelai(), "ollama": await _ollama(), "models": models, "certificate": _certificate()}


# ---- 設定(.env) ----

_ENV_LINE = re.compile(r"^\s*(#\s*)?([A-Z][A-Z0-9_]*)\s*=(.*)$")
_SECRET_KEY = re.compile(r"TOKEN|KEY|PASSWORD|SECRET|PRIVATE", re.IGNORECASE)
_VALID_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _mask(key: str, value: str) -> str:
    """秘密の値は伏せる(設定されていることと末尾の数文字だけ)。"""
    if not _SECRET_KEY.search(key):
        return value
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 8 else "…"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    # 行末のコメント(空白 + #)は値に含めない
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _env_entries() -> list[dict[str, Any]]:
    """.env の項目と、.env.example にある(まだ設定していない)項目。説明は .env.example の直前のコメント。"""
    described: dict[str, str] = {}
    known: list[str] = []
    comment: list[str] = []
    for line in _read(_ENV_EXAMPLE).splitlines():
        match = _ENV_LINE.match(line)
        if match:
            key = match.group(2)
            if key not in known:
                known.append(key)
                described[key] = " ".join(comment)[-300:]
            comment = []
        elif line.strip().startswith("#") and not set(line.strip()) <= {"#", "=", " "}:
            comment.append(line.strip().lstrip("#").strip())
        elif not line.strip():
            comment = []
    current: dict[str, str] = {}
    for line in _read(_ENV_FILE).splitlines():
        match = _ENV_LINE.match(line)
        if match and not match.group(1):
            current[match.group(2)] = _unquote(match.group(3))
    entries = []
    for key in [*current, *[k for k in known if k not in current]]:
        value = current.get(key)
        entries.append(
            {
                "key": key,
                "set": value is not None,
                "secret": bool(_SECRET_KEY.search(key)),
                "value": _mask(key, value) if value is not None else None,
                "description": described.get(key, ""),
            }
        )
    return entries


@router.get("/env")
async def get_env() -> dict[str, Any]:
    return {"path": str(_ENV_FILE), "entries": _env_entries()}


class EnvUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    # None で項目を消す(コメントにする)
    value: str | None = Field(None, max_length=4000)


@router.put("/env")
async def put_env(req: EnvUpdate) -> dict[str, Any]:
    """
    .env の1項目を書き換える(コメントや並びはそのまま)。無い項目は末尾に足す。値を None にすると、その行を
    コメントにする。反映にはバックエンドの再起動が要る(起動時に読むため)。
    """
    if not _VALID_KEY.match(req.key):
        raise HTTPException(status_code=422, detail="項目名は英大文字・数字・_ で、英大文字から始めてください。")
    if req.value is not None and ("\n" in req.value or "\r" in req.value):
        raise HTTPException(status_code=422, detail="値に改行は入れられません。")
    try:
        raw = _ENV_FILE.read_bytes().decode("utf-8-sig") if _ENV_FILE.exists() else ""
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f".env を読めません: {exc}")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    # 空白や # を含む値は引用符で囲む
    value = req.value
    if value is not None and (re.search(r"\s|#|\"|'", value) or value == ""):
        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    written = False
    for index, line in enumerate(lines):
        match = _ENV_LINE.match(line)
        if not match or match.group(2) != req.key or match.group(1):
            continue
        lines[index] = f"# {line.strip()}" if value is None else f"{req.key}={value}"
        written = True
        break
    if not written and value is not None:
        lines.append(f"{req.key}={value}")
    if _ENV_FILE.exists():
        # 書き換える前の .env を1世代だけ残す(.gitignore 済み)
        shutil.copy2(_ENV_FILE, _ENV_FILE.with_name(_ENV_FILE.name + ".bak"))
    _ENV_FILE.write_text(newline.join(lines) + newline, encoding="utf-8", newline="")
    return {"entries": _env_entries(), "restart_required": True}


# ---- データの管理 ----

_STORAGE = [
    ("生成した画像(画像生成ページ)", "outputs/history"),
    ("漫画のコマ", "outputs/manga/panels"),
    ("漫画のページ(合成)", "outputs/manga/v2"),
    ("キャラの参照画像", "outputs/manga/refs"),
    ("参照画像の候補", "outputs/manga/refs/candidates"),
    ("データセット", "outputs/training_data"),
    ("取り込んだ漫画", "data/imports"),
    ("判定モデル", "data/models"),
    ("評価用データ", "data/eval"),
    ("DB のバックアップ", "data/backups"),
]


def _referenced_references() -> set[str]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT reference_image_path FROM characters WHERE reference_image_path IS NOT NULL")
        return {Path(r[0]).name for r in rows}
    finally:
        conn.close()


def _referenced_panels() -> set[str]:
    conn = get_connection()
    try:
        return {Path(r[0]).name for r in conn.execute("SELECT image_path FROM manga_panels")}
    finally:
        conn.close()


def _cleanup_targets(category: str) -> list[Path]:
    """消してよいファイル。DB から参照されているものは入れない。"""
    if category == "candidates":
        folder = _PROJECT_ROOT / "outputs/manga/refs/candidates"
        return sorted(folder.glob("*.png")) if folder.is_dir() else []
    if category == "references":
        folder = _PROJECT_ROOT / "outputs/manga/refs"
        used = _referenced_references()
        return sorted(p for p in folder.glob("*.png") if p.name not in used) if folder.is_dir() else []
    if category == "panels":
        folder = _PROJECT_ROOT / "outputs/manga/panels"
        used = _referenced_panels()
        return sorted(p for p in folder.glob("*.png") if p.name not in used) if folder.is_dir() else []
    raise HTTPException(status_code=404, detail="その種類はありません。")


_CLEANUP = {
    "candidates": "参照画像の候補(選んだものは参照画像として別に保存済み)",
    "references": "どのキャラにも使われていない参照画像",
    "panels": "どのシーンにも使われていないコマの絵(描き直す前の絵など)",
}


@router.get("/storage")
async def get_storage() -> dict[str, Any]:
    usage = []
    for label, rel in _STORAGE:
        size, count = _dir_bytes(_PROJECT_ROOT / rel)
        usage.append({"label": label, "path": rel, "bytes": size, "files": count})
    disk = shutil.disk_usage(_PROJECT_ROOT)
    cleanup = []
    for category, label in _CLEANUP.items():
        targets = _cleanup_targets(category)
        cleanup.append(
            {
                "category": category,
                "label": label,
                "files": len(targets),
                "bytes": sum(p.stat().st_size for p in targets),
            }
        )
    return {"usage": usage, "disk": {"free": disk.free, "total": disk.total}, "cleanup": cleanup}


@router.post("/cleanup/{category}")
async def post_cleanup(category: str) -> dict[str, Any]:
    targets = _cleanup_targets(category)
    freed = 0
    for path in targets:
        try:
            size = path.stat().st_size
            path.unlink()
            freed += size
        except OSError:
            continue
    return {"deleted": len(targets), "bytes": freed}


def _backups() -> list[dict[str, Any]]:
    if not _BACKUP_DIR.is_dir():
        return []
    return [
        {"name": p.name, "bytes": p.stat().st_size, "created_at": p.stat().st_mtime}
        for p in sorted(_BACKUP_DIR.glob("app-*.db"), reverse=True)
    ]


@router.get("/backups")
async def get_backups() -> dict[str, Any]:
    return {"dir": str(_BACKUP_DIR), "backups": _backups()}


@router.post("/backups")
def post_backup() -> dict[str, Any]:
    """DB を data/backups へ写す(SQLite のバックアップ機能。書き込み中でも壊れない写しになる)。"""
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = _BACKUP_DIR / f"app-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    source = sqlite3.connect(db._DB_PATH)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return {"created": target.name, "backups": _backups()}


@router.delete("/backups/{name}")
async def delete_backup(name: str) -> dict[str, Any]:
    if not re.fullmatch(r"app-\d{8}-\d{6}\.db", name):
        raise HTTPException(status_code=404, detail="そのバックアップはありません。")
    path = _BACKUP_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="そのバックアップはありません。")
    path.unlink()
    return {"backups": _backups()}


# ---- サーバーの操作 ----


class RestartRequest(BaseModel):
    target: str = Field("all", pattern="^(all|backend|frontend)$")
    # True なら http、False なら https(証明書があれば)。None なら今のまま
    http: bool | None = None


@router.post("/restart")
async def post_restart(req: RestartRequest) -> dict[str, Any]:
    """
    scripts/dev-ctl.ps1 でサーバーを起動し直す(このバックエンド自身も止まるので、切り離した別のプロセスで行う)。
    動いているジョブは止まる(途中までの結果は DB に残る)。
    """
    script = _PROJECT_ROOT / "scripts" / "dev-ctl.ps1"
    if os.name != "nt" or not script.is_file():
        raise HTTPException(
            status_code=501, detail="この環境では画面から再起動できません(Windows の dev-ctl.ps1 を使います)。"
        )
    current_http = (_read(_RUN_DIR / "backend.scheme") or "http") == "http"
    has_cert = (_CERT_DIR / "server.crt").is_file()
    use_http = current_http if req.http is None else req.http
    if not use_http and not has_cert:
        raise HTTPException(status_code=409, detail="証明書がありません(scripts/make_lan_cert.py で作ってください)。")
    if req.http is not None and req.http != current_http and req.target != "all":
        raise HTTPException(status_code=409, detail="http/https を切り替えるときは、両方(all)を起動し直してください。")
    args = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "restart",
        "-Target",
        req.target,
    ]
    # 証明書があるのに http で動かすときだけ -Http を付ける(証明書が無ければ付けなくても http)
    if use_http and has_cert:
        args.append("-Http")
    env = {k: v for k, v in os.environ.items() if k != "NAI_HTTP"}
    # 窓を出さずに、このバックエンドが止まっても動き続けるプロセスにする。DETACHED_PROCESS(コンソール無し)だと
    # PowerShell は何もせずに終わる(実機で確認)ので、CREATE_NO_WINDOW を使う
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(  # noqa: S603 引数は固定の組み合わせだけ
        args,
        cwd=_PROJECT_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    return {"restarting": req.target, "scheme": "http" if use_http else "https"}


# ---- アプリの既定値 ----


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    return {"settings": app_settings.describe()}


class SettingUpdate(BaseModel):
    # None で既定に戻す
    value: Any = None


@router.put("/settings/{key:path}")
async def put_setting(key: str, req: SettingUpdate) -> dict[str, Any]:
    try:
        app_settings.set_value(key, req.value)
    except KeyError:
        raise HTTPException(status_code=404, detail="その設定はありません。")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"settings": app_settings.describe()}
