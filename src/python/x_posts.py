"""
X への投稿(予約・スレッド・記録)。API を呼ぶ部分は x_api.py、画面との出入り口は routes/x_posts.py。

1件の投稿(x_posts の1行)は「パート」の並び。パートは X の1つの投稿(本文と画像4枚まで)で、2つ目からは前の
パートへの返信(スレッド)にする。漫画を数ページまとめて投稿するときは、4ページずつのパートに分ける。

画像の指定(ref):
  ギャラリーの画像のキー(history:… / panel:… / illustration:… / dataset:…)
  本棚の本のページ  book:{物語のID}:{ページの番号(0始まり)}

安全のために、登録するときに画像ごとに確かめる(どれか1枚でも当たれば、投稿を登録しない):
- 成人向けの画像(ギャラリー・本棚の成人向けの印、または判定モデルの評価)に、未成年のキャラ(キャラシートで未成年、
  または未成年の手がかりがある)が出ている物語・キャラの画像は投稿しない
- 成人向けの画像に、未成年を思わせるタグ(プロンプトまたは判定モデル)があれば投稿しない(minor_guard.image_decision)
- 成人向けの画像が1枚でもあれば、投稿全体をセンシティブにする(外せない)
投稿する画像からは、NovelAI の生成情報を取り除く(出版と同じ publish.strip_metadata)。

予約: scheduled_at になったら、バックエンドの見回り(tick。30 秒ごと)が投稿する。バックエンドが止まっていて
MISS_AFTER より遅れたものは投稿せずに「見送り」にする(思わぬ時間に投稿しないように)。

multi では、.env の X のキーは運営者のアカウントのものなので、投稿できるのは X_POST_USER_ID(既定 1)の利用者だけ。
"""

from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from PIL import Image

from . import minor_guard, publish, x_api
from .client import app_mode
from .db import as_user, current_user_id, get_connection, user_files

log = logging.getLogger(__name__)

SCHEDULED, POSTING, POSTED, FAILED, CANCELLED, MISSED, DELETED = (
    "scheduled", "posting", "posted", "failed", "cancelled", "missed", "deleted",
)
MAX_PARTS = 25
MAX_IMAGES_PER_PART = 4
# 予約の時刻からこれ以上遅れたら投稿しない(バックエンドが止まっていたなど)
MISS_AFTER = timedelta(hours=6)
# 判定モデルの評価(questionable + explicit)がこれ以上なら成人向けとみなす(minor_guard と同じ値)
_SEXUAL_RATING = 0.5

_run_lock = threading.Lock()


class PostRejected(ValueError):
    """投稿として登録できない(安全の確認に当たった・指定がおかしい)。reasons は画面に出す説明。"""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(" / ".join(reasons))
        self.reasons = reasons


# ---- 利用者 ----


def owner_user_id() -> int | None:
    """X のキーの持ち主(multi は X_POST_USER_ID、既定 1。single は None)。"""
    if app_mode() != "multi":
        return None
    try:
        return int(os.environ.get("X_POST_USER_ID", "1"))
    except ValueError:
        return 1


def is_owner() -> bool:
    return app_mode() != "multi" or current_user_id() == owner_user_id()


# ---- 画像 ----


@dataclass
class ImageInfo:
    ref: str
    path: str
    label: str = ""
    prompt: str = ""
    adult: bool = False
    story_id: int | None = None
    character_id: int | None = None
    warnings: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"ref": self.ref, "path": self.path, "label": self.label, "adult": self.adult}


