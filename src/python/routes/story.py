"""
物語生成 → 挿絵(V5コマ割り) → 漫画出力パイプライン。

流れ: 前提(premise) → Ollamaでシーン分割ドラフト作成 → シーンごとにOllamaが
継続用の誘導文(seed_cue)を作り、それをNovelAI公式Kayraに渡して本文を自動で書き継ぐ
→ ページ単位でV5にコマ割り画像を生成させる → 全ページを縦に連結して1枚の漫画にする。
"""

from __future__ import annotations

import asyncio
import json
import re
from base64 import b64decode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator, Awaitable, Callable
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from novelai import AsyncNovelAI

from ..client import get_client
from ..db import (
    add_story_scenes,
    create_manga_page,
    create_story,
    delete_character,
    get_connection,
    get_story,
    list_manga_pages,
    list_stories,
    list_characters,
    list_story_scenes,
    save_character,
    set_scene_characters,
    update_scene_tags,
    update_scene_writing,
    update_story_final_image,
    update_story_scene_count,
    update_story_status,
)
from ..keystore_crypto import decrypt_keystore, decrypt_object
from ..manga_export import assemble_manga
from ..models import (
    CharacterResponse,
    CharacterSaveRequest,
    MangaPageResponse,
    StoryDraftCreateRequest,
    StoryIllustrateRequest,
    StoryImportRequest,
    StoryJobResponse,
    StoryResponse,
    StorySplitRequest,
    StorySummary,
    SetSceneCharactersRequest,
)
from ..novelai_image_v5 import generate_manga_page
from ..novelai_text import generate_kayra
from .llm import sse_event, strip_think_tags, stream_llm_text

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/story", tags=["story"])

# 物語本文(storycontent)は image.novelai.net 側のユーザーストレージにある。
# persistent access token は拒否されるため、実ログインで発行されたセッショントークンが必要
# (routes/chunks.py の promptmacros 取得と同じ制約)。
_IMAGE_API = "https://image.novelai.net"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MANGA_DIR = _PROJECT_ROOT / "outputs" / "manga"

# ローカルLLM(qwen3-vl:2b)に自由形式で書式(【シーンN】等)を指示しても、実機で
# 見出し記法や番号付けが毎回違う形に崩れることを複数回確認した(日本語括弧、
# 英語"Scene N:"、markdown見出しのみ、等)。テキストの後付けパースで追いかけ
# 続けるのは非現実的なため、Ollamaの構造化出力(format=JSON Schema)でスキーマに
# 適合する出力だけをサンプリングさせる方式に切り替える。
def _draft_json_schema(n_scenes: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "minItems": n_scenes,
                "maxItems": n_scenes,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "text": {"type": "string"},
                        "prompt_tags": {"type": "string"},
                    },
                    "required": ["title", "text", "prompt_tags"],
                },
            }
        },
        "required": ["scenes"],
    }


def draft_json_system_prompt(n_scenes: int) -> str:
    return (
        "あなたはNovelAI画像生成シナリオ用の物語ドラフト生成AIです。\n"
        "ユーザーの前提設定から、画像生成に適した情景描写を含む短編物語をJSON形式で生成してください。\n\n"
        f"- scenes配列にちょうど{n_scenes}個の要素を含める\n"
        "- title: シーンの短い日本語タイトル\n"
        "- text: 情景描写(日本語、3〜5文、情感豊かに)。ユーザーの前提と関係のない内容にしないこと\n"
        "- prompt_tags: この情景を画像生成するための英語タグ(コンマ区切り)\n"
        "- 各シーンは画像1枚で表現できる情景に絞る"
    )


def parse_story_draft(draft_text: str) -> list[dict[str, Any]]:
    """draft_json_system_prompt + JSON Schema制約で得られたJSON文字列をパースする。"""

    try:
        data = json.loads(draft_text)
    except json.JSONDecodeError:
        return []

    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        return []

    result: list[dict[str, Any]] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        result.append(
            {
                "draft_title": (str(scene.get("title") or "")).strip() or None,
                "draft_text": (str(scene.get("text") or "")).strip(),
                "draft_prompt_tags": (str(scene.get("prompt_tags") or "")).strip(),
            }
        )
    return result


def _story_response(story: dict[str, Any] | None) -> StoryResponse:
    if story is None:
        raise HTTPException(status_code=404, detail="story not found")
    return StoryResponse.model_validate(story)


_DRAFT_MAX_ATTEMPTS = 3
_DRAFT_MIN_BODY_LENGTH = 20

# タグ付け1リクエストの大きさは、ローカルLLMのコンテキスト長に収まるよう決める。
# 実機のOllamaは qwen3-vl:2b を context_length=4096 で載せており(/api/ps で確認)、
# 日本語はほぼ1文字=1トークンなので、10シーン×300字を丸ごと送ると入力だけで
# 3,000トークン超になり、出力枠と合わせて溢れる。英語の物語が通って日本語の物語で
# タグが1件も付かなかったのはこれが原因だった。
# そこで「1シーンあたりの文字数」「バッチのシーン数」「出力トークン数」の3つを
# 絞り、合計が4096に収まるようにしている(8×150字≒1,200 + 指示文 + 出力1,024)。
_TAG_BATCH_SIZE = 8
_TAG_TEXT_CHARS = 150
_TAG_MAX_TOKENS = 1024


@dataclass
class _Job:
    """
    分割/挿絵生成の進捗。長編は数十分かかり、その間ブラウザが眠ったり閉じられたりする。
    リクエストに紐付けて実行すると切断でタスクごとキャンセルされ、実機では511シーンの
    分割が丸ごと失われたため、処理はリクエストと切り離したタスクで動かし、進捗だけを
    ここに書き出してポーリングで読ませる。
    """

    story_id: int
    kind: str
    status: str = "running"
    message: str = ""
    progress: int = 0
    total: int = 0
    detail: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


