"""
漫画の取り込み(構成の参考): PDF や画像を受け取ってページを保存し、コマごとの構成を読み取る。
読み取りは画像モデルで1コマ十数秒かかるので、バックグラウンドで進めて GET /{id}/job で進み具合を返す。
読み取った構成は漫画ドラフト(/api/manga-draft の import_id)で、コマ運びの参考として使う。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from ..db import (
    create_manga_import,
    delete_manga_import,
    get_connection,
    get_manga_import,
    list_manga_imports,
    update_manga_import,
)
from ..manga_import import analyze_page, classify_panel, load_pages, panel_dict
from ..models import MangaImportRequest

router = APIRouter(prefix="/api/manga-import", tags=["manga-import"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# 取り込んだページは公開しない場所(data/)に置く。市販の作品も入るため outputs/ とは分ける
_IMPORT_DIR = _PROJECT_ROOT / "data" / "imports"


@dataclass
class _ImportJob:
    status: str = "running"
    message: str = "開始しました"
    progress: int = 0
    total: int = 0
    detail: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


_jobs: dict[int, _ImportJob] = {}


def _page_dir(import_id: int) -> Path:
    return _IMPORT_DIR / str(import_id)


def _page_path(import_id: int, page_index: int) -> Path:
    return _page_dir(import_id) / f"page_{page_index + 1:03d}.png"


def _decode(data: str) -> bytes:
    payload = data.split(",", 1)[1] if data.startswith("data:") else data
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="ファイルを読み取れませんでした。")


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    pages = (item.get("analysis") or {}).get("pages") or []
    return {
        "id": item["id"],
        "title": item["title"],
        "page_count": item["page_count"],
        "status": item["status"],
        "panel_count": sum(len(p["panels"]) for p in pages),
        "created_at": item["created_at"],
    }


async def _analyze(import_id: int, page_count: int, use_vision: bool, job: _ImportJob) -> None:
    """ページごとにコマと構成を読み取り、1ページ終わるごとに保存する(途中でも結果を見られる)。"""
    pages: list[dict[str, Any]] = []
    try:
        for index in range(page_count):
            job.message = f"{index + 1}ページ目のコマを探しています"
            panels, overlays = await asyncio.to_thread(analyze_page, _page_path(import_id, index))
            if use_vision:
                with Image.open(_page_path(import_id, index)) as src:
                    image = src.convert("RGB")
                for k, panel in enumerate(panels, start=1):
                    job.message = f"{index + 1}ページ目 {k}/{len(panels)}コマ目の役割を読み取っています"
                    x, y, w, h = panel.box
                    panel.role, panel.emotion = await classify_panel(image.crop((x, y, x + w, y + h)))
            # overlays: コマにまたがって描かれた絵(何段にもまたがって立つ人物など)の数
            pages.append({"panels": [panel_dict(p) for p in panels], "overlays": overlays})
            conn = get_connection()
            try:
                update_manga_import(conn, import_id, analysis={"pages": pages})
            finally:
                conn.close()
            job.progress = index + 1
        job.status = "done"
        job.message = f"{page_count}ページ・{sum(len(p['panels']) for p in pages)}コマを読み取りました"
        status = "analyzed"
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status = "error"
        job.detail = str(exc)
        status = "error"
    conn = get_connection()
    try:
        update_manga_import(conn, import_id, status=status)
    finally:
        conn.close()


def _start(import_id: int, page_count: int, use_vision: bool) -> _ImportJob:
    running = _jobs.get(import_id)
    if running is not None and running.status == "running":
        raise HTTPException(status_code=409, detail="この取り込みは読み取り中です。")
    job = _ImportJob(total=page_count)
    _jobs[import_id] = job
    conn = get_connection()
    try:
        update_manga_import(conn, import_id, status="analyzing")
    finally:
        conn.close()
    job.task = asyncio.create_task(_analyze(import_id, page_count, use_vision, job))
    return job


def _job_response(job: _ImportJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {k: getattr(job, k) for k in ("status", "message", "progress", "total", "detail")}


@router.post("")
async def post_import(req: MangaImportRequest) -> dict[str, Any]:
    """PDF・画像を取り込み、構成の読み取りを始める。ファイルは渡した順にページになる。"""
    try:
        pages = await asyncio.to_thread(load_pages, [(f.name, _decode(f.data)) for f in req.files])
    except (UnidentifiedImageError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=f"ページを読み込めませんでした: {exc}")
    if not pages:
        raise HTTPException(status_code=400, detail="ページがありません。")
    conn = get_connection()
    try:
        item = create_manga_import(conn, req.title.strip(), len(pages))
    finally:
        conn.close()
    _page_dir(item["id"]).mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(pages):
        page.save(_page_path(item["id"], index), "PNG")
    job = _start(item["id"], len(pages), req.use_vision)
    return {**_summary(item), "job": _job_response(job)}


@router.get("")
async def get_imports() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return [_summary(item) for item in list_manga_imports(conn)]
    finally:
        conn.close()


def _require(import_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        item = get_manga_import(conn, import_id)
    finally:
        conn.close()
    if item is None:
        raise HTTPException(status_code=404, detail="import not found")
    return item


@router.get("/{import_id}")
async def get_import(import_id: int) -> dict[str, Any]:
    """取り込みの中身。analysis.pages[].panels[] にコマの位置(box)と構成がある。"""
    item = _require(import_id)
    return {**_summary(item), "analysis": item.get("analysis")}


@router.get("/{import_id}/job")
async def get_import_job(import_id: int) -> dict[str, Any] | None:
    return _job_response(_jobs.get(import_id))


@router.post("/{import_id}/analyze")
async def reanalyze(import_id: int, use_vision: bool = True) -> dict[str, Any] | None:
    """構成を読み取り直す(読み取りに失敗したときや、画像モデルを後から使うとき)。"""
    item = _require(import_id)
    return _job_response(_start(import_id, item["page_count"], use_vision))


@router.get("/{import_id}/pages/{page_index}")
async def get_page(import_id: int, page_index: int) -> FileResponse:
    path = _page_path(import_id, page_index)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="page not found")
    return FileResponse(path, media_type="image/png")


@router.delete("/{import_id}", status_code=204)
async def delete_import(import_id: int) -> None:
    """取り込みとページ画像を消す(読み取り中なら止める)。"""
    _require(import_id)
    job = _jobs.pop(import_id, None)
    if job is not None and job.task is not None and not job.task.done():
        job.task.cancel()
    conn = get_connection()
    try:
        delete_manga_import(conn, import_id)
    finally:
        conn.close()
    shutil.rmtree(_page_dir(import_id), ignore_errors=True)
