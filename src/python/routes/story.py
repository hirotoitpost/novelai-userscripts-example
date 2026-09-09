"""
物語生成 → 挿絵(V5コマ割り) → 漫画出力パイプライン。

流れ: 前提(premise) → Ollamaでシーン分割ドラフト作成 → シーンごとにOllamaが
継続用の誘導文(seed_cue)を作り、それをNovelAI公式Kayraに渡して本文を自動で書き継ぐ
→ ページ単位でV5にコマ割り画像を生成させる → 全ページを縦に連結して1枚の漫画にする。
"""

from __future__ import annotations

import json
import re
from base64 import b64decode
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator
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
    get_connection,
    get_story,
    list_manga_pages,
    list_stories,
    list_story_scenes,
    update_scene_writing,
    update_story_final_image,
    update_story_status,
)
from ..keystore_crypto import decrypt_keystore, decrypt_object
from ..manga_export import assemble_manga
from ..models import (
    MangaPageResponse,
    StoryDraftCreateRequest,
    StoryImportRequest,
    StoryResponse,
    StorySummary,
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


def _split_text_into_scenes(text: str, n_scenes: int) -> list[str]:
    """
    公式サイトなどで既に書かれた物語を、本文を一切書き換えずにちょうど n_scenes 個
    (テキストの分量が足りない場合はそれ以下)の場面に分割する。

    段落(空行区切り)を最小単位とし、段落数が n_scenes 以上あれば段落の個数で
    n_scenes 個の連続したグループへ均等に分ける(文字数の重みは見ない:
    1段落が閾値を超えているだけで後続のバケットを1つ丸ごと飛ばしてしまう、
    という不具合を文字数ベースの貪欲法で実機確認したため)。段落数が n_scenes に
    満たない場合は、文単位(。！？の直後)にさらに分割してから同じグルーピングを行う。
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]

    if len(paragraphs) < n_scenes:
        pieces = [s.strip() for p in paragraphs for s in re.split(r"(?<=[。！？])", p) if s.strip()]
        if pieces:
            paragraphs = pieces

    if not paragraphs:
        return []

    n_scenes = min(n_scenes, len(paragraphs))
    base, extra = divmod(len(paragraphs), n_scenes)

    scenes: list[str] = []
    start = 0
    for i in range(n_scenes):
        size = base + (1 if i < extra else 0)
        scenes.append("\n".join(paragraphs[start : start + size]))
        start += size
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
    return "\n\n".join(f"場面{i}:\n{text}" for i, text in enumerate(scene_texts, start=1))


def _parse_import_tags(tags_text: str, n_scenes: int) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(tags_text)
    except json.JSONDecodeError:
        return None

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != n_scenes:
        return None

    result: list[dict[str, Any]] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            return None
        result.append(
            {
                "draft_title": (str(scene.get("title") or "")).strip() or None,
                "draft_prompt_tags": (str(scene.get("prompt_tags") or "")).strip(),
            }
        )
    return result


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


@router.post("/{story_id}/split")
async def split_story(story_id: int, request: Request) -> StreamingResponse:
    """
    /import で raw_text のまま保存しておいた物語を、本文には一切手を加えずに
    n_scenes個の場面へ分割する。Ollamaはタグ付け(タイトル・画像生成タグ)のためだけに
    使い、本文の生成/書き換えには使わない。分割したシーンは novelai_text を
    draft_text と同じ値で埋め、/write (Kayraによる自動執筆)をスキップした状態で
    保存するため、そのまま /illustrate へ進める。
    """

    async def gen() -> AsyncGenerator[str, None]:
        try:
            conn = get_connection()
            try:
                story = get_story(conn, story_id)
            finally:
                conn.close()
            if story is None:
                yield sse_event("error", {"detail": "story not found"})
                return
            if story["scenes"]:
                yield sse_event("error", {"detail": "この物語は既に分割済みです。"})
                return
            raw_text = story.get("raw_text")
            if not raw_text:
                yield sse_event("error", {"detail": "raw_text がありません(インポートされた物語ではない可能性があります)。"})
                return

            scene_texts = _split_text_into_scenes(raw_text, story["n_scenes"])
            if not scene_texts:
                yield sse_event("error", {"detail": "本文からシーンを分割できませんでした。"})
                return

            n_scenes = len(scene_texts)
            if n_scenes != story["n_scenes"]:
                yield sse_event(
                    "progress",
                    {"message": f"本文の分量から、{n_scenes}シーンに調整しました(要求: {story['n_scenes']})"},
                )

            tags_by_scene: list[dict[str, Any]] | None = None
            for attempt in range(1, _DRAFT_MAX_ATTEMPTS + 1):
                if await request.is_disconnected():
                    return
                yield sse_event(
                    "progress", {"message": f"Ollamaでタグ付け中(試行{attempt}/{_DRAFT_MAX_ATTEMPTS})"}
                )
                parts: list[str] = []
                async for delta in stream_llm_text(
                    [
                        {"role": "system", "content": _import_tags_system_prompt(n_scenes)},
                        {"role": "user", "content": _format_scenes_for_tagging(scene_texts)},
                    ],
                    request,
                    max_tokens=4096,
                    json_schema=_import_tags_json_schema(n_scenes),
                ):
                    parts.append(delta)
                if await request.is_disconnected():
                    return

                tags_by_scene = _parse_import_tags(strip_think_tags("".join(parts)), n_scenes)
                if tags_by_scene is not None:
                    break
                yield sse_event(
                    "progress",
                    {"message": f"タグ付け出力の形式が不十分だったため再試行します({attempt}/{_DRAFT_MAX_ATTEMPTS})"},
                )

            if tags_by_scene is None:
                yield sse_event("progress", {"message": "タグ付けに失敗したため、タグなしで分割します。"})
                tags_by_scene = [{"draft_title": None, "draft_prompt_tags": ""} for _ in scene_texts]

            scenes = [
                {
                    "draft_title": tags["draft_title"],
                    "draft_text": text,
                    "draft_prompt_tags": tags["draft_prompt_tags"],
                    "novelai_text": text,
                }
                for text, tags in zip(scene_texts, tags_by_scene)
            ]

            conn = get_connection()
            try:
                add_story_scenes(conn, story_id, scenes)
                update_story_status(conn, story_id, "written")
                result = get_story(conn, story_id)
            finally:
                conn.close()
            yield sse_event("done", _story_response(result).model_dump())
        except Exception as exc:
            yield sse_event("error", {"detail": str(exc)})

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


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


@router.post("/{story_id}/illustrate")
async def illustrate_story(story_id: int, client: ClientDep, request: Request) -> StreamingResponse:
    async def gen() -> AsyncGenerator[str, None]:
        conn = get_connection()
        try:
            scenes = list_story_scenes(conn, story_id)
            if not scenes:
                yield sse_event("error", {"detail": "story not found"})
                return

            pages_scenes: dict[int, list[dict[str, Any]]] = {}
            for scene in scenes:
                pages_scenes.setdefault(scene["page_index"], []).append(scene)

            _MANGA_DIR.mkdir(parents=True, exist_ok=True)
            results = []
            page_indexes = sorted(pages_scenes)
            for i, page_index in enumerate(page_indexes, start=1):
                if await request.is_disconnected():
                    return
                yield sse_event(
                    "progress", {"message": f"ページ{i}/{len(page_indexes)}: V5でコマ割り画像を生成中..."}
                )

                page_scenes = pages_scenes[page_index]
                panels = [
                    {
                        "draft_text": s["novelai_text"] or s["draft_text"],
                        "draft_prompt_tags": s["draft_prompt_tags"],
                    }
                    for s in page_scenes
                ]
                image_bytes = await generate_manga_page(client, panels, seed=story_id * 1000 + page_index)

                filename = f"story{story_id}_page{page_index}_{uuid4().hex[:8]}.png"
                (_MANGA_DIR / filename).write_bytes(image_bytes)
                image_path = f"outputs/manga/{filename}"

                scene_ids = [s["id"] for s in page_scenes]
                record = create_manga_page(conn, story_id, page_index, image_path, scene_ids)
                results.append(MangaPageResponse.model_validate(record).model_dump())

            update_story_status(conn, story_id, "illustrated")
            yield sse_event("done", results)
        except Exception as exc:
            yield sse_event("error", {"detail": str(exc)})
        finally:
            conn.close()

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


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
