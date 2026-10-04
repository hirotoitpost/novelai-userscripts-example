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
import time
from collections import Counter
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
    characters_by_scene,
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
    update_story_layout,
    update_story_scene_count,
    update_story_status,
)
from ..keystore_crypto import decrypt_keystore, decrypt_object
from ..manga_export import assemble_manga
from ..notify import notify_job_finished
from ..models import (
    CharacterResponse,
    CharacterSaveRequest,
    MangaPageResponse,
    StoryDraftCreateRequest,
    StoryIllustrateRequest,
    StoryImportRequest,
    StoryJobResponse,
    StoryLayoutRequest,
    StoryPageStats,
    StoryResponse,
    StorySplitRequest,
    StorySummary,
    SetSceneCharactersRequest,
)
from ..novelai_image_v5 import DIALOGUE_RE, generate_manga_page
from ..novelai_text import generate_kayra
from ..novelai_text_oa import stream_chat
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
    raw_text = story.get("raw_text") or ""
    # 末尾の改行など空白だけの残りは「未分割」に数えない
    unsplit = len(raw_text[_covered_length(raw_text, story["scenes"]) :].strip()) if story["scenes"] else 0
    return StoryResponse.model_validate({**story, "unsplit_chars": unsplit})


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
# 送信中の通知タスク(参照を持たないと途中で GC されることがある)
_notify_tasks: set[asyncio.Task[None]] = set()


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
        started = time.monotonic()
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
        finally:
            # キャンセル中でも通知だけは送り切る(スマホを見ていない間に終わることが多い)
            notice = notify_job_finished(
                story_id,
                kind,
                job.status,
                job.detail if job.status == "error" else job.message,
                time.monotonic() - started,
            )
            notify_task = asyncio.get_running_loop().create_task(notice)
            _notify_tasks.add(notify_task)
            notify_task.add_done_callback(_notify_tasks.discard)

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