def resolve(refs: Iterable[str]) -> dict[str, ImageInfo]:
    """画像の指定(ref)を、ファイルと成人向けの印に。見つからないものは入れない。"""
    from .routes.library import _books, _gallery_images, get_book

    wanted = list(dict.fromkeys(refs))
    found: dict[str, ImageInfo] = {}
    gallery_refs = [r for r in wanted if not r.startswith("book:")]
    if gallery_refs:
        by_key = {item.key: item for item in _gallery_images()}
        for ref in gallery_refs:
            item = by_key.get(ref)
            if item is None:
                continue
            label = item.label or (f"{item.story_title} {item.index + 1}" if item.story_title and item.index is not None else "")
            found[ref] = ImageInfo(
                ref, item.path, label, item.prompt or "", item.adult, item.story_id, item.character_id
            )
    book_refs = [r for r in wanted if r.startswith("book:")]
    if book_refs:
        books = {b.id: b for b in _books()}
        details: dict[int, dict[str, Any]] = {}
        for ref in book_refs:
            try:
                _, story, page = ref.split(":")
                story_id, index = int(story), int(page)
            except ValueError:
                continue
            book = books.get(story_id)
            if book is None:
                continue
            if story_id not in details:
                details[story_id] = get_book(story_id)
            pages = details[story_id]["pages"]
            if 0 <= index < len(pages):
                found[ref] = ImageInfo(ref, pages[index], f"{book.title} p.{index + 1}", "", book.adult, story_id)
    return found


def expand_refs(refs: Iterable[str]) -> list[str]:
    """本1冊の指定(book:{物語のID})を、その本の全ページ(book:{ID}:{番号})に広げる。ほかはそのまま。"""
    from fastapi import HTTPException

    from .routes.library import get_book

    result: list[str] = []
    for ref in refs:
        pieces = ref.split(":")
        if len(pieces) == 2 and pieces[0] == "book" and pieces[1].isdigit():
            try:
                pages = get_book(int(pieces[1]))["pages"]
            except HTTPException:
                continue
            result += [f"book:{pieces[1]}:{index}" for index in range(len(pages))]
        else:
            result.append(ref)
    return list(dict.fromkeys(result))


def _character_ids(conn: sqlite3.Connection, info: ImageInfo) -> list[int]:
    from .db import story_characters

    ids = [info.character_id] if info.character_id else []
    if info.story_id is not None:
        ids += [int(c["id"]) for c in story_characters(conn, info.story_id)]
    return list(dict.fromkeys(ids))


Scorer = Callable[[Image.Image], list[Any]]


def _default_scorer(image: Image.Image) -> list[Any]:
    from .image_tagger import tag_image

    return tag_image(image)


