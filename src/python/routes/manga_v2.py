"""
漫画v2のAPI。シーンごとのコマ画像を NovelAI で生成し、テンプレートへの嵌め込みと
吹き出し(縦書きセリフ)をこちらで描いてページにする。

ジョブ(進捗のポーリング・キャンセル)は routes/story.py の仕組みをそのまま使うので、
進捗は GET /api/story/{story_id}/job で見る。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import random
import re
import unicodedata
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from PIL import Image
from novelai import AsyncNovelAI

from ..client import get_client
from ..db import (
    characters_by_scene,
    get_character,
    get_connection,
    get_manga_v2_compose_settings,
    get_manga_import,
    get_manga_v2_overrides,
    get_manga_v2_page_layouts,
    get_manga_v2_sfx_fonts,
    get_manga_v2_sfx_stamps,
    set_manga_v2_page_layouts,
    adult_stamp_sources,
    set_stamp_source_adult,
    delete_stamp,
    delete_stamp_source,
    get_stamp,
    get_story,
    list_stamp_sources,
    list_stamps,
    replace_stamp_source,
    update_stamp_label,
    list_manga_panels,
    list_story_scenes,
    set_manga_v2_compose_settings,
    set_manga_v2_overrides,
    set_manga_v2_sfx_fonts,
    set_manga_v2_sfx_stamps,
    update_character_reference,
    update_character_sheet,
    update_scene_narration,
    update_scene_sfx,
    update_story_final_image,
    upsert_manga_panel,
)
from ..manga_v2.compose import (
    LetteringStyle,
    PanelContent,
    compose_pages,
    compose_panel,
    concat_pages,
    page_png,
    split_dense_panels,
)
from ..manga_v2.fonts import CATALOG, CATALOG_BY_ID, LICENSE_NAME, download_font
from ..manga_v2.stamps import (
    STAMP_DIR,
    SheetSource,
    ZipStamp,
    fetch_pixiv_sheets,
    is_monochrome,
    is_stamp_sheet,
    pixiv_artwork_id,
    split_sheet,
    stamps_from_zip,
)
from ..manga_v2.layout import (
    PAGE_HEIGHT,
    PAGE_WIDTH,
    TEMPLATES,
    generation_size,
    scene_rects,
)
from ..manga_v2.lettering import DEFAULT_SFX_FONT_ID, available_fonts, draw_sfx, fit_sfx, resolve_font
from ..manga_v2.panel_shapes import trace_page
from ..manga_v2.prompt import build_panel_negative, build_panel_prompt, is_sexual
from ..manga_v2.speakers import CastMember, attribute_speakers, cast_members
from ..models import (
    MangaV2CatalogFont,
    MangaV2SfxFontRequest,
    MangaV2SfxStampRequest,
    MangaV2Stamp,
    MangaV2StampImportRequest,
    MangaV2StampLabelRequest,
    MangaV2StampSource,
    MangaV2StampSourceUpdate,
    MangaV2StampUploadRequest,
    MangaV2StampZipRequest,
    MangaV2CharacterReferenceRequest,
    MangaV2ReferenceCandidate,
    MangaV2ReferenceCandidatesRequest,
    MangaV2ComposeRequest,
    MangaV2MakeRequest,
    MangaV2PageLayoutsRequest,
    MangaV2ComposeResponse,
    MangaV2DownloadRequest,
    MangaV2Font,
    MangaV2OverrideRequest,
    MangaV2ScaleRequest,
    MangaV2Panel,
    MangaV2PanelsRequest,
    MangaV2SceneNarrationRequest,
    MangaV2SceneSfxRequest,
    MangaV2SuggestSfxRequest,
    MangaV2Template,
    StoryJobResponse,
)
from ..character_sheet import character_prompt_tags, characters_negative, characters_seed, join_tags
from ..novelai_image_v5 import DIALOGUE_RE, CharacterReferenceInput, generate_image_v5, reference_image_b64
from .llm import stream_llm_text, strip_think_tags
from .story import _MANGA_DIR, _PROJECT_ROOT, _Job, _job_response, _start_job

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/manga-v2", tags=["manga-v2"])

_PANEL_DIR = _MANGA_DIR / "panels"
_REFERENCE_DIR = _MANGA_DIR / "refs"
# 参照画像の候補(選ばれなかったものも残る。選んだものは refs へ写す)
_CANDIDATE_DIR = _REFERENCE_DIR / "candidates"
# 候補の構図。顔と服装が分かり、背景が絵柄を引っ張らないもの
_CANDIDATE_TAGS = "solo, cowboy shot, standing, looking at viewer, smile, simple background, white background"
# キャラ参照を使うコマの生成モデル。V5はキャラ参照に未対応(500が返る)。
_REFERENCE_MODEL = "nai-diffusion-4-5-full"
_PAGE_DIR = _MANGA_DIR / "v2"
# characterPrompts の上限(V4系と同じ)
_MAX_CHARACTERS = 4
# vary_seed のとき、シーンごとにシードをずらす間隔
_SEED_STEP = 37


def _require_template(template_id: str) -> None:
    if template_id not in TEMPLATES:
        raise HTTPException(status_code=400, detail=f"不明なテンプレートです: {template_id}")


# 本文中で効果音を明示する記法。《ガタッ》のように書くと描き文字になる。
_SFX_MARK_RE = re.compile(r"《([^》]+)》")
# 「」の中身がカタカナ(と長音・促音・記号)だけの短いものは、セリフではなく効果音とみなす。
# ひらがなの「あっ…！」等は声なので吹き出しのままにする。
_KATAKANA_SFX_RE = re.compile(r"[ァ-ヶー・…‥！？!?〜～　 ]{1,12}")
_HAS_KATAKANA_RE = re.compile(r"[ァ-ヶ]")


def _is_katakana_sfx(line: str) -> bool:
    return bool(_KATAKANA_SFX_RE.fullmatch(line)) and bool(_HAS_KATAKANA_RE.search(line))


# 地の文の中の「」(店名・書名・強調。例: 古書店「時雨堂」の店主)はセリフではない。
# セリフの「」は行頭か、文の切れ目の直後から始まる。
_SPOKEN_PREFIX = set("\n。！？!?」』　 ")


def _is_spoken(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1] not in _SPOKEN_PREFIX:
        return False
    # 『』の直後に助詞が続くものは書名・作品名(『星の旅人』という本)。「」は「…」と言った、
    # のようにセリフでも助詞が続くので対象にしない。
    return not (text[start] == "『" and end < len(text) and text[end] in _TITLE_PARTICLES)


_TITLE_PARTICLES = set("のとをがはにもで")


_THOUGHT_LINE_RE = re.compile(r"^[ 　]*（([^）\n]{1,80})）[ 　]*$", re.MULTILINE)


def _scene_text(scene: dict[str, Any]) -> str:
    return scene["novelai_text"] or scene["draft_text"] or ""


def _spoken_spans(text: str) -> tuple[list[tuple[int, int, str]], list[str]]:
    """本文から (吹き出しにするセリフの (開始, 終了, 中身) の並び, 本文中の効果音) を取り出す。"""
    sfx: list[str] = [m.group(1).strip() for m in _SFX_MARK_RE.finditer(text) if m.group(1).strip()]
    spoken: list[tuple[int, int, str]] = []
    for match in DIALOGUE_RE.finditer(text):
        line = match.group(1).strip()
        if not line or not _is_spoken(text, match.start(), match.end()):
            continue
        if _is_katakana_sfx(line):
            sfx.append(line)
        else:
            spoken.append((match.start(), match.end(), line))
    # 1行まるごと（…）の独白は心の声。括弧ごと渡すと雲形の吹き出しになる
    for match in _THOUGHT_LINE_RE.finditer(text):
        spoken.append((match.start(), match.end(), f"（{match.group(1).strip()}）"))
    return sorted(spoken), sfx


def _scene_speakers(scene: dict[str, Any]) -> tuple[list[int | None], list[CastMember]]:
    """
    セリフ(_lettering の並び)ごとの話し手のキャラIDと、シーンの登場キャラ。
    キャラが割り当てられていないシーンは話し手を推定しない(空の並び)。
    """
    cast = cast_members(scene.get("characters") or [])
    if not cast:
        return [], []
    text = _scene_text(scene)
    return attribute_speakers(text, _spoken_spans(text)[0], cast), cast


def _lettering(scene: dict[str, Any]) -> tuple[list[str], list[str]]:
    """
    シーンから (吹き出しにするセリフ, 描き文字にする効果音) を取り出す。
    効果音は本文中の《》・カタカナだけのセリフに、シーンに設定した効果音(手入力やAI提案)を足す。
    """
    spoken, sfx = _spoken_spans(_scene_text(scene))
    dialogue = [line for _, _, line in spoken]
    for extra in scene.get("sfx") or []:
        if extra.strip() and extra.strip() not in sfx:
            sfx.append(extra.strip())
    return dialogue, sfx


@router.get("/fonts", response_model=list[MangaV2Font])
async def get_fonts() -> list[dict[str, str]]:
    return [{"id": f.id, "label": f.label} for f in available_fonts()]


@router.get("/font-catalog", response_model=list[MangaV2CatalogFont])
async def get_font_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": f.id,
            "label": f.label,
            "mood": f.mood,
            "installed": f.installed,
            "license": LICENSE_NAME,
            "source_url": f.source_url,
        }
        for f in CATALOG
    ]


@router.post("/font-catalog/{font_id}/download", status_code=204)
async def post_font_download(font_id: str) -> None:
    font = CATALOG_BY_ID.get(font_id)
    if font is None:
        raise HTTPException(status_code=404, detail="一覧にないフォントです。")
    try:
        await download_font(font)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ダウンロードに失敗しました: {exc}")


@router.get("/font-preview")
def get_font_preview(font: str, text: str = "ドドド") -> Response:
    """効果音を指定フォントの描き文字で描いた見本(PNG、白地)。フォント選びの比較用。"""
    fonts = {f.id: f.path for f in available_fonts()}
    if font not in fonts:
        raise HTTPException(status_code=404, detail="そのフォントは使えません(未ダウンロード)。")
    layout = fit_sfx(text[:12], 640, 480)
    image = Image.new("RGB", (max(layout.width + 40, 120), layout.height + 40), (255, 255, 255))
    draw_sfx(image, (20, 20, 20 + layout.width, 20 + layout.height), layout, fonts[font])
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


# ---- 描き文字スタンプ ----


def _save_stamps(source: SheetSource, sheets: list[Image.Image]) -> int:
    """シートを切り分けて data/stamps/<source>/ に保存し、DBの取り込み元ごと入れ替える。"""
    target = STAMP_DIR / source.key
    if target.exists():
        for old in target.glob("*.png"):
            old.unlink()
    target.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for sheet_index, sheet in enumerate(sheets):
        for index, stamp in enumerate(split_sheet(sheet)):
            name = f"s{sheet_index:02d}_{index:03d}.png"
            stamp.save(target / name)
            rows.append(
                {
                    "sheet": sheet_index,
                    "idx": index,
                    "image_path": f"data/stamps/{source.key}/{name}",
                    "width": stamp.width,
                    "height": stamp.height,
                }
            )
    conn = get_connection()
    try:
        replace_stamp_source(conn, source.key, source.title, source.author, source.url, rows, source.adult)
    finally:
        conn.close()
    return len(rows)


@router.post("/stamps/import", response_model=MangaV2StampSource)
async def import_stamps(req: MangaV2StampImportRequest) -> dict[str, Any]:
    """pixiv の素材作品を取り込む。透過の素材シートだけを1語ずつのスタンプに切り分ける。"""
    artwork_id = pixiv_artwork_id(req.url)
    if artwork_id is None:
        raise HTTPException(status_code=400, detail="pixiv の作品URL(https://www.pixiv.net/artworks/...)を指定してください。")
    try:
        source, sheets = await fetch_pixiv_sheets(artwork_id)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"pixiv から取得できませんでした: {exc}")
    if not sheets:
        raise HTTPException(status_code=400, detail="透過の素材シートが見つかりませんでした。")
    await asyncio.to_thread(_save_stamps, source, sheets)
    return _stamp_source(source.key)


@router.post("/stamps/upload", response_model=MangaV2StampSource)
async def upload_stamps(req: MangaV2StampUploadRequest) -> dict[str, Any]:
    """手元の素材シート(透過PNG)を取り込む。"""
    data = req.image.split(",", 1)[1] if req.image.startswith("data:") else req.image
    try:
        sheet = Image.open(io.BytesIO(base64.b64decode(data)))
        sheet.load()
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="画像を読み取れませんでした。")
    if not is_stamp_sheet(sheet):
        raise HTTPException(status_code=400, detail="背景が透過した素材シート(PNG)を指定してください。")
    source = SheetSource(key=f"upload-{uuid4().hex[:8]}", title=req.title, author=req.author, url=req.url)
    await asyncio.to_thread(_save_stamps, source, [sheet])
    return _stamp_source(source.key)


def _save_zip_stamps(source: SheetSource, stamps: list[ZipStamp]) -> int:
    """1語1ファイルの素材を保存する。sheet=デザイン番号、idx=色違いの番号、読みはファイル名から。"""
    target = STAMP_DIR / source.key
    target.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for number, stamp in enumerate(stamps):
        name = f"z{number:04d}.png"
        stamp.image.save(target / name)
        rows.append(
            {
                "sheet": stamp.design,
                "idx": stamp.variant,
                "image_path": f"data/stamps/{source.key}/{name}",
                "width": stamp.image.width,
                "height": stamp.image.height,
                "label": stamp.label,
            }
        )
    conn = get_connection()
    try:
        replace_stamp_source(conn, source.key, source.title, source.author, source.url, rows, source.adult)
    finally:
        conn.close()
    return len(rows)


@router.post("/stamps/upload-zip", response_model=MangaV2StampSource)
async def upload_stamp_zip(req: MangaV2StampZipRequest) -> dict[str, Any]:
    """
    1語1ファイルの素材集(透過PNGのZIP)を取り込む。ファイル名の数字より前を読みにする
    (「くちゅ1_0007.png」→「くちゅ」)。同じ素材集を取り込み直すと入れ替わる。
    """
    data = req.zip.split(",", 1)[1] if req.zip.startswith("data:") else req.zip
    try:
        folder, stamps = await asyncio.to_thread(stamps_from_zip, base64.b64decode(data))
    except (ValueError, OSError, zipfile.BadZipFile):
        raise HTTPException(status_code=400, detail="ZIPを読み取れませんでした。")
    if not stamps:
        raise HTTPException(status_code=400, detail="ZIPにPNGの素材が見つかりませんでした。")
    title = req.title.strip() or folder or "ZIP素材"
    key = "zip-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
    source = SheetSource(key=key, title=title, author=req.author, url=req.url, adult=req.adult)
    await asyncio.to_thread(_save_zip_stamps, source, stamps)
    return _stamp_source(source.key)


def _stamp_source(key: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        return next(s for s in list_stamp_sources(conn) if s["key"] == key)
    finally:
        conn.close()


@router.get("/stamp-sources", response_model=list[MangaV2StampSource])
async def get_stamp_sources() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_stamp_sources(conn)
    finally:
        conn.close()


@router.put("/stamp-sources/{key}", status_code=204)
async def put_stamp_source(key: str, req: MangaV2StampSourceUpdate) -> None:
    """成人向けの素材かどうか。成人向けの素材は描き文字の自動選択で既定では使わない。"""
    conn = get_connection()
    try:
        set_stamp_source_adult(conn, key, req.adult)
    finally:
        conn.close()


@router.delete("/stamp-sources/{key}", status_code=204)
async def remove_stamp_source(key: str) -> None:
    conn = get_connection()
    try:
        delete_stamp_source(conn, key)
    finally:
        conn.close()
    target = STAMP_DIR / key
    if target.is_dir() and target.resolve().parent == STAMP_DIR.resolve():
        for file in target.glob("*.png"):
            file.unlink()
        target.rmdir()


@router.get("/stamps", response_model=list[MangaV2Stamp])
async def get_stamps(source: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_stamps(conn, source)
    finally:
        conn.close()


@router.get("/stamps/{stamp_id}/image")
async def get_stamp_image(stamp_id: int) -> FileResponse:
    conn = get_connection()
    try:
        stamp = get_stamp(conn, stamp_id)
    finally:
        conn.close()
    if stamp is None or not (_PROJECT_ROOT / stamp["image_path"]).is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(_PROJECT_ROOT / stamp["image_path"], media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@router.put("/stamps/{stamp_id}", status_code=204)
async def put_stamp_label(stamp_id: int, req: MangaV2StampLabelRequest) -> None:
    conn = get_connection()
    try:
        update_stamp_label(conn, stamp_id, req.label.strip())
    finally:
        conn.close()


@router.delete("/stamps/{stamp_id}", status_code=204)
async def remove_stamp(stamp_id: int) -> None:
    """切り分けに失敗した塊(2語がくっついた等)を一覧から消す。"""
    conn = get_connection()
    try:
        stamp = get_stamp(conn, stamp_id)
        delete_stamp(conn, stamp_id)
    finally:
        conn.close()
    if stamp is not None:
        (_PROJECT_ROOT / stamp["image_path"]).unlink(missing_ok=True)


@router.get("/{story_id}/sfx-stamps")
async def get_sfx_stamps(story_id: int) -> dict[str, int]:
    conn = get_connection()
    try:
        return get_manga_v2_sfx_stamps(conn, story_id)
    finally:
        conn.close()


@router.put("/{story_id}/sfx-stamps", status_code=204)
async def put_sfx_stamp(story_id: int, req: MangaV2SfxStampRequest) -> None:
    conn = get_connection()
    try:
        stamps = get_manga_v2_sfx_stamps(conn, story_id)
        if req.stamp_id is None:
            stamps.pop(req.word, None)
        else:
            stamps[req.word] = req.stamp_id
        set_manga_v2_sfx_stamps(conn, story_id, stamps)
    finally:
        conn.close()


@router.get("/templates", response_model=list[MangaV2Template])
async def get_templates() -> list[dict[str, Any]]:
    return [{"id": t.id, "label": t.label, "panels": len(t.panels)} for t in TEMPLATES.values()]


@router.post("/characters/{character_id}/reference-candidates", response_model=list[MangaV2ReferenceCandidate])
async def generate_reference_candidates(
    character_id: int, req: MangaV2ReferenceCandidatesRequest, client: ClientDep
) -> list[dict[str, Any]]:
    """
    キャラシート(容姿・服装)だけで、参照画像の候補をシードを変えて数枚生成する。気に入った1枚を
    PUT .../reference の candidate_path と seed で登録すると、以後のコマはその見た目とシードに寄る。
    """
    conn = get_connection()
    try:
        character = get_character(conn, character_id)
    finally:
        conn.close()
    if character is None:
        raise HTTPException(status_code=404, detail="キャラが見つかりません。")
    tags = character_prompt_tags(character)
    if not tags:
        raise HTTPException(status_code=400, detail="キャラシートに容姿のタグがありません。")

    _CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, Any]] = []
    for _ in range(req.count):
        seed = random.randrange(4294967296)
        image = await generate_image_v5(
            client.api_key,
            build_panel_prompt(_CANDIDATE_TAGS, color=req.color, complexity=None),
            join_tags(build_panel_negative(None, color=req.color), character.get("negative_tags")),
            model=req.model or _REFERENCE_MODEL,
            width=832,
            height=1216,
            seed=seed,
            character_tags=[tags],
        )
        filename = f"char{character_id}_{seed}_{uuid4().hex[:6]}.png"
        (_CANDIDATE_DIR / filename).write_bytes(image)
        candidates.append({"path": f"outputs/manga/refs/candidates/{filename}", "seed": seed})
    return candidates


def _candidate_bytes(path: str) -> bytes:
    """候補の画像を読む。候補の置き場所の外は読まない。"""
    file = (_PROJECT_ROOT / path).resolve()
    if not file.is_relative_to(_CANDIDATE_DIR.resolve()) or not file.is_file():
        raise HTTPException(status_code=404, detail="その候補の画像が見つかりません。")
    return file.read_bytes()


@router.put("/characters/{character_id}/reference", status_code=204)
async def put_character_reference(character_id: int, req: MangaV2CharacterReferenceRequest) -> None:
    """キャラ参照の画像を登録する。アップロード画像・生成済みのコマの絵・参照の候補のどれかを使う。"""
    if req.candidate_path:
        image_bytes = _candidate_bytes(req.candidate_path)
    elif req.image:
        data = req.image.split(",", 1)[1] if req.image.startswith("data:") else req.image
        try:
            image_bytes = base64.b64decode(data)
        except ValueError:
            raise HTTPException(status_code=400, detail="画像を読み取れませんでした。")
    elif req.scene_id is not None:
        conn = get_connection()
        try:
            row = conn.execute("SELECT image_path FROM manga_panels WHERE scene_id = ?", (req.scene_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="そのシーンにはまだコマの絵がありません。")
        image_bytes = (_PROJECT_ROOT / row["image_path"]).read_bytes()
    else:
        raise HTTPException(status_code=400, detail="image・scene_id・candidate_path のどれかを指定してください。")

    _REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"char{character_id}_{uuid4().hex[:8]}.png"
    (_REFERENCE_DIR / filename).write_bytes(image_bytes)
    conn = get_connection()
    try:
        update_character_reference(conn, character_id, f"outputs/manga/refs/{filename}")
        if req.seed is not None:
            update_character_sheet(conn, character_id, {"seed": req.seed})
    finally:
        conn.close()


@router.delete("/characters/{character_id}/reference", status_code=204)
async def delete_character_reference(character_id: int) -> None:
    conn = get_connection()
    try:
        update_character_reference(conn, character_id, None)
    finally:
        conn.close()


@router.put("/scenes/{scene_id}/sfx", status_code=204)
async def put_scene_sfx(scene_id: int, req: MangaV2SceneSfxRequest) -> None:
    sfx = None if req.sfx is None else [t.strip() for t in req.sfx if t.strip()]
    conn = get_connection()
    try:
        update_scene_sfx(conn, scene_id, sfx)
    finally:
        conn.close()


# ---- 効果音のAI提案 ----

# 1回に渡すシーン数と、1シーンあたりに送る本文の長さ。story.py のタグ付けと同じく、
# ローカルLLMのコンテキスト長(4096)に収まるよう絞っている。
_SFX_BATCH_SIZE = 8
_SFX_TEXT_CHARS = 200
_SFX_MAX_TOKENS = 512
_SFX_MAX_ATTEMPTS = 2
_SFX_PER_SCENE = 2
# 提案として受け付ける形(カナ・長音・促音・記号のみ、短いもの)
_SFX_ACCEPT_RE = re.compile(r"[ァ-ヶぁ-んー・…‥！？!?〜～]{1,10}")
# 擬音語らしい形。小さいモデル(qwen2.5:7b)は「ドア」のような普通の名詞や意味のない
# カナ列も返すので、促音・長音・三点リーダを含む/「ン」で終わる/繰り返す、のどれかを満たす
# ものだけ採用する(実機で「ドア」「ミソス」が返り、この条件で両方落ちる)。
_ONOMATOPOEIA_RE = re.compile(r".*[ッっーｰ…‥〜～].*|.*[ンん][！!…]*|.*(..?.?)\1.*")


def _sfx_json_schema(n_scenes: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "minItems": n_scenes,
                "maxItems": n_scenes,
                "items": {
                    "type": "object",
                    "properties": {"sfx": {"type": "array", "items": {"type": "string"}, "maxItems": _SFX_PER_SCENE}},
                    "required": ["sfx"],
                },
            }
        },
        "required": ["scenes"],
    }


def _sfx_system_prompt(n_scenes: int) -> str:
    return (
        "あなたは日本の漫画の効果音(描き文字)を考えるアシスタントです。\n"
        "物語を場面ごとに分けた本文が渡されます。各場面のコマに描き文字として入れる"
        "擬音語・擬態語をJSON形式で返してください。\n\n"
        f"- scenes配列にちょうど{n_scenes}個の要素を、渡された場面の順番通りに含める\n"
        f"- sfx: その場面の効果音を0〜{_SFX_PER_SCENE}個。カタカナ(長音・促音・「…」「！」可)で10文字以内\n"
        "- 音や動き・空気感がはっきりある場面にだけ付ける。静かで何も起きない場面は空配列にする\n"
        "- 音そのもの・様子そのものを表す擬音語/擬態語だけ。物の名前(名詞)、セリフ、説明文、"
        "登場人物の名前は書かない\n"
        "  良い例: ガタッ, ドキッ, ザァァ…, シーン, ゴゴゴ, バタン！, ニャー, ザワザワ, ヒュウウ, コツコツ\n"
        "  悪い例: ドア, ネコ, ドアが開く, 驚いた, 「こんにちは」"
    )


def _format_scenes_for_sfx(texts: list[str]) -> str:
    return "\n\n".join(f"場面{i}:\n{t[:_SFX_TEXT_CHARS]}" for i, t in enumerate(texts, start=1))


def _parse_sfx(text: str, n_scenes: int) -> list[list[str]] | None:
    try:
        scenes = json.loads(text).get("scenes")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(scenes, list) or not scenes:
        return None
    result: list[list[str]] = []
    for scene in scenes[:n_scenes]:
        items = scene.get("sfx") if isinstance(scene, dict) else None
        cleaned: list[str] = []
        for item in items if isinstance(items, list) else []:
            word = str(item).strip().strip("「」『』\"'")
            if _SFX_ACCEPT_RE.fullmatch(word) and _ONOMATOPOEIA_RE.fullmatch(word) and word not in cleaned:
                cleaned.append(word)
        result.append(cleaned[:_SFX_PER_SCENE])
    return result


async def _suggest_sfx_batch(texts: list[str]) -> list[list[str]] | None:
    for _ in range(_SFX_MAX_ATTEMPTS):
        parts: list[str] = []
        async for delta in stream_llm_text(
            [
                {"role": "system", "content": _sfx_system_prompt(len(texts))},
                {"role": "user", "content": _format_scenes_for_sfx(texts)},
            ],
            max_tokens=_SFX_MAX_TOKENS,
            json_schema=_sfx_json_schema(len(texts)),
        ):
            parts.append(delta)
        parsed = _parse_sfx(strip_think_tags("".join(parts)), len(texts))
        if parsed is not None:
            return parsed
    return None


@router.post("/{story_id}/suggest-sfx", response_model=StoryJobResponse)
async def suggest_sfx(story_id: int, req: MangaV2SuggestSfxRequest) -> dict[str, Any]:
    """ローカルLLMにシーンごとの効果音を提案させ、シーンに保存する(バックグラウンドジョブ)。"""
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
    finally:
        conn.close()
    if not scenes:
        raise HTTPException(status_code=404, detail="先にシーン分割を実行してください。")
    targets = [
        s
        for s in scenes
        if s["scene_index"] >= req.scene_from
        and (req.scene_to is None or s["scene_index"] <= req.scene_to)
        and (req.overwrite or s["sfx"] is None)
    ]
    if not targets:
        raise HTTPException(status_code=400, detail="対象のシーンがありません(範囲内は効果音を設定済みです)。")

    async def runner(job: _Job) -> None:
        batches = [targets[i : i + _SFX_BATCH_SIZE] for i in range(0, len(targets), _SFX_BATCH_SIZE)]
        job.total = len(batches)
        added = 0
        for index, batch in enumerate(batches, start=1):
            job.message = f"効果音を考えています {index}/{len(batches)}"
            suggestions = await _suggest_sfx_batch([s["novelai_text"] or s["draft_text"] for s in batch])
            if suggestions:
                conn = get_connection()
                try:
                    for scene, sfx in zip(batch, suggestions):
                        update_scene_sfx(conn, scene["id"], sfx)
                        added += len(sfx)
                finally:
                    conn.close()
            job.progress = index
        job.message = f"{len(targets)}シーンに効果音を{added}個提案しました"

    return _job_response(_start_job(story_id, "sfx", runner))


@router.put("/scenes/{scene_id}/narration", status_code=204)
async def put_scene_narration(scene_id: int, req: MangaV2SceneNarrationRequest) -> None:
    conn = get_connection()
    try:
        update_scene_narration(conn, scene_id, None if req.narration is None else req.narration.strip())
    finally:
        conn.close()


# ---- ナレーションのAI作成 ----

_NARRATION_TEXT_CHARS = 300
_NARRATION_MAX_CHARS = 40
_NARRATION_BATCH_SIZE = 6
_NARRATION_MAX_TOKENS = 768


def _narration_source(scene: dict[str, Any]) -> str:
    """セリフ(「」『』)と《効果音》を除いた地の文。"""
    text = scene["novelai_text"] or scene["draft_text"] or ""
    text = _SFX_MARK_RE.sub("", DIALOGUE_RE.sub("", text))
    return re.sub(r"\s+", " ", text).strip()


def _narration_json_schema(n_scenes: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "minItems": n_scenes,
                "maxItems": n_scenes,
                "items": {
                    "type": "object",
                    "properties": {"narration": {"type": "string"}},
                    "required": ["narration"],
                },
            }
        },
        "required": ["scenes"],
    }


def _narration_system_prompt(n_scenes: int) -> str:
    return (
        "あなたは小説を漫画にするときの、ナレーション(コマの隅に入る四角い枠の文)を書くアシスタントです。\n"
        "場面ごとの地の文(セリフは除いてあります)が渡されます。各場面のナレーションをJSON形式で返してください。\n\n"
        f"- scenes配列にちょうど{n_scenes}個の要素を、渡された場面の順番通りに含める\n"
        f"- narration: 日本語で1〜2文、{_NARRATION_MAX_CHARS}文字以内。時間・場所・状況や心情を短く\n"
        "- 地の文に書かれていないことは足さない。絵を見ればわかる細かい描写は省く\n"
        "- 地の文がほとんど無い場面や、ナレーションが要らない場面は空文字にする\n"
        "  良い例: その日、私は廃校を訪れた。 / 夕暮れの庭に、見知らぬ少女が立っていた。"
    )


def _parse_narration(text: str, n_scenes: int) -> list[str] | None:
    try:
        scenes = json.loads(text).get("scenes")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(scenes, list) or not scenes:
        return None
    result: list[str] = []
    for scene in scenes[:n_scenes]:
        value = scene.get("narration") if isinstance(scene, dict) else ""
        narration = str(value or "").strip().strip("「」『』\"")
        # 長すぎるものは文の切れ目で詰める(枠に収まらないため)
        if len(narration) > _NARRATION_MAX_CHARS * 2:
            narration = narration[: _NARRATION_MAX_CHARS * 2].rsplit("。", 1)[0] + "。"
        result.append(narration)
    return result


async def _suggest_narration_batch(texts: list[str]) -> list[str] | None:
    for _ in range(_SFX_MAX_ATTEMPTS):
        parts: list[str] = []
        async for delta in stream_llm_text(
            [
                {"role": "system", "content": _narration_system_prompt(len(texts))},
                {
                    "role": "user",
                    "content": "\n\n".join(
                        f"場面{i}:\n{t[:_NARRATION_TEXT_CHARS] or '(地の文なし)'}" for i, t in enumerate(texts, 1)
                    ),
                },
            ],
            max_tokens=_NARRATION_MAX_TOKENS,
            json_schema=_narration_json_schema(len(texts)),
        ):
            parts.append(delta)
        parsed = _parse_narration(strip_think_tags("".join(parts)), len(texts))
        if parsed is not None:
            return parsed
    return None


@router.post("/{story_id}/suggest-narration", response_model=StoryJobResponse)
async def suggest_narration(story_id: int, req: MangaV2SuggestSfxRequest) -> dict[str, Any]:
    """ローカルLLMに地の文からナレーションを作らせ、シーンに保存する(バックグラウンドジョブ)。"""
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
    finally:
        conn.close()
    if not scenes:
        raise HTTPException(status_code=404, detail="先にシーン分割を実行してください。")
    targets = [
        s
        for s in scenes
        if s["scene_index"] >= req.scene_from
        and (req.scene_to is None or s["scene_index"] <= req.scene_to)
        and (req.overwrite or s["narration"] is None)
    ]
    if not targets:
        raise HTTPException(status_code=400, detail="対象のシーンがありません(範囲内はナレーションを設定済みです)。")

    async def runner(job: _Job) -> None:
        batches = [targets[i : i + _NARRATION_BATCH_SIZE] for i in range(0, len(targets), _NARRATION_BATCH_SIZE)]
        job.total = len(batches)
        written = 0
        for index, batch in enumerate(batches, start=1):
            job.message = f"ナレーションを書いています {index}/{len(batches)}"
            results = await _suggest_narration_batch([_narration_source(s) for s in batch])
            if results:
                conn = get_connection()
                try:
                    for scene, narration in zip(batch, results):
                        update_scene_narration(conn, scene["id"], narration)
                        written += bool(narration)
                finally:
                    conn.close()
            job.progress = index
        job.message = f"{len(targets)}シーン中{written}シーンにナレーションを付けました"

    return _job_response(_start_job(story_id, "narration", runner))


@router.get("/{story_id}/panels", response_model=list[MangaV2Panel])
async def get_panels(story_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return list_manga_panels(conn, story_id)
    finally:
        conn.close()


@router.get("/{story_id}/speakers")
async def get_speakers(story_id: int) -> list[dict[str, Any]]:
    """
    シーンごとの吹き出しのセリフと、合成で使う話し手の判定結果。台本の確認用。
    speaker_id が null のセリフは、しっぽを一番近い顔へ向ける(話し手が分からない)。
    """
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
    finally:
        conn.close()
    if not scenes:
        raise HTTPException(status_code=404, detail="story not found")
    result: list[dict[str, Any]] = []
    for scene in scenes:
        lines = _lettering(scene)[0]
        speakers, _ = _scene_speakers(scene)
        names = {c["id"]: c["name"] for c in scene.get("characters") or []}
        result.append(
            {
                "scene_id": scene["id"],
                "scene_index": scene["scene_index"],
                "lines": [
                    {"text": line, "speaker_id": speaker, "speaker_name": names.get(speaker) if speaker else None}
                    for line, speaker in zip(lines, speakers or [None] * len(lines))
                ],
            }
        )
    return result


@router.post("/{story_id}/panels", response_model=StoryJobResponse)
async def generate_panels(story_id: int, req: MangaV2PanelsRequest, client: ClientDep) -> dict[str, Any]:
    """
    シーンごとにコマの絵を生成する。生成サイズはテンプレート上でそのシーンが入るコマの
    形に合わせる(テンプレートを後で変えても、合成時に切り抜いて嵌め込む)。
    """
    _require_template(req.template)
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
        existing = {p["scene_id"] for p in list_manga_panels(conn, story_id)}
    finally:
        conn.close()
    if not scenes:
        raise HTTPException(status_code=404, detail="先にシーン分割を実行してください。")

    targets = [
        s
        for s in scenes
        if s["scene_index"] >= req.scene_from
        and (req.scene_to is None or s["scene_index"] <= req.scene_to)
        and not (req.skip_existing and s["id"] in existing)
    ]
    if not targets:
        raise HTTPException(status_code=400, detail="生成するシーンがありません(範囲内は生成済みです)。")

    # get_client はリクエスト終了時にクライアントを閉じるので、トークンだけ持ち出す
    api_key = client.api_key

    async def runner(job: _Job) -> None:
        await _run_panels(job, story_id, req, api_key, targets)

    return _job_response(_start_job(story_id, "panels", runner))


def _scene_references(
    characters: list[dict[str, Any]], req: MangaV2PanelsRequest
) -> list[CharacterReferenceInput]:
    """
    シーンに出るキャラのうち、参照画像があるもの全員分。以前は1人分(名前順で最初の1人)だけを
    使っていたため、二人の場面でもう一人の見た目まで最初の1人に寄ってしまっていた。
    """
    refs: list[CharacterReferenceInput] = []
    for character in characters:
        path = character.get("reference_image_path")
        if path and (_PROJECT_ROOT / path).is_file():
            refs.append(
                CharacterReferenceInput(
                    image_b64=reference_image_b64((_PROJECT_ROOT / path).read_bytes()),
                    strength=req.reference_strength,
                    fidelity=req.reference_fidelity,
                )
            )
    return refs


# 人数のタグ。一人ずつの容姿にある「1girl」「1boy」を数えて、二人以上の場面の全体のプロンプトに入れる
_COUNT_TAGS = {"1girl": "girls", "1boy": "boys", "1other": "others"}


def _cast_prompt(scene_characters: list[dict[str, Any]]) -> tuple[list[str], list[str], str]:
    """
    (キャラごとの容姿, キャラごとのネガティブ, 全体のプロンプトに足す人数タグ)。
    キャラごとの容姿には、そのシーンでのそのキャラの表情・動作(action_tags)も入れる。全体のタグに書くと
    仕草や表情が全員に付いてしまう(実機: 先生の「あごに手」を栄子もしていた)。
    一人なら従来どおり。二人以上なら各キャラの「solo」を外し(一人しか描かれなくなる)、
    「2girls」のような人数を全体に足す。
    """
    pairs = [
        (join_tags(character_prompt_tags(c), c.get("action_tags")), c.get("negative_tags") or "")
        for c in scene_characters
    ]
    pairs = [(tags, negative) for tags, negative in pairs if tags][:_MAX_CHARACTERS]
    if len(pairs) < 2:
        return [tags for tags, _ in pairs], [negative for _, negative in pairs], ""
    counts: dict[str, int] = {}
    stripped: list[str] = []
    for tags, _ in pairs:
        items = [t.strip() for t in tags.split(",") if t.strip()]
        for item in items:
            if item in _COUNT_TAGS:
                counts[item] = counts.get(item, 0) + 1
        stripped.append(", ".join(t for t in items if t != "solo"))
    count_tags = ", ".join(f"{n}{_COUNT_TAGS[tag]}" if n > 1 else tag for tag, n in counts.items())
    return stripped, [negative for _, negative in pairs], count_tags


async def _run_panels(
    job: _Job, story_id: int, req: MangaV2PanelsRequest, api_key: str, targets: list[dict[str, Any]]
) -> None:
    settings = req.settings
    # 性的な場面は場面ごとに未成年対策のネガティブを足すので、ここでは基本形だけ作る
    conn = get_connection()
    try:
        characters = characters_by_scene(conn, story_id)
        layouts = get_manga_v2_page_layouts(conn, story_id)
    finally:
        conn.close()
    # コマの形(生成サイズを決める)。写したコマ割りがあればそれ、無ければテンプレート
    rects = scene_rects(layouts, req.template, max(s["scene_index"] for s in targets) + 1)

    _PANEL_DIR.mkdir(parents=True, exist_ok=True)
    job.total = len(targets)
    for i, scene in enumerate(targets, start=1):
        job.message = f"シーン{scene['scene_index'] + 1}のコマを生成中 ({i}/{len(targets)})"
        width, height = generation_size(rects[scene["scene_index"]])
        # そのシーンに出るキャラだけの容姿を渡す(v1はページ内の全員をまとめていた)
        # (キャラシートの普段の服装も含む)
        scene_characters = characters.get(scene["id"], [])
        character_tags, character_negatives, count_tags = _cast_prompt(scene_characters)
        # 二人以上なら、キャラごとのネガティブはキャラの欄に分ける(全体にまとめると打ち消し合う)
        multiple = len(character_tags) > 1
        # シードの優先順: 設定で指定 > キャラシートの基準シード > シーンごとに変える
        seed = settings.seed
        if seed is None:
            seed = characters_seed(scene_characters)
        if seed is None:
            seed = story_id * 1000 + scene["scene_index"]
        elif req.vary_seed:
            seed = (seed + scene["scene_index"] * _SEED_STEP) % 4294967296
        references = _scene_references(scene_characters, req) if req.use_character_reference else []
        if references:
            job.message += f"(キャラ参照{len(references)}人・V4.5)"
        sexual = is_sexual(scene["draft_prompt_tags"])
        image = await generate_image_v5(
            api_key,
            build_panel_prompt(
                join_tags(count_tags, scene["draft_prompt_tags"]), color=req.color, complexity=settings.complexity
            ),
            join_tags(
                build_panel_negative(settings.negative_prompt, color=req.color, sexual=sexual),
                "" if multiple else characters_negative(scene_characters),
            ),
            model=_REFERENCE_MODEL if references else settings.model,
            width=width,
            height=height,
            steps=settings.steps,
            scale=settings.scale,
            sampler=settings.sampler,
            noise_schedule=settings.noise_schedule,
            cfg_rescale=settings.cfg_rescale,
            seed=seed,
            character_tags=character_tags,
            character_reference=references,
            character_negatives=character_negatives if multiple else None,
        )
        filename = f"story{story_id}_scene{scene['scene_index']}_{uuid4().hex[:8]}.png"
        (_PANEL_DIR / filename).write_bytes(image)

        conn = get_connection()
        try:
            upsert_manga_panel(
                conn, story_id, scene["id"], f"outputs/manga/panels/{filename}", seed, width, height
            )
        finally:
            conn.close()
        job.progress = i

    job.message = f"{len(targets)}コマの絵を生成しました"


def _compose_inputs(
    story_id: int, req: MangaV2ComposeRequest
) -> tuple[list[PanelContent], LetteringStyle, dict[str, int]]:
    """
    合成に渡すコマ(シーン順)と文字の設定を DB から組み立てる。合成とダウンロードで共用する。
    冒頭だけ試せるよう、絵がある最後のシーンまでを対象にする(途中の未生成コマは灰色)。
    3つ目は PanelContent.key(シーンID) → シーン番号(0始まり)。ダウンロードのファイル名に使う。
    """
    _require_template(req.template)
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
        panels = {p["scene_id"]: p for p in list_manga_panels(conn, story_id)}
        overrides = get_manga_v2_overrides(conn, story_id)
        sfx_fonts = get_manga_v2_sfx_fonts(conn, story_id)
        stamp_paths = {
            word: _PROJECT_ROOT / stamp["image_path"]
            for word, stamp_id in get_manga_v2_sfx_stamps(conn, story_id).items()
            if (stamp := get_stamp(conn, stamp_id)) is not None
        }
    finally:
        conn.close()
    installed = {f.id: f.path for f in available_fonts()}
    style = LetteringStyle(
        font_path=resolve_font(req.font),
        sfx_font_path=resolve_font(req.sfx_font, DEFAULT_SFX_FONT_ID),
        bubble_opacity=req.bubble_opacity,
        overrides={key: (pos[0], pos[1]) for key, pos in overrides.items() if not key.startswith(_SCALE_PREFIX)},
        scales={key[len(_SCALE_PREFIX) :]: pos[0] for key, pos in overrides.items() if key.startswith(_SCALE_PREFIX)},
        text_scale=req.text_scale,
        sfx_scale=req.sfx_scale,
        # 消したフォントを指していても合成は止めず、既定の効果音フォントで描く
        sfx_font_paths={word: installed[fid] for word, fid in sfx_fonts.items() if fid in installed},
        sfx_stamps=stamp_paths,
    )
    if not panels:
        raise HTTPException(status_code=400, detail="先にコマの絵を生成してください。")

    last = max(s["scene_index"] for s in scenes if s["id"] in panels)
    contents: list[PanelContent] = []
    for s in scenes:
        if s["scene_index"] > last:
            continue
        speakers, cast = _scene_speakers(s)
        contents.append(
            PanelContent(
                _PROJECT_ROOT / panels[s["id"]]["image_path"] if s["id"] in panels else None,
                *_lettering(s),
                key=str(s["id"]),
                narration=s.get("narration") or "",
                speakers=speakers,
                cast=cast,
            )
        )
    return contents, style, {str(s["id"]): s["scene_index"] for s in scenes}


def _page_layouts(story_id: int) -> list[Any]:
    conn = get_connection()
    try:
        return get_manga_v2_page_layouts(conn, story_id)
    finally:
        conn.close()


def _split(contents: list[PanelContent], max_lines: int, layouts: list[Any]) -> list[PanelContent]:
    """
    セリフの多いシーンを寄りのコマに分ける。写したコマ割りを使うときは分けない(コマが増えると、
    元の作品のページの切れ目とずれるため)。
    """
    return split_dense_panels(contents, 0 if layouts else max_lines)


@router.put("/{story_id}/page-layouts")
async def put_page_layouts(story_id: int, req: MangaV2PageLayoutsRequest) -> dict[str, Any]:
    """
    取り込んだ作品(/api/manga-import)のページごとのコマ割りを、この物語のコマ割りにする(import_id が
    null ならテンプレートに戻す)。シーンは先頭から順に、写したページのコマに入る。
    """
    from .manga_import import _page_path  # 取り込みのページ画像の場所(循環を避けて使うときに読む)

    conn = get_connection()
    try:
        if get_story(conn, story_id) is None:
            raise HTTPException(status_code=404, detail="story not found")
        if req.import_id is None:
            set_manga_v2_page_layouts(conn, story_id, None)
            return {"pages": 0, "panels": 0}
        item = get_manga_import(conn, req.import_id)
    finally:
        conn.close()
    if item is None:
        raise HTTPException(status_code=404, detail="import not found")
    pages = (item.get("analysis") or {}).get("pages") or []
    if not pages:
        raise HTTPException(status_code=409, detail="取り込んだ作品のコマをまだ読み取っていません。")
    layouts = []
    for index, page in enumerate(pages):
        # コマの枠の形(斜め・枠なし・裁ち落とし)と見開きかどうかは、ページの画像から読む
        with Image.open(_page_path(req.import_id, index)) as image:
            layout = trace_page(image, [tuple(p["box"]) for p in page["panels"]])
        if layout["panels"]:
            layouts.append(layout)
    conn = get_connection()
    try:
        set_manga_v2_page_layouts(conn, story_id, layouts)
    finally:
        conn.close()
    return {
        "pages": len(layouts),
        "panels": sum(len(layout["panels"]) for layout in layouts),
        "spreads": sum(1 for layout in layouts if layout["spread"]),
    }


# 画像処理で数秒かかるので、同期関数にしてスレッドプールで実行させる(イベントループを塞がない)
@router.post("/{story_id}/compose", response_model=MangaV2ComposeResponse)
def compose(story_id: int, req: MangaV2ComposeRequest) -> dict[str, Any]:
    """コマの絵をテンプレートに嵌め込み、セリフを吹き出しで描いてページにする。"""
    contents, style, _ = _compose_inputs(story_id, req)
    layouts = _page_layouts(story_id)
    composed = compose_pages(req.template, _split(contents, req.max_lines_per_panel, layouts), style, layouts)
    pages = [page for page, _ in composed]

    _PAGE_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex[:8]
    page_paths: list[str] = []
    for index, page in enumerate(pages):
        filename = f"story{story_id}_page{index}_{token}.png"
        (_PAGE_DIR / filename).write_bytes(page_png(page))
        page_paths.append(f"outputs/manga/v2/{filename}")
    final_name = f"story{story_id}_final_{token}.png"
    (_PAGE_DIR / final_name).write_bytes(concat_pages(pages))
    final_path = f"outputs/manga/v2/{final_name}"

    conn = get_connection()
    try:
        update_story_final_image(conn, story_id, final_path)
        # ダウンロードやスタジオを開き直したときに、この見た目を再現できるよう物語ごとに残す
        set_manga_v2_compose_settings(conn, story_id, req.model_dump())
    finally:
        conn.close()
    return {
        "pages": page_paths,
        "final_image_path": final_path,
        "page_width": PAGE_WIDTH,
        "page_height": PAGE_HEIGHT,
        # 見開きのページは横2ページ分の幅になる
        "page_widths": [page.width for page in pages],
        "elements": [
            [
                {
                    "key": e.key,
                    "kind": e.kind,
                    "text": e.text,
                    "box": list(e.box),
                    "panel": list(e.panel),
                    "moved": e.key in style.overrides,
                    "scale": style.scales.get(e.key),
                }
                for e in elements
            ]
            for _, elements in composed
        ],
    }


# ---- ダウンロード ----

# zip 内のフォルダ名・PDF のファイル名
_DOWNLOAD_NAMES: dict[str, str] = {
    "pages": "pages",
    "pages_clean": "pages_no_text",
    "panels": "panels",
    "panels_clean": "panels_no_text",
}
# PDF の1ページの解像度(dpi)。ページ画像 1200×1700 px が A4 よりやや大きい程度になる。
_PDF_RESOLUTION = 150.0
_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _download_images(
    kind: str,
    contents: list[PanelContent],
    style: LetteringStyle,
    scene_numbers: dict[str, int],
    req: MangaV2DownloadRequest,
    layouts: list[Any] | None = None,
) -> list[tuple[str, Image.Image]]:
    """ダウンロードの1種類分を (ファイル名, 画像) の並びで作る。ページ・コマの並びは合成と同じ。"""
    split = _split(contents, req.max_lines_per_panel, layouts or [])
    if kind in ("pages", "pages_clean"):
        if kind == "pages_clean":
            # 寄りのコマもセリフありと同じ並びにするため、分けた後で文字だけを外す
            split = [replace(c, dialogue=[], sfx=[], narration="") for c in split]
        pages = [page for page, _ in compose_pages(req.template, split, style, layouts)]
        width = max(2, len(str(len(pages))))
        return [(f"page_{i + 1:0{width}d}.png", page) for i, page in enumerate(pages)]

    if kind == "panels_clean":
        result: list[tuple[str, Image.Image]] = []
        for name, path in _clean_panel_files(contents, scene_numbers):
            with Image.open(path) as src:
                result.append((name, src.convert("RGB")))
        return result
    width = _scene_number_width(scene_numbers)
    return [
        (
            f"scene_{scene_numbers[c.key] + 1:0{width}d}{f'_{c.zoom_step + 1}' if c.zoom_step else ''}.png",
            compose_panel(c, style),
        )
        for c in split
        if c.image_path is not None and c.image_path.is_file()
    ]


def _scene_number_width(scene_numbers: dict[str, int]) -> int:
    return max(2, len(str(max(scene_numbers.values()) + 1)))


def _clean_panel_files(contents: list[PanelContent], scene_numbers: dict[str, int]) -> list[tuple[str, Path]]:
    """生成したコマの絵そのもの(セリフなし)のファイル。シーンごとに1枚。"""
    width = _scene_number_width(scene_numbers)
    return [
        (f"scene_{scene_numbers[c.key] + 1:0{width}d}.png", c.image_path)
        for c in contents
        if c.image_path is not None and c.image_path.is_file()
    ]


def _pdf_bytes(images: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=_PDF_RESOLUTION)
    return buf.getvalue()


def _attachment_header(story_id: int, title: str | None, extension: str) -> str:
    """ASCII の filename と、日本語のタイトルを入れた filename*(RFC 5987)の両方を付ける。"""
    fallback = f"manga_story{story_id}.{extension}"
    name = _FILENAME_UNSAFE_RE.sub("_", (title or "").strip())[:80]
    if not name:
        return f'attachment; filename="{fallback}"'
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(f'{name}.{extension}')}"


@router.post("/{story_id}/make", response_model=StoryJobResponse)
async def make_manga(story_id: int, req: MangaV2MakeRequest, client: ClientDep) -> dict[str, Any]:
    """
    まだ絵の無いコマを生成し、続けてページに合成する(1つのジョブ)。コマの生成だけをジョブにして合成を
    画面から呼ぶと、生成中に画面を開き直したときに合成が行われないまま終わってしまう(実機で確認)。
    進捗は GET /api/story/{id}/job、できた漫画は物語の final_image_path で見る。
    """
    _require_template(req.panels.template)
    _require_template(req.compose.template)
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
        existing = {p["scene_id"] for p in list_manga_panels(conn, story_id)}
    finally:
        conn.close()
    if not scenes:
        raise HTTPException(status_code=404, detail="story not found")
    targets = [s for s in scenes if s["id"] not in existing]
    api_key = client.api_key

    async def runner(job: _Job) -> None:
        if targets:
            await _run_panels(job, story_id, req.panels, api_key, targets)
        job.message = "ページに合成しています"
        result = await asyncio.to_thread(compose, story_id, req.compose)
        job.message = f"漫画ができました({len(result['pages'])}ページ)"

    return _job_response(_start_job(story_id, "manga", runner))


@router.post("/{story_id}/download")
def download(story_id: int, req: MangaV2DownloadRequest) -> Response:
    """
    最後に合成したときの設定でページ/コマを作り直し、PDF か zip にまとめて返す。DB や合成済みのページは変えない。
    まだ合成していない物語ではリクエストの設定を使う。
    zip は選んだ種類ごとのフォルダに PNG を入れる。PDF は1種類ならその PDF、複数なら PDF をまとめた zip。
    """
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        saved = get_manga_v2_compose_settings(conn, story_id)
    finally:
        conn.close()
    if saved:
        # 端末ごとの画面の設定ではなく、最後に合成したレイアウトにそろえる
        req = req.model_copy(update=MangaV2ComposeRequest.model_validate(saved).model_dump())
    contents, style, scene_numbers = _compose_inputs(story_id, req)
    layouts = _page_layouts(story_id)
    title = (story or {}).get("title")
    kinds = list(dict.fromkeys(req.contents))  # 重複を除き、選んだ順を保つ

    if req.format == "pdf" and len(kinds) == 1:
        images = _download_images(kinds[0], contents, style, scene_numbers, req, layouts)
        return Response(
            _pdf_bytes([image for _, image in images]),
            media_type="application/pdf",
            headers={"Content-Disposition": _attachment_header(story_id, title, "pdf")},
        )

    buf = io.BytesIO()
    # PNG/PDF は既に圧縮されているので、zip では圧縮し直さない
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
        for kind in kinds:
            folder = _DOWNLOAD_NAMES[kind]
            if req.format == "zip" and kind == "panels_clean":
                # 生成した PNG をそのまま入れる(作り直さないので速く、NovelAI の生成情報も残る)
                for name, path in _clean_panel_files(contents, scene_numbers):
                    archive.write(path, f"{folder}/{name}")
                continue
            images = _download_images(kind, contents, style, scene_numbers, req, layouts)
            if req.format == "pdf":
                archive.writestr(f"{folder}.pdf", _pdf_bytes([image for _, image in images]))
            else:
                for name, image in images:
                    archive.writestr(f"{folder}/{name}", page_png(image))
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _attachment_header(story_id, title, "zip")},
    )


# ---- 効果音ごとのフォント ----


@router.get("/{story_id}/sfx-fonts")
async def get_sfx_fonts(story_id: int) -> dict[str, str]:
    conn = get_connection()
    try:
        return get_manga_v2_sfx_fonts(conn, story_id)
    finally:
        conn.close()


@router.put("/{story_id}/sfx-fonts", status_code=204)
async def put_sfx_font(story_id: int, req: MangaV2SfxFontRequest) -> None:
    conn = get_connection()
    try:
        fonts = get_manga_v2_sfx_fonts(conn, story_id)
        if req.font:
            fonts[req.word] = req.font
        else:
            fonts.pop(req.word, None)
        set_manga_v2_sfx_fonts(conn, story_id, fonts)
    finally:
        conn.close()


_SFX_FONT_BATCH_SIZE = 20


def _sfx_font_system_prompt(fonts: list[Any]) -> str:
    catalog = "\n".join(f"- {f.id}: {f.mood}" for f in fonts)
    return (
        "あなたは漫画の描き文字(効果音)に合うフォントを選ぶアシスタントです。\n"
        "効果音の一覧が渡されます。それぞれに、次のフォントの中から最も雰囲気が合うものを1つ選び、"
        "JSON形式で返してください。fontには必ず下のIDのどれかを書くこと。\n\n"
        f"{catalog}"
    )


def _sfx_font_json_schema(font_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "choices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"word": {"type": "string"}, "font": {"type": "string", "enum": font_ids}},
                    "required": ["word", "font"],
                },
            }
        },
        "required": ["choices"],
    }


async def _choose_sfx_fonts(words: list[str], fonts: list[Any]) -> dict[str, str]:
    font_ids = [f.id for f in fonts]
    for _ in range(_SFX_MAX_ATTEMPTS):
        parts: list[str] = []
        async for delta in stream_llm_text(
            [
                {"role": "system", "content": _sfx_font_system_prompt(fonts)},
                {"role": "user", "content": "効果音: " + "、".join(words)},
            ],
            max_tokens=_SFX_MAX_TOKENS,
            json_schema=_sfx_font_json_schema(font_ids),
        ):
            parts.append(delta)
        try:
            choices = json.loads(strip_think_tags("".join(parts))).get("choices")
        except (json.JSONDecodeError, AttributeError):
            continue
        if not isinstance(choices, list):
            continue
        result = {
            str(c.get("word", "")).strip(): str(c.get("font", ""))
            for c in choices
            if isinstance(c, dict) and str(c.get("word", "")).strip() in words and c.get("font") in font_ids
        }
        if result:
            return result
    return {}


_MONOCHROME_CACHE: dict[str, bool] = {}


def _stamp_is_monochrome(stamp: dict[str, Any]) -> bool:
    path = stamp["image_path"]
    if path not in _MONOCHROME_CACHE:
        try:
            with Image.open(_PROJECT_ROOT / path) as image:
                _MONOCHROME_CACHE[path] = is_monochrome(image)
        except OSError:
            _MONOCHROME_CACHE[path] = False
    return _MONOCHROME_CACHE[path]


def normalize_reading(text: str) -> str:
    """
    効果音の読みを比べるための正規化。ひらがな/カタカナ、促音・長音・三点リーダ・
    記号の違いは同じとみなす(「ギィ……」と「ぎぃ」、「ドキッ」と「ドキ」を同じ語として扱う)。
    """
    text = unicodedata.normalize("NFKC", text)
    text = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)
    return re.sub(r"[っーｰ〜~…・.!！?？♥♡\s]", "", text)


@router.post("/{story_id}/suggest-sfx-fonts", response_model=StoryJobResponse)
async def suggest_sfx_fonts(story_id: int, req: MangaV2SuggestSfxRequest) -> dict[str, Any]:
    """
    範囲内のシーンの効果音それぞれの描き文字を決める。読みが一致するスタンプがあれば
    スタンプを割り当て、無ければダウンロード済みの描き文字フォントから合うものをAIに選ばせる。
    """
    fonts = [f for f in CATALOG if f.installed]
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
        current_fonts = get_manga_v2_sfx_fonts(conn, story_id)
        current_stamps = get_manga_v2_sfx_stamps(conn, story_id)
        excluded = set() if req.include_adult else adult_stamp_sources(conn)
        labeled = [st for st in list_stamps(conn) if st["label"] and st["source_key"] not in excluded]
    finally:
        conn.close()
    words: list[str] = []
    for scene in scenes:
        if scene["scene_index"] < req.scene_from or (req.scene_to is not None and scene["scene_index"] > req.scene_to):
            continue
        for word in _lettering(scene)[1]:
            decided = word in current_fonts or word in current_stamps
            if word not in words and (req.overwrite or not decided):
                words.append(word)
    if not words:
        raise HTTPException(status_code=400, detail="描き文字を選ぶ効果音がありません(範囲内は選択済みです)。")

    # 読みが一致するスタンプ。表記まで同じもの(「ビクッ」に「ビクッ」)を優先し、無ければ
    # 正規化して一致するもの(「ビクッ」に「ビク」)。同じ読みが複数あれば先に取り込んだもの。
    # 色違いの素材集では同じ読みのスタンプが何十個もあるので、モノクロ漫画に合う
    # 無彩色(黒・白)のものを先に並べておく(先に見つかったものを採用するため)。
    stamp_by_label: dict[str, int] = {}
    stamp_by_reading: dict[str, int] = {}
    # 初回は画像を開いて判定するので、イベントループを塞がないようスレッドで並べ替える
    ordered = await asyncio.to_thread(sorted, labeled, key=lambda st: not _stamp_is_monochrome(st))
    for stamp in ordered:
        stamp_by_label.setdefault(stamp["label"], stamp["id"])
        stamp_by_reading.setdefault(normalize_reading(stamp["label"]), stamp["id"])
    stamp_choices = {
        w: stamp_by_label.get(w) or stamp_by_reading[normalize_reading(w)]
        for w in words
        if w in stamp_by_label or normalize_reading(w) in stamp_by_reading
    }
    font_words = [w for w in words if w not in stamp_choices]
    if font_words and not fonts:
        raise HTTPException(status_code=400, detail="一致するスタンプの無い効果音があります。先に描き文字フォントをダウンロードしてください。")

    async def runner(job: _Job) -> None:
        if stamp_choices:
            conn = get_connection()
            try:
                mapping = get_manga_v2_sfx_stamps(conn, story_id)
                mapping.update(stamp_choices)
                set_manga_v2_sfx_stamps(conn, story_id, mapping)
            finally:
                conn.close()
        batches = [font_words[i : i + _SFX_FONT_BATCH_SIZE] for i in range(0, len(font_words), _SFX_FONT_BATCH_SIZE)]
        job.total = len(batches)
        chosen = 0
        for index, batch in enumerate(batches, start=1):
            job.message = f"効果音のフォントを選んでいます {index}/{len(batches)}"
            result = await _choose_sfx_fonts(batch, fonts)
            if result:
                conn = get_connection()
                try:
                    mapping = get_manga_v2_sfx_fonts(conn, story_id)
                    mapping.update(result)
                    set_manga_v2_sfx_fonts(conn, story_id, mapping)
                    # フォントに決めた語は、以前のスタンプの割り当てを外す(上書き時)
                    stamps = get_manga_v2_sfx_stamps(conn, story_id)
                    for word in result:
                        stamps.pop(word, None)
                    set_manga_v2_sfx_stamps(conn, story_id, stamps)
                finally:
                    conn.close()
                chosen += len(result)
            job.progress = index
        job.message = (
            f"{len(words)}個の効果音のうち、スタンプ{len(stamp_choices)}個・フォント{chosen}個を選びました"
        )

    return _job_response(_start_job(story_id, "sfx_fonts", runner))


# 個別の大きさは、位置の手動配置と同じ保存先(stories.manga_v2_overrides)に、この接頭辞を付けたキーで持つ
_SCALE_PREFIX = "scale:"


@router.put("/{story_id}/scales", status_code=204)
async def put_scale(story_id: int, req: MangaV2ScaleRequest) -> None:
    """吹き出し/描き文字1つの大きさを保存する(scale が None なら自動に戻す)。"""
    conn = get_connection()
    try:
        overrides = get_manga_v2_overrides(conn, story_id)
        if req.scale is None:
            overrides.pop(_SCALE_PREFIX + req.key, None)
        else:
            overrides[_SCALE_PREFIX + req.key] = [round(req.scale, 3)]
        set_manga_v2_overrides(conn, story_id, overrides)
    finally:
        conn.close()


@router.put("/{story_id}/overrides", status_code=204)
async def put_override(story_id: int, req: MangaV2OverrideRequest) -> None:
    """吹き出し/描き文字を手で動かした位置を保存する(x, y が None なら自動配置に戻す)。"""
    conn = get_connection()
    try:
        overrides = get_manga_v2_overrides(conn, story_id)
        if req.x is None or req.y is None:
            overrides.pop(req.key, None)
        else:
            overrides[req.key] = [round(req.x, 4), round(req.y, 4)]
        set_manga_v2_overrides(conn, story_id, overrides)
    finally:
        conn.close()


@router.delete("/{story_id}/overrides", status_code=204)
async def clear_overrides(story_id: int) -> None:
    conn = get_connection()
    try:
        set_manga_v2_overrides(conn, story_id, {})
    finally:
        conn.close()
