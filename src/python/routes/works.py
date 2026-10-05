"""
作品の強い関連: 作者(出典・原作者)、スピンオフ、クロスオーバー。

「作品」はシリーズ全体(kind='series')か、シリーズに入っていない物語(kind='story')。巻ごとではなく
作品に付けるので、シリーズのどの巻を開いても同じ関連が見える。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import (
    add_work_author,
    add_work_relation,
    create_author,
    delete_author,
    delete_work_relation,
    find_work_relation,
    get_author,
    get_connection,
    get_series,
    get_story,
    list_authors,
    list_work_authors,
    list_work_relations,
    remove_work_author,
    update_author,
)
from .library import _books

router = APIRouter(tags=["works"])

WorkKind = Literal["series", "story"]
RelationType = Literal["spinoff", "crossover"]


class WorkRef(BaseModel):
    kind: WorkKind
    id: int


class Work(WorkRef):
    title: str
    cover_path: str | None = None
    volume_count: int = 1
    adult: bool = False
    # 開くときの物語(シリーズは1巻)
    first_story_id: int


def _works() -> dict[tuple[str, int], Work]:
    """本棚の本から作品の一覧を作る。シリーズは1巻の表紙と題を使う。"""
    works: dict[tuple[str, int], Work] = {}
    for book in sorted(_books(), key=lambda b: b.volume_no or 0):
        if book.series_id is None:
            works[("story", book.id)] = Work(
                kind="story",
                id=book.id,
                title=book.title,
                cover_path=book.cover_path,
                adult=book.adult,
                first_story_id=book.id,
            )
            continue
        key = ("series", book.series_id)
        if key in works:
            works[key].volume_count += 1
            works[key].adult = works[key].adult or book.adult
        else:
            works[key] = Work(
                kind="series",
                id=book.series_id,
                title=book.series_title or book.title,
                cover_path=book.cover_path,
                adult=book.adult,
                first_story_id=book.id,
            )
    return works


def _require_work(ref: WorkRef) -> None:
    conn = get_connection()
    try:
        if ref.kind == "series":
            if get_series(conn, ref.id) is None:
                raise HTTPException(status_code=404, detail="シリーズがありません。")
            return
        story = get_story(conn, ref.id)
    finally:
        conn.close()
    if story is None:
        raise HTTPException(status_code=404, detail="物語がありません。")
    if story.get("series_id") is not None:
        raise HTTPException(
            status_code=400,
            detail="シリーズの巻には、シリーズとして関連を付けてください。",
        )


@router.get("/api/works", response_model=list[Work])
def list_works() -> list[Work]:
    return sorted(_works().values(), key=lambda w: w.title)


# ---- 関連 ----


class RelatedWork(BaseModel):
    relation_id: int
    type: RelationType
    # spinoff_of: この作品が相手のスピンオフ / has_spinoff: 相手がこの作品のスピンオフ / crossover: 対等
    role: Literal["spinoff_of", "has_spinoff", "crossover"]
    work: Work


class Author(BaseModel):
    id: int
    name: str
    platform: str = ""
    url: str = ""


class WorkRelations(BaseModel):
    authors: list[Author]
    related: list[RelatedWork]


@router.get("/api/works/{kind}/{work_id}/relations", response_model=WorkRelations)
def get_relations(kind: WorkKind, work_id: int) -> dict[str, Any]:
    works = _works()
    conn = get_connection()
    try:
        rows = list_work_relations(conn, kind, work_id)
        authors = list_work_authors(conn).get((kind, work_id), [])
    finally:
        conn.close()
    related = []
    for row in rows:
        mine_is_from = row["from_kind"] == kind and row["from_id"] == work_id
        other = (
            (row["to_kind"], row["to_id"])
            if mine_is_from
            else (row["from_kind"], row["from_id"])
        )
        if other not in works:
            continue  # 相手が本棚に出ない(本文も漫画も無い)作品
        if row["type"] == "crossover":
            role = "crossover"
        else:
            role = "spinoff_of" if mine_is_from else "has_spinoff"
        related.append(
            {
                "relation_id": row["id"],
                "type": row["type"],
                "role": role,
                "work": works[other],
            }
        )
    return {"authors": authors, "related": related}


class RelationRequest(BaseModel):
    type: RelationType
    # spinoff は source が派生作品、target が元作品。crossover は順不同。
    source: WorkRef
    target: WorkRef


@router.post("/api/works/relations", status_code=201)
def create_relation(req: RelationRequest) -> dict[str, int]:
    if req.source == req.target:
        raise HTTPException(
            status_code=400, detail="同じ作品どうしは関連付けられません。"
        )
    _require_work(req.source)
    _require_work(req.target)
    conn = get_connection()
    try:
        # クロスオーバーは逆向きも同じ関連、スピンオフは逆向き(元作品が派生作品のスピンオフ)を許さない
        if find_work_relation(
            conn,
            req.type,
            req.source.kind,
            req.source.id,
            req.target.kind,
            req.target.id,
        ):
            raise HTTPException(
                status_code=409, detail="この2作品には既に同じ種類の関連があります。"
            )
        row = add_work_relation(
            conn,
            req.type,
            req.source.kind,
            req.source.id,
            req.target.kind,
            req.target.id,
        )
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=409, detail="この関連は既にあります。")
    return {"id": row["id"]}


@router.delete("/api/works/relations/{relation_id}", status_code=204)
def remove_relation(relation_id: int) -> None:
    conn = get_connection()
    try:
        if not delete_work_relation(conn, relation_id):
            raise HTTPException(status_code=404, detail="関連がありません。")
    finally:
        conn.close()


# ---- 作者 ----


class AuthorWithCount(Author):
    note: str = ""
    work_count: int = 0


class AuthorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    platform: str = Field("", max_length=200)
    url: str = Field("", max_length=1000)
    note: str = Field("", max_length=2000)


@router.get("/api/authors", response_model=list[AuthorWithCount])
def get_authors() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_authors(conn)
    finally:
        conn.close()


@router.post("/api/authors", response_model=AuthorWithCount, status_code=201)
def post_author(req: AuthorRequest) -> dict[str, Any]:
    conn = get_connection()
    try:
        return create_author(
            conn,
            req.name.strip(),
            req.platform.strip(),
            req.url.strip(),
            req.note.strip(),
        )
    finally:
        conn.close()


@router.put("/api/authors/{author_id}", status_code=204)
def put_author(author_id: int, req: AuthorRequest) -> None:
    conn = get_connection()
    try:
        if get_author(conn, author_id) is None:
            raise HTTPException(status_code=404, detail="作者がありません。")
        update_author(
            conn, author_id, {k: v.strip() for k, v in req.model_dump().items()}
        )
    finally:
        conn.close()


@router.delete("/api/authors/{author_id}", status_code=204)
def remove_author(author_id: int) -> None:
    """作者を消す(作品は消えない。作品に付けていた作者の印だけ外れる)。"""
    conn = get_connection()
    try:
        delete_author(conn, author_id)
    finally:
        conn.close()


class WorkAuthorRequest(BaseModel):
    author_id: int


@router.post("/api/works/{kind}/{work_id}/authors", status_code=204)
def attach_author(kind: WorkKind, work_id: int, req: WorkAuthorRequest) -> None:
    _require_work(WorkRef(kind=kind, id=work_id))
    conn = get_connection()
    try:
        if get_author(conn, req.author_id) is None:
            raise HTTPException(status_code=404, detail="作者がありません。")
        add_work_author(conn, kind, work_id, req.author_id)
    finally:
        conn.close()


@router.delete("/api/works/{kind}/{work_id}/authors/{author_id}", status_code=204)
def detach_author(kind: WorkKind, work_id: int, author_id: int) -> None:
    conn = get_connection()
    try:
        remove_work_author(conn, kind, work_id, author_id)
    finally:
        conn.close()