def _covered_length(raw_text: str, scenes: list[dict[str, Any]]) -> int:
    """
    raw_text のうち、既存シーンの本文が覆っている先頭部分の長さ。

    分割は本文を書き換えず、段落(または文)の単位を改行で繋いでシーンにしているので、
    各シーンの draft_text を行ごとに先頭から順に探していけば、どこまで分割済みかが分かる。
    冒頭だけ分割した物語の続きを分割する位置と、登場人物抽出の対象範囲に使う。
    """
    pos = 0
    for scene in scenes:
        for line in scene["draft_text"].split("\n"):
            if not line:
                continue
            index = raw_text.find(line, pos)
            if index >= 0:
                pos = index + len(line)
    return pos


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
        "- prompt_tags: その場面の情景を画像生成するための英語タグ(コンマ区切り)\n"
        "- prompt_tags は英語のみ。日本語や中国語のタグは使わない\n"
        "  悪い例: 女子高中生, 舐め, 少女，害羞，闭眼\n"
        "  良い例: 1girl, cardigan, beach, embarrassed, blush\n"
        "- 本文に書かれていない服装・場所(制服、教室など)を足さない\n"
        "- 人名はタグにしない(画像生成モデルは名前を解釈できない)。"
        "その人物の見た目を表すタグに置き換える"
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
        # 英語で書けと指示してもCJKのタグが返ることがある(実機で64ページ中11ページ)。
        # そのまま渡すとV5がコマを描き分けられず、同じ絵の繰り返しになるので落とす。
        # 全部落ちたシーンはタグ空=未設定となり、/retag で付け直せる。
        result.append(
            {
                "draft_title": (str(scene.get("title") or "")).strip() or None,
                "draft_prompt_tags": _english_tags_only(str(scene.get("prompt_tags") or "")),
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
async def split_story(story_id: int, req: StorySplitRequest, client: ClientDep) -> dict[str, Any]:
    """
    /import で raw_text のまま保存しておいた物語を、本文には一切手を加えずに場面へ
    分割する。Ollamaはタグ付け(タイトル・画像生成タグ)のためだけに使い、本文の生成/
    書き換えには使わない。分割したシーンは novelai_text を draft_text と同じ値で埋め、
    /write (Kayraによる自動執筆)をスキップした状態で保存するため、そのまま
    /illustrate へ進める。

    長編では数十分かかるため、処理はバックグラウンドで走らせて即座に返す。
    進捗は GET /{story_id}/job を見る。

    max_scenes を指定すると冒頭のそのシーン数だけを処理する(テスト・事前確認用)。
    その後もう一度呼ぶと、残りの本文を既存シーンの続きとして分割する。
    """
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
    finally:
        conn.close()

    if story is None:
        raise HTTPException(status_code=404, detail="story not found")
    if not story.get("raw_text"):
        raise HTTPException(
            status_code=400, detail="raw_text がありません(インポートされた物語ではない可能性があります)。"
        )
    # 冒頭だけ分割済みなら続きから分割する。最後まで分割済みならやることが無い。
    if story["scenes"] and not story["raw_text"][_covered_length(story["raw_text"], story["scenes"]) :].strip():
        raise HTTPException(status_code=409, detail="この物語は既に最後まで分割済みです。")

    options = TagOptions(adult=req.adult, api_key=client.api_key if req.adult else None)

    async def runner(job: _Job) -> None:
        await _run_split(job, story_id, req, options)

    return _job_response(_start_job(story_id, "split", runner))


@dataclass(frozen=True)
class TagOptions:
    """タグ付けの設定。adult なら NovelAI の文章モデルで露骨なタグ(nsfw 付き)を付ける。"""

    adult: bool = False
    api_key: str | None = None


def _adult_tags_system_prompt(n_scenes: int) -> str:
    return (
        "You tag scenes of an adult (18+) Japanese story for the NovelAI image model.\n"
        "All characters are adults. Read each scene and return JSON only, no prose:\n"
        '{"scenes": [{"title": "<short Japanese title>", "prompt_tags": "<English danbooru tags>"}]}\n\n'
        f"- exactly {n_scenes} items, in the given order\n"
        "- prompt_tags: comma separated English danbooru-style tags describing what is visible: "
        "people count (1girl, 1boy), clothing state (nude, topless, bikini, clothes pull...), the sexual act "
        "and body parts exactly as written (explicit tags such as nipples, pussy, penis, sex, fellatio, cum are fine), "
        "pose, expression, location, framing\n"
        "- start with nsfw if the scene is sexual\n"
        "- whenever genitals are visible or involved, include explicit, uncensored; for a woman's genitals also "
        "pussy, and add related tags that fit the scene and character: pussy juice (aroused/wet), pubic hair "
        "(mature woman, unless shaved), clitoris (close-up, touching), anus (from behind, spread, all fours), "
        "spread legs, spread pussy, focus pussy, cumdrip, cum in pussy, vaginal, penis, testicles\n"
        "- if clothes are pulled aside or removed, say so (bikini pull, panties aside, clothes lift, nude) so the "
        "genitals are not hidden\n"
        "- never use tags implying minors (child, loli, shota, school uniform, student, classroom) and do not add "
        "places or clothes not in the text\n"
        "- no character names"
    )


def _extract_json_object(text: str) -> str:
    """前置きや ```json ``` で囲まれた応答から、最初の JSON オブジェクトだけを取り出す。"""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text


async def _tag_scene_batch(batch_texts: list[str], options: TagOptions | None = None) -> list[dict[str, Any]] | None:
    """1バッチ分のタグ付け。形式が崩れたら数回まで再試行し、それでも駄目ならNone。"""
    options = options or TagOptions()
    for _ in range(_DRAFT_MAX_ATTEMPTS):
        parts: list[str] = []
        if options.adult and options.api_key:
            # 成人向けはローカルLLM(qwen2.5:7b)だと露骨な語を避けて曖昧なタグになるため、
            # NovelAI の文章モデルを使う(スキーマ指定はできないので JSON を本文から取り出す)
            async for delta in stream_chat(
                options.api_key,
                [
                    {"role": "system", "content": _adult_tags_system_prompt(len(batch_texts))},
                    {"role": "user", "content": _format_scenes_for_tagging(batch_texts)},
                ],
                max_tokens=_TAG_MAX_TOKENS * 2,
            ):
                parts.append(delta)
        else:
            async for delta in stream_llm_text(
                [
                    {"role": "system", "content": _import_tags_system_prompt(len(batch_texts))},
                    {"role": "user", "content": _format_scenes_for_tagging(batch_texts)},
                ],
                max_tokens=_TAG_MAX_TOKENS,
                json_schema=_import_tags_json_schema(len(batch_texts)),
            ):
                parts.append(delta)
        tags = _parse_import_tags(_extract_json_object(strip_think_tags("".join(parts))), len(batch_texts))
        if tags is not None:
            for tag in tags:
                tag["draft_prompt_tags"] = sanitize_scene_tags(tag["draft_prompt_tags"], adult=options.adult)
            return tags
    return None


# 未成年を思わせるタグ。成人向けの場面では必ず取り除く(性的な場面に子どもを描かない)
_MINOR_TAGS = re.compile(
    r"^(child|children|kid|kids|loli|lolita|shota|toddler|baby|young girl|little girl|young boy|little boy|"
    r"school uniform|serafuku|student|schoolgirl|schoolboy|classroom|elementary school|middle school|high school|"
    r"randoseru|children with cameras|petite child|underage|teen|teenager)$",
    re.IGNORECASE,
)
_SEXUAL_HINT = re.compile(
    r"\b(nsfw|nude|naked|sex|penis|pussy|nipples|fellatio|cum|vaginal|anal|masturbation|fingering|intercourse|"
    r"topless|bottomless|erection|ejaculation|orgasm)\b",
    re.IGNORECASE,
)


# 性器が見える/関わる場面の語。これがあれば explicit, uncensored を必ず付ける(付けないと布や構図で
# 隠されがち)。女性器が関わる語なら pussy も付ける。チャンクの実データ(191件)でも
# explicit, uncensored, pussy, pussy juice, spread legs, pubic hair の組み合わせで使われている。
_GENITAL_HINT = re.compile(
    r"\b(pussy|vagina|vaginal|clitoris|labia|anus|anal|penis|testicles|sex|intercourse|penetration|creampie|"
    r"cum in pussy|cumdrip|fingering|cunnilingus|spread legs|pubic hair|pussy juice)\b",
    re.IGNORECASE,
)
_FEMALE_GENITAL_HINT = re.compile(
    r"\b(pussy|vagina|vaginal|clitoris|labia|sex|intercourse|penetration|creampie|cum in pussy|cumdrip|"
    r"fingering|cunnilingus|spread legs|pubic hair|pussy juice)\b",
    re.IGNORECASE,
)


def sanitize_scene_tags(tags: str, *, adult: bool) -> str:
    """
    成人向け、または性的な語を含むタグから未成年を思わせるタグを除く。成人向けなら先頭に nsfw を付け、
    性器が関わる場面には explicit, uncensored(女性器なら pussy も)を足す。
    """
    items = [t.strip() for t in tags.split(",") if t.strip()]
    sexual = adult or any(_SEXUAL_HINT.search(t) for t in items)
    if sexual:
        items = [t for t in items if not _MINOR_TAGS.match(t)]
    lower = [t.lower() for t in items]
    if adult and any(_GENITAL_HINT.search(t) for t in items):
        extra = [t for t in ("explicit", "uncensored") if t not in lower]
        if any(_FEMALE_GENITAL_HINT.search(t) for t in items) and "pussy" not in lower:
            extra.append("pussy")
        items = [items[0], *extra, *items[1:]] if lower[0] == "nsfw" else [*extra, *items]
        lower = [t.lower() for t in items]
    if adult and items and "nsfw" not in lower and any(_SEXUAL_HINT.search(t) for t in items):
        items.insert(0, "nsfw")
    return ", ".join(dict.fromkeys(items))


async def _apply_tags(scenes: list[dict[str, Any]], texts: list[str], options: TagOptions | None = None) -> int:
    """
    1バッチ分のタグ付けとDB反映。使えるタグが付いたシーン数を返す(0なら丸ごと失敗)。

    書き込んだ行数ではなく中身のある行数を数える。CJKタグが落とされて空になった行を
    成功に数えると、打ち切り判定が働かず、完了メッセージも実態より多く見える。
    """
    tags = await _tag_scene_batch(texts, options)
    if not tags:
        return 0
    applied = 0
    conn = get_connection()
    try:
        for scene, tag in zip(scenes, tags):
            update_scene_tags(conn, scene["id"], tag["draft_title"], tag["draft_prompt_tags"])
            if tag["draft_prompt_tags"]:
                applied += 1
    finally:
        conn.close()
    return applied


# タグ付けが最初から一つも通らない場合、以降のバッチも通らないことがほとんどなので、
# 長編で何十分も無駄に回さないよう早い段階で打ち切る。実機では qwen3-vl:2b が
# 日本語入力に対して思考を延々と出し続け、出力枠を使い切って本文(JSON)を一度も
# 返さないという状態を確認している(英語の物語では同じ設定で成功する)。
_TAG_GIVE_UP_AFTER = 3


def _should_give_up(batch_index: int, tagged: int) -> bool:
    return tagged == 0 and batch_index >= _TAG_GIVE_UP_AFTER


async def _run_split(job: _Job, story_id: int, req: StorySplitRequest, options: TagOptions | None = None) -> None:
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        if story is None:
            raise RuntimeError("story not found")
        raw_text = story["raw_text"] or ""
        existing = story["scenes"]
        remaining = raw_text[_covered_length(raw_text, existing) :] if existing else raw_text
        all_texts = _split_text_into_scenes(remaining, req.max_paragraphs, req.max_chars)
        if not all_texts:
            raise RuntimeError("本文からシーンを分割できませんでした。")
        scene_texts = all_texts[: req.max_scenes] if req.max_scenes else all_texts
        partial = len(scene_texts) < len(all_texts)

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
            start_index=len(existing),
        )
        update_story_scene_count(conn, story_id, len(existing) + len(scene_texts))
        update_story_status(conn, story_id, "written")
        # タグ付けは今回追加したシーンだけ
        scenes = list_story_scenes(conn, story_id)[len(existing) :]
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
        tagged += await _apply_tags(batch_scenes, batch_texts, options)
        job.progress = index
        if _should_give_up(index, tagged):
            gave_up = True
            break

    job.message = f"{len(scene_texts)}シーンに分割し、{tagged}シーンにタグを付けました"
    if partial:
        job.message += f"(冒頭のみ。残り約{len(all_texts) - len(scene_texts)}シーンは「続きを分割」で処理できます)"
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
            complexity=settings.complexity,
            seed=seed,
            **negative,
        )

        filename = f"story{story_id}_page{page_index}_{uuid4().hex[:8]}.png"
        (_MANGA_DIR / filename).write_bytes(image_bytes)

        conn = get_connection()
        try:
            create_manga_page(
                conn,
                story_id,
                page_index,
                f"outputs/manga/{filename}",
                [s["id"] for s in page_scenes],
                seed=seed,
            )
            update_story_status(conn, story_id, "illustrated")
        finally:
            conn.close()
        job.progress = i

    job.message = f"{len(targets)}ページの挿絵を生成しました"


