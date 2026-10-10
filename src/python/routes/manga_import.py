"""
漫画の取り込み(構成の参考): PDF や画像を受け取ってページを保存し、コマごとの構成を読み取る。
読み取りは画像モデルで1コマ十数秒かかるので、バックグラウンドで進めて GET /{id}/job で進み具合を返す。
読み取った構成は漫画ドラフト(/api/manga-draft の import_id)で、コマ運びの参考として使う。
"""

from __future__ import annotations

import asyncio
import os
import base64
import binascii
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from novelai import AsyncNovelAI
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
from ..client import get_client
from ..models import MangaImportAutoRequest, MangaImportRequest

router = APIRouter(prefix="/api/manga-import", tags=["manga-import"])
ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

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
    # 似た漫画を作ったときの物語
    story_id: int | None = None


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


def _start(
    import_id: int,
    page_count: int,
    use_vision: bool,
    auto: MangaImportAutoRequest | None = None,
    api_key: str | None = None,
) -> _ImportJob:
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

    async def run() -> None:
        await _analyze(import_id, page_count, use_vision, job)
        if auto is not None and api_key and job.status == "done":
            # 読み取りが終わったら、同じジョブのまま似た漫画を作る(画面には1つの処理として見せる)
            job.status = "running"
            await _similar(import_id, auto, api_key, job)

    job.task = asyncio.create_task(run())
    return job


async def _similar(import_id: int, req: MangaImportAutoRequest, api_key: str, job: _ImportJob) -> None:
    """似た漫画を作る(下の make_similar)。失敗も状態として返す。"""
    try:
        job.story_id = await make_similar(import_id, req, api_key, job)
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status = "error"
        job.detail = str(exc)


async def make_similar(import_id: int, req: MangaImportAutoRequest, api_key: str, job: Any) -> int:
    """
    取り込んだ漫画に似た漫画を作り、物語のIDを返す: ページの絵から舞台と人物の見た目を読み取る →
    キャラ(選んだもの、無ければ見た目から作る)と参照画像 → 大枠と1ページ=1話の台本(NovelAI の文章モデル)
    → 取り込んだ作品のコマ割りで物語を作る → コマの生成(キャラ参照)と合成。
    """
    from ..cast import auto_reference
    from ..db import get_character, save_character
    from ..manga_draft import draft_characters, generate_episode, generate_outlines
    from ..manga_similar import name_people, read_pages, theme_of
    from ..models import MangaDraftCreateRequest, MangaDraftPanel, MangaV2ComposeRequest, MangaV2PanelsRequest
    from .manga_draft import _import_structure, post_create
    from .manga_v2 import _run_panels, compose

    structure = _import_structure(import_id)[: req.max_pages]
    item = _require(import_id)
    pages = (item.get("analysis") or {}).get("pages") or []
    used = [i for i, page in enumerate(pages) if page["panels"]][: len(structure)]
    job.progress, job.total = 0, len(used)
    job.message = "取り込んだページの舞台と人物の見た目を読み取っています"
    images = []
    for index in used:
        with Image.open(_page_path(import_id, index)) as src:
            images.append(src.convert("RGB"))
    boxes = [[tuple(p["box"]) for p in pages[index]["panels"]] for index in used]
    features = await asyncio.to_thread(read_pages, images, boxes)

    # キャラ: 選んだもの。無ければ、ページの人物の見た目から作る
    conn = get_connection()
    try:
        characters = [c for c in (get_character(conn, i) for i in req.character_ids) if c]
    finally:
        conn.close()
    if not characters:
        if not features.people:
            raise RuntimeError("取り込んだページから人物を見つけられませんでした。使うキャラを選んでください。")
        job.message = "登場人物の名前と人物像を考えています"
        named = await name_people(api_key, features.people, req.genre, features.setting)
        conn = get_connection()
        try:
            for person, info in zip(features.people, named):
                name = info["name"]
                taken = {c["name"] for c in characters}
                suffix = 2
                while get_character_by_name(conn, name) or name in taken:
                    name = f"{info['name']}{suffix}"
                    suffix += 1
                tags = [person.gender, *person.tags, *[t for t in info.get("look", "").split(", ") if t]]
                created = save_character(conn, name, ", ".join(dict.fromkeys(tags)), info["profile"])
                characters.append(created)
        finally:
            conn.close()

    if req.references:
        missing = [c for c in characters if not c.get("reference_image_path") and (c.get("appearance_tags") or "").strip()]
        for index, character in enumerate(missing, start=1):
            job.message = f"参照画像を作っています {index}/{len(missing)}: {character['name']}(候補4枚から選びます)"
            await auto_reference(api_key, character)

    cast = draft_characters(characters)
    job.message = "大枠シナリオを考えています"
    outlines = await generate_outlines(
        api_key, theme_of(features), req.genre, cast, len(structure), structure=structure
    )
    outline = outlines[0]
    episodes: list[list[dict[str, Any]]] = []
    for index in range(len(structure)):
        job.message = f"台本を書いています {index + 1}/{len(structure)}話"
        episodes.append(
            await generate_episode(
                api_key,
                outline,
                index,
                cast,
                previous_panels=episodes[-1] if episodes else None,
                structure=structure[index],
            )
        )
    story = await post_create(
        MangaDraftCreateRequest(
            title=outline["title"],
            character_ids=[c.id for c in cast],
            episodes=[[MangaDraftPanel.model_validate(p) for p in episode] for episode in episodes],
            import_id=import_id,
            use_import_layout=True,
        )
    )
    job.story_id = story.id

    color = (not features.monochrome) if req.color is None else req.color
    panels = MangaV2PanelsRequest(template="grid4", color=color, use_character_reference=True, vary_seed=True)
    conn = get_connection()
    try:
        from ..db import list_story_scenes

        targets = list_story_scenes(conn, story.id)
    finally:
        conn.close()
    await _run_panels(job, story.id, panels, api_key, targets)
    job.message = "ページに合成しています"
    result = await asyncio.to_thread(compose, story.id, MangaV2ComposeRequest(template="grid4"))
    job.message = f"漫画ができました: {outline['title']}({len(result['pages'])}ページ・{len(targets)}コマ)"
    return story.id