# 物語ごとに同時に1ジョブだけ。プロセス内メモリなので再起動で消えるが、
# 途中結果はDBへ逐次書き込むので処理そのものは失われない。
_jobs: dict[int, _Job] = {}


def _job_response(job: _Job) -> dict[str, Any]:
    return {
        "story_id": job.story_id,
        "kind": job.kind,
        "status": job.status,
        "message": job.message,
        "progress": job.progress,
        "total": job.total,
        "detail": job.detail,
    }


def _start_job(story_id: int, kind: str, runner: Callable[[_Job], Awaitable[None]]) -> _Job:
    running = _jobs.get(story_id)
    if running is not None and running.status == "running":
        raise HTTPException(status_code=409, detail="この物語は既に処理中です。")

    job = _Job(story_id=story_id, kind=kind, message="開始しました")
    _jobs[story_id] = job

    async def wrapper() -> None:
        try:
            await runner(job)
            if job.status == "running":
                job.status = "done"
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.message = "キャンセルしました"
            raise
        except Exception as exc:  # noqa: BLE001 ジョブの失敗は状態として持たせる
            job.status = "error"
            job.detail = str(exc)

    job.task = asyncio.create_task(wrapper())
    return job


def _is_usable_draft(scenes: list[dict[str, Any]]) -> bool:
    """
    タイトルだけが繰り返されて本文が実質空、といった「パースはできたが
    中身が薄すぎる」ケースも実機で確認したため、最低限の本文量を要求する。
    """
    return bool(scenes) and all(
        len(scene["draft_text"]) >= _DRAFT_MIN_BODY_LENGTH and scene["draft_text"] != scene["draft_title"]
        for scene in scenes
    )


@router.post("/draft")
async def create_story_draft(req: StoryDraftCreateRequest, request: Request) -> StreamingResponse:
    async def gen() -> AsyncGenerator[str, None]:
        try:
            # ローカルLLM(小型の思考モデル)は稀に指示を無視して無関係な言語で
            # 推論過程をそのまま出力してしまうなど、フォーマット以前に生成自体が
            # 崩れることがある(実機で複数回確認済み)。パース失敗は再試行で救える
            # ことが多いため、諦める前に複数回試す。
            draft_text = ""
            scenes: list[dict[str, Any]] = []
            for attempt in range(1, _DRAFT_MAX_ATTEMPTS + 1):
                if await request.is_disconnected():
                    return
                parts: list[str] = []
                async for delta in stream_llm_text(
                    [
                        {"role": "system", "content": draft_json_system_prompt(req.n_scenes)},
                        {"role": "user", "content": req.premise},
                    ],
                    request,
                    # ローカルの思考モデル(qwen3-vl:2b)は本文を書く前に大量の<think>推論を
                    # 消費することがあり(実測で6000トークン超)、上限が低いと内容が空のまま
                    # 打ち切られてしまう。余裕を持って大きめに設定する。
                    max_tokens=8192,
                    json_schema=_draft_json_schema(req.n_scenes),
                ):
                    parts.append(delta)
                    chars = sum(len(p) for p in parts)
                    yield sse_event(
                        "progress",
                        {"message": f"Ollamaでドラフト生成中(試行{attempt}/{_DRAFT_MAX_ATTEMPTS}、{chars}文字)"},
                    )

                if await request.is_disconnected():
                    return

                draft_text = strip_think_tags("".join(parts))
                # minItems/maxItemsをスキーマに含めても実機では守られないことがある
                # ため、念のため要求されたシーン数に切り詰める。
                scenes = parse_story_draft(draft_text)[: req.n_scenes]
                if _is_usable_draft(scenes):
                    break
                yield sse_event("progress", {"message": f"出力の形式が不十分だったため再試行します({attempt}/{_DRAFT_MAX_ATTEMPTS})"})

            if not _is_usable_draft(scenes):
                preview = draft_text[:500]
                yield sse_event(
                    "error",
                    {
                        "detail": (
                            f"ドラフトのパースに失敗しました({_DRAFT_MAX_ATTEMPTS}回試行)。"
                            f"LLM出力の形式が想定と異なります。出力プレビュー: {preview!r}"
                        )
                    },
                )
                return

            conn = get_connection()
            try:
                story = create_story(conn, req.premise, req.n_scenes, req.panels_per_page)
                add_story_scenes(conn, story["id"], scenes)
                result = get_story(conn, story["id"])
            finally:
                conn.close()
            yield sse_event("done", _story_response(result).model_dump())
        except Exception as exc:
            yield sse_event("error", {"detail": str(exc)})

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


async def _fetch_decrypted_objects(
    object_type: str, http_client: httpx.AsyncClient, keystore: dict[str, bytes]
) -> list[dict[str, Any]]:
    """GET /user/objects/{object_type} を取得し、keystoreで復号できたアイテムだけを返す。"""
    resp = await http_client.get(f"{_IMAGE_API}/user/objects/{object_type}")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    body = resp.json()
    items = body.get("objects", body) if isinstance(body, dict) else body

    result: list[dict[str, Any]] = []
    for item in items:
        try:
            decrypted = decrypt_object(item, keystore)
        except Exception:  # noqa: BLE001 一部アイテムの復号失敗はスキップして続行する
            continue
        if not isinstance(decrypted, dict):
            continue
        decrypted["remote_object_id"] = item.get("id")
        result.append(decrypted)
    return result


