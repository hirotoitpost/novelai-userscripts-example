"""
出版(パブリッシュ)の API(/api/publications)。ギャラリーで選んだ画像と、本棚で選んだ本を、NovelAI の生成情報を
取り除いた出版物として保存し、ZIP にまとめる。処理の中身は publish.py。

  POST   /api/publications                 出版物を作る
  GET    /api/publications                 一覧(新しい順)
  GET    /api/publications/{id}/download   ZIP を受け取る
  GET    /api/publications/{id}/cover      1枚目の画像(一覧の表紙)
  DELETE /api/publications/{id}            出版物を消す(元の画像・本は消えない)
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import publish
from ..db import (
    create_publication,
    delete_publication,
    get_connection,
    get_publication,
    list_publications,
    update_publication,
)
from ..db import user_files

router = APIRouter(prefix="/api/publications", tags=["publications"])

# 利用者のデータのファイルの基準(single はプロジェクトのフォルダ、multi はその人のフォルダ。db.UserPath)
_PROJECT_ROOT = user_files()
MAX_IMAGES = 1000
MAX_BOOKS = 50


class PublishRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    # ギャラリーの画像のキー(history:… / panel:… / illustration:… / dataset:…)。渡した順に並ぶ
    image_keys: list[str] = Field(default_factory=list, max_length=MAX_IMAGES)
    # 本棚の本(物語のID)。漫画のページを入れる。ページの無い本は本文を入れる
    book_ids: list[int] = Field(default_factory=list, max_length=MAX_BOOKS)


def _entries(req: PublishRequest) -> list[publish.Entry]:
    from .library import _gallery_images, get_book

    entries: list[publish.Entry] = []
    if req.image_keys:
        by_key = {item.key: item for item in _gallery_images()}
        paths = [by_key[key].path for key in dict.fromkeys(req.image_keys) if key in by_key]
        if paths:
            entries.append(publish.Entry("images", "画像", images=paths))
    for story_id in dict.fromkeys(req.book_ids):
        try:
            detail = get_book(story_id)
        except HTTPException:
            continue
        entries.append(
            publish.Entry(
                "book", detail["title"], images=list(detail["pages"]), text=detail.get("text") or "", story_id=story_id
            )
        )
    return entries


def _connect() -> sqlite3.Connection:
    return get_connection()


@router.post("")
def post_publication(req: PublishRequest) -> dict[str, Any]:
    """
    出版物を作る。画像から NovelAI の生成情報を取り除いて別に保存し、ZIP にまとめる(元の画像・本は変えない)。
    取り除いたあとに生成情報が残っていないことを確かめ、残っていれば作らない。
    """
    if not req.image_keys and not req.book_ids:
        raise HTTPException(status_code=422, detail="出版する画像か本を選んでください。")
    entries = _entries(req)
    if not entries:
        raise HTTPException(status_code=404, detail="選んだ画像・本が見つかりません。")
    conn = _connect()
    try:
        publication = create_publication(conn, req.title.strip())
    finally:
        conn.close()
    try:
        built = publish.build(publication["id"], req.title.strip(), entries)
    except publish.MetadataRemains as exc:
        _discard(publication["id"])
        raise HTTPException(status_code=500, detail=f"メタデータを取り除けなかったので、出版物は作っていません: {exc}")
    except (OSError, ValueError) as exc:
        _discard(publication["id"])
        raise HTTPException(status_code=422, detail=f"出版物を作れませんでした: {exc}")
    conn = _connect()
    try:
        return update_publication(conn, publication["id"], **built) or {}
    finally:
        conn.close()


def _discard(publication_id: int) -> None:
    conn = _connect()
    try:
        delete_publication(conn, publication_id)
    finally:
        conn.close()
    publish.remove(publication_id)


@router.get("")
def get_publications() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        return list_publications(conn)
    finally:
        conn.close()


def _require(publication_id: int) -> dict[str, Any]:
    conn = _connect()
    try:
        publication = get_publication(conn, publication_id)
    finally:
        conn.close()
    if publication is None or not publication.get("zip_path"):
        raise HTTPException(status_code=404, detail="publication not found")
    return publication


@router.get("/{publication_id}/download")
def download_publication(publication_id: int) -> FileResponse:
    publication = _require(publication_id)
    path = _PROJECT_ROOT / publication["zip_path"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="ZIP が見つかりません(ファイルが消されています)。")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/{publication_id}/cover")
def get_cover(publication_id: int) -> FileResponse:
    """出版物の1枚目の画像(生成情報を取り除いたもの)。"""
    publication = _require(publication_id)
    files = sorted((_PROJECT_ROOT / publication["dir"] / "files").rglob("*.png"))
    if not files:
        raise HTTPException(status_code=404, detail="画像がありません。")
    return FileResponse(files[0], media_type="image/png")


@router.delete("/{publication_id}", status_code=204)
def remove_publication(publication_id: int) -> None:
    """出版物(保存した画像と ZIP)を消す。元の画像・本は消えない。"""
    _require(publication_id)
    _discard(publication_id)