def get_character_by_name(conn: Any, name: str) -> bool:
    return conn.execute("SELECT 1 FROM characters WHERE name = ?", (name,)).fetchone() is not None


def _job_response(job: _ImportJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {k: getattr(job, k) for k in ("status", "message", "progress", "total", "detail", "story_id")}


def _optional_api_key(authorization: str | None = Header(None)) -> str | None:
    """NovelAI のトークン(Authorization か .env の NOVELAI_API_TOKEN)。無ければ None(取り込みだけなら要らない)。"""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return os.environ.get("NOVELAI_API_TOKEN") or None


@router.post("")
async def post_import(
    req: MangaImportRequest, api_key: Annotated[str | None, Depends(_optional_api_key)]
) -> dict[str, Any]:
    """PDF・画像を取り込み、構成の読み取りを始める。ファイルは渡した順にページになる。"""
    try:
        pages = await asyncio.to_thread(load_pages, [(f.name, _decode(f.data)) for f in req.files])
    except (UnidentifiedImageError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=f"ページを読み込めませんでした: {exc}")
    if not pages:
        raise HTTPException(status_code=400, detail="ページがありません。")
    if req.auto_manga is not None and not api_key:
        raise HTTPException(status_code=401, detail="似た漫画を作るには NovelAI へのログインが必要です。")
    conn = get_connection()
    try:
        item = create_manga_import(conn, req.title.strip(), len(pages))
    finally:
        conn.close()
    _page_dir(item["id"]).mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(pages):
        page.save(_page_path(item["id"], index), "PNG")
    job = _start(item["id"], len(pages), req.use_vision, req.auto_manga, api_key)
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


@router.post("/{import_id}/similar")
async def post_similar(import_id: int, req: MangaImportAutoRequest, client: ClientDep) -> dict[str, Any] | None:
    """読み取り済みの取り込みから、似た漫画を作る(ジョブ。進み具合は GET /{import_id}/job)。"""
    item = _require(import_id)
    if item["status"] != "analyzed":
        raise HTTPException(status_code=409, detail="取り込んだ作品の構成をまだ読み取っていません。")
    running = _jobs.get(import_id)
    if running is not None and running.status == "running":
        raise HTTPException(status_code=409, detail="この取り込みは処理中です。")
    job = _ImportJob()
    _jobs[import_id] = job
    job.task = asyncio.create_task(_similar(import_id, req, client.api_key, job))
    return _job_response(job)


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