@router.get("/{story_id}/page-stats", response_model=list[StoryPageStats])
async def get_page_stats(story_id: int) -> list[dict[str, Any]]:
    """
    ページごとのセリフ数と登場人物の充足度を返す。

    1ページの生成には時間とAnlasがかかるので、全ページを機械的に流す前に当たりを
    付けられるようにする。

    実機で2ページを生成して比べた限りでは、識別できたのは登場人物の充足度だった。
    悪かったページは全64ページで唯一キャラが4/8コマにしか付いておらず、コマ数が
    増えて同じ構図の反復になった。一方セリフ数は当てにならない - むしろ悪かった方が
    全ページ中で最多(40)で、良かったページは25だった。説明や議論の場面は会話量が
    多くても絵の変化に乏しいためと思われる。2ページの比較でしかないので、
    判断材料として出すに留め、良し悪しの決めつけはしない。
    """
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
        if not scenes:
            raise HTTPException(status_code=404, detail="story not found")
        by_scene = characters_by_scene(conn, story_id)
        seeds = {page["page_index"]: page.get("seed") for page in list_manga_pages(conn, story_id)}
    finally:
        conn.close()

    pages: dict[int, list[dict[str, Any]]] = {}
    for scene in scenes:
        pages.setdefault(scene["page_index"], []).append(scene)

    result: list[dict[str, Any]] = []
    for page_index in sorted(pages):
        page_scenes = pages[page_index]
        names: list[str] = []
        for scene in page_scenes:
            for character in by_scene.get(scene["id"], []):
                if character["name"] not in names:
                    names.append(character["name"])
        result.append(
            {
                "page_index": page_index,
                "scenes": len(page_scenes),
                "dialogue_lines": sum(
                    len(DIALOGUE_RE.findall(scene["novelai_text"] or scene["draft_text"]))
                    for scene in page_scenes
                ),
                "scenes_with_characters": sum(1 for s in page_scenes if by_scene.get(s["id"])),
                "characters": names,
                "generated": page_index in seeds,
                "seed": seeds.get(page_index),
            }
        )
    return result