def _match_content_for_story(
    story_meta: dict[str, Any], contents_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """stories オブジェクトのメタ情報から、対応する storycontent オブジェクトを探す。"""
    for key in ("remoteStoryId", "remoteId", "id"):
        ref = story_meta.get(key)
        if isinstance(ref, str) and ref in contents_by_id:
            return contents_by_id[ref]
    return None


@router.get("/remote")
async def list_remote_stories(
    encryption_key: str = Query(..., description="POST /api/chunks/encryption-key で取得した base64 鍵"),
    authorization: str | None = Header(None),
) -> list[dict[str, Any]]:
    """
    NovelAI公式サイトのストーリーエディタで書いた物語を、貼り付けなしで一覧取得する。

    本文(document)はbase64+msgpack(msgpackrのレコード定義拡張)で保存されており、
    デコードにはmsgpackrのJS実装が要るため、ここでは生の文字列のまま返して
    フロントエンド(novelaiDocument.ts)でプレーンテキストへ復元する。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization ヘッダーが必要です")
    token = authorization[7:]

    try:
        key = b64decode(encryption_key)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"encryption_key が不正です: {exc}")

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(f"{_IMAGE_API}/user/keystore")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        try:
            keystore = decrypt_keystore(resp.json(), key)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"keystore の復号に失敗しました: {exc}")

        stories_meta = await _fetch_decrypted_objects("stories", client, keystore)
        contents = await _fetch_decrypted_objects("storycontent", client, keystore)

    contents_by_id = {c["remote_object_id"]: c for c in contents if c.get("remote_object_id")}

    result: list[dict[str, Any]] = []
    for meta in stories_meta:
        content = _match_content_for_story(meta, contents_by_id)
        result.append(
            {
                "remote_object_id": meta.get("remote_object_id"),
                "title": meta.get("title") or "(無題)",
                "description": meta.get("description") or "",
                "text_preview": meta.get("textPreview") or "",
                "document": (content.get("document") if content else None) or None,
            }
        )
    return result


# 文の切れ目。日本語の句点類は直後で、ラテン文字の終止符は後ろに空白が続く場合だけ
# 区切る(小数点や略語で切らないため)。どちらも幅ゼロで区切るので、連結すれば元の
# 本文がそのまま復元される(取り込んだ本文は書き換えない、という前提を守るため)。
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？])|(?<=[.!?])(?=\s)")


def _split_into_units(text: str, max_chars: int) -> list[str]:
    """
    本文を段落へ分け、max_chars を超える段落は文単位へさらに分解する。

    公式サイトから取り込んだ本文はセクション区切りが単一改行のため、空行だけでなく
    任意の改行を段落境界として扱う。改行がほとんど無い物語(実機データで1段落3,000字超の
    英語作品を確認)ではこの文分割が効き、1コマに収まらない巨大なシーンができるのを防ぐ。
    1文だけで max_chars を超える場合はそれ以上分割せず、そのまま1単位とする。
    """
    paragraphs = [p.strip() for p in re.split(r"\n+", text.strip()) if p.strip()]

    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue

        buffer = ""
        for sentence in _SENTENCE_BOUNDARY_RE.split(paragraph):
            if not sentence.strip():
                continue
            if buffer and len(buffer) + len(sentence) > max_chars:
                units.append(buffer)
                buffer = ""
            buffer += sentence
        if buffer:
            units.append(buffer)
    return units


def _split_text_into_scenes(text: str, max_paragraphs: int, max_chars: int) -> list[str]:
    """
    本文を書き換えずに場面へ分割する。段落を順に詰めていき、段落数が max_paragraphs に
    達するか、合計が max_chars を超える時点で次のシーンへ区切る(シーン数は結果として
    決まる)。

    シーン数を先に決める方式だと、長編ほど1シーンが際限なく長くなり、そのまま画像
    プロンプトへ載せられなくなるため、1シーンの上限を指定する方式にしている。
    """
    units = _split_into_units(text, max_chars)
    if not units:
        return []

    scenes: list[str] = []
    current: list[str] = []
    current_len = 0
    for unit in units:
        if current and (len(current) >= max_paragraphs or current_len + len(unit) > max_chars):
            scenes.append("\n".join(current))
            current, current_len = [], 0
        current.append(unit)
        current_len += len(unit)
    if current:
        scenes.append("\n".join(current))
    return scenes


def _import_tags_json_schema(n_scenes: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "minItems": n_scenes,
                "maxItems": n_scenes,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "prompt_tags": {"type": "string"},
                    },
                    "required": ["title", "prompt_tags"],
                },
            }
        },
        "required": ["scenes"],
    }


def _import_tags_system_prompt(n_scenes: int) -> str:
    return (
        "あなたはNovelAI画像生成タグ付けAIです。\n"
        "ユーザーから、既に完成している物語を場面ごとに分割した本文が渡されます。\n"
        "本文は一切書き換えず、各場面に短い日本語タイトルと、画像生成用の英語タグ"
        "(コンマ区切り)だけを付与してJSON形式で返してください。\n\n"
        f"- scenes配列にちょうど{n_scenes}個の要素を、渡された場面の順番通りに含める\n"
        "- title: その場面の短い日本語タイトル\n"
        "- prompt_tags: その場面の情景を画像生成するための英語タグ(コンマ区切り)"
    )


def _format_scenes_for_tagging(scene_texts: list[str]) -> str:
    """タグ付けに必要なのは場面の要旨だけなので、本文は先頭だけを送って入力量を抑える。"""
    return "\n\n".join(
        f"場面{i}:\n{text[:_TAG_TEXT_CHARS]}" for i, text in enumerate(scene_texts, start=1)
    )


def _parse_import_tags(tags_text: str, n_scenes: int) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(tags_text)
    except json.JSONDecodeError:
        return None

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return None

    # 件数がバッチと一致しないことは実機で普通に起きる(スキーマのminItems/maxItemsは
    # 守られないことがある)。以前は不一致なら全件捨てていたが、それだと1件足りない
    # だけで10シーン分のタグ付けが無駄になるため、取れた分だけ先頭から採用する。
    # 余ったシーンはタグ空のまま残り、後から /retag で埋め直せる。
    result: list[dict[str, Any]] = []
    for scene in scenes[:n_scenes]:
        if not isinstance(scene, dict):
            continue
        result.append(
            {
                "draft_title": (str(scene.get("title") or "")).strip() or None,
                "draft_prompt_tags": (str(scene.get("prompt_tags") or "")).strip(),
            }
        )
    return result or None


def _premise_label(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return f"[インポート] {first_line[:100]}"


@router.post("/import", response_model=StoryResponse)
async def import_story(req: StoryImportRequest) -> StoryResponse:
    """
    公式サイト等で既に書かれた物語テキストを、シーン分割・タグ付けを一切行わずに
    raw_text としてそのままDBへ保存する。Ollamaの呼び出しは無いため即座に完了する。
    シーン分割・タグ付け(→挿絵生成の前提)は、後で /{story_id}/split を呼んで
    好きなタイミングで行う。
    """
    conn = get_connection()
    try:
        story = create_story(conn, _premise_label(req.text), req.n_scenes, req.panels_per_page, raw_text=req.text)
        result = get_story(conn, story["id"])
    finally:
        conn.close()
    return _story_response(result)


@router.post("/{story_id}/split", response_model=StoryJobResponse)
async def split_story(story_id: int, req: StorySplitRequest) -> dict[str, Any]:
    """
    /import で raw_text のまま保存しておいた物語を、本文には一切手を加えずに場面へ
    分割する。Ollamaはタグ付け(タイトル・画像生成タグ)のためだけに使い、本文の生成/
    書き換えには使わない。分割したシーンは novelai_text を draft_text と同じ値で埋め、
    /write (Kayraによる自動執筆)をスキップした状態で保存するため、そのまま
    /illustrate へ進める。

    長編では数十分かかるため、処理はバックグラウンドで走らせて即座に返す。
    進捗は GET /{story_id}/job を見る。
    """
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
    finally:
        conn.close()

    if story is None:
        raise HTTPException(status_code=404, detail="story not found")
    if story["scenes"]:
        raise HTTPException(status_code=409, detail="この物語は既に分割済みです。")
    if not story.get("raw_text"):
        raise HTTPException(
            status_code=400, detail="raw_text がありません(インポートされた物語ではない可能性があります)。"
        )

    async def runner(job: _Job) -> None:
        await _run_split(job, story_id, req)

    return _job_response(_start_job(story_id, "split", runner))


async def _tag_scene_batch(batch_texts: list[str]) -> list[dict[str, Any]] | None:
    """1バッチ分のタグ付け。形式が崩れたら数回まで再試行し、それでも駄目ならNone。"""
    for _ in range(_DRAFT_MAX_ATTEMPTS):
        parts: list[str] = []
        async for delta in stream_llm_text(
            [
                {"role": "system", "content": _import_tags_system_prompt(len(batch_texts))},
                {"role": "user", "content": _format_scenes_for_tagging(batch_texts)},
            ],
            max_tokens=_TAG_MAX_TOKENS,
            json_schema=_import_tags_json_schema(len(batch_texts)),
        ):
            parts.append(delta)
        tags = _parse_import_tags(strip_think_tags("".join(parts)), len(batch_texts))
        if tags is not None:
            return tags
    return None


async def _apply_tags(scenes: list[dict[str, Any]], texts: list[str]) -> int:
    """1バッチ分のタグ付けとDB反映。付けられたシーン数を返す(0なら丸ごと失敗)。"""
    tags = await _tag_scene_batch(texts)
    if not tags:
        return 0
    conn = get_connection()
    try:
        for scene, tag in zip(scenes, tags):
            update_scene_tags(conn, scene["id"], tag["draft_title"], tag["draft_prompt_tags"])
    finally:
        conn.close()
    return min(len(scenes), len(tags))


# タグ付けが最初から一つも通らない場合、以降のバッチも通らないことがほとんどなので、
# 長編で何十分も無駄に回さないよう早い段階で打ち切る。実機では qwen3-vl:2b が
# 日本語入力に対して思考を延々と出し続け、出力枠を使い切って本文(JSON)を一度も
# 返さないという状態を確認している(英語の物語では同じ設定で成功する)。
_TAG_GIVE_UP_AFTER = 3


def _should_give_up(batch_index: int, tagged: int) -> bool:
    return tagged == 0 and batch_index >= _TAG_GIVE_UP_AFTER


async def _run_split(job: _Job, story_id: int, req: StorySplitRequest) -> None:
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        if story is None:
            raise RuntimeError("story not found")
        scene_texts = _split_text_into_scenes(story["raw_text"] or "", req.max_paragraphs, req.max_chars)
        if not scene_texts:
            raise RuntimeError("本文からシーンを分割できませんでした。")

        # タグ付けはシーン数に比例して長くかかるので、先に本文だけのシーンを保存して
        # 物語として成立させておく。途中で止まっても分割結果は残り、タグが空でも
        # 挿絵生成は本文から行える。
        add_story_scenes(
            conn,
            story_id,
            [
                {"draft_title": None, "draft_text": text, "draft_prompt_tags": "", "novelai_text": text}
                for text in scene_texts
            ],
        )
        update_story_scene_count(conn, story_id, len(scene_texts))
        update_story_status(conn, story_id, "written")
        scenes = list_story_scenes(conn, story_id)
        panels_per_page = story["panels_per_page"]
    finally:
        conn.close()

    batches = [
        (scenes[i : i + _TAG_BATCH_SIZE], scene_texts[i : i + _TAG_BATCH_SIZE])
        for i in range(0, len(scene_texts), _TAG_BATCH_SIZE)
    ]
    job.total = len(batches)
    pages = -(-len(scene_texts) // panels_per_page)
    job.message = f"{len(scene_texts)}シーン({pages}ページ相当)に分割しました。タグ付け中..."

    tagged = 0
    gave_up = False
    for index, (batch_scenes, batch_texts) in enumerate(batches, start=1):
        job.message = f"タグ付け中 {index}/{len(batches)}"
        tagged += await _apply_tags(batch_scenes, batch_texts)
        job.progress = index
        if _should_give_up(index, tagged):
            gave_up = True
            break

    job.message = f"{len(scene_texts)}シーンに分割し、{tagged}シーンにタグを付けました"
    if gave_up:
        job.message += "(タグ付けが連続で失敗したため中断しました。挿絵生成は本文だけでも実行できます)"
    elif tagged < len(scene_texts):
        job.message += f"(未設定{len(scene_texts) - tagged}シーンは「タグ付けを実行」でやり直せます)"


@router.post("/{story_id}/write")
async def write_story(story_id: int, client: ClientDep, request: Request) -> StreamingResponse:
    async def gen() -> AsyncGenerator[str, None]:
        conn = get_connection()
        try:
            scenes = list_story_scenes(conn, story_id)
            if not scenes:
                yield sse_event("error", {"detail": "story not found"})
                return

            running_text = ""
            for i, scene in enumerate(scenes, start=1):
                if await request.is_disconnected():
                    return
                seed_cue = ""
                for seed_attempt in range(1, 3):
                    yield sse_event(
                        "progress",
                        {"message": f"シーン{i}/{len(scenes)}: Ollamaが呼び水を作成中...(試行{seed_attempt}/2)"},
                    )
                    parts: list[str] = []
                    async for delta in stream_llm_text(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "あなたは小説の続きを書くAIへの「呼び水」を作る役割です。\n"
                                    "これまでの本文と、次のシーンの下書きを渡すので、"
                                    "自然につながる書き出しを日本語1〜2文だけ出力してください。"
                                    "説明や前置きは不要、本文の一部として使える文だけを出力すること。"
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"これまでの本文:\n{running_text or '(まだ本文はありません)'}\n\n"
                                    f"次のシーンの下書き:\n{scene['draft_text']}"
                                ),
                            },
                        ],
                        request,
                        max_tokens=2048,  # 短い出力でも<think>推論に数千トークン使うことがあるため余裕を持たせる
                    ):
                        parts.append(delta)
                    if await request.is_disconnected():
                        return
                    seed_cue = strip_think_tags("".join(parts)).strip()
                    if seed_cue:
                        break

                if not seed_cue:
                    # Ollamaが2回とも空を返した場合、Kayraに空の文脈を渡すと前提と
                    # 無関係な内容を書き始めてしまう(実機で確認済み: 猫と少女の話の
                    # はずが宇宙船の話になった)。シーンの下書き自体を呼び水として使う。
                    seed_cue = scene["draft_text"][:200]

                yield sse_event("progress", {"message": f"シーン{i}/{len(scenes)}: NovelAI公式(Kayra)が本文を執筆中..."})
                kayra_context = f"{running_text}\n{seed_cue}" if running_text else seed_cue
                continuation = await generate_kayra(client, kayra_context, max_length=80)

                novelai_text = f"{seed_cue}{continuation}"
                update_scene_writing(conn, scene["id"], seed_cue, novelai_text)
                running_text = f"{running_text}\n{novelai_text}" if running_text else novelai_text

            update_story_status(conn, story_id, "written")
            yield sse_event("done", _story_response(get_story(conn, story_id)).model_dump())
        except Exception as exc:
            yield sse_event("error", {"detail": str(exc)})
        finally:
            conn.close()

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/{story_id}/illustrate", response_model=StoryJobResponse)
async def illustrate_story(story_id: int, req: StoryIllustrateRequest, client: ClientDep) -> dict[str, Any]:
    """
    ページ単位でV5にコマ割り画像を生成させる。長編は分割すると数十〜百ページ規模に
    なり一度に生成できないため、page_from/page_to で生成するページ範囲を絞れる。

    1ページに数十秒かかり全体では数十分になるので、処理はバックグラウンドで走らせて
    即座に返す。進捗は GET /{story_id}/job を見る。
    """
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
    finally:
        conn.close()

    if not scenes:
        raise HTTPException(status_code=404, detail="story not found")

    page_indexes = sorted({scene["page_index"] for scene in scenes})
    targets = [
        p for p in page_indexes if p >= req.page_from and (req.page_to is None or p <= req.page_to)
    ]
    if not targets:
        raise HTTPException(status_code=400, detail="指定した範囲にページがありません。")

    # get_client はリクエスト終了時にクライアントを閉じるため、バックグラウンドでは
    # 使えない。必要なのはトークンだけなのでここで取り出しておく。
    api_key = client.api_key

    async def runner(job: _Job) -> None:
        await _run_illustrate(job, story_id, req, api_key, targets)

    return _job_response(_start_job(story_id, "illustrate", runner))


async def _run_illustrate(
    job: _Job, story_id: int, req: StoryIllustrateRequest, api_key: str, targets: list[int]
) -> None:
    job.total = len(targets)
    settings = req.settings
    negative = {"negative_prompt": settings.negative_prompt} if settings.negative_prompt else {}

    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
    finally:
        conn.close()

    pages_scenes: dict[int, list[dict[str, Any]]] = {}
    for scene in scenes:
        pages_scenes.setdefault(scene["page_index"], []).append(scene)

    _MANGA_DIR.mkdir(parents=True, exist_ok=True)
    for i, page_index in enumerate(targets, start=1):
        job.message = f"ページ{page_index + 1}を生成中 ({i}/{len(targets)})"
        page_scenes = pages_scenes[page_index]
        panels = [
            {
                "draft_text": s["novelai_text"] or s["draft_text"],
                "draft_prompt_tags": s["draft_prompt_tags"],
            }
            for s in page_scenes
        ]
        # ページ内のコマに登場するキャラの容姿タグをまとめて渡す。characterPrompts は
        # 画像全体に効く指定でコマごとには分けられないため、重複を除いた和集合になる。
        character_tags: list[str] = []
        for scene in page_scenes:
            for character in scene.get("characters", []):
                tags = character["appearance_tags"].strip()
                if tags and tags not in character_tags:
                    character_tags.append(tags)

        # シード未指定ならページごとに変える(従来動作)。指定時は全ページ固定。
        seed = settings.seed if settings.seed is not None else story_id * 1000 + page_index
        image_bytes = await generate_manga_page(
            api_key,
            panels,
            character_tags=character_tags,
            model=settings.model,
            width=settings.width,
            height=settings.height,
            steps=settings.steps,
            scale=settings.scale,
            sampler=settings.sampler,
            noise_schedule=settings.noise_schedule,
            cfg_rescale=settings.cfg_rescale,
            seed=seed,
            **negative,
        )

        filename = f"story{story_id}_page{page_index}_{uuid4().hex[:8]}.png"
        (_MANGA_DIR / filename).write_bytes(image_bytes)

        conn = get_connection()
        try:
            create_manga_page(
                conn, story_id, page_index, f"outputs/manga/{filename}", [s["id"] for s in page_scenes]
            )
            update_story_status(conn, story_id, "illustrated")
        finally:
            conn.close()
        job.progress = i

    job.message = f"{len(targets)}ページの挿絵を生成しました"


@router.post("/{story_id}/retag", response_model=StoryJobResponse)
async def retag_story(story_id: int) -> dict[str, Any]:
    """
    タグが空のシーンにだけタグ付けをやり直す。分割は先にDBへ書き込む方式なので、
    タグ付けの途中で中断/キャンセルするとタグ無しのシーンが残る。その埋め直し用。
    """
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
    finally:
        conn.close()

    if not scenes:
        raise HTTPException(status_code=404, detail="story not found")
    untagged = [scene for scene in scenes if not scene["draft_prompt_tags"]]
    if not untagged:
        raise HTTPException(status_code=409, detail="タグ付けされていないシーンはありません。")

    async def runner(job: _Job) -> None:
        await _run_retag(job, untagged)

    return _job_response(_start_job(story_id, "split", runner))


async def _run_retag(job: _Job, untagged: list[dict[str, Any]]) -> None:
    batches = [untagged[i : i + _TAG_BATCH_SIZE] for i in range(0, len(untagged), _TAG_BATCH_SIZE)]
    job.total = len(batches)
    job.message = f"タグ未設定の{len(untagged)}シーンにタグ付けします"

    tagged = 0
    gave_up = False
    for index, batch in enumerate(batches, start=1):
        job.message = f"タグ付け中 {index}/{len(batches)}"
        tagged += await _apply_tags(batch, [scene["draft_text"] for scene in batch])
        job.progress = index
        if _should_give_up(index, tagged):
            gave_up = True
            break

    job.message = f"{len(untagged)}シーン中{tagged}シーンにタグを付けました"
    if gave_up:
        job.message += "(連続で失敗したため中断しました)"


@router.get("/{story_id}/job", response_model=StoryJobResponse | None)
async def get_story_job(story_id: int) -> dict[str, Any] | None:
    """進行中(または直前)のジョブの状態。ブラウザを閉じても処理は続くので、開き直したらこれを見る。"""
    job = _jobs.get(story_id)
    return _job_response(job) if job else None


@router.post("/{story_id}/job/cancel", response_model=StoryJobResponse | None)
async def cancel_story_job(story_id: int) -> dict[str, Any] | None:
    job = _jobs.get(story_id)
    if job is None:
        return None
    if job.status == "running" and job.task is not None:
        job.task.cancel()
    return _job_response(job)


@router.post("/{story_id}/export-manga")
async def export_manga(story_id: int) -> dict[str, str]:
    conn = get_connection()
    try:
        pages = list_manga_pages(conn, story_id)
        if not pages:
            raise HTTPException(status_code=404, detail="manga pages not found; call /illustrate first")

        scenes_by_id = {s["id"]: s for s in list_story_scenes(conn, story_id)}
        assemble_input = []
        for page in pages:
            captions = [
                (scenes_by_id[sid]["novelai_text"] or scenes_by_id[sid]["draft_text"])
                for sid in page["scene_ids"]
                if sid in scenes_by_id
            ]
            assemble_input.append(
                {
                    "image_path": _PROJECT_ROOT / page["image_path"],
                    "caption": "\n".join(c.strip() for c in captions if c and c.strip()),
                }
            )

        final_bytes = assemble_manga(assemble_input)
        _MANGA_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"story{story_id}_final_{uuid4().hex[:8]}.png"
        (_MANGA_DIR / filename).write_bytes(final_bytes)
        image_path = f"outputs/manga/{filename}"

        update_story_status(conn, story_id, "completed")
        update_story_final_image(conn, story_id, image_path)
        return {"image_path": image_path}
    finally:
        conn.close()


@router.get("", response_model=list[StorySummary])
async def list_stories_route(limit: int = 50) -> list[StorySummary]:
    """ブラウザ再読み込み後などに過去の物語一覧へ戻れるようにする。"""
    conn = get_connection()
    try:
        return [StorySummary.model_validate(s) for s in list_stories(conn, limit)]
    finally:
        conn.close()


# 抽出は1回のリクエストで扱うシーン数を絞る。タグ付けと同じくローカルLLMの
# コンテキストに収める必要があり、加えて「誰が出ているか」と「その人の容姿」の
# 2種類を同時に返させるぶん出力も長くなる。
_CHARACTER_BATCH_SIZE = 5


def _character_json_schema(n_scenes: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "minItems": n_scenes,
                "maxItems": n_scenes,
                "items": {
                    "type": "object",
                    "properties": {"names": {"type": "string"}},
                    "required": ["names"],
                },
            },
            "characters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "appearance_tags": {"type": "string"},
                    },
                    "required": ["name", "appearance_tags"],
                },
            },
        },
        "required": ["scenes", "characters"],
    }


def _character_system_prompt(n_scenes: int) -> str:
    return (
        "あなたは物語から登場人物を抽出するAIです。\n"
        "渡された場面それぞれについて、そこに登場する人物の名前を挙げ、"
        "さらに登場した人物の容姿を画像生成用の英語タグにしてJSONで返してください。\n\n"
        f"- scenes配列にちょうど{n_scenes}個の要素を、渡された場面の順番通りに含める\n"
        "- scenes[].names: その場面に登場する人物名をコンマ区切りで(該当なしなら空文字)\n"
        "- 名前として挙げてよいのは固有名詞(人名・あだ名・「先生」のような固有の呼称)だけ\n"
        "- 代名詞は絶対に名前として扱わない(私/僕/俺/あたし/彼/彼女/あなた/君/我 など)\n"
        "- 「生徒達」「二人の女子」のような集団や、文になっている説明も名前にしない\n"
        "- 名前が分からない人物はその場面では挙げない\n"
        "- characters: この範囲で登場した人物の name と appearance_tags\n"
        "- appearance_tags は必ず英語のタグをコンマ区切りで書く(日本語・中国語は使わない)\n"
        '- 例: {"name": "サクラ", "appearance_tags": "1girl, long black hair, blue eyes, school uniform"}'
    )


# 一人称視点の作品では、代名詞がそのまま人物名として大量に返ってくることを実機で確認した
# (私/彼女/我/我是/私は…が別々のキャラとして登録され、同一人物の容姿も矛盾していた)。
# プロンプトで禁じても完全には従わないため、コード側でも弾く。
_PRONOUN_NAMES = {
    "私", "私は", "わたし", "あたし", "僕", "ぼく", "俺", "おれ", "自分", "我", "我是", "我々",
    "彼", "彼女", "彼ら", "彼女ら", "あなた", "貴方", "君", "きみ", "お前", "おまえ",
    "二人", "三人", "皆", "みんな", "全員", "男", "女", "少年", "少女",
}
_GROUP_SUFFIXES = ("達", "たち", "ら", "群", "全員")
_MAX_NAME_LENGTH = 15


def _is_usable_character_name(name: str) -> bool:
    if not name or name in _PRONOUN_NAMES:
        return False
    # 「私、彼女」のように複数を1つにまとめた文字列や、文になっている説明を弾く。
    if len(name) > _MAX_NAME_LENGTH or any(ch in name for ch in "、。！？,"):
        return False
    if name.endswith(_GROUP_SUFFIXES):
        return False
    # 「私（教師）」のように代名詞に補足を付けた形も、結局は語り手を指していて
    # 別のキャラとして登録すると重複するだけなので弾く。
    if any(name.startswith(pronoun) for pronoun in _PRONOUN_NAMES):
        return False
    return True


def _clean_appearance_tags(tags: str) -> str:
    """
    英語タグ以外を落とす。NovelAIのプロンプトは英語(danbooru系)前提なので、
    日本語や中国語のまま渡しても効かない。実機では「髪色茶色」「中等身材」といった
    出力が混ざったため、非ASCIIを多く含むタグは捨てる。
    """
    kept: list[str] = []
    for tag in tags.split(","):
        tag = tag.strip()
        if not tag:
            continue
        ascii_ratio = sum(1 for ch in tag if ch.isascii()) / len(tag)
        if ascii_ratio > 0.8:
            kept.append(tag)
    return ", ".join(kept)


def _parse_characters(text: str, n_scenes: int) -> tuple[list[list[str]], dict[str, str]] | None:
    """(場面ごとの人物名リスト, 人物名→容姿タグ) を返す。パースできなければ None。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        return None

    per_scene: list[list[str]] = []
    for scene in raw_scenes[:n_scenes]:
        names_text = scene.get("names") if isinstance(scene, dict) else None
        names = [n.strip() for n in str(names_text or "").split(",") if n.strip()]
        per_scene.append([n for n in names if _is_usable_character_name(n)])

    appearances: dict[str, str] = {}
    for entry in data.get("characters") or []:
        if not isinstance(entry, dict):
            continue
        tags = _clean_appearance_tags(str(entry.get("appearance_tags") or ""))
        # name に「生徒達, 我」のように複数を詰めて返してくることがあるので分解する。
        for name in str(entry.get("name") or "").split(","):
            name = name.strip()
            if _is_usable_character_name(name):
                appearances[name] = tags

    return per_scene, appearances


@router.post("/{story_id}/extract-characters", response_model=StoryJobResponse)
async def extract_characters(story_id: int) -> dict[str, Any]:
    """
    本文から登場人物と容姿を抽出し、キャラとして登録してシーンに割り当てる。
    キャラは物語をまたいで使い回せるよう名前で一意にしており、既に同名で登録済みなら
    そちらを使う(AIアシスタントのキャラ生成で作った設定もそのまま流用できる)。
    """
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
    finally:
        conn.close()

    if not scenes:
        raise HTTPException(status_code=404, detail="先にシーン分割を実行してください。")

    async def runner(job: _Job) -> None:
        await _run_extract_characters(job, scenes)

    return _job_response(_start_job(story_id, "characters", runner))


async def _run_extract_characters(job: _Job, scenes: list[dict[str, Any]]) -> None:
    batches = [
        scenes[i : i + _CHARACTER_BATCH_SIZE] for i in range(0, len(scenes), _CHARACTER_BATCH_SIZE)
    ]
    job.total = len(batches)
    found = 0

    for index, batch in enumerate(batches, start=1):
        job.message = f"登場人物を抽出中 {index}/{len(batches)}"
        texts = [scene["draft_text"][:_TAG_TEXT_CHARS] for scene in batch]

        parsed = None
        for _ in range(_DRAFT_MAX_ATTEMPTS):
            parts: list[str] = []
            async for delta in stream_llm_text(
                [
                    {"role": "system", "content": _character_system_prompt(len(batch))},
                    {"role": "user", "content": _format_scenes_for_tagging(texts)},
                ],
                max_tokens=_TAG_MAX_TOKENS,
                json_schema=_character_json_schema(len(batch)),
            ):
                parts.append(delta)
            parsed = _parse_characters(strip_think_tags("".join(parts)), len(batch))
            if parsed is not None:
                break

        job.progress = index
        if parsed is None:
            if _should_give_up(index, found):
                job.message = f"登場人物を抽出できませんでした({index}バッチ試行)"
                return
            continue

        per_scene, appearances = parsed
        conn = get_connection()
        try:
            ids_by_name: dict[str, int] = {}
            for name, tags in appearances.items():
                ids_by_name[name] = save_character(conn, name, tags)["id"]

            for scene, names in zip(batch, per_scene):
                character_ids = []
                for name in names:
                    if name not in ids_by_name:
                        # 場面側にだけ出てきた名前も、容姿は後で埋められるよう登録しておく。
                        ids_by_name[name] = save_character(conn, name, "")["id"]
                    character_ids.append(ids_by_name[name])
                if character_ids:
                    set_scene_characters(conn, scene["id"], character_ids)
                    found += 1
        finally:
            conn.close()

    job.message = f"{found}シーンに登場人物を割り当てました"


@router.get("/characters", response_model=list[CharacterResponse])
async def get_characters() -> list[dict[str, Any]]:
    """登録済みキャラの一覧(物語共通)。"""
    conn = get_connection()
    try:
        return list_characters(conn)
    finally:
        conn.close()


@router.post("/characters", response_model=CharacterResponse)
async def create_character(req: CharacterSaveRequest) -> dict[str, Any]:
    """キャラを登録/更新する。同じ名前なら上書き(容姿タグが空なら既存値を残す)。"""
    conn = get_connection()
    try:
        return save_character(conn, req.name, req.appearance_tags, req.notes)
    finally:
        conn.close()


@router.delete("/characters/{character_id}", status_code=204)
async def delete_character_endpoint(character_id: int) -> None:
    conn = get_connection()
    try:
        delete_character(conn, character_id)
    finally:
        conn.close()


@router.put("/scenes/{scene_id}/characters", status_code=204)
async def put_scene_characters(scene_id: int, req: SetSceneCharactersRequest) -> None:
    """シーンに登場するキャラを設定し直す(抽出結果の手直し用)。"""
    conn = get_connection()
    try:
        set_scene_characters(conn, scene_id, req.character_ids)
    finally:
        conn.close()


@router.get("/manga-file")
async def get_manga_file(path: str) -> FileResponse:
    """
    outputs/manga/ 配下の画像ファイルだけを配信する(パストラバーサル対策込み)。

    注意: このルートは /{story_id} より前に登録しなければならない。Starletteは
    パスを構造(セグメント数)で先にマッチさせ、型変換(int)に失敗しても後続の
    リテラルルートへはフォールバックせず422を返すため、/{story_id} を先に
    登録すると "/manga-file" がそちらにマッチしてしまう(実機で確認済み)。
    """
    full_path = (_PROJECT_ROOT / path).resolve()
    try:
        full_path.relative_to(_MANGA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="invalid path")
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(full_path, media_type="image/png")


@router.get("/{story_id}", response_model=StoryResponse)
async def get_story_route(story_id: int) -> StoryResponse:
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        if story is None:
            raise HTTPException(status_code=404, detail="story not found")
        return _story_response(story)
    finally:
        conn.close()


@router.get("/{story_id}/pages", response_model=list[MangaPageResponse])
async def get_manga_pages(story_id: int) -> list[MangaPageResponse]:
    conn = get_connection()
    try:
        return [MangaPageResponse.model_validate(p) for p in list_manga_pages(conn, story_id)]
    finally:
        conn.close()
