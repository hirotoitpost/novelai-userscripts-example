"""
漫画の取り込み: PDF や画像を受け取ってページを保存し、コマごとの構成を読み取る。
読み取りは画像モデルで1コマ十数秒かかるので、バックグラウンドで進めて GET /{id}/job で進み具合を返す。

取り込むときに使い方(purpose)を選ぶ:
- similar(似た漫画を作る): コマ運び・場面・所作・セリフの型を読み、新しい話の漫画にする(make_similar)。
  セリフの文面は残さない。
- rebuild(自分の作品を作り直す): セリフの文面も読み、セリフごと台本に起こして描き直す(make_rebuild)。
  自分の作品(権利のある作品)のためのもの。

描き文字(手描きの効果音)は、場所の候補だけを見つける(読みは分からない)。画面で、候補を外す・読みを入れる・
見落としを足す(PUT …/sfx)。読みを入れたものに形の似た候補には、読みの候補を出す(GET …/sfx-suggestions)。

取り込みには、漫画ドラフトのジャンルを割り当てられる(genre。取り込むときか、あとから PATCH で。候補に無い
ジャンルを割り当てると、候補(/api/manga-draft/genres)にも足す)。似た漫画を作るときの
ジャンルになり、漫画ドラフトでは同じジャンルの取り込みが構成の参考の先頭に出る。

読み取った構成は、漫画ドラフト(/api/manga-draft の import_id)でもコマ運びの参考として使える。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from novelai import AsyncNovelAI
from fastapi.responses import FileResponse, Response
from PIL import Image, UnidentifiedImageError

from ..db import (
    PerUser,
    add_manga_genre,
    create_manga_import,
    delete_manga_import,
    get_connection,
    get_manga_import,
    list_manga_imports,
    update_manga_import,
)
from ..manga_import import analyze_page, classify_panel, load_pages, panel_dict
from ..client import get_client, operator_token
from ..models import (
    MangaImportAutoRequest,
    MangaImportLinesRequest,
    MangaImportRequest,
    MangaImportSfxRequest,
    MangaImportUpdateRequest,
)

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


_jobs: PerUser[_ImportJob] = PerUser()  # 取り込みの ID ごと(multi では利用者ごと。db.PerUser)


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
        "purpose": item.get("purpose") or "structure",
        "genre": item.get("genre") or "",
        "panel_count": sum(len(p["panels"]) for p in pages),
        "created_at": item["created_at"],
    }


async def _analyze(import_id: int, page_count: int, use_vision: bool, job: _ImportJob, purpose: str) -> None:
    """ページごとにコマと構成を読み取り、1ページ終わるごとに保存する(途中でも結果を見られる)。"""
    pages: list[dict[str, Any]] = []
    try:
        for index in range(page_count):
            what = "コマ・場面・セリフ・描き文字" if purpose in ("similar", "rebuild") else "コマ"
            job.message = f"{index + 1}ページ目の{what}を読み取っています"
            panels, overlays = await asyncio.to_thread(analyze_page, _page_path(import_id, index), purpose)
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
    purpose: str,
    auto: MangaImportAutoRequest | None = None,
    api_key: str | None = None,
) -> _ImportJob:
    running = _jobs.get(import_id)
    if running is not None and running.status == "running":
        raise HTTPException(status_code=409, detail="この取り込みは読み取り中です。")
    job = _ImportJob(total=page_count)
    _jobs[import_id] = job
    _forget_sfx_features(import_id)
    conn = get_connection()
    try:
        update_manga_import(conn, import_id, status="analyzing", purpose=purpose)
    finally:
        conn.close()

    async def run() -> None:
        await _analyze(import_id, page_count, use_vision, job, purpose)
        if auto is not None and api_key and job.status == "done":
            # 読み取りが終わったら、同じジョブのまま漫画を作る(画面には1つの処理として見せる)
            job.status = "running"
            await _make(import_id, auto, api_key, job, rebuild=purpose == "rebuild")

    job.task = asyncio.create_task(run())
    return job


async def _make(import_id: int, req: MangaImportAutoRequest, api_key: str, job: _ImportJob, *, rebuild: bool) -> None:
    """似た漫画を作る(make_similar)か、作り直す(make_rebuild)。失敗も状態として返す。"""
    try:
        maker = make_rebuild if rebuild else make_similar
        job.story_id = await maker(import_id, req, api_key, job)
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status = "error"
        job.detail = str(exc)


# ジャンルを割り当てていない取り込みから似た漫画を作るときのジャンル
DEFAULT_GENRE = "日常コメディ"


def _genre(import_id: int, req: MangaImportAutoRequest) -> str:
    """作る漫画のジャンル: 指定があればそれ、無ければ取り込みに割り当てたジャンル、それも無ければ既定。"""
    return (req.genre or "").strip() or (_require(import_id).get("genre") or "").strip() or DEFAULT_GENRE


def _used_pages(import_id: int, max_pages: int) -> tuple[list[int], list[list[dict[str, Any]]]]:
    """漫画にするページ(コマのあるページを冒頭から max_pages まで)の番号と、そのコマ。"""
    from .manga_draft import _MAX_EPISODES, _MAX_PANELS_PER_EPISODE

    pages = (_require(import_id).get("analysis") or {}).get("pages") or []
    used = [i for i, page in enumerate(pages) if page["panels"]][: min(max_pages, _MAX_EPISODES)]
    if not used:
        raise RuntimeError("取り込んだ作品にコマが見つかりません。")
    return used, [pages[i]["panels"][:_MAX_PANELS_PER_EPISODE] for i in used]


async def _prepare_cast(
    import_id: int, used: list[int], req: MangaImportAutoRequest, api_key: str, job: Any
) -> tuple[list[dict[str, Any]], Any]:
    """
    使うキャラ(選んだもの。無ければ、ページの人物の見た目から作る)と、その参照画像を用意する。
    戻り値は(キャラ, ページの絵から読み取った舞台・見た目)。
    """
    from ..cast import auto_reference
    from ..db import get_character, save_character
    from ..manga_similar import name_people, read_pages

    pages = (_require(import_id).get("analysis") or {}).get("pages") or []
    job.progress, job.total = 0, len(used)
    job.message = "取り込んだページの舞台と人物の見た目を読み取っています"
    images = []
    for index in used:
        with Image.open(_page_path(import_id, index)) as src:
            images.append(src.convert("RGB"))
    boxes = [[tuple(p["box"]) for p in pages[index]["panels"]] for index in used]
    features = await asyncio.to_thread(read_pages, images, boxes)

    conn = get_connection()
    try:
        characters = [c for c in (get_character(conn, i) for i in req.character_ids) if c]
    finally:
        conn.close()
    if not characters:
        if not features.people:
            raise RuntimeError("取り込んだページから人物を見つけられませんでした。使うキャラを選んでください。")
        job.message = "登場人物の名前と人物像を考えています"
        named = await name_people(api_key, features.people, _genre(import_id, req), features.setting)
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

    if req.references and req.make_images:
        missing = [c for c in characters if not c.get("reference_image_path") and (c.get("appearance_tags") or "").strip()]
        for index, character in enumerate(missing, start=1):
            job.message = f"参照画像を作っています {index}/{len(missing)}: {character['name']}(候補4枚から選びます)"
            await auto_reference(api_key, character)
    return characters, features


async def _finish(
    import_id: int,
    title: str,
    cast: list[Any],
    episodes: list[list[dict[str, Any]]],
    req: MangaImportAutoRequest,
    monochrome: bool,
    api_key: str,
    job: Any,
) -> int:
    """台本から物語を作り(取り込んだ作品のコマ割りで)、コマの絵を生成してページに合成する。物語のIDを返す。"""
    from ..db import list_story_scenes
    from ..models import MangaDraftCreateRequest, MangaDraftPanel, MangaV2ComposeRequest, MangaV2PanelsRequest
    from .manga_draft import post_create
    from .manga_v2 import _run_panels, compose

    story = await post_create(
        MangaDraftCreateRequest(
            title=title[:200],
            character_ids=[c.id for c in cast],
            episodes=[[MangaDraftPanel.model_validate(p) for p in episode] for episode in episodes],
            import_id=import_id,
            use_import_layout=True,
        )
    )
    job.story_id = story.id
    count = sum(len(e) for e in episodes)
    if not req.make_images:
        job.message = f"台本ができました: {title}({len(episodes)}ページ・{count}コマ。コマの絵はまだ生成していません)"
        return story.id

    color = (not monochrome) if req.color is None else req.color
    panels = MangaV2PanelsRequest(template="grid4", color=color, use_character_reference=True, vary_seed=True)
    conn = get_connection()
    try:
        targets = list_story_scenes(conn, story.id)
    finally:
        conn.close()
    await _run_panels(job, story.id, panels, api_key, targets)
    job.message = "ページに合成しています"
    result = await asyncio.to_thread(compose, story.id, MangaV2ComposeRequest(template="grid4"))
    job.message = f"漫画ができました: {title}({len(result['pages'])}ページ・{len(targets)}コマ)" + (f"。{job.guard_note}" if getattr(job, "guard_note", "") else "")
    return story.id


async def make_similar(import_id: int, req: MangaImportAutoRequest, api_key: str, job: Any) -> int:
    """
    取り込んだ漫画に似た漫画を作り、物語のIDを返す: ページの絵から舞台と人物の見た目を読み取る →
    キャラ(選んだもの、無ければ見た目から作る)と参照画像 → 大枠と1ページ=1話の台本(NovelAI の文章モデル)
    → 取り込んだ作品のコマ割りで物語を作る → コマの生成(キャラ参照)と合成。

    場面・所作を読んである取り込みでは、コマごとの場面(舞台・小物・構図)と所作(表情・動作)を再現する。
    セリフは、数・長さ・調子だけを合わせて新しく作る(セリフの無いコマは、セリフ無しのまま)。
    """
    from ..manga_draft import draft_characters, generate_episode, generate_outlines
    from ..manga_import import structure_lines
    from ..manga_similar import apply_import_panels, theme_of, wordless

    used, page_panels = _used_pages(import_id, req.max_pages)
    structure = [structure_lines(panels) for panels in page_panels]
    characters, features = await _prepare_cast(import_id, used, req, api_key, job)

    cast = draft_characters(characters)
    job.message = "大枠シナリオを考えています"
    theme = theme_of(features)
    notes = "セリフのない漫画。セリフや説明に頼らず、絵(表情・動作・場面)だけで伝わる話にする。" if wordless(page_panels) else ""
    outlines = await generate_outlines(
        api_key, theme, _genre(import_id, req), cast, len(structure), notes=notes, structure=structure
    )
    outline = outlines[0]
    episodes: list[list[dict[str, Any]]] = []
    for index in range(len(structure)):
        job.message = f"台本を書いています {index + 1}/{len(structure)}話"
        episode = await generate_episode(
            api_key,
            outline,
            index,
            cast,
            notes=notes,
            previous_panels=episodes[-1] if episodes else None,
            structure=structure[index],
        )
        episodes.append(apply_import_panels(episode, page_panels[index]))
    return await _finish(import_id, outline["title"], cast, episodes, req, features.monochrome, api_key, job)


async def make_rebuild(import_id: int, req: MangaImportAutoRequest, api_key: str, job: Any) -> int:
    """
    取り込んだ漫画を、セリフごと台本に起こして描き直し、物語のIDを返す(自分の作品のためのもの):
    キャラ(選んだもの、無ければ見た目から作る)と参照画像 → ページごとに、セリフの話し手・描く人物・所作の
    割り振りを文章モデルに決めさせる(セリフの文面は読み取ったまま) → 取り込んだ作品のコマ割りで物語を作る
    → コマの生成と合成。
    """
    from ..manga_draft import draft_characters
    from ..manga_rebuild import assign_page, script_panels

    item = _require(import_id)
    if (item.get("purpose") or "structure") != "rebuild":
        raise RuntimeError("この取り込みはセリフを読み取っていません。「自分の作品を作り直す」で読み取り直してください。")
    used, page_panels = _used_pages(import_id, req.max_pages)
    characters, features = await _prepare_cast(import_id, used, req, api_key, job)

    cast = draft_characters(characters)
    by_id = {c["id"]: c for c in characters}
    looks = {c.name: (by_id[c.id].get("appearance_tags") or "") for c in cast}
    episodes: list[list[dict[str, Any]]] = []
    for index, panels in enumerate(page_panels):
        job.message = f"セリフの話し手を決めています {index + 1}/{len(page_panels)}ページ"
        episodes.append(script_panels(panels, await assign_page(api_key, panels, cast, looks)))
    return await _finish(import_id, item["title"], cast, episodes, req, features.monochrome, api_key, job)


def get_character_by_name(conn: Any, name: str) -> bool:
    return conn.execute("SELECT 1 FROM characters WHERE name = ?", (name,)).fetchone() is not None


def _job_response(job: _ImportJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {k: getattr(job, k) for k in ("status", "message", "progress", "total", "detail", "story_id")}


def _optional_api_key(authorization: str | None = Header(None)) -> str | None:
    """
    NovelAI のトークン(Authorization か .env の NOVELAI_API_TOKEN。APP_MODE=multi では .env を使わない)。
    無ければ None(取り込みだけなら要らない)。
    """
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return operator_token()


@router.post("")
async def post_import(
    req: MangaImportRequest, api_key: Annotated[str | None, Depends(_optional_api_key)]
) -> dict[str, Any]:
    """PDF・画像を取り込み、読み取りを始める。ファイルは渡した順にページになる。使い方は purpose で選ぶ。"""
    try:
        pages = await asyncio.to_thread(load_pages, [(f.name, _decode(f.data)) for f in req.files])
    except (UnidentifiedImageError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=f"ページを読み込めませんでした: {exc}")
    if not pages:
        raise HTTPException(status_code=400, detail="ページがありません。")
    if req.auto_manga is not None and not api_key:
        raise HTTPException(status_code=401, detail="続けて漫画を作るには NovelAI へのログインが必要です。")
    conn = get_connection()
    try:
        item = create_manga_import(conn, req.title.strip(), len(pages), req.purpose, req.genre.strip())
        # 候補に無いジャンルを割り当てたら、候補にも足す
        if req.genre.strip():
            add_manga_genre(conn, req.genre.strip())
    finally:
        conn.close()
    _page_dir(item["id"]).mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(pages):
        page.save(_page_path(item["id"], index), "PNG")
    job = _start(item["id"], len(pages), req.use_vision, req.purpose, req.auto_manga, api_key)
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


@router.patch("/{import_id}")
async def patch_import(import_id: int, req: MangaImportUpdateRequest) -> dict[str, Any]:
    """取り込みを手直しする。genre: 漫画ドラフトのジャンルへの割り当て(空文字で外す)。"""
    _require(import_id)
    if req.genre is not None:
        conn = get_connection()
        try:
            update_manga_import(conn, import_id, genre=req.genre.strip())
            if req.genre.strip():
                add_manga_genre(conn, req.genre.strip())
        finally:
            conn.close()
    return _summary(_require(import_id))


@router.get("/{import_id}/job")
async def get_import_job(import_id: int) -> dict[str, Any] | None:
    return _job_response(_jobs.get(import_id))


@router.post("/{import_id}/analyze")
async def reanalyze(import_id: int, use_vision: bool = True, purpose: str | None = None) -> dict[str, Any] | None:
    """
    読み取り直す(読み取りに失敗したときや、画像モデルを後から使うとき)。purpose を渡すと使い方を変える
    (similar / rebuild)。渡さなければ、取り込んだときの使い方のまま。読み取り直すと、手直ししたセリフと
    描き文字は消える。
    """
    item = _require(import_id)
    if purpose is not None and purpose not in ("similar", "rebuild"):
        raise HTTPException(status_code=422, detail="purpose は similar か rebuild です。")
    return _job_response(
        _start(import_id, item["page_count"], use_vision, purpose or item.get("purpose") or "structure")
    )


def _start_make(import_id: int, req: MangaImportAutoRequest, api_key: str, *, rebuild: bool) -> dict[str, Any] | None:
    item = _require(import_id)
    if item["status"] != "analyzed":
        raise HTTPException(status_code=409, detail="取り込んだ作品をまだ読み取っていません。")
    if rebuild and (item.get("purpose") or "structure") != "rebuild":
        raise HTTPException(
            status_code=409,
            detail="この取り込みはセリフを読み取っていません。「自分の作品を作り直す」で読み取り直してください。",
        )
    running = _jobs.get(import_id)
    if running is not None and running.status == "running":
        raise HTTPException(status_code=409, detail="この取り込みは処理中です。")
    job = _ImportJob()
    _jobs[import_id] = job
    job.task = asyncio.create_task(_make(import_id, req, api_key, job, rebuild=rebuild))
    return _job_response(job)


@router.post("/{import_id}/similar")
async def post_similar(import_id: int, req: MangaImportAutoRequest, client: ClientDep) -> dict[str, Any] | None:
    """読み取り済みの取り込みから、似た漫画を作る(ジョブ。進み具合は GET /{import_id}/job)。"""
    return _start_make(import_id, req, client.api_key, rebuild=False)


@router.post("/{import_id}/rebuild")
async def post_rebuild(import_id: int, req: MangaImportAutoRequest, client: ClientDep) -> dict[str, Any] | None:
    """
    セリフを読み取った取り込み(purpose が rebuild)を、セリフごと台本に起こして描き直す(ジョブ)。
    make_images を False にすると台本(物語)を作るところまでで、コマの絵は生成しない。
    """
    return _start_make(import_id, req, client.api_key, rebuild=True)


@router.put("/{import_id}/pages/{page_index}/panels/{panel_index}/lines")
async def put_panel_lines(
    import_id: int, page_index: int, panel_index: int, req: MangaImportLinesRequest
) -> dict[str, Any]:
    """読み取ったセリフを手直しする(purpose が rebuild のとき)。そのコマのセリフを、渡した並びに置き換える。"""
    from ..manga_reading import MAX_LINE_CHARS, line_features

    item = _require(import_id)
    if (item.get("purpose") or "structure") != "rebuild":
        raise HTTPException(status_code=409, detail="この取り込みはセリフの文面を持っていません。")
    analysis, panel = _panel(item, page_index, panel_index)
    old = panel.get("lines") or []
    lines = []
    for index, raw in enumerate(req.lines):
        text = raw.strip()[:MAX_LINE_CHARS]
        if not text:
            continue
        line = {**line_features(text), "text": text}
        # 位置は、同じ並びにあった元のセリフのものを引き継ぐ
        if index < len(old) and old[index].get("box"):
            line["box"] = old[index]["box"]
        lines.append(line)
    panel["lines"] = lines
    panel["detailed"] = True
    conn = get_connection()
    try:
        update_manga_import(conn, import_id, analysis=analysis)
    finally:
        conn.close()
    return {**_summary(item), "analysis": analysis}


def _panel(item: dict[str, Any], page_index: int, panel_index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """(analysis, そのコマ)。無ければ 404。"""
    analysis = item.get("analysis") or {}
    pages = analysis.get("pages") or []
    if not (0 <= page_index < len(pages)) or not (0 <= panel_index < len(pages[page_index]["panels"])):
        raise HTTPException(status_code=404, detail="panel not found")
    return analysis, pages[page_index]["panels"][panel_index]


@router.put("/{import_id}/pages/{page_index}/panels/{panel_index}/sfx")
async def put_panel_sfx(
    import_id: int, page_index: int, panel_index: int, req: MangaImportSfxRequest
) -> dict[str, Any]:
    """
    コマの描き文字を手直しする。渡した並びに置き換える: 描き文字でない候補は並びから外し、見落とされたものは
    box なしで足す。読み(reading)を残すのは purpose が rebuild のときだけ(similar では読みを捨てる)。
    """
    from ..manga_import import sfx_size

    item = _require(import_id)
    purpose = item.get("purpose") or "structure"
    if purpose not in ("similar", "rebuild"):
        raise HTTPException(status_code=409, detail="この取り込みは描き文字を読み取っていません。読み取り直してください。")
    analysis, panel = _panel(item, page_index, panel_index)
    with Image.open(_page_path(import_id, page_index)) as src:
        width, height = src.size
    sfx = []
    for raw in req.sfx:
        reading = raw.reading.strip() if purpose == "rebuild" else ""
        if raw.box is None:
            # 手で足したもの: 読みが無ければ意味が無いので捨てる
            if reading:
                sfx.append({"box": None, "size": "中", "reading": reading})
            continue
        x0, y0, x1, y1 = (round(v) for v in raw.box)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, width), min(y1, height)
        if x1 <= x0 or y1 <= y0:
            raise HTTPException(status_code=422, detail="描き文字の位置がページの外です。")
        box = [x0, y0, x1, y1]
        sfx.append({"box": box, "size": sfx_size(tuple(box), tuple(panel["box"])), "reading": reading})
    panel["sfx"] = sfx
    conn = get_connection()
    try:
        update_manga_import(conn, import_id, analysis=analysis)
    finally:
        conn.close()
    return {**_summary(item), "analysis": analysis}


# 描き文字の特徴(形の似かたを比べるためのベクトル)。キーは(取り込み, ページ, 位置)
_sfx_features: dict[tuple[int, int, tuple[int, ...]], Any] = {}
# 切り抜きのまわりに足す余白(ピクセル)
_CROP_MARGIN = 6


def _crop(import_id: int, page_index: int, box: list[int] | tuple[int, ...], margin: int = _CROP_MARGIN) -> Image.Image:
    with Image.open(_page_path(import_id, page_index)) as src:
        image = src.convert("RGB")
    x0, y0, x1, y1 = box
    return image.crop((max(x0 - margin, 0), max(y0 - margin, 0), min(x1 + margin, image.width), min(y1 + margin, image.height)))


def _suggest(import_id: int, pages: list[dict[str, Any]]) -> dict[str, list[str]]:
    """読みの無い描き文字ごとの、読みの候補(同じ取り込みの、読みを入れた描き文字のうち形の似ているもの)。"""
    from ..sfx_similarity import features, rank_readings

    entries = [
        (page_index, panel_index, index, sfx)
        for page_index, page in enumerate(pages)
        for panel_index, panel in enumerate(page["panels"])
        for index, sfx in enumerate(panel.get("sfx") or [])
        if sfx.get("box")
    ]
    known = [e for e in entries if str(e[3].get("reading") or "").strip()]
    unknown = [e for e in entries if not str(e[3].get("reading") or "").strip()]
    if not known or not unknown:
        return {}
    keys = {(import_id, e[0], tuple(e[3]["box"])) for e in known + unknown}
    missing = [k for k in keys if k not in _sfx_features]
    for key, vector in zip(missing, features([_crop(import_id, k[1], k[2], 0) for k in missing])):
        _sfx_features[key] = vector
    import numpy as np

    matrix = np.stack([_sfx_features[(import_id, e[0], tuple(e[3]["box"]))] for e in known])
    readings = [str(e[3]["reading"]).strip() for e in known]
    return {
        f"{e[0]}-{e[1]}-{e[2]}": rank_readings(_sfx_features[(import_id, e[0], tuple(e[3]["box"]))], matrix, readings)
        for e in unknown
    }


@router.get("/{import_id}/sfx-suggestions")
async def get_sfx_suggestions(import_id: int) -> dict[str, Any]:
    """
    読みの無い描き文字の、読みの候補。キーは「ページ-コマ-描き文字の番号」(0始まり)、値は似ている順の読み。
    同じ取り込みの中で読みを入れた描き文字と、形を比べて出す(同じ描き手なら、上位5つに正解が入るのは半分ほど。
    自動では決めない)。モデルを用意できなければ available が False。
    """
    item = _require(import_id)
    pages = (item.get("analysis") or {}).get("pages") or []
    try:
        return {"available": True, "suggestions": await asyncio.to_thread(_suggest, import_id, pages)}
    except Exception as exc:  # noqa: BLE001 候補が出せなくても、読みは手で入れられる
        return {"available": False, "suggestions": {}, "detail": str(exc)}


@router.get("/{import_id}/pages/{page_index}/crop")
async def get_page_crop(import_id: int, page_index: int, x0: int, y0: int, x1: int, y1: int) -> Response:
    """ページの一部(描き文字の候補を見せるための切り抜き)。"""
    if not _page_path(import_id, page_index).is_file():
        raise HTTPException(status_code=404, detail="page not found")
    if x1 <= x0 or y1 <= y0:
        raise HTTPException(status_code=422, detail="切り抜く範囲が正しくありません。")
    crop = await asyncio.to_thread(_crop, import_id, page_index, (x0, y0, x1, y1))
    if crop.width < 1 or crop.height < 1:
        raise HTTPException(status_code=422, detail="切り抜く範囲がページの外です。")
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


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
    _forget_sfx_features(import_id)


def _forget_sfx_features(import_id: int) -> None:
    for key in [k for k in _sfx_features if k[0] == import_id]:
        del _sfx_features[key]