@router.put("/{story_id}/layout", response_model=StoryResponse)
async def set_story_layout(story_id: int, req: StoryLayoutRequest) -> StoryResponse:
    """1ページのコマ数を変更する。既存シーンのページ割り当ても振り直す。"""
    conn = get_connection()
    try:
        if get_story(conn, story_id) is None:
            raise HTTPException(status_code=404, detail="story not found")
        update_story_layout(conn, story_id, req.panels_per_page)
        result = get_story(conn, story_id)
    finally:
        conn.close()
    return _story_response(result)


@router.post("/{story_id}/retag", response_model=StoryJobResponse)
async def retag_story(story_id: int, client: ClientDep, adult: bool = False, all: bool = False) -> dict[str, Any]:  # noqa: A002
    """
    タグが空のシーンにだけタグ付けをやり直す。分割は先にDBへ書き込む方式なので、
    タグ付けの途中で中断/キャンセルするとタグ無しのシーンが残る。その埋め直し用。
    all=True なら全シーンのタグを付け直す。adult=True なら成人向けのタグ(NovelAI の文章モデル)。
    """
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
    finally:
        conn.close()

    if not scenes:
        raise HTTPException(status_code=404, detail="story not found")
    untagged = scenes if all else [scene for scene in scenes if not scene["draft_prompt_tags"]]
    if not untagged:
        raise HTTPException(status_code=409, detail="タグ付けされていないシーンはありません。")
    options = TagOptions(adult=adult, api_key=client.api_key if adult else None)

    async def runner(job: _Job) -> None:
        await _run_retag(job, untagged, options)

    return _job_response(_start_job(story_id, "split", runner))


