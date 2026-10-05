"""
シリーズ(巻)。長い物語は生成に時間がかかるので、1巻のシーン数に目安の上限を設けて巻に分ける。
巻同士の話が途切れないよう、次の巻を書くときは「シリーズの設定」「これまでのあらすじ」
「前巻の結び」を物語エディタのメモリに入れて渡す。
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from novelai import AsyncNovelAI
from pydantic import BaseModel, Field

from ..client import get_client
from ..db import (
    create_draft,
    create_series,
    get_connection,
    get_series,
    get_story,
    list_series,
    list_series_volumes,
    set_draft_series,
    set_story_recap,
    set_story_series,
    set_story_title,
    split_story_into_volumes,
    story_text,
    update_draft,
    update_series,
)
from ..models import MangaV2ComposeRequest
from ..novelai_text_oa import stream_chat
from .llm import stream_llm_text, strip_think_tags
from .story import _covered_length, _jobs

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/series", tags=["series"])

DEFAULT_MAX_SCENES = 20
# 次の巻に渡す「これまでのあらすじ」の巻数。全巻分を渡すと NovelAI の文脈に収まらなくなる
_RECAP_VOLUMES = 3
# 次の巻に渡す前巻の結び(本文の末尾)の文字数
_ENDING_CHARS = 600
# あらすじを作るときに渡す本文の長さ(冒頭, 結末側)。結末に近いほど次の巻に効くので結末側を多めに渡す。
# NovelAI の文章モデルは文脈が 28,672 トークンあるので多めに、ローカルLLMは文脈が 4096 トークン
# (日本語はほぼ1文字=1トークン)なので、指示と出力の枠を残して収める。
_RECAP_EXCERPT_NOVELAI = (2000, 10000)
_RECAP_EXCERPT_LOCAL = (500, 1900)
_ORIGIN_RE = re.compile(r"^\[[^\]]{1,10}\]\s*")
# 題の末尾に付いた「 3巻」など(巻に分けるときに付け直すので外しておく)
_VOLUME_SUFFIX_RE = re.compile(r"\s*\d+巻\s*$")


class SeriesVolume(BaseModel):
    story_id: int
    volume_no: int
    title: str
    status: str
    scene_count: int
    recap: str | None = None


class SeriesResponse(BaseModel):
    id: int
    title: str
    source: str
    memory: str
    max_scenes: int
    created_at: str
    volumes: list[SeriesVolume]


def _series_response(series_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        series = get_series(conn, series_id)
        volumes = list_series_volumes(conn, series_id)
    finally:
        conn.close()
    if series is None:
        raise HTTPException(status_code=404, detail="series not found")
    return {
        **series,
        "volumes": [
            {
                "story_id": v["id"],
                "volume_no": v["volume_no"],
                "title": v["title"] or v["premise"],
                "status": v["status"],
                "scene_count": v["scene_count"],
                "recap": v["recap"],
            }
            for v in volumes
        ],
    }


def _base_title(story: dict[str, Any]) -> str:
    title = (story.get("title") or story.get("premise") or "").strip()
    title = _VOLUME_SUFFIX_RE.sub("", _ORIGIN_RE.sub("", title))
    # 前提文をそのまま題にしている物語は長いので、最初の文で切る
    return (
        re.split(r"(?<=[。！？!?])", title, maxsplit=1)[0][:60] or f"物語{story['id']}"
    )


def _volume_size(max_scenes: int, panels_per_page: int) -> int:
    """
    1巻のシーン数。上限以下で、ページのコマ数の倍数にする(漫画v1の挿絵ページが巻をまたがないように)。
    1ページのコマ数が上限より多いときは上限のまま。
    """
    if 0 < panels_per_page <= max_scenes:
        return max_scenes // panels_per_page * panels_per_page
    return max_scenes


def _require_idle(story_id: int) -> None:
    job = _jobs.get(story_id)
    if job is not None and job.status == "running":
        raise HTTPException(
            status_code=409,
            detail="この物語は処理中です。終わってから実行してください。",
        )


def split_into_volumes(story_id: int, max_scenes: int) -> int | None:
    """
    物語を巻に分け、シリーズIDを返す(上限以下なら何もせず None)。単巻ならシリーズを作り、
    シリーズの最後の巻なら後ろに巻を足す。途中の巻は、後ろの巻番号がずれるので分けない。
    """
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        if story is None:
            raise HTTPException(status_code=404, detail="story not found")
        size = _volume_size(max_scenes, story["panels_per_page"])
        if len(story["scenes"]) <= size:
            return None
        series_id = story.get("series_id")
        if series_id is not None:
            volumes = list_series_volumes(conn, series_id)
            if volumes and volumes[-1]["id"] != story_id:
                raise HTTPException(
                    status_code=409,
                    detail="シリーズの途中の巻は分けられません(後ろの巻番号がずれるため)。",
                )
            series = get_series(conn, series_id)
            base = series["title"] if series else _base_title(story)
            first_no = story.get("volume_no") or 1
        else:
            base = _base_title(story)
            series_id = create_series(
                conn, base, memory=story.get("memory") or "", max_scenes=max_scenes
            )["id"]
            first_no = 1
        raw = story.get("raw_text") or ""
        remaining = raw[_covered_length(raw, story["scenes"]) :] if raw else ""
        ids = split_story_into_volumes(conn, story_id, size, remaining)
        for offset, volume_id in enumerate(ids):
            set_story_series(conn, volume_id, series_id, first_no + offset)
            set_story_title(conn, volume_id, f"{base} {first_no + offset}巻")
        settings = story.get("manga_v2_compose_settings")
    finally:
        conn.close()

    # 漫画v2で合成済みだった物語は、巻ごとに合成し直す(1巻目の完成画像は全巻分だったので外してある)
    if settings:
        from .manga_v2 import compose  # 循環 import を避けて遅延で読む

        for volume_id in ids:
            try:
                compose(volume_id, MangaV2ComposeRequest.model_validate_json(settings))
            except HTTPException:
                pass  # コマの絵が無い巻は合成できない(後で絵を生成してから合成する)
    return series_id


@router.get("", response_model=list[SeriesResponse])
def get_all_series() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        ids = [s["id"] for s in list_series(conn)]
    finally:
        conn.close()
    return [_series_response(series_id) for series_id in ids]


@router.get("/{series_id}", response_model=SeriesResponse)
def get_one_series(series_id: int) -> dict[str, Any]:
    return _series_response(series_id)


class SeriesCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source: str = Field("", max_length=500)
    memory: str = ""
    max_scenes: int = Field(DEFAULT_MAX_SCENES, ge=1, le=1000)
    # 巻にする物語(この順で1巻から)。どれも単巻であること
    story_ids: list[int] = Field(min_length=1, max_length=500)


@router.post("", response_model=SeriesResponse)
def create_series_route(req: SeriesCreateRequest) -> dict[str, Any]:
    """既にある単巻の物語を、並べた順に1巻・2巻…としてシリーズにまとめる。"""
    if len(set(req.story_ids)) != len(req.story_ids):
        raise HTTPException(status_code=400, detail="同じ物語が2回指定されています。")
    conn = get_connection()
    try:
        for story_id in req.story_ids:
            story = get_story(conn, story_id)
            if story is None:
                raise HTTPException(
                    status_code=404, detail=f"物語{story_id}がありません。"
                )
            if story.get("series_id") is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"物語{story_id}は既に別のシリーズの巻です。",
                )
        series_id = create_series(
            conn, req.title, req.source, req.memory, req.max_scenes
        )["id"]
        for volume_no, story_id in enumerate(req.story_ids, start=1):
            set_story_series(conn, story_id, series_id, volume_no)
    finally:
        conn.close()
    return _series_response(series_id)


class SeriesUpdateRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    source: str | None = Field(None, max_length=500)
    memory: str | None = None
    max_scenes: int | None = Field(None, ge=1, le=1000)


@router.put("/{series_id}", response_model=SeriesResponse)
def update_series_route(series_id: int, req: SeriesUpdateRequest) -> dict[str, Any]:
    conn = get_connection()
    try:
        if get_series(conn, series_id) is None:
            raise HTTPException(status_code=404, detail="series not found")
        update_series(conn, series_id, req.model_dump(exclude_none=True))
    finally:
        conn.close()
    return _series_response(series_id)


class SplitRequest(BaseModel):
    max_scenes: int = Field(DEFAULT_MAX_SCENES, ge=1, le=1000)


@router.post("/split/{story_id}", response_model=SeriesResponse)
def split_route(story_id: int, req: SplitRequest) -> dict[str, Any]:
    """長い物語を、1巻 max_scenes シーンまで(ページのコマ数の倍数に丸める)の巻に分ける。"""
    _require_idle(story_id)
    series_id = split_into_volumes(story_id, req.max_scenes)
    if series_id is None:
        raise HTTPException(
            status_code=400, detail="この物語は上限以下なので、分ける必要がありません。"
        )
    return _series_response(series_id)


# ---- あらすじ・次の巻 ----


def _recap_excerpt(text: str, head: int, tail: int) -> str:
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n\n(中略)\n\n{text[-tail:]}"


async def _generate_recap(story_id: int, api_key: str | None) -> str:
    """
    巻のあらすじを作る。NovelAI の文章モデルを使う(実機でローカルLLMに作らせたところ、登場人物の名前を
    作り替え、話を取り違え、中国語の字が混ざった)。シリーズの設定(登場人物の名前と関係)も渡し、
    設定に無い名前を作らないよう指示する。NovelAI のトークンが無いときだけローカルLLMを使う。
    """
    conn = get_connection()
    try:
        text = story_text(conn, story_id)
        story = get_story(conn, story_id)
        series = (
            get_series(conn, story["series_id"])
            if story and story.get("series_id")
            else None
        )
    finally:
        conn.close()
    if not text.strip():
        raise HTTPException(
            status_code=400, detail="本文が無いので、あらすじを作れません。"
        )
    setting = (
        (series or {}).get("memory") or (story or {}).get("memory") or ""
    ).strip()
    system = (
        "あなたは小説の編集者です。与えられた物語の本文を、続きの巻を書く人が話の流れを引き継げるよう、"
        "日本語で200〜300字のあらすじにまとめてください。登場人物の名前と関係、起きた出来事、"
        "最後の場面で誰がどこで何をしているかを必ず含めてください。本文に無い出来事や名前を作らないでください。"
        "あらすじの本文だけを出力してください。"
    )
    if setting:
        system += f"\n\n登場人物の名前と設定は次のとおりです。名前は必ずこれに合わせてください。\n{setting}"
    head, tail = _RECAP_EXCERPT_NOVELAI if api_key else _RECAP_EXCERPT_LOCAL
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _recap_excerpt(text, head, tail)},
    ]
    try:
        if api_key:
            parts = [
                delta async for delta in stream_chat(api_key, messages, max_tokens=800)
            ]
        else:
            parts = [delta async for delta in stream_llm_text(messages, max_tokens=700)]
    except Exception as exc:  # noqa: BLE001 生成の失敗は画面に伝える
        raise HTTPException(
            status_code=502, detail=f"あらすじを作れませんでした: {exc}"
        ) from exc
    recap = strip_think_tags("".join(parts)).strip()
    if not recap:
        raise HTTPException(
            status_code=502, detail="あらすじが空でした。もう一度試してください。"
        )
    return recap


class RecapRequest(BaseModel):
    recap: str | None = Field(None, max_length=4000)


@router.post("/volumes/{story_id}/recap")
async def generate_recap_route(story_id: int, client: ClientDep) -> dict[str, str]:
    """巻のあらすじを作って保存する(上書き)。"""
    recap = await _generate_recap(story_id, getattr(client, "api_key", None))
    conn = get_connection()
    try:
        set_story_recap(conn, story_id, recap)
    finally:
        conn.close()
    return {"recap": recap}


@router.put("/volumes/{story_id}/recap", status_code=204)
def put_recap(story_id: int, req: RecapRequest) -> None:
    """あらすじを手で直す(空にすると次の巻を作るときに作り直す)。"""
    conn = get_connection()
    try:
        set_story_recap(conn, story_id, (req.recap or "").strip() or None)
    finally:
        conn.close()


def _continuity_memory(
    series: dict[str, Any], volumes: list[dict[str, Any]], ending: str
) -> str:
    """次の巻を書くときのメモリ: シリーズの設定 + これまでのあらすじ(直近の巻) + 前巻の結び。"""
    parts = []
    if series["memory"].strip():
        parts.append(series["memory"].strip())
    if series["source"].strip():
        parts.append(f"出典・原作: {series['source'].strip()}")
    recaps = [v for v in volumes if v["recap"]][-_RECAP_VOLUMES:]
    if recaps:
        parts.append(
            "【これまでのあらすじ】\n"
            + "\n".join(f"第{v['volume_no']}巻: {v['recap'].strip()}" for v in recaps)
        )
    if ending.strip():
        parts.append(f"【前巻の結び】\n…{ending.strip()}")
    return "\n\n".join(parts)


@router.post("/{series_id}/next-volume")
async def next_volume(series_id: int, client: ClientDep) -> dict[str, int]:
    """
    最後の巻の続きを書くための下書きを物語エディタに作る。最後の巻にあらすじが無ければ先に作る。
    同じ巻の下書きが既にあればそれを返す(押し直しても下書きが増えないように)。
    「漫画にする」と、この下書きはシリーズの次の巻になる。
    """
    conn = get_connection()
    try:
        series = get_series(conn, series_id)
        volumes = list_series_volumes(conn, series_id)
        if series is None or not volumes:
            raise HTTPException(status_code=404, detail="series not found")
        next_no = volumes[-1]["volume_no"] + 1
        existing = conn.execute(
            "SELECT id FROM story_drafts WHERE series_id = ? AND volume_no = ? AND story_id IS NULL",
            (series_id, next_no),
        ).fetchone()
    finally:
        conn.close()
    if existing:
        return {"draft_id": existing["id"], "volume_no": next_no}

    last = volumes[-1]
    if not last["recap"]:
        last["recap"] = await _generate_recap(
            last["id"], getattr(client, "api_key", None)
        )
        conn = get_connection()
        try:
            set_story_recap(conn, last["id"], last["recap"])
        finally:
            conn.close()

    conn = get_connection()
    try:
        ending = story_text(conn, last["id"])[-_ENDING_CHARS:]
        draft = create_draft(conn, f"{series['title']} {next_no}巻")
        update_draft(
            conn,
            draft["id"],
            {
                "memory": _continuity_memory(series, volumes, ending),
                "author_note": (
                    f"[ これは「{series['title']}」の第{next_no}巻。"
                    "前巻の結びの直後から、同じ登場人物・同じ文体で話を続ける。 ]"
                ),
            },
        )
        set_draft_series(conn, draft["id"], series_id, next_no)
    finally:
        conn.close()
    return {"draft_id": draft["id"], "volume_no": next_no}
