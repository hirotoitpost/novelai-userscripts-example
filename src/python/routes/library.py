"""
ギャラリー(生成した画像の一覧)と本棚(物語・漫画を読む)。

画像は生成元ごとに別の表・フォルダにあるので、ここで1つの一覧にまとめて返す。件数は数百程度なので、
全件を読んでから Python で絞り込み・並べ替え・ページ分けをする。
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

from ..db import (
    delete_generation_entry,
    delete_manga_page,
    delete_manga_panel,
    delete_story,
    forget_library_items,
    get_connection,
    get_generation_entry,
    get_story,
    list_adult_marks,
    list_bookmarks,
    list_generation_history,
    list_library_pages,
    list_library_panels,
    list_library_stories,
    list_scene_tags,
    list_story_texts,
    set_adult_mark,
    set_bookmark,
    set_generation_image_paths,
)
from ..manga_v2.prompt import is_sexual
from .story import _jobs

router = APIRouter(prefix="/api/library", tags=["library"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_OUTPUTS = _PROJECT_ROOT / "outputs"
# 配信してよいフォルダ(生成履歴と漫画)。LoRA の学習データなどは出さない。
_SERVED_DIRS = (_OUTPUTS / "history", _OUTPUTS / "manga")
_THUMB_DIR = _OUTPUTS / ".thumbs"
_THUMB_WIDTHS = (240, 360, 480, 720)
_PDF_RESOLUTION = 150.0
_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# 前提文の頭書き。例: 「[インポート] 本文…」「[エディタ] 題」
_ORIGIN_RE = re.compile(r"^\[([^\]]{1,10})\]\s*(.*)$", re.DOTALL)

ImageSource = Literal["generate", "panel", "illustration"]
# 一覧の絞り込み: すべて / 一般向けだけ / 成人向けだけ
Rating = Literal["all", "general", "adult"]
# 漫画の性的な場面の判定(is_sexual)に加えて、成人向けを示すタグ
_EXTRA_ADULT_RE = re.compile(r"\b(explicit|uncensored|hentai|r-?18)\b", re.IGNORECASE)
# 本文で成人向けを判定する語。普通の文章にも出うる語(「挿入」「快楽」など)は入れず、
# 1語だけで決めないよう _ADULT_TEXT_MIN_HITS 回以上出てきたときだけ成人向けとみなす。
_ADULT_TEXT_RE = re.compile(
    r"セックス|膣|陰茎|陰核|ペニス|ちんぽ|ちんちん|おちんぽ|まんこ|クリトリス|乳首|愛液|精液|射精|中出し|"
    r"フェラ|手マン|潮吹き|全裸|性器|勃起|肉棒|秘部|秘所|アナル|肛門|ディルド|バイブ|絶頂|喘ぎ|喘いで|"
    r"イっちゃ|イッちゃ|イク[ッっ！!]"
)
_ADULT_TEXT_MIN_HITS = 3


# ---- 共通 ----


def _served_path(path: str) -> Path:
    """リポジトリルート相対のパスを、配信してよいフォルダの中にある実ファイルに直す。"""
    full = (_PROJECT_ROOT / path).resolve()
    if not any(full.is_relative_to(d.resolve()) for d in _SERVED_DIRS):
        raise HTTPException(status_code=403, detail="invalid path")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return full


def _attachment(name: str, extension: str, fallback: str) -> str:
    """ASCII の filename と、日本語名を入れた filename*(RFC 5987)の両方を付ける。"""
    safe = _FILENAME_UNSAFE_RE.sub("_", name.strip())[:80]
    header = f'attachment; filename="{fallback}.{extension}"'
    return (
        f"{header}; filename*=UTF-8''{quote(f'{safe}.{extension}')}" if safe else header
    )


@router.get("/file")
def get_file(path: str, download: bool = False) -> FileResponse:
    full = _served_path(path)
    return FileResponse(
        full, media_type="image/png", filename=full.name if download else None
    )


@router.get("/thumb")
def get_thumb(path: str, w: int = 360) -> FileResponse:
    """
    一覧用の縮小画像(WebP)。元画像は1枚1〜2MBあり、スマホで数十枚並べると重いので縮小して返す。
    作った縮小画像は outputs/.thumbs に残し、元画像が更新されたら(更新日時が変われば)作り直す。
    """
    full = _served_path(path)
    width = min(_THUMB_WIDTHS, key=lambda candidate: abs(candidate - w))
    digest = hashlib.sha1(
        f"{full}:{full.stat().st_mtime_ns}:{width}".encode()
    ).hexdigest()
    thumb = _THUMB_DIR / f"{digest}.webp"
    if not thumb.is_file():
        _THUMB_DIR.mkdir(parents=True, exist_ok=True)
        with Image.open(full) as src:
            img = src.convert("RGB")
            if img.width > width:
                img = img.resize(
                    (width, round(img.height * width / img.width)),
                    Image.Resampling.LANCZOS,
                )
            tmp = thumb.with_suffix(".tmp")
            img.save(tmp, format="WEBP", quality=80)
            tmp.replace(thumb)
    # 内容はパスと更新日時で決まるので、ブラウザに長くキャッシュさせてよい
    return FileResponse(
        thumb,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=604800"},
    )


class BookmarkRequest(BaseModel):
    kind: Literal["image", "book"]
    key: str = Field(min_length=1, max_length=200)
    bookmarked: bool


@router.put("/bookmarks", status_code=204)
def put_bookmark(req: BookmarkRequest) -> None:
    conn = get_connection()
    try:
        set_bookmark(conn, req.kind, req.key, req.bookmarked)
    finally:
        conn.close()


class AdultRequest(BaseModel):
    kind: Literal["image", "book"]
    key: str = Field(min_length=1, max_length=200)
    # True/False で手動指定、None で手動指定を外して自動判定に戻す
    adult: bool | None


@router.put("/adult", status_code=204)
def put_adult(req: AdultRequest) -> None:
    conn = get_connection()
    try:
        set_adult_mark(conn, req.kind, req.key, req.adult)
    finally:
        conn.close()


# ---- 成人向けの判定 ----


def _tags_adult(tags: str | None) -> bool:
    return bool(tags) and (is_sexual(tags) or bool(_EXTRA_ADULT_RE.search(tags or "")))


def _story_adult() -> tuple[dict[int, bool], dict[int, bool]]:
    """
    物語ごとの (自動判定, 手動指定込みの判定)。どれか1シーンでも性的なタグがあるか、本文に露骨な語が
    _ADULT_TEXT_MIN_HITS 回以上出てくれば成人向けとみなす。外れていれば手動で指定してもらう。
    """
    conn = get_connection()
    try:
        scenes = list_scene_tags(conn)
        texts = list_story_texts(conn)
        marks = list_adult_marks(conn, "book")
    finally:
        conn.close()
    auto: dict[int, bool] = {}
    for row in scenes:
        if not auto.get(row["story_id"]) and _tags_adult(row["draft_prompt_tags"]):
            auto[row["story_id"]] = True
    # タグの無い物語(取り込んだだけ・本文だけ)は本文で判定する
    for row in texts:
        if (
            not auto.get(row["story_id"])
            and len(_ADULT_TEXT_RE.findall(row["text"] or "")) >= _ADULT_TEXT_MIN_HITS
        ):
            auto[row["story_id"]] = True
    effective = dict(auto)
    for key, adult in marks.items():
        if key.isdigit():
            effective[int(key)] = adult
    return auto, effective


def _matches_rating(adult: bool, rating: Rating) -> bool:
    return rating == "all" or adult == (rating == "adult")


# ---- ギャラリー ----


class GalleryImage(BaseModel):
    # 生成元ごとの識別子。history:{履歴ID}:{n} / panel:{コマID} / illustration:{ページID}
    key: str
    source: ImageSource
    path: str
    created_at: str
    width: int | None = None
    height: int | None = None
    prompt: str = ""
    seed: int | None = None
    model: str | None = None
    story_id: int | None = None
    story_title: str | None = None
    # コマならシーン番号、挿絵ならページ番号(0始まり)
    index: int | None = None
    label: str | None = None
    bookmarked: bool = False
    # 成人向けか(手動指定があればそれ、無ければ自動判定)。adult_auto は自動判定、adult_manual は手動指定
    adult: bool = False
    adult_auto: bool = False
    adult_manual: bool | None = None


class GalleryResponse(BaseModel):
    items: list[GalleryImage]
    total: int
    # 絞り込み用: 画像がある物語(新しい順)
    stories: list[dict[str, Any]]


def _parse_size(size: str) -> tuple[int | None, int | None]:
    """「832x1216」や「[832, 1216]」の形。portrait などのプリセット名は大きさが分からないので None。"""
    match = re.fullmatch(r"\s*\[?\s*(\d+)\s*[x×,]\s*(\d+)\s*\]?\s*", size or "")
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _history_key(entry_id: int, path: str) -> str:
    # 1回の生成の何枚目かではなくファイル名で識別する(1枚消しても他の画像のキーが変わらない)
    return f"history:{entry_id}:{Path(path).name}"


def _gallery_images() -> list[GalleryImage]:
    conn = get_connection()
    try:
        history = list_generation_history(conn, limit=100000)
        panels = list_library_panels(conn)
        pages = list_library_pages(conn)
        marks = list_bookmarks(conn, "image")
        adult_marks = list_adult_marks(conn, "image")
    finally:
        conn.close()
    _, story_adult = _story_adult()

    items: list[GalleryImage] = []
    for row in history:
        width, height = _parse_size(row["size"])
        for path in row["image_paths"]:
            items.append(
                GalleryImage(
                    key=_history_key(row["id"], path),
                    source="generate",
                    path=path,
                    created_at=row["created_at"],
                    width=width,
                    height=height,
                    prompt=row["prompt"],
                    seed=row["seed"],
                    model=row["model"],
                )
            )
    for row in panels:
        items.append(
            GalleryImage(
                key=f"panel:{row['id']}",
                source="panel",
                path=row["image_path"],
                created_at=row["created_at"],
                width=row["width"],
                height=row["height"],
                prompt=row["draft_prompt_tags"] or "",
                seed=row["seed"],
                story_id=row["story_id"],
                story_title=row["story_title"],
                index=row["scene_index"],
                label=row["draft_title"],
            )
        )
    for row in pages:
        items.append(
            GalleryImage(
                key=f"illustration:{row['id']}",
                source="illustration",
                path=row["image_path"],
                created_at=row["created_at"],
                seed=row["seed"],
                story_id=row["story_id"],
                story_title=row["story_title"],
                index=row["page_index"],
            )
        )
    for item in items:
        item.bookmarked = item.key in marks
        # 漫画のコマ・挿絵は、そのコマのタグに出ていなくても物語が成人向けなら成人向けに寄せる
        item.adult_auto = _tags_adult(item.prompt) or (
            item.story_id is not None and story_adult.get(item.story_id, False)
        )
        item.adult_manual = adult_marks.get(item.key)
        item.adult = (
            item.adult_manual if item.adult_manual is not None else item.adult_auto
        )
    # ファイルが消えているもの(手で消した等)は出さない
    return [item for item in items if (_PROJECT_ROOT / item.path).is_file()]


@router.get("/images", response_model=GalleryResponse)
def list_images(
    source: str | None = None,
    story_id: int | None = None,
    bookmarked: bool = False,
    rating: Rating = "all",
    q: str = "",
    sort: Literal["new", "old"] = "new",
    offset: int = 0,
    limit: int = 60,
) -> dict[str, Any]:
    """
    source はカンマ区切りで複数指定できる(generate,panel,illustration)。q はプロンプト・物語名・
    コマの題を空白区切りの AND で探す(大文字小文字は区別しない)。
    """
    items = _gallery_images()
    stories: dict[int, dict[str, Any]] = {}
    for item in sorted(items, key=lambda i: i.created_at, reverse=True):
        if item.story_id is not None and item.story_id not in stories:
            stories[item.story_id] = {"id": item.story_id, "title": item.story_title}

    if source:
        wanted = {s.strip() for s in source.split(",") if s.strip()}
        items = [i for i in items if i.source in wanted]
    if story_id is not None:
        items = [i for i in items if i.story_id == story_id]
    if bookmarked:
        items = [i for i in items if i.bookmarked]
    items = [i for i in items if _matches_rating(i.adult, rating)]
    for word in q.lower().split():
        items = [
            i
            for i in items
            if word in f"{i.prompt} {i.story_title or ''} {i.label or ''}".lower()
        ]
    # 同じ時刻のもの(1回の生成で複数枚)はキーの順に並べる
    items.sort(key=lambda i: (i.created_at, i.key), reverse=sort == "new")
    limit = max(1, min(limit, 500))
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "stories": list(stories.values()),
    }


class ImagesDownloadRequest(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=2000)


@router.post("/images/download")
def download_images(req: ImagesDownloadRequest) -> Response:
    """選んだ画像を元のPNGのままzipにまとめる(NovelAIの生成情報も残る)。"""
    by_key = {item.key: item for item in _gallery_images()}
    chosen = [by_key[key] for key in dict.fromkeys(req.keys) if key in by_key]
    if not chosen:
        raise HTTPException(status_code=404, detail="画像が見つかりません。")
    buf = io.BytesIO()
    # PNG は既に圧縮されているので、zip では圧縮し直さない
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
        for item in chosen:
            name = item.key.replace(":", "_")
            archive.write(_PROJECT_ROOT / item.path, f"{item.source}/{name}.png")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="gallery_{len(chosen)}images.zip"'
        },
    )


# ---- 本棚 ----


class Book(BaseModel):
    id: int
    title: str
    # 題の頭に付いていた出どころ(インポート/エディタ など)。無ければ None
    origin: str | None = None
    status: str
    created_at: str
    updated_at: str
    cover_path: str | None
    # 読める漫画の種類。v2=コマごとに生成して合成したページ、v1=V5でページごと生成した挿絵
    manga: Literal["v2", "v1"] | None
    page_count: int
    scene_count: int
    char_count: int
    bookmarked: bool = False
    adult: bool = False
    adult_auto: bool = False
    adult_manual: bool | None = None


def _v2_pages(final_image_path: str | None) -> list[str]:
    """
    漫画v2で最後に合成したページ。合成のたびに story{id}_page{n}_{token}.png と
    story{id}_final_{token}.png を書き出すので、完成画像と同じ token のページを集める。
    """
    if not final_image_path:
        return []
    match = re.fullmatch(
        r"outputs/manga/v2/story(\d+)_final_([0-9a-f]+)\.png", final_image_path
    )
    if not match:
        return []
    story_id, token = match.groups()
    found = []
    for path in (_OUTPUTS / "manga" / "v2").glob(f"story{story_id}_page*_{token}.png"):
        page = re.fullmatch(rf"story{story_id}_page(\d+)_{token}\.png", path.name)
        if page:
            found.append((int(page.group(1)), f"outputs/manga/v2/{path.name}"))
    return [p for _, p in sorted(found)]


def _book_pages(
    story: dict[str, Any], v1_pages: dict[int, list[str]]
) -> tuple[Literal["v2", "v1"] | None, list[str]]:
    pages = _v2_pages(story.get("final_image_path"))
    if pages:
        return "v2", pages
    pages = [p for p in v1_pages.get(story["id"], []) if (_PROJECT_ROOT / p).is_file()]
    return ("v1", pages) if pages else (None, [])


def _books() -> list[Book]:
    conn = get_connection()
    try:
        stories = list_library_stories(conn)
        v1_rows = list_library_pages(conn)
        marks = list_bookmarks(conn, "book")
        adult_marks = list_adult_marks(conn, "book")
    finally:
        conn.close()
    adult_auto, _ = _story_adult()
    v1_pages: dict[int, list[str]] = {}
    for row in v1_rows:
        v1_pages.setdefault(row["story_id"], []).append(row["image_path"])

    books = []
    for story in stories:
        manga, pages = _book_pages(story, v1_pages)
        panel = story["first_panel_path"]
        cover = (
            pages[0]
            if pages
            else (panel if panel and (_PROJECT_ROOT / panel).is_file() else None)
        )
        chars = (
            story["scene_chars"]
            if story["scene_count"]
            else len(story["raw_text"] or "")
        )
        # 題の無い物語は前提文を題にする。取り込み時に付けた「[インポート] 」などの頭書きは分けておく
        raw_title = (story["title"] or story["premise"] or "").strip()
        origin = _ORIGIN_RE.match(raw_title)
        books.append(
            Book(
                id=story["id"],
                title=(origin.group(2).strip() if origin else raw_title)
                or f"物語{story['id']}",
                origin=origin.group(1) if origin else None,
                status=story["status"],
                created_at=story["created_at"],
                updated_at=story["updated_at"] or story["created_at"],
                cover_path=cover,
                manga=manga,
                page_count=len(pages),
                scene_count=story["scene_count"],
                char_count=chars,
                bookmarked=str(story["id"]) in marks,
                adult=adult_marks.get(
                    str(story["id"]), adult_auto.get(story["id"], False)
                ),
                adult_auto=adult_auto.get(story["id"], False),
                adult_manual=adult_marks.get(str(story["id"])),
            )
        )
    return books


@router.get("/books", response_model=list[Book])
def list_books(
    kind: Literal["all", "manga", "text"] = "all",
    bookmarked: bool = False,
    rating: Rating = "all",
    q: str = "",
    sort: Literal["updated", "created", "title"] = "updated",
) -> list[Book]:
    """kind=manga は読める漫画がある物語、text は漫画が無く本文だけの物語。"""
    books = [b for b in _books() if b.char_count > 0 or b.page_count > 0]
    if kind == "manga":
        books = [b for b in books if b.manga]
    elif kind == "text":
        books = [b for b in books if not b.manga]
    if bookmarked:
        books = [b for b in books if b.bookmarked]
    books = [b for b in books if _matches_rating(b.adult, rating)]
    for word in q.lower().split():
        books = [b for b in books if word in b.title.lower()]
    if sort == "title":
        books.sort(key=lambda b: b.title)
    else:
        books.sort(
            key=lambda b: b.updated_at if sort == "updated" else b.created_at,
            reverse=True,
        )
    return books


class BookDetail(Book):
    pages: list[str]
    text: str


def _book_text(story: dict[str, Any]) -> str:
    if story["scenes"]:
        return "\n\n".join(
            f"{s.get('seed_cue') or ''}{s['novelai_text']}"
            if s.get("novelai_text")
            else s["draft_text"]
            for s in story["scenes"]
        ).strip()
    return (story.get("raw_text") or "").strip()


@router.get("/books/{story_id}", response_model=BookDetail)
def get_book(story_id: int) -> dict[str, Any]:
    book = next((b for b in _books() if b.id == story_id), None)
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        v1_rows = [r for r in list_library_pages(conn) if r["story_id"] == story_id]
    finally:
        conn.close()
    if book is None or story is None:
        raise HTTPException(status_code=404, detail="story not found")
    _, pages = _book_pages(story, {story_id: [r["image_path"] for r in v1_rows]})
    return {**book.model_dump(), "pages": pages, "text": _book_text(story)}


@router.get("/books/{story_id}/download")
def download_book(
    story_id: int, format: Literal["pdf", "zip", "txt"] = "pdf"
) -> Response:
    """
    本棚からのダウンロード。漫画は合成済みのページをそのまま(PDF/zip)、txt は本文。
    作り直さないので、漫画v1の物語でも同じように落とせる。
    """
    detail = get_book(story_id)
    title = detail["title"]
    fallback = f"story{story_id}"
    if format == "txt":
        if not detail["text"]:
            raise HTTPException(status_code=404, detail="本文がありません。")
        body = f"{title}\n\n{detail['text']}\n".encode("utf-8")
        return Response(
            body,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": _attachment(title, "txt", fallback)},
        )

    pages = detail["pages"]
    if not pages:
        raise HTTPException(status_code=404, detail="読める漫画のページがありません。")
    if format == "pdf":
        images = []
        for path in pages:
            with Image.open(_PROJECT_ROOT / path) as src:
                images.append(src.convert("RGB"))
        buf = io.BytesIO()
        images[0].save(
            buf,
            format="PDF",
            save_all=True,
            append_images=images[1:],
            resolution=_PDF_RESOLUTION,
        )
        return Response(
            buf.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": _attachment(title, "pdf", fallback)},
        )

    buf = io.BytesIO()
    width = max(2, len(str(len(pages))))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
        for i, path in enumerate(pages):
            archive.write(_PROJECT_ROOT / path, f"page_{i + 1:0{width}d}.png")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _attachment(title, "zip", fallback)},
    )


# ---- 削除 ----


def _remove_file(path: str | None) -> None:
    """配信フォルダ(生成履歴・漫画)の中のファイルだけを消す。無ければ何もしない。"""
    if not path:
        return
    full = (_PROJECT_ROOT / path).resolve()
    if any(full.is_relative_to(d.resolve()) for d in _SERVED_DIRS) and full.is_file():
        full.unlink()


def _delete_history_image(entry_id: int, filename: str) -> bool:
    conn = get_connection()
    try:
        entry = get_generation_entry(conn, entry_id)
        if entry is None:
            return False
        target = next(
            (p for p in entry["image_paths"] if Path(p).name == filename), None
        )
        if target is None:
            return False
        rest = [p for p in entry["image_paths"] if p != target]
        if rest:
            set_generation_image_paths(conn, entry_id, rest)
        else:
            # 最後の1枚なら履歴ごと消し、i2i・キャラクター参照の元画像も片付ける
            delete_generation_entry(conn, entry_id)
            _remove_file(entry.get("i2i_image_path"))
            for ref in entry["character_references"]:
                _remove_file(ref.get("image_path"))
    finally:
        conn.close()
    _remove_file(target)
    return True


class ImagesDeleteRequest(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=2000)


@router.post("/images/delete")
def delete_images(req: ImagesDeleteRequest) -> dict[str, int]:
    """
    ギャラリーで選んだ画像を消す(元に戻せない)。生成履歴の画像は履歴から外し、漫画のコマ・挿絵は
    その行を消す。漫画のコマを消しても合成済みのページはそのまま残る(合成し直すとそのコマが空く)。
    """
    deleted: list[str] = []
    for key in dict.fromkeys(req.keys):
        kind, _, rest = key.partition(":")
        if kind == "history":
            entry_id, _, filename = rest.partition(":")
            if entry_id.isdigit() and _delete_history_image(int(entry_id), filename):
                deleted.append(key)
        elif kind in ("panel", "illustration") and rest.isdigit():
            conn = get_connection()
            try:
                path = (delete_manga_panel if kind == "panel" else delete_manga_page)(
                    conn, int(rest)
                )
            finally:
                conn.close()
            if path is not None:
                _remove_file(path)
                deleted.append(key)
    conn = get_connection()
    try:
        forget_library_items(conn, "image", deleted)
    finally:
        conn.close()
    return {"deleted": len(deleted)}


@router.delete("/books/{story_id}", status_code=204)
def delete_book(story_id: int) -> None:
    """
    物語を消す(元に戻せない)。シーン・コマ・挿絵の行と画像、合成したページも消す。
    物語エディタの下書きは残る(物語との結び付きだけ外れる)。
    """
    job = _jobs.get(story_id)
    if job is not None and job.status == "running":
        raise HTTPException(
            status_code=409,
            detail="この物語は処理中です。終わってから削除してください。",
        )
    image_keys = [item.key for item in _gallery_images() if item.story_id == story_id]
    conn = get_connection()
    try:
        if get_story(conn, story_id) is None:
            raise HTTPException(status_code=404, detail="story not found")
        paths = delete_story(conn, story_id)
        forget_library_items(conn, "image", image_keys)
        forget_library_items(conn, "book", [str(story_id)])
    finally:
        conn.close()
    for path in paths:
        _remove_file(path)
    # 合成したページ・完成画像(漫画v2は outputs/manga/v2、v1 の完成画像は outputs/manga)
    for folder in (_OUTPUTS / "manga" / "v2", _OUTPUTS / "manga"):
        for file in folder.glob(f"story{story_id}_*.png"):
            file.unlink()