async def _run_retag(job: _Job, untagged: list[dict[str, Any]], options: TagOptions | None = None) -> None:
    batches = [untagged[i : i + _TAG_BATCH_SIZE] for i in range(0, len(untagged), _TAG_BATCH_SIZE)]
    job.total = len(batches)
    job.message = f"タグ未設定の{len(untagged)}シーンにタグ付けします"

    tagged = 0
    gave_up = False
    for index, batch in enumerate(batches, start=1):
        job.message = f"タグ付け中 {index}/{len(batches)}"
        tagged += await _apply_tags(batch, [scene["draft_text"] for scene in batch], options)
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


# 登場人物の抽出は「全体から名前を確定 → 描写を探す → シーンへ割当」の順で行う。
# 以前はシーン数件ずつ独立にLLMへ投げていたが、全体像が無いため同一人物が別名で
# 何度も登録され(私/私は/我/我是)、容姿も数行の窓からの推測になっていた。
# この順序ならLLMは「候補が人物か」「描写を英語タグに直す」という小さな判断だけを担い、
# 名前の収集とシーンへの割当は決定的な処理で済む。

# 敬称付きの呼びかけ。日本語の小説では人物名の手がかりとして最も精度が高い。
_HONORIFIC_RE = re.compile(r"([一-龥ぁ-んァ-ヶーA-Za-z]{1,8})(さん|ちゃん|くん|君|様|さま|先輩|先生)(?![者間生])")
# カタカナ表記の名前。一般語も拾うので後段の判定で落とす。
_KATAKANA_RE = re.compile(r"[ァ-ヶー]{3,10}")
# 「〜」と太郎は言った のような、会話文直後の話者位置。
_SPEAKER_RE = re.compile(r"[」』]\s*と?([一-龥ぁ-んァ-ヶー]{2,6})(?:は|が)")

# 固有名詞は作中で繰り返し現れる。1〜2回しか出ない語はまず人物名ではない。
_MIN_NAME_OCCURRENCES = 3
_MAX_NAME_CANDIDATES = 30

# 容姿が書かれている箇所を探すための手がかり。
_APPEARANCE_KEYWORDS = (
    "髪", "瞳", "目", "背", "身長", "体型", "スタイル", "服", "着", "制服", "眼鏡", "メガネ",
    "顔", "肌", "唇", "胸", "帽子", "スカート", "ドレス", "コート", "姿",
)
_APPEARANCE_PASSAGE_LIMIT = 3
_APPEARANCE_PASSAGE_CHARS = 200


# 一人称視点の作品では、代名詞がそのまま人物名として返ってくることを実機で確認した
# (私/彼女/我/我是…が別々のキャラとして登録され、同一人物の容姿も矛盾していた)。
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


