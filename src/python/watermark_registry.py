"""
透かしの台帳。外に出す画像(X への投稿・出版物)に見えない透かし(watermark.py)を入れ、何をいつどこへ出したかを
画像の指紋と一緒に残す。ネットで見かけた画像が自分の出したものかを、透かしと指紋で照合する。

指紋は2つ:
- sha256: ファイルの中身そのもの(1 バイトでも違えば別物。出したファイルそのままかを見る)
- 知覚ハッシュ(pHash、64 ビット): 見た目の要約。再圧縮・縮小しても近い値になる(ハミング距離で比べる)

照合の順: 中身が同じ → 透かしをそのまま読む → 指紋の近い記録の大きさに戻してから透かしを読む → 指紋が近いだけ。
"""

from __future__ import annotations

import hashlib
import io
import logging
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np
from PIL import Image

from . import watermark
from .db import get_connection

log = logging.getLogger(__name__)

X_POST = "x_post"
PUBLICATION = "publication"
PURPOSE_LABEL = {X_POST: "X への投稿", PUBLICATION: "出版物"}
# 指紋が同じ絵とみなす距離(64 ビット中)。SIMILAR は「似ている」の目安
MATCH_DISTANCE = 6
SIMILAR_DISTANCE = 12
_CANDIDATES = 5


def phash(image: Image.Image) -> str:
    """知覚ハッシュ(32×32 に縮めた明るさの DCT の低い周波数 8×8 を、中央値で 0/1 に)。16 桁の 16 進。"""
    gray = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float32)
    low = cv2.dct(gray)[:8, :8].flatten()
    bits = low[1:] > np.median(low[1:])  # 直流(全体の明るさ)は使わない
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_mark_id(conn: sqlite3.Connection) -> int:
    while True:
        mark_id = secrets.randbelow(2**32 - 1) + 1
        if conn.execute("SELECT 1 FROM watermarks WHERE mark_id = ?", (mark_id,)).fetchone() is None:
            return mark_id


def _existing(conn: sqlite3.Connection, purpose: str, ref: str | None, x_post_id: int | None) -> int | None:
    """X への投稿をやり直したときは、同じ投稿・同じ画像に同じ番号を使う。"""
    if purpose != X_POST or ref is None or x_post_id is None:
        return None
    row = conn.execute(
        "SELECT mark_id FROM watermarks WHERE purpose = ? AND ref = ? AND x_post_id = ? ORDER BY id DESC LIMIT 1",
        (purpose, ref, x_post_id),
    ).fetchone()
    return int(row[0]) if row else None


