"""
対話生成(/api/studio)。文章モデルと相談してタグを決め(turn)、そのタグで描く(generate)。
描くのは、利用者が画面で「このタグで生成」を押したときだけ(turn は案を返すだけで、Anlas を使わない)。
会話は利用者のフォルダに JSON で残す(sessions)。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from novelai import AsyncNovelAI
from novelai.types import CharacterReference, GenerateImageParams
from pydantic import BaseModel, Field

from .. import studio
from ..client import get_client
from ..db import get_character, get_connection, record_generation, user_files
from . import help as help_route

router = APIRouter(prefix="/api/studio", tags=["studio"])
ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

_HISTORY_DIR = user_files() / "outputs" / "history"
_SESSIONS_DIR = user_files() / "studio" / "sessions"
_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_SESSIONS = 200


def _load_character(character_id: int | None) -> dict[str, Any] | None:
    if not character_id:
        return None
    conn = get_connection()
    try:
        character = get_character(conn, character_id)
    finally:
        conn.close()
    if character is None:
        raise HTTPException(status_code=404, detail=f"キャラ {character_id} が見つかりません")
    return character


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=6000)


class Current(BaseModel):
    scene: str = Field("", max_length=4000)
    negative: str = Field("", max_length=4000)
    size: Literal["portrait", "landscape", "square"] = "portrait"
    use_outfit: bool = False


class TurnRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=60)
    character_id: int | None = None
    current: Current | None = None
    # 漫画のコマを描き直すとき(キャラは、そのシーンの登場人物になる)
    scene_id: int | None = None


@router.post("/turn")
async def post_turn(req: TurnRequest, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """発言に答えて、次のタグの案を返す(描かない)。JSON が崩れていたら1回だけ聞き直す。"""
    panel = None
    character: Any
    if req.scene_id:
        panel = await asyncio.to_thread(_panel_context, req.scene_id)
        character = panel["cast"]
    else:
        character = await asyncio.to_thread(_load_character, req.character_id)
    api_key = await help_route.novelai_key(authorization)
    if not api_key:
        raise HTTPException(status_code=422, detail="NovelAI のトークンがありません(ログインし直してください)。")
    current = req.current.model_dump() if req.current else None
    messages = studio.build_messages(character, [t.model_dump() for t in req.history], req.message, current, panel)
    from ..novelai_text_oa import stream_chat

    proposal = None
    raw = ""
    for _ in range(2):
        try:
            raw = "".join([d async for d in stream_chat(api_key, messages, max_tokens=1200, temperature=0.3)])
        except Exception as exc:  # noqa: BLE001 文章モデルの失敗は画面に伝える
            raise HTTPException(status_code=502, detail=f"NovelAI から答えをもらえませんでした: {exc}") from exc
        proposal = studio.parse_reply(raw, current)
        if proposal is not None:
            break
        messages = [*messages, {"role": "assistant", "content": raw},
                    {"role": "user", "content": "決めた JSON オブジェクトだけを返してください。"}]
    if proposal is None:
        raise HTTPException(status_code=502, detail="文章モデルの答えを読み取れませんでした。言い方を変えてもう一度送ってください。")
    if proposal.action == "generate" and not proposal.scene and current:
        proposal.scene = current.get("scene") or ""
    proposal.scene = studio.clean_scene(character, proposal.scene)
    before = current or {}
    return {
        "proposal": proposal.to_dict(),
        # 会話の履歴に残す形(次の turn で、モデル自身の前の答えとして渡す)
        "assistant_turn": raw.strip(),
        "diff": {
            "scene": studio.tag_diff(before.get("scene"), proposal.scene),
            "negative": studio.tag_diff(before.get("negative"), proposal.negative),
        },
    }


class FromImageRequest(BaseModel):
    prompt: str = Field(..., max_length=8000)
    character_id: int | None = None


@router.post("/from-image")
async def post_from_image(req: FromImageRequest) -> dict[str, str]:
    """生成済みの画像のプロンプトから、キャラシートが入れる分を除いた場面のタグ(画像から始めるとき)。"""
    character = await asyncio.to_thread(_load_character, req.character_id)
    return {"scene": studio.scene_from_prompt(character, req.prompt)}


class GenerateRequest(Current):
    character_id: int | None = None
    seed: int | None = Field(None, ge=0, le=4294967295)
    use_reference: bool = True
    reference_strength: float = Field(0.55, ge=0.0, le=1.0)
    based_on: int | None = None


def _references(character_id: int | None, use_reference: bool, strength: float) -> tuple[list[Any], list[Any]]:
    """キャラ参照と絵柄セット(参照画像が無いキャラは、参照なしで描く)。"""
    from ..mcp_reference import character_reference, style_reference

    if not character_id:
        return [], []
    ref = None
    if use_reference:
        try:
            ref = character_reference(character_id, "character", 1.0, strength)
        except ValueError:
            ref = None
    style = style_reference(None, character_id)
    return ([ref] if ref else []), ([style] if style else [])


@router.post("/generate")
async def post_generate(req: GenerateRequest, client: ClientDep) -> dict[str, Any]:
    """場面のタグとキャラシートで1枚描き、生成履歴(ギャラリー・キャラの画像)に残す。"""
    character = await asyncio.to_thread(_load_character, req.character_id)
    try:
        prompt, negative = studio.build_prompt(character, req.scene, req.negative, req.use_outfit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not prompt:
        raise HTTPException(status_code=422, detail="タグが空です。")
    refs, styles = await asyncio.to_thread(_references, req.character_id, req.use_reference, req.reference_strength)
    if styles:
        prompt = studio.join_tags(prompt, styles[0].style_tags)
        negative = studio.join_tags(negative, styles[0].negative_tags)
    seed = req.seed if req.seed is not None else random.randint(0, 2**32 - 1)
    kwargs: dict[str, Any] = {
        "prompt": prompt, "negative_prompt": negative or None, "model": "nai-diffusion-4-5-full",
        "size": req.size, "steps": 27, "scale": 5.0, "quality": True, "uc_preset": "light", "n_samples": 1, "seed": seed,
    }
    sdk_refs = [CharacterReference(image=r.image, type=r.type, fidelity=r.fidelity, strength=r.strength) for r in refs]
    sdk_refs += [CharacterReference(image=s.image, type="style", fidelity=s.fidelity, strength=s.strength) for s in styles]
    if sdk_refs:
        kwargs["character_references"] = sdk_refs
    try:
        images = await client.image.generate(GenerateImageParams(**kwargs))
    except Exception as exc:  # noqa: BLE001
        from .image import _http_status

        raise HTTPException(status_code=_http_status(exc), detail=str(exc)) from exc
    if not images:
        raise HTTPException(status_code=502, detail="画像が返ってきませんでした。")
    buf = io.BytesIO()
    images[0].save(buf, format="PNG")
    raw = buf.getvalue()

    filename = f"{uuid4().hex}_studio.png"

    def save() -> dict[str, Any]:
        from ..mcp_reference import style_record

        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        (_HISTORY_DIR / filename).write_bytes(raw)
        records = [r.record(_HISTORY_DIR) for r in refs] + [style_record(s, _HISTORY_DIR) for s in styles]
        conn = get_connection()
        try:
            return record_generation(
                conn, prompt=prompt, negative_prompt=negative or None, model=kwargs["model"], size=req.size,
                steps=kwargs["steps"], scale=kwargs["scale"], seed=seed, chunk_ids=[],
                image_paths=[f"outputs/history/{filename}"], character_references=records or None,
                based_on_id=req.based_on, character_id=req.character_id,
            )
        finally:
            conn.close()

    entry = await asyncio.to_thread(save)
    return {
        "history_id": entry["id"],
        "path": f"outputs/history/{filename}",
        "image": base64.b64encode(raw).decode(),
        "seed": seed,
        "prompt": prompt,
        "negative": negative,
    }


# ---- 会話の保存 ----


class Session(BaseModel):
    title: str = Field("", max_length=200)
    character_id: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def _session_path(sid: str):
    if not _SESSION_ID.match(sid):
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    return _SESSIONS_DIR / f"{sid}.json"


def _list_sessions() -> list[dict[str, Any]]:
    if not _SESSIONS_DIR.is_dir():
        return []
    items = []
    for path in _SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append({"id": path.stem, "title": data.get("title") or "", "character_id": data.get("character_id"),
                      "updated_at": data.get("updated_at") or ""})
    return sorted(items, key=lambda s: s["updated_at"], reverse=True)


@router.get("/sessions")
async def get_sessions() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_sessions)


@router.post("/sessions")
async def create_session(req: Session) -> dict[str, Any]:
    sid = uuid4().hex
    return await asyncio.to_thread(_write_session, sid, req)


def _write_session(sid: str, req: Session) -> dict[str, Any]:
    path = _session_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() and len(list(path.parent.glob("*.json"))) >= MAX_SESSIONS:
        raise HTTPException(status_code=422, detail=f"保存できる会話は {MAX_SESSIONS} 件までです。古い会話を消してください。")
    body = {**req.model_dump(), "updated_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return {"id": sid, **body}


@router.get("/sessions/{sid}")
async def get_session(sid: str) -> dict[str, Any]:
    path = _session_path(sid)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    return {"id": sid, **json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))}


@router.put("/sessions/{sid}")
async def put_session(sid: str, req: Session) -> dict[str, Any]:
    if not _session_path(sid).is_file():
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    return await asyncio.to_thread(_write_session, sid, req)


@router.delete("/sessions/{sid}")
async def delete_session(sid: str) -> dict[str, bool]:
    path = _session_path(sid)
    if path.is_file():
        path.unlink()
    return {"ok": True}


# ---- 漫画のコマ(物語エディタの「対話で描き直す」) ----


def _panel_context(scene_id: int) -> dict[str, Any]:
    """シーンの内容・登場人物・いまのコマの絵。cast は登場人物のキャラシート(モデルに渡す。画面には返さない)。"""
    from ..db import characters_by_scene, get_scene, get_story, list_manga_panels, list_panel_candidates

    conn = get_connection()
    try:
        scene = get_scene(conn, scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="シーンが見つかりません")
        story = get_story(conn, scene["story_id"])
        cast = characters_by_scene(conn, scene["story_id"]).get(scene_id, [])
        panel = next((p for p in list_manga_panels(conn, scene["story_id"]) if p["scene_id"] == scene_id), None)
        candidates = list_panel_candidates(conn, scene_id)
    finally:
        conn.close()
    return {
        "scene_id": scene_id,
        "story_id": scene["story_id"],
        "story_title": (story or {}).get("title") or "",
        "scene_index": scene["scene_index"],
        "title": scene.get("draft_title") or "",
        "text": scene.get("draft_text") or "",
        "prompt_tags": scene.get("draft_prompt_tags") or "",
        "absent_tags": scene.get("absent_tags") or "",
        "characters": [{"id": c["id"], "name": c["name"]} for c in cast],
        "panel": {"image_path": panel["image_path"], "seed": panel["seed"]} if panel else None,
        "candidates": len(candidates),
        "cast": cast,
    }


@router.get("/panel/{scene_id}")
async def get_panel(scene_id: int) -> dict[str, Any]:
    """コマの描き直しを始めるときの、シーンの内容・登場人物・いまのコマの絵。"""
    context = await asyncio.to_thread(_panel_context, scene_id)
    context.pop("cast")
    return context


class PanelDrawRequest(BaseModel):
    prompt_tags: str = Field(..., max_length=4000)
    absent_tags: str = Field("", max_length=1000)
    seed: int | None = Field(None, ge=0, le=4294967295)
    # 漫画のページで選んでいる設定(物語エディタから渡す)
    template: str = "grid4"
    color: bool = False
    manga_style: bool = True
    use_character_reference: bool = False
    reference_strength: float = Field(1.0, ge=0.0, le=1.0)
    reference_fidelity: float = Field(1.0, ge=0.0, le=1.0)
    settings: dict[str, Any] = Field(default_factory=dict)


PANEL_TIMEOUT_SECONDS = 600


def _candidate_rows(scene_id: int) -> list[dict[str, Any]]:
    from ..db import list_panel_candidates

    conn = get_connection()
    try:
        return list_panel_candidates(conn, scene_id)
    finally:
        conn.close()


@router.post("/panel/{scene_id}/draw")
async def post_panel_draw(scene_id: int, req: PanelDrawRequest, client: ClientDep) -> dict[str, Any]:
    """
    このタグでコマを1枚描き、コマの候補に入れる(コマの絵はまだ変えない。採用は /adopt)。
    描き方(人物の容姿・キャラ参照・未成年の安全対策・ネガティブ)は漫画のページの「描き直す」と同じ。
    """
    from ..models import MangaImageSettings, MangaV2PanelsRequest
    from . import manga_v2
    from . import story as story_route

    context = await asyncio.to_thread(_panel_context, scene_id)
    story_id = context["story_id"]
    before = {int(r["id"]) for r in await asyncio.to_thread(_candidate_rows, scene_id)}
    settings = {**req.settings, "seed": req.seed if req.seed is not None else random.randint(0, 2**32 - 1)}
    try:
        image_settings = MangaImageSettings(**settings)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"画像の設定が正しくありません: {exc}") from exc
    panels_req = MangaV2PanelsRequest(
        scene_from=context["scene_index"], scene_to=context["scene_index"], template=req.template, skip_existing=False,
        color=req.color, manga_style=req.manga_style, use_character_reference=req.use_character_reference,
        reference_strength=req.reference_strength, reference_fidelity=req.reference_fidelity,
        as_candidate=True, prompt_tags=req.prompt_tags, absent_tags=req.absent_tags, settings=image_settings,
    )
    await manga_v2.generate_panels(story_id, panels_req, client)
    job = None
    for _ in range(PANEL_TIMEOUT_SECONDS):
        await asyncio.sleep(1)
        job = story_route._jobs.get(story_id)
        if job is None or job.status != "running":
            break
    if job is not None and job.status == "running":
        raise HTTPException(status_code=504, detail="時間内に描き終わりませんでした(物語のページで進み具合を見られます)。")
    if job is not None and job.status == "error":
        raise HTTPException(status_code=502, detail=f"描けませんでした: {job.detail or job.message}")
    rows = [r for r in await asyncio.to_thread(_candidate_rows, scene_id) if int(r["id"]) not in before]
    if not rows:
        # 未成年の安全対策で止まった など
        raise HTTPException(status_code=422, detail=(job.message if job else "") or "描けませんでした。")
    row = rows[-1]
    return {
        "candidate_id": row["id"], "path": row["image_path"], "seed": row["seed"],
        "width": row["width"], "height": row["height"], "message": job.message if job else "",
    }


class PanelAdoptRequest(BaseModel):
    candidate_id: int
    prompt_tags: str | None = Field(None, max_length=4000)
    absent_tags: str | None = Field(None, max_length=1000)


@router.post("/panel/{scene_id}/adopt")
async def post_panel_adopt(scene_id: int, req: PanelAdoptRequest) -> dict[str, Any]:
    """
    候補をコマの絵にし、描いたときのタグをシーンに保存する(次に描き直しても同じタグになる)。
    タグを先に保存する(タグを変えると絵が「描き直し待ち」になるが、採用でその印が消える)。
    """
    from ..db import adopt_panel_candidate, update_scene_absent, update_scene_content

    def run() -> dict[str, Any]:
        conn = get_connection()
        try:
            exists = conn.execute(
                "SELECT 1 FROM manga_panel_candidates WHERE id = ? AND scene_id = ?", (req.candidate_id, scene_id)
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="その候補はこのコマにありません(もう採用したか、消した候補です)。")
            if req.prompt_tags is not None:
                update_scene_content(conn, scene_id, draft_prompt_tags=req.prompt_tags)
            if req.absent_tags is not None:
                update_scene_absent(conn, scene_id, req.absent_tags)
            chosen = adopt_panel_candidate(conn, scene_id, req.candidate_id)
        finally:
            conn.close()
        return {"image_path": chosen["image_path"] if chosen else None, "seed": chosen["seed"] if chosen else None}

    return await asyncio.to_thread(run)
