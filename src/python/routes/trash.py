"""
ごみ箱の API(/api/trash)。中身は trash.py。

  GET    /                       預かっているものの一覧(新しい順。期限を過ぎたものはここで片付く)
  GET    /{id}/files/{index}     預かったファイル(一覧の見本の画像)
  POST   /{id}/restore           元に戻す(同じものが新しくできていれば 409)
  DELETE /{id}                   完全に消す(元に戻せない)
  DELETE /                       ごみ箱を空にする(元に戻せない)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import trash

router = APIRouter(prefix="/api/trash", tags=["trash"])


@router.get("")
async def list_trash() -> dict[str, Any]:
    return {"retention_days": trash.RETENTION_DAYS, "entries": trash.list_entries()}


@router.get("/{entry_id}/files/{index}")
async def get_trash_file(entry_id: int, index: int) -> FileResponse:
    path = trash.stored_file(entry_id, index)
    if path is None:
        raise HTTPException(status_code=404, detail="ファイルがありません。")
    return FileResponse(path)


@router.post("/{entry_id}/restore")
async def restore_trash(entry_id: int) -> dict[str, Any]:
    try:
        restored = trash.restore(entry_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="ごみ箱にありません(期限を過ぎて片付いたかもしれません)。")
    except trash.TrashConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": restored["id"], "kind": restored["kind"], "label": restored["label"]}


@router.delete("/{entry_id}", status_code=204)
async def purge_trash(entry_id: int) -> None:
    if not trash.purge(entry_id):
        raise HTTPException(status_code=404, detail="ごみ箱にありません。")


@router.delete("")
async def empty_trash() -> dict[str, int]:
    entries = trash.list_entries()
    for entry in entries:
        trash.purge(entry["id"])
    return {"purged": len(entries)}
