"""
うごイラ(Pixiv のパラパラ漫画のような動く絵)を作る。

A. 部分だけ描き直す(この版): 元の絵を1枚決め、目・口・髪など動かす所だけを NovelAI のインペイントで何枚か描き直す。
   描き直した範囲だけを元の絵に重ねるので、範囲の外(顔の輪郭・服・背景)は全部のコマで同じになる(ちらつかない)。
   範囲は頭の検出(manga_v2/detect.py)の枠から割り出す(実際の絵で、目は枠の高さの 45〜68%、口は 74〜97% にあった)。

動き(まばたき・口パク など)は DB(ugoira_motions)にあり、画面から足したり直したりできる: 描き直す範囲(region)と、
コマごとのタグ(frames。空のタグは元の絵のまま)と、コマごとの表示時間(delays、ミリ秒)。

コマは outputs/ugoira/{番号}/ に置き、並び・表示時間は ugoira_projects に持つ。書き出しは Pixiv 用 ZIP(コマの画像を
順番に)・GIF・アニメ WebP。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .db import get_connection

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FOLDER = "outputs/ugoira"
REGIONS = ("eyes", "mouth", "face", "hair", "person", "all")
REGION_LABELS = {
    "eyes": "目", "mouth": "口", "face": "顔(表情)", "hair": "髪",
    "person": "体ごと(背景は元のまま)", "all": "絵全体",
}
# B. 全体を少しずつ描き直す(img2img)範囲。ほかの範囲は A(インペイント)
WHOLE_REGIONS = ("person", "all")
# person の範囲: 頭の枠を単位に、左右へ広げ、頭の少し上から絵の下まで(体の動き。背景は元の絵のまま重ねる)
_PERSON_SPREAD = 1.4
_PERSON_FEATHER = 18
# 頭の枠に対する範囲(左, 上, 右, 下。枠の幅・高さの割合)。hair は枠を広げて、顔の範囲を除く
_BOXES = {
    # 目は広めに(下まぶたが範囲の外に残ると、そこに合わせて目が開いたまま描かれた。実機で確認)
    "eyes": (0.15, 0.38, 0.85, 0.74),
    "mouth": (0.25, 0.72, 0.75, 1.0),
    "face": (0.12, 0.38, 0.88, 1.0),
}
_HAIR_SPREAD = (0.4, 0.05, 0.4, 0.9)
_FEATHER = 6
MAX_PIXELS = 1024 * 1024

# 描き直す範囲ごとに、元のプロンプトから外すタグ(コマのタグとぶつかるもの)。目は、目のことを書いたタグを全部外す
# (yellow eyes・sanpaku・detailed eyes などが残っていると、closed eyes を入れても目が開いたまま描かれた。実機で確認)
_CONFLICTS = {
    "eyes": r".*\beyes?\b.*|.*eyelash.*|sanpaku|wink|sleepy|looking at viewer|staring",
    "mouth": r".*\b(mouth|lips?|teeth|tongue|fang)\b.*|:d|:o|:p|:3|smile|grin|talking|frown|pout",
    "face": r".*\b(eyes?|mouth|lips?|teeth)\b.*|smile|grin|frown|pout|crying|tears|blush|surprised|angry|sad|happy|expressionless|sanpaku|wink",
    "hair": r"hair (blowing|flowing)|floating hair|wind",
    # 体の動き: 姿勢・手・腕・頭の向きのタグ
    "person": (
        r".*\b(arms?|hands?|waving|wave|pose|posing|leaning|bowing|nodding|jumping|tilt|tilted|crossed|outstretched|raised)\b.*"
        r"|standing|sitting|kneeling|squatting|looking (back|away|down|up|to the side)|head tilt"
    ),
    "all": r".*\b(arms?|hands?|waving|wave|pose|posing|leaning|bowing|nodding|jumping|tilt|tilted)\b.*|standing|sitting",
}
# コマのタグを強める重み(NovelAI の 1.4::タグ::)。元の絵の描き方に引っ張られないように
FRAME_WEIGHT = 1.4
_EYE_COLOR = re.compile(
    r"^(light |dark )?(black|brown|red|orange|yellow|green|blue|aqua|purple|pink|grey|gray|white|silver|gold|golden|amber|heterochromia)( eyes)?$",
    re.IGNORECASE,
)

DEFAULT_MOTIONS = [
    {"name": "まばたき", "region": "eyes", "frames": ["", "half-closed eyes", "closed eyes", "half-closed eyes"],
     "delays": [2200, 60, 90, 60]},
    {"name": "口パク(話す)", "region": "mouth", "frames": ["closed mouth", "open mouth, talking", "parted lips", "open mouth, :o"],
     "delays": [140, 140, 140, 140]},
    {"name": "笑顔に変わる", "region": "face", "frames": ["", "slight smile", "smile", "smile, closed eyes, happy"],
     "delays": [800, 150, 150, 1500]},
    {"name": "髪の揺れ", "region": "hair", "frames": ["", "hair blowing, wind", "floating hair, wind", "hair blowing, wind"],
     "delays": [250, 250, 250, 250]},
    # B. 全体を少しずつ描き直す(体ごと。背景は元の絵のまま)
    {"name": "手を振る", "region": "person", "frames": ["", "waving, hand up", "waving, arm raised, open hand", "waving, hand up"],
     "delays": [300, 200, 200, 200]},
    {"name": "首をかしげる", "region": "person", "frames": ["", "head tilt", "head tilt, smile", "head tilt"],
     "delays": [700, 250, 900, 250]},
]
# 既定の動きを入れるとき、すでに入れたもの(消したものも)は入れ直さない
_SEEDED_KEY = "ugoira.seeded_motions"


class UgoiraError(ValueError):
    """作れない(頭が見つからない・動きが変 など)。"""


# ---- 範囲 ----


def region_box(head: tuple[float, float, float, float], region: str, size: tuple[int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = head
    w, h = x1 - x0, y1 - y0
    if region == "hair":
        left, top, right, bottom = _HAIR_SPREAD
        box = (x0 - left * w, y0 - top * h, x1 + right * w, y1 + bottom * h)
    else:
        a, b, c, d = _BOXES[region]
        box = (x0 + a * w, y0 + b * h, x0 + c * w, y0 + d * h)
    return (max(0, int(box[0])), max(0, int(box[1])), min(size[0], int(box[2])), min(size[1], int(box[3])))


def region_mask(head: tuple[float, float, float, float], region: str, size: tuple[int, int]) -> Image.Image:
    """描き直す範囲の白いマスク(ふちをぼかす。髪のときは顔を除く)。L の画像。"""
    if region == "all":
        return Image.new("L", size, 255)
    if region == "person":
        x0, y0, x1, y1 = head
        w = x1 - x0
        mask = Image.new("L", size, 0)
        box = (max(0, x0 - _PERSON_SPREAD * w), max(0, y0 - 0.15 * (y1 - y0)), min(size[0], x1 + _PERSON_SPREAD * w), size[1])
        ImageDraw.Draw(mask).rectangle(box, fill=255)
        return mask.filter(ImageFilter.GaussianBlur(_PERSON_FEATHER))
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle(region_box(head, region, size), fill=255)
    if region == "hair":
        draw.ellipse(region_box(head, "face", size), fill=0)
    return mask.filter(ImageFilter.GaussianBlur(_FEATHER))


def pick_head(heads: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    """いちばん大きい頭(主役)。"""
    if not heads:
        raise UgoiraError("頭が見つかりませんでした(人物がはっきり写っている絵を選んでください)。")
    return max(heads, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))


def fit_size(size: tuple[int, int]) -> tuple[int, int]:
    """描き直す大きさ(1MP 以内・64 の倍数。Opus で Anlas がかからない範囲)。"""
    w, h = size
    zoom = min(1.0, (MAX_PIXELS / (w * h)) ** 0.5)
    return max(64, int(w * zoom) // 64 * 64), max(64, int(h * zoom) // 64 * 64)


# コマのタグに合わせて、ネガティブに足すタグ(閉じた目のコマに open eyes を入れると、はじめて目が閉じた。実機で確認)
_FRAME_NEGATIVES = (
    # half-closed eyes の closed eyes には当てない
    (re.compile(r"(?<!-)\bclosed eyes\b"), "open eyes, eyes visible, pupils, iris"),
    (re.compile(r"\bhalf-closed eyes\b"), "wide eyes, closed eyes"),
    (re.compile(r"\b(open mouth|talking|:o|:d)\b"), "closed mouth"),
    (re.compile(r"\bclosed mouth\b"), "open mouth, teeth"),
    (re.compile(r"\bsmile\b"), "frown"),
)


def frame_negative(tags: str) -> str:
    """コマのタグとぶつかる見た目を、ネガティブに入れる分。"""
    lower = tags.lower()
    return ", ".join(dict.fromkeys(neg for pattern, neg in _FRAME_NEGATIVES if pattern.search(lower)))


def frame_prompt(base: str, region: str, tags: str) -> str:
    """元のプロンプトから、範囲とぶつかるタグを外して、コマのタグを前に足す。"""
    pattern = re.compile(rf"^({_CONFLICTS[region]})$", re.IGNORECASE)
    kept = [
        t.strip() for t in base.split(",")
        # 目の色(yellow eyes など)は残す(半目のコマで色が変わらないように)
        if t.strip() and (not pattern.match(t.strip()) or _EYE_COLOR.match(t.strip()))
    ]
    frame = [f"{FRAME_WEIGHT}::{t.strip()}::" for t in tags.split(",") if t.strip()]
    return ", ".join(dict.fromkeys([*frame, *kept]))


def composite(source: Image.Image, generated: Image.Image, mask: Image.Image) -> Image.Image:
    """描き直した絵の、範囲の中だけを元の絵に重ねる(範囲の外は元のまま)。"""
    return Image.composite(generated.convert("RGB").resize(source.size), source, mask)


# ---- 動き(DB) ----


def _seed_motions(conn: Any) -> None:
    """
    既定の動きを、まだ入れていないものだけ入れる(あとから増えた既定の動きも入る。ユーザーが消したものは入れ直さない)。
    入れた名前は ugoira_meta に覚える。前の版で入れた動き(名前が同じもの)は、入れたものとして覚える。
    """
    row = conn.execute("SELECT value FROM ugoira_meta WHERE key = ?", (_SEEDED_KEY,)).fetchone()
    seeded = set(json.loads(row["value"])) if row else set()
    existing = {r["name"] for r in conn.execute("SELECT name FROM ugoira_motions")}
    seeded |= {m["name"] for m in DEFAULT_MOTIONS if m["name"] in existing}
    now = datetime.now(timezone.utc).isoformat()
    for m in DEFAULT_MOTIONS:
        if m["name"] in seeded:
            continue
        conn.execute(
            "INSERT INTO ugoira_motions (name, region, frames, delays, created_at) VALUES (?, ?, ?, ?, ?)",
            (m["name"], m["region"], json.dumps(m["frames"], ensure_ascii=False), json.dumps(m["delays"]), now),
        )
        seeded.add(m["name"])
    conn.execute(
        "INSERT INTO ugoira_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_SEEDED_KEY, json.dumps(sorted(seeded), ensure_ascii=False)),
    )
    conn.commit()


def list_motions() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        _seed_motions(conn)
        rows = conn.execute("SELECT * FROM ugoira_motions ORDER BY id").fetchall()
    finally:
        conn.close()
    return [{**dict(r), "frames": json.loads(r["frames"]), "delays": json.loads(r["delays"])} for r in rows]


def check_motion(name: str, region: str, frames: list[str], delays: list[int]) -> None:
    if not name.strip():
        raise UgoiraError("名前を入れてください。")
    if region not in REGIONS:
        raise UgoiraError(f"範囲は {', '.join(REGIONS)} のどれかです。")
    if not 2 <= len(frames) <= 24 or len(delays) != len(frames):
        raise UgoiraError("コマは2〜24枚で、表示時間はコマと同じ数にしてください。")
    if any(not 20 <= d <= 10000 for d in delays):
        raise UgoiraError("表示時間は 20〜10000 ミリ秒にしてください。")


def save_motion(motion_id: int | None, name: str, region: str, frames: list[str], delays: list[int]) -> dict[str, Any]:
    check_motion(name, region, frames, delays)
    conn = get_connection()
    try:
        if motion_id is None:
            motion_id = conn.execute(
                "INSERT INTO ugoira_motions (name, region, frames, delays, created_at) VALUES (?, ?, ?, ?, ?)",
                (name.strip(), region, json.dumps(frames, ensure_ascii=False), json.dumps(delays), datetime.now(timezone.utc).isoformat()),
            ).lastrowid
        else:
            conn.execute(
                "UPDATE ugoira_motions SET name = ?, region = ?, frames = ?, delays = ? WHERE id = ?",
                (name.strip(), region, json.dumps(frames, ensure_ascii=False), json.dumps(delays), motion_id),
            )
        conn.commit()
    finally:
        conn.close()
    return next(m for m in list_motions() if m["id"] == motion_id)


def delete_motion(motion_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM ugoira_motions WHERE id = ?", (motion_id,))
        conn.commit()
    finally:
        conn.close()


# ---- うごイラ(DB とファイル) ----


@dataclass
class Frame:
    path: str
    delay: int
    tags: str = ""


def _row(row: Any) -> dict[str, Any]:
    project = dict(row)
    project["frames"] = json.loads(project["frames"])
    return project


def get_project(project_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM ugoira_projects WHERE id = ?", (project_id,)).fetchone()
    finally:
        conn.close()
    return _row(row) if row else None


def list_projects() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM ugoira_projects ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def create_project(name: str, source_key: str, method: str) -> int:
    conn = get_connection()
    try:
        project_id = conn.execute(
            "INSERT INTO ugoira_projects (name, source_key, method, frames, created_at) VALUES (?, ?, ?, '[]', ?)",
            (name, source_key, method, datetime.now(timezone.utc).isoformat()),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()
    return int(project_id)


def set_frames(project_id: int, frames: list[dict[str, Any]]) -> dict[str, Any]:
    """コマの並び・表示時間を変える(コマを消すのは並びから外す。ファイルは、うごイラごと消すときに消える)。"""
    project = get_project(project_id)
    if project is None:
        raise UgoiraError("うごイラが見つかりません。")
    known = {f["path"] for f in project["frames"]}
    cleaned = []
    for frame in frames:
        if frame.get("path") not in known:
            raise UgoiraError(f"知らないコマです: {frame.get('path')}")
        delay = int(frame.get("delay") or 100)
        if not 20 <= delay <= 10000:
            raise UgoiraError("表示時間は 20〜10000 ミリ秒にしてください。")
        cleaned.append({"path": frame["path"], "delay": delay, "tags": frame.get("tags") or ""})
    if not cleaned:
        raise UgoiraError("コマを1枚は残してください。")
    _store_frames(project_id, cleaned)
    return get_project(project_id) or {}


def _store_frames(project_id: int, frames: list[dict[str, Any]]) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE ugoira_projects SET frames = ? WHERE id = ?", (json.dumps(frames, ensure_ascii=False), project_id))
        conn.commit()
    finally:
        conn.close()


def frame_dir(project_id: int) -> Path:
    return _PROJECT_ROOT / FOLDER / str(project_id)


def save_frame(project_id: int, index: int, image: Image.Image) -> str:
    folder = frame_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"frame_{index:03d}_{datetime.now().strftime('%H%M%S%f')}.png"
    image.save(folder / name)
    return f"{FOLDER}/{project_id}/{name}"


# ---- 書き出し ----


def _frames_images(project: dict[str, Any], max_side: int | None) -> list[tuple[Image.Image, int]]:
    result = []
    for frame in project["frames"]:
        image = Image.open(_PROJECT_ROOT / frame["path"]).convert("RGB")
        if max_side and max(image.size) > max_side:
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        result.append((image, int(frame["delay"])))
    return result


def export(project: dict[str, Any], fmt: str, max_side: int | None = None) -> tuple[bytes, str, str]:
    """(中身, ファイルの拡張子, MIME)。fmt は zip(Pixiv のうごイラ用)・gif・webp。"""
    return encode(_frames_images(project, max_side), fmt)


def encode(frames: list[tuple[Image.Image, int]], fmt: str) -> tuple[bytes, str, str]:
    """(絵, 表示時間ミリ秒) の並びを書き出す。export と、漫画の動くページの書き出しで共用する。"""
    if not frames:
        raise UgoiraError("コマがありません。")
    buf = io.BytesIO()
    if fmt == "zip":
        # Pixiv のうごイラ: コマの画像を順番どおりの名前で入れた ZIP(表示時間は投稿の画面で決める)。
        # 表示時間の控えとして animation.json も入れる(Pixiv が書き出す形と同じ: frames[].file / delay)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            meta = []
            for index, (image, delay) in enumerate(frames):
                name = f"{index:06d}.jpg"
                part = io.BytesIO()
                image.save(part, "JPEG", quality=92)
                zf.writestr(name, part.getvalue())
                meta.append({"file": name, "delay": delay})
            zf.writestr("animation.json", json.dumps({"frames": meta}, indent=1))
        return buf.getvalue(), "zip", "application/zip"
    images = [image for image, _ in frames]
    delays = [delay for _, delay in frames]
    if fmt == "gif":
        palette = [image.convert("P", palette=Image.Palette.ADAPTIVE, colors=256) for image in images]
        palette[0].save(buf, "GIF", save_all=True, append_images=palette[1:], duration=delays, loop=0, disposal=1)
        return buf.getvalue(), "gif", "image/gif"
    if fmt == "webp":
        images[0].save(buf, "WEBP", save_all=True, append_images=images[1:], duration=delays, loop=0, quality=90)
        return buf.getvalue(), "webp", "image/webp"
    raise UgoiraError("書き出す形は zip・gif・webp のどれかです。")