def mark(
    data: bytes,
    *,
    purpose: str,
    source_path: str = "",
    label: str = "",
    ref: str | None = None,
    x_post_id: int | None = None,
    publication_id: int | None = None,
) -> bytes:
    """
    画像に透かしを入れた PNG(付属情報なし)を返し、台帳に記録する。
    小さすぎて入れられない画像は、そのまま返す(台帳にも残さない)。
    """
    with Image.open(io.BytesIO(data)) as opened:
        image = opened.convert("RGB")
    conn = get_connection()
    try:
        mark_id = _existing(conn, purpose, ref, x_post_id) or _new_mark_id(conn)
        try:
            marked = watermark.embed(image, mark_id)
        except ValueError as exc:
            log.info("透かしを入れられませんでした(%s): %s", source_path or ref, exc)
            return data
        buffer = io.BytesIO()
        marked.save(buffer, "PNG")
        result = buffer.getvalue()
        conn.execute(
            """
            INSERT INTO watermarks (mark_id, purpose, source_path, ref, label, x_post_id, publication_id,
                                    sha256, phash, width, height, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mark_id, purpose, source_path, ref, label, x_post_id, publication_id,
                hashlib.sha256(result).hexdigest(), phash(marked), marked.width, marked.height, _now(),
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


def _public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["purpose_label"] = PURPOSE_LABEL.get(item["purpose"], item["purpose"])
    item["x_post_url"] = None
    item["publication_title"] = None
    if item.get("x_post_id"):
        from . import x_posts

        post = x_posts.get_post(int(item["x_post_id"]))
        if post and post.get("urls"):
            item["x_post_url"] = post["urls"][0]
    conn = get_connection()
    try:
        if item.get("publication_id"):
            found = conn.execute("SELECT title FROM publications WHERE id = ?", (item["publication_id"],)).fetchone()
            item["publication_title"] = found[0] if found else None
        from .nft_records import token_for

        item["nft"] = token_for(conn, int(item["mark_id"]))
    finally:
        conn.close()
    return item


def list_marks(limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    """台帳(新しい順)。同じ番号を何度も出したもの(投稿のやり直し)は、いちばん新しい1件だけ。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM watermarks WHERE id IN (SELECT MAX(id) FROM watermarks GROUP BY mark_id)
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return [_public(row) for row in rows]


def _by_mark(conn: sqlite3.Connection, mark_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM watermarks WHERE mark_id = ? ORDER BY id DESC LIMIT 1", (mark_id,)).fetchone()


def verify(data: bytes) -> dict[str, Any]:
    """
    画像が台帳のどれかを照合する。戻り値:
      found: 自分が出した画像と分かったか
      method: exact(ファイルが同じ) / watermark(透かしを読めた) / watermark_resized(大きさを戻して読めた) /
              similar(透かしは読めないが、見た目がほぼ同じ) / none
      mark_id / distance(指紋の距離) / record(台帳の行) / foreign_mark(台帳に無い番号の透かしが読めた)
    """
    with Image.open(io.BytesIO(data)) as opened:
        image = opened.convert("RGB")
    digest = hashlib.sha256(data).hexdigest()
    query_hash = phash(image)
    conn = get_connection()
    try:
        exact = conn.execute("SELECT * FROM watermarks WHERE sha256 = ? ORDER BY id DESC LIMIT 1", (digest,)).fetchone()
        if exact is not None:
            return {"found": True, "method": "exact", "mark_id": exact["mark_id"], "distance": 0, "record": _public(exact)}
        reading = watermark.extract(image)
        foreign = None
        if reading.mark_id is not None:
            row = _by_mark(conn, reading.mark_id)
            if row is not None:
                return {
                    "found": True, "method": "watermark", "mark_id": reading.mark_id,
                    "distance": distance(query_hash, row["phash"]), "record": _public(row),
                }
            foreign = reading.mark_id
        rows = conn.execute("SELECT * FROM watermarks WHERE id IN (SELECT MAX(id) FROM watermarks GROUP BY mark_id)").fetchall()
    finally:
        conn.close()
    near = sorted(((distance(query_hash, row["phash"]), row) for row in rows), key=lambda pair: pair[0])
    near = [pair for pair in near if pair[0] <= SIMILAR_DISTANCE][:_CANDIDATES]
    for dist, row in near:
        size = (int(row["width"]), int(row["height"]))
        if size == image.size:
            continue
        resized = watermark.extract(image.resize(size, Image.Resampling.LANCZOS))
        if resized.mark_id == row["mark_id"]:
            return {"found": True, "method": "watermark_resized", "mark_id": row["mark_id"], "distance": dist,
                    "record": _public(row)}
    if near and near[0][0] <= MATCH_DISTANCE:
        dist, row = near[0]
        return {"found": True, "method": "similar", "mark_id": row["mark_id"], "distance": dist, "record": _public(row)}
    return {
        "found": False, "method": "none", "mark_id": None,
        "distance": near[0][0] if near else None,
        "record": _public(near[0][1]) if near else None,
        "foreign_mark": foreign,
    }


def forget_publication(publication_id: int) -> None:
    """作りかけで失敗した出版物の行を消す(外に出ていないので台帳に残さない)。"""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM watermarks WHERE purpose = ? AND publication_id = ?", (PUBLICATION, publication_id))
        conn.commit()
    finally:
        conn.close()