def _english_tags_only(tags: str) -> str:
    """
    英語タグ以外を落とす。NovelAIのプロンプトは英語(danbooru系)前提なので、
    日本語や中国語のまま渡しても効かない。実機では「髪色茶色」「中等身材」といった
    出力が混ざったため、非ASCIIを多く含むタグは捨てる。

    容姿タグと場面タグの両方で使う。場面タグが全部落ちて空になった場合、そのシーンは
    「タグ未設定」として扱われ /retag の対象に戻る。CJKのまま画像生成に渡すより、
    付け直させたほうがよい。
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


# 敬称を外した形。「美香」と「美香さん」、「美琴」と「美琴ちゃん」が別人として
# 登録されるのを防ぐ(実データで確認)。「先生」のように敬称そのものが呼称になって
# いる語は、外すと空になるのでそのまま残る。
_HONORIFIC_SUFFIXES = ("さん", "ちゃん", "くん", "君", "様", "さま", "先輩")


def _canonical_name(name: str) -> str:
    for suffix in _HONORIFIC_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


# メモリの人物設定の行。「名前(読み) 説明」「名前: 説明」の形(行頭の「登場人物:」は外す)。
# メモリの人物設定の行。「名前(読み) 説明」「名前: 説明」の形(行頭の「登場人物:」は外す)。
# 姓と名を空白で区切った「矢野 栄子(やの えいこ) 説明」も受け付け、名だけの表記(本文の「栄子」)も
# 同じ人物として扱う。名は4文字まで・直後に読みか区切りが来る場合だけ(「源三 七十歳の店主」を
# 姓名と取り違えないため)。
_MEMORY_CHARACTER_RE = re.compile(
    r"^(?P<family>[^\s(（:：、。]{1,8})"
    r"(?:[ 　](?P<given>[^\s(（:：、。]{1,4})(?=[(（:：\s　]))?"
    r"(?:[(（](?P<reading>[^)）]{1,20})[)）])?[\s　:：]+(?P<description>.+)$"
)
_MEMORY_SECTION_RE = re.compile(r"^(?:登場人物|キャラクター|人物)[:：\s　]*")


def _memory_characters(memory: str) -> dict[str, dict[str, Any]]:
    """
    メモリから人物設定を取り出す: {名前: {"aliases": [...], "description": 説明}}。
    容姿の語を含む行だけを人物とみなす(「舞台: 港町」「文体: 三人称」を除くため)。
    姓名を空白で区切って書いた人物は、姓名続き・名だけ・読み(名の読みも)を別名にする。
    """
    result: dict[str, dict[str, Any]] = {}
    for raw in memory.splitlines():
        line = _MEMORY_SECTION_RE.sub("", raw.strip())
        match = _MEMORY_CHARACTER_RE.match(line)
        if not match:
            continue
        family, given = match.group("family"), match.group("given")
        reading, description = match.group("reading"), match.group("description")
        name = family + (given or "")
        if not any(word in description for word in _APPEARANCE_KEYWORDS) or not _is_usable_character_name(name):
            continue
        aliases = [name]
        if given:
            aliases += [f"{family} {given}", given]
        if reading:
            parts = reading.split()
            aliases.append("".join(parts))
            if given and len(parts) == 2:
                aliases.append(parts[1])
        aliases = list(dict.fromkeys(a for a in aliases if a))
        result[name] = {"aliases": aliases, "description": f"{name}: {description}"}
    return result


def _merge_candidates(defined: dict[str, dict[str, Any]], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """メモリの人物を先頭に置き、本文から拾った候補のうち同じ人物の表記はそちらへまとめる。"""
    merged = [{"name": name, "aliases": list(info["aliases"])} for name, info in defined.items()]
    for candidate in found:
        owner = next((m for m in merged if _canonical_name(candidate["name"]) in map(_canonical_name, m["aliases"])), None)
        if owner is None:
            merged.append(candidate)
            continue
        for alias in candidate["aliases"]:
            if alias not in owner["aliases"]:
                owner["aliases"].append(alias)
    for person in merged:
        person["aliases"].sort(key=len, reverse=True)
    return merged


def _collect_name_candidates(text: str) -> list[dict[str, Any]]:
    """
    本文全体から人物名の候補を集める(LLMは使わない)。
    敬称違いは1件にまとめ、{"name": 代表表記, "aliases": [表記...]} の形で
    出現回数の多い順に返す。ここでは人物かどうかの判断はせず、候補を絞るだけ。
    """
    counts: Counter[str] = Counter()
    for match in _HONORIFIC_RE.finditer(text):
        counts[match.group(1) + match.group(2)] += 1
    for match in _KATAKANA_RE.finditer(text):
        counts[match.group(0)] += 1
    for match in _SPEAKER_RE.finditer(text):
        counts[match.group(1)] += 1

    groups: dict[str, Counter[str]] = {}
    for name, count in counts.items():
        if count < _MIN_NAME_OCCURRENCES or not _is_usable_character_name(name):
            continue
        # 「ておいて」のような動詞語尾の誤検出を落とす。人物名なら漢字かカタカナを含む。
        if not any("\u4e00" <= ch <= "\u9fff" or "\u30a1" <= ch <= "\u30f6" for ch in name):
            continue
        groups.setdefault(_canonical_name(name), Counter())[name] = count

    ordered = sorted(groups.values(), key=lambda surfaces: -sum(surfaces.values()))
    return [
        {
            "name": surfaces.most_common(1)[0][0],
            # 長い表記から先に照合する(「美香さん」を「美香」より優先して数えるため)。
            "aliases": sorted(surfaces, key=len, reverse=True),
        }
        for surfaces in ordered[:_MAX_NAME_CANDIDATES]
    ]


def _people_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"people": {"type": "array", "items": {"type": "string"}}},
        "required": ["people"],
    }


async def _confirm_people(candidates: list[str]) -> list[str]:
    """
    候補のうち人物を指すものだけを選ばせる。入力は語のリストだけなので小さく、
    ローカルの小型モデルでも安定する。失敗したら敬称付きの候補だけを残す。
    """
    system = (
        "あなたは小説の語彙から登場人物を見分けるAIです。\n"
        "渡された語のうち、人物を指すものだけを people 配列に入れてJSONで返してください。\n\n"
        "- 人名・あだ名・その人物を指す呼称(「先生」「お姉さん」など)は人物として扱う\n"
        "- 身体の部位、行為、物、場所、地名、一般名詞は人物ではないので除外する\n"
        "- 渡された語をそのままの表記で返す(語を作り変えない)"
    )
    for _ in range(_DRAFT_MAX_ATTEMPTS):
        parts: list[str] = []
        async for delta in stream_llm_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(candidates)},
            ],
            max_tokens=_TAG_MAX_TOKENS,
            json_schema=_people_json_schema(),
        ):
            parts.append(delta)
        try:
            data = json.loads(strip_think_tags("".join(parts)))
        except json.JSONDecodeError:
            continue
        people = data.get("people")
        if isinstance(people, list):
            # 実在しない名前を作られても困るので、候補にあるものだけ採用する。
            confirmed = [str(p).strip() for p in people if str(p).strip() in candidates]
            if confirmed:
                return confirmed

    return [name for name in candidates if _HONORIFIC_RE.fullmatch(name)]


def _appearance_passages(text: str, aliases: list[str]) -> list[str]:
    """名前と容姿の語が同じ文に出てくる箇所を集める。無ければ空(推測はしない)。"""
    passages: list[str] = []
    for sentence in _SENTENCE_BOUNDARY_RE.split(text):
        if any(alias in sentence for alias in aliases) and any(
            word in sentence for word in _APPEARANCE_KEYWORDS
        ):
            passages.append(sentence.strip()[:_APPEARANCE_PASSAGE_CHARS])
            if len(passages) >= _APPEARANCE_PASSAGE_LIMIT:
                break
    return passages


async def _describe_appearance(name: str, passages: list[str]) -> str:
    """本文の描写だけを根拠に容姿を英語タグへ直す。"""
    system = (
        "あなたは小説の描写を画像生成用のタグへ変換するAIです。\n"
        f"「{name}」の容姿について、渡された本文に書かれている特徴だけを"
        "英語のタグ(コンマ区切り)にして appearance_tags に入れ、JSONで返してください。\n\n"
        "- 本文に書かれていない特徴は足さない\n"
        "- 髪色・髪型・目の色・服装・年齢層など、絵に描ける特徴だけを挙げる\n"
        "- 出力は英語のタグのみ(日本語や中国語は使わない)\n"
        "- 人間なら 1girl / 1boy などの人数タグを先頭に付ける。動物・人外なら人数タグは付けず、"
        "fox, cat などの種族タグを先頭に置く\n"
        '- 例(人間): {"appearance_tags": "1girl, long black hair, blue eyes, school uniform"}\n'
        '- 例(動物): {"appearance_tags": "small white fox, animal, golden eyes, red collar"}'
    )
    schema = {
        "type": "object",
        "properties": {"appearance_tags": {"type": "string"}},
        "required": ["appearance_tags"],
    }
    for _ in range(_DRAFT_MAX_ATTEMPTS):
        parts: list[str] = []
        async for delta in stream_llm_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(passages)},
            ],
            max_tokens=_TAG_MAX_TOKENS,
            json_schema=schema,
        ):
            parts.append(delta)
        try:
            data = json.loads(strip_think_tags("".join(parts)))
        except json.JSONDecodeError:
            continue
        tags = _drop_people_count_for_animals(_english_tags_only(str(data.get("appearance_tags") or "")))
        if tags:
            return tags
    return ""


_ANIMAL_WORDS = ("fox", "cat", "dog", "wolf", "rabbit", "bird", "animal", "dragon", "kitsune")
_PEOPLE_COUNT_RE = re.compile(r"^\d+(?:girls?|boys?|others?)$")


def _drop_people_count_for_animals(tags: str) -> str:
    """
    動物のキャラに 1girl / 1boy が付くと人間(獣耳の少女など)で描かれてしまう。プロンプトで
    指示しても小さいモデルは付けてくる(実機の qwen2.5:7b で「1girl, small white fox」)ので、
    動物の語があれば人数タグを落とす。
    """
    items = [t.strip() for t in tags.split(",") if t.strip()]
    if not any(word in t.lower() for t in items for word in _ANIMAL_WORDS):
        return tags
    return ", ".join(t for t in items if not _PEOPLE_COUNT_RE.match(t.lower()))


@router.post("/{story_id}/extract-characters", response_model=StoryJobResponse)
async def extract_characters(story_id: int, overwrite_appearance: bool = False) -> dict[str, Any]:
    """
    本文全体から登場人物を洗い出し、容姿の描写があれば対応付けて、登場するシーンへ
    割り当てる。キャラは物語をまたいで使い回せるよう名前で一意にしているので、
    既に同名で登録済みならそちらを使う(AIアシスタントのキャラ生成で作った設定も流用できる)。

    overwrite_appearance=False(既定)なら、登録済みで容姿タグがあるキャラの容姿は変えない
    (手で直したタグを守るため。容姿が空のキャラだけ埋める)。True なら抽出結果で上書きする。
    """
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
    finally:
        conn.close()

    if story is None or not story["scenes"]:
        raise HTTPException(status_code=404, detail="先にシーン分割を実行してください。")

    async def runner(job: _Job) -> None:
        await _run_extract_characters(job, story, overwrite_appearance)

    return _job_response(_start_job(story_id, "characters", runner))


async def _run_extract_characters(job: _Job, story: dict[str, Any], overwrite_appearance: bool = False) -> None:
    scenes = story["scenes"]
    # 取り込んだ物語は raw_text が原文。ドラフト生成のものは無いのでシーンを繋ぐ。
    # 冒頭だけ分割した物語では、まだシーンになっていない残りの本文は対象にしない。
    raw_text = story.get("raw_text")
    source_text = (
        raw_text[: _covered_length(raw_text, scenes)]
        if raw_text
        else "\n".join(scene["draft_text"] for scene in scenes)
    )
    # 物語エディタから来た物語は、メモリの1行1人の人物設定(「源三(げんぞう) 七十歳の店主。白髪…」)を
    # 名前と容姿の一次情報として使う。本文からの候補探しはひらがなの名前(こはく)を拾えず、
    # 容姿も本文には書かれていないことが多いため。
    defined = _memory_characters(story.get("memory") or "")

    job.total = 3
    job.message = "本文から名前の候補を集めています"
    candidates = _merge_candidates(defined, _collect_name_candidates(source_text))
    if not candidates:
        job.message = (
            "名前の候補が見つかりませんでした"
            "(固有名詞が出てこない作品では、キャラを手動で登録してください)"
        )
        return
    job.progress = 1

    job.message = f"{len(candidates)}件の候補から人物を判定しています"
    # メモリで人物として定義した名前はLLMの判定にかけない(判定は本文から拾った候補だけ)
    confirmed = await _confirm_people([c["name"] for c in candidates if c["name"] not in defined])
    people = [c for c in candidates if c["name"] in confirmed or c["name"] in defined]
    if not people:
        job.message = "候補から人物を判定できませんでした"
        return
    job.progress = 2

    conn = get_connection()
    try:
        existing = {c["name"]: c for c in list_characters(conn)}
        described = 0
        kept = 0
        for index, person in enumerate(people, start=1):
            current = existing.get(person["name"])
            if not overwrite_appearance and current and current["appearance_tags"].strip():
                # 容姿は登録済みのものを使う(LLMにも問い合わせない)
                person["id"] = current["id"]
                kept += 1
                continue
            job.message = f"容姿の描写を探しています {index}/{len(people)}: {person['name']}"
            passages = _appearance_passages(source_text, person["aliases"])
            if person["name"] in defined:
                passages = [defined[person["name"]]["description"], *passages][:_APPEARANCE_PASSAGE_LIMIT]
            tags = await _describe_appearance(person["name"], passages) if passages else ""
            if tags:
                described += 1
            person["id"] = save_character(conn, person["name"], tags)["id"]

        # 名前が本文に出るシーンへ割り当てる。既知の表記を探すだけなのでLLMは不要。
        assigned = 0
        for scene in scenes:
            text = scene["novelai_text"] or scene["draft_text"]
            character_ids = [
                person["id"]
                for person in people
                if any(alias in text for alias in person["aliases"])
            ]
            if character_ids:
                set_scene_characters(conn, scene["id"], character_ids)
                assigned += 1
    finally:
        conn.close()

    job.progress = 3
    job.message = (
        f"{len(people)}人を登録し(容姿の描写が見つかったのは{described}人"
        + (f"、登録済みの容姿をそのまま使ったのは{kept}人" if kept else "")
        + f")、{assigned}/{len(scenes)}シーンに割り当てました"
    )

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