def check_images(infos: list[ImageInfo], scorer: Scorer | None = None) -> list[str]:
    """
    画像ごとの安全の確認。投稿できない理由(無ければ空)。成人向けかどうかは、印に加えて判定モデルでも見て、
    info.adult を更新する(成人向けの画像があれば、投稿全体をセンシティブにするため)。
    """
    from .db import get_character

    score = scorer or _default_scorer
    reasons: list[str] = []
    conn = get_connection()
    try:
        for info in infos:
            path = user_files() / info.path
            try:
                with Image.open(path) as opened:
                    image = opened.convert("RGB")
            except OSError:
                reasons.append(f"{info.label or info.ref}: 画像を開けません")
                continue
            scores = score(image)
            rating = {s.tag: s.probability for s in scores if s.category == "rating"}
            if rating.get("questionable", 0) + rating.get("explicit", 0) >= _SEXUAL_RATING:
                info.adult = True
            if not info.adult:
                continue
            name = info.label or info.ref
            for character_id in _character_ids(conn, info):
                character = get_character(conn, character_id)
                if character is None:
                    continue
                age = minor_guard.character_age(character)
                if age.state == minor_guard.MINOR:
                    reasons.append(f"{name}: 成人向けの画像に未成年のキャラ({character.get('name')})が出ています")
                elif age.state == minor_guard.UNDEFINED:
                    info.warnings.append(f"年齢区分が未定義のキャラ: {character.get('name')}")
            decision = minor_guard.image_decision(info.prompt, scores)
            if decision.action == minor_guard.BLOCK:
                reasons += [f"{name}: {r}" for r in decision.reasons]
    finally:
        conn.close()
    return list(dict.fromkeys(reasons))


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """
    投稿する画像: 生成情報を取り除いた PNG。5MB を超えるなら JPEG にして(必要なら縮めて)収める。
    戻り値は (中身, MIME)。
    """
    clean = publish.strip_metadata(data)
    if len(clean) <= x_api.MAX_IMAGE_BYTES:
        return clean, "image/png"
    with Image.open(io.BytesIO(clean)) as opened:
        image = opened.convert("RGB")
    for scale in (1.0, 0.85, 0.7, 0.5):
        resized = image if scale == 1.0 else image.resize(
            (int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS
        )
        for quality in (92, 85, 75):
            buffer = io.BytesIO()
            resized.save(buffer, "JPEG", quality=quality, optimize=True)
            if buffer.tell() <= x_api.MAX_IMAGE_BYTES:
                return buffer.getvalue(), "image/jpeg"
    raise x_api.XApiError("画像を 5MB 以下にできませんでした。")


# ---- 記録 ----


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["parts"] = json.loads(item["parts"] or "[]")
    item["tweet_ids"] = json.loads(item["tweet_ids"] or "[]")
    item["metrics"] = json.loads(item["metrics"]) if item.get("metrics") else None
    item["sensitive"] = bool(item["sensitive"])
    item["urls"] = [x_api.post_url(item.get("username"), pid) for pid in item["tweet_ids"]]
    return item


def get_post(post_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        return _row(conn.execute("SELECT * FROM x_posts WHERE id = ?", (post_id,)).fetchone())
    finally:
        conn.close()


def list_posts() -> list[dict[str, Any]]:
    """新しい順(予約中のものは予約の時刻順で先に)。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM x_posts ORDER BY CASE status WHEN 'scheduled' THEN 0 WHEN 'posting' THEN 0 ELSE 1 END, "
            "CASE status WHEN 'scheduled' THEN scheduled_at END ASC, COALESCE(posted_at, created_at) DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()
    return [item for row in rows if (item := _row(row)) is not None]


def _update(post_id: int, **fields: Any) -> None:
    if not fields:
        return
    for key in ("parts", "tweet_ids", "metrics"):
        if key in fields and not isinstance(fields[key], str) and fields[key] is not None:
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    columns = ", ".join(f"{key} = ?" for key in fields)
    conn = get_connection()
    try:
        conn.execute(f"UPDATE x_posts SET {columns} WHERE id = ?", (*fields.values(), post_id))
        conn.commit()
    finally:
        conn.close()


def delete_record(post_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM x_posts WHERE id = ?", (post_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


@dataclass
class PartInput:
    text: str
    refs: list[str]


def build_parts(parts: list[PartInput], scorer: Scorer | None = None) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """
    パートの指定を確かめて、保存する形にする。戻り値は (パート, 成人向けの画像があるか, 注意)。
    投稿できなければ PostRejected。
    """
    reasons: list[str] = []
    if not parts:
        raise PostRejected(["投稿する内容がありません。"])
    if len(parts) > MAX_PARTS:
        raise PostRejected([f"スレッドは {MAX_PARTS} 件までです。"])
    infos = resolve(ref for part in parts for ref in part.refs)
    for number, part in enumerate(parts, start=1):
        label = f"{number}件目" if len(parts) > 1 else "投稿"
        if len(part.refs) > MAX_IMAGES_PER_PART:
            reasons.append(f"{label}: 画像は1件に {MAX_IMAGES_PER_PART} 枚までです。")
        if not part.text.strip() and not part.refs:
            reasons.append(f"{label}: 本文か画像を入れてください。")
        if x_api.weighted_length(part.text) > x_api.MAX_WEIGHTED:
            reasons.append(f"{label}: 本文が長すぎます(全角 140 字・半角 280 字まで)。")
        missing = [ref for ref in part.refs if ref not in infos]
        if missing:
            reasons.append(f"{label}: 画像が見つかりません({', '.join(missing[:3])})")
    if reasons:
        raise PostRejected(reasons)
    used = [infos[ref] for ref in dict.fromkeys(ref for part in parts for ref in part.refs)]
    blocked = check_images(used, scorer)
    if blocked:
        raise PostRejected(blocked)
    warnings = list(dict.fromkeys(w for info in used for w in info.warnings))
    saved = [
        {"text": part.text, "images": [infos[ref].public() for ref in part.refs]}
        for part in parts
    ]
    return saved, any(info.adult for info in used), warnings


def create_post(
    title: str,
    parts: list[PartInput],
    *,
    sensitive: bool,
    scheduled_at: datetime | None,
    scorer: Scorer | None = None,
) -> dict[str, Any]:
    """投稿を登録する(scheduled_at が無ければ今すぐ。投稿するのは見回り)。"""
    saved, adult, warnings = build_parts(parts, scorer)
    when = scheduled_at or _now()
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO x_posts (status, title, parts, sensitive, scheduled_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (SCHEDULED, title.strip(), json.dumps(saved, ensure_ascii=False), int(sensitive or adult), _iso(when), _iso(_now())),
        )
        conn.commit()
        post_id = int(cur.lastrowid or 0)
    finally:
        conn.close()
    post = get_post(post_id) or {}
    return {**post, "warnings": warnings}


def update_scheduled(
    post_id: int,
    title: str,
    parts: list[PartInput],
    *,
    sensitive: bool,
    scheduled_at: datetime | None,
    scorer: Scorer | None = None,
) -> dict[str, Any]:
    """予約中の投稿を書き換える(予約中・失敗・見送り・取り消しのものだけ。投稿済みは変えない)。"""
    post = get_post(post_id)
    if post is None:
        raise KeyError(post_id)
    if post["status"] not in (SCHEDULED, FAILED, MISSED, CANCELLED) or post["tweet_ids"]:
        raise PostRejected(["投稿を始めたものは書き換えられません。"])
    saved, adult, warnings = build_parts(parts, scorer)
    _update(
        post_id,
        title=title.strip(),
        parts=saved,
        sensitive=int(sensitive or adult),
        scheduled_at=_iso(scheduled_at or _now()),
        status=SCHEDULED,
        error=None,
    )
    return {**(get_post(post_id) or {}), "warnings": warnings}


def set_status(post_id: int, status: str, *, scheduled_at: datetime | None = None) -> dict[str, Any] | None:
    fields: dict[str, Any] = {"status": status, "error": None}
    if scheduled_at is not None:
        fields["scheduled_at"] = _iso(scheduled_at)
    _update(post_id, **fields)
    return get_post(post_id)


# ---- 投稿する ----


def run_post(post_id: int, client: x_api.XClient | None = None) -> dict[str, Any] | None:
    """
    1件を投稿する(パートを順にスレッドにする)。投稿できた分の ID はパートごとに保存するので、途中で止まっても
    やり直しは続きから。失敗したら status を failed にして error に理由を残す。
    """
    post = get_post(post_id)
    if post is None or post["status"] not in (SCHEDULED, FAILED):
        return post
    _update(post_id, status=POSTING, error=None)
    tweet_ids: list[str] = list(post["tweet_ids"])
    try:
        x = client or x_api.XClient()
        username = post.get("username") or x.me().get("username")
        if username and username != post.get("username"):
            _update(post_id, username=username)
        root = user_files()
        for index, part in enumerate(post["parts"]):
            if index < len(tweet_ids):
                continue
            media_ids: list[str] = []
            for number, image in enumerate(part.get("images") or [], start=1):
                data, mime = prepare_image((root / image["path"]).read_bytes())
                media_id = x.upload_image(data, mime, f"image{number}.{'png' if mime == 'image/png' else 'jpg'}")
                x.set_media_metadata(media_id, sensitive=post["sensitive"])
                media_ids.append(media_id)
            reply_to = tweet_ids[-1] if tweet_ids else None
            tweet_ids.append(x.create_post(part.get("text") or "", media_ids, reply_to))
            _update(post_id, tweet_ids=tweet_ids)
    except (x_api.XApiError, OSError, publish.MetadataRemains, ValueError) as exc:
        done = f"({len(tweet_ids)}/{len(post['parts'])} 件は投稿済み。やり直すと続きから投稿します)" if tweet_ids else ""
        _update(post_id, status=FAILED, error=f"{exc}{done}")
        log.warning("X への投稿 %s に失敗しました: %s", post_id, exc)
        return get_post(post_id)
    _update(post_id, status=POSTED, posted_at=_iso(_now()))
    return get_post(post_id)


def due_posts(now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or _now()
    return [
        post for post in list_posts()
        if post["status"] == SCHEDULED and (when := _parse(post["scheduled_at"])) is not None and when <= current
    ]


def tick(now: datetime | None = None, client: x_api.XClient | None = None) -> int:
    """
    見回り: 時刻になった予約を投稿する(遅れすぎたものは見送り)。投稿した件数。同時に2つは走らせない。
    呼ぶ側で、X のキーの持ち主の利用者にしておく(run_tick)。
    """
    if not _run_lock.acquire(blocking=False):
        return 0
    try:
        current = now or _now()
        count = 0
        for post in due_posts(current):
            when = _parse(post["scheduled_at"]) or current
            if current - when > MISS_AFTER:
                _update(post["id"], status=MISSED, error="予約の時刻にバックエンドが止まっていたため、投稿を見送りました。")
                continue
            run_post(post["id"], client)
            count += 1
        return count
    finally:
        _run_lock.release()


def run_tick() -> int:
    """バックエンドの見回りから呼ぶ(キーが無ければ何もしない)。"""
    if not x_api.configured():
        return 0
    with as_user(owner_user_id()):
        return tick()


def recover_interrupted() -> None:
    """起動したとき: 投稿の途中で止まったもの(posting のまま)を失敗にする(やり直すと続きから)。"""
    if not x_api.configured():
        return
    with as_user(owner_user_id()):
        for post in list_posts():
            if post["status"] == POSTING:
                _update(
                    post["id"],
                    status=FAILED,
                    error=f"投稿の途中でバックエンドが止まりました({len(post['tweet_ids'])}/{len(post['parts'])} 件は投稿済み。やり直すと続きから投稿します)",
                )


def delete_remote(post_id: int, client: x_api.XClient | None = None) -> dict[str, Any] | None:
    """X から投稿を消す(スレッドは全部。記録は残して status を deleted に)。"""
    post = get_post(post_id)
    if post is None:
        return None
    x = client or x_api.XClient()
    remaining = list(post["tweet_ids"])
    for tweet_id in reversed(post["tweet_ids"]):
        x.delete_post(tweet_id)
        remaining.remove(tweet_id)
    _update(post_id, status=DELETED, error=None)
    return get_post(post_id)


def refresh_metrics(post_ids: list[int], client: x_api.XClient | None = None) -> list[dict[str, Any]]:
    """投稿済みの反応の数を X から読む(読み取りの料金がかかるので、画面のボタンを押したときだけ)。"""
    posts = [p for pid in post_ids if (p := get_post(pid)) is not None and p["tweet_ids"] and p["status"] == POSTED]
    if not posts:
        return []
    x = client or x_api.XClient()
    ids = [tid for p in posts for tid in p["tweet_ids"]]
    found = x.metrics(ids)
    now = _iso(_now())
    for post in posts:
        per_part = [found.get(tid) for tid in post["tweet_ids"]]
        total: dict[str, int] = {}
        for metrics in per_part:
            for key, value in (metrics or {}).items():
                total[key] = total.get(key, 0) + int(value)
        _update(post["id"], metrics={"total": total, "parts": per_part}, metrics_at=now)
    return [p for pid in post_ids if (p := get_post(pid)) is not None]
