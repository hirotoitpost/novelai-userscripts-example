from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from novelai import AsyncNovelAI
from novelai.types import CharacterReference, InpaintParams
from PIL import Image, UnidentifiedImageError

from ..body_parts import PARTS, face_crop, part_mask
from ..character_sheet import (
    adult_only_tags,
    blocked_tags,
    build_variation_shots,
    caption_for,
    character_minor_tags,
    core_blocked_tags,
    join_tags,
    look_tags,
    minor_tags,
    safe_negative,
    sheet_negative,
    sheet_prompt,
)
from ..client import get_client
from ..face_sheet import BACKGROUND as FACE_BACKGROUND
from ..face_sheet import ITEMS as FACE_ITEMS
from ..face_sheet import SIZES as FACE_SIZES
from ..face_sheet import presets as face_presets
from ..dataset_variations import clean_value
from ..dataset_variations import extract as extract_variation_tags
from .. import guard_password
from ..guard_password import guard_token
from ..sessions import require_admin
from ..db import (
    add_dataset_variations,
    delete_dataset_situation,
    delete_dataset_state,
    delete_dataset_variation,
    get_dataset_situation,
    get_dataset_state,
    list_dataset_situations,
    list_dataset_states,
    save_dataset_situation,
    save_dataset_state,
    delete_guard_profile,
    get_character,
    get_connection,
    get_guard_connection,
    get_guard_profile,
    list_dataset_variations,
    list_guard_profiles,
    save_guard_profile,
)
from ..lora_dataset import (
    GenConfig,
    Shot,
    build_character_references,
    build_controlnet,
    build_preview_shots,
    build_shots,
    generate_one,
    run_dataset,
)
from ..models import (
    CharacterDatasetRequest,
    DatasetSituationRequest,
    DatasetStateRequest,
    DatasetVariationsAddRequest,
    DatasetVariationsExtractRequest,
    CharacterSheetPromptResponse,
    DatasetImportRequest,
    FaceSheetRequest,
    GuardCoreResponse,
    GuardProfileResponse,
    GuardProfileSaveRequest,
    LoraDatasetRequest,
)
from ..similarity import similarity
from ..db import user_files

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/lora-dataset", tags=["lora-dataset"])

# 利用者のデータのファイルの基準(single はプロジェクトのフォルダ、multi はその人のフォルダ。db.UserPath)
_PROJECT_ROOT = user_files()
_SAFE_NAME_RE = re.compile(r"[^\w\-]+")


def _safe_name(name: str) -> str:
    return _SAFE_NAME_RE.sub("_", name.strip()) or "character"


def _build_cfg(req: LoraDatasetRequest, output_root: Path) -> GenConfig:
    try:
        return GenConfig(
            output_root=output_root,
            model=req.model,
            steps=req.steps,
            scale=req.scale,
            sampler=req.sampler,
            noise_schedule=req.noise_schedule,
            cfg_rescale=req.cfg_rescale,
            negative_prompt=req.negative_prompt,
            seed=req.seed,
            seed_offset=req.seed_offset,
            micro_variation_tags=req.micro_variation_tags,
            shuffle_tags=req.shuffle_tags,
            character_references=build_character_references(req.character_reference),
            controlnet=build_controlnet(req.vibe_transfer),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _stream_dataset(
    client: AsyncNovelAI,
    shots: list[Shot],
    cfg: GenConfig,
    output_root: Path,
    include_image_data: bool,
) -> StreamingResponse:
    async def event_gen():
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        succeeded = failed = 0
        try:
            async for event in run_dataset(client, shots, cfg, include_image_data=include_image_data):
                if event["status"] == "ok":
                    succeeded += 1
                else:
                    failed += 1
                yield _sse("progress", event)
        except Exception as exc:
            yield _sse("error", {"detail": str(exc)})
            return

        yield _sse("complete", {
            "total": succeeded + failed,
            "succeeded": succeeded,
            "failed": failed,
            "output_path": str(output_root),
        })

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/generate")
async def generate_lora_dataset(req: LoraDatasetRequest, client: ClientDep) -> StreamingResponse:
    output_root = _PROJECT_ROOT / "outputs" / _safe_name(req.root_name) / _safe_name(req.character_id)
    shots = build_shots(req.trigger_word, req.base_tags, req.extra_tags, req.outfit_tag)
    cfg = _build_cfg(req, output_root)
    return _stream_dataset(client, shots, cfg, output_root, include_image_data=False)


@router.post("/preview")
async def preview_lora_dataset(req: LoraDatasetRequest, client: ClientDep) -> StreamingResponse:
    """顔/上半身/全身を1枚ずつ生成してプレビューする（本番100枚と同じ保存先に保存される）。"""
    output_root = _PROJECT_ROOT / "outputs" / _safe_name(req.root_name) / _safe_name(req.character_id)
    shots = build_preview_shots(build_shots(req.trigger_word, req.base_tags, req.extra_tags, req.outfit_tag))
    cfg = _build_cfg(req, output_root)
    return _stream_dataset(client, shots, cfg, output_root, include_image_data=True)


# --- キャラシートからのデータセット ---


def _load_character(character_id: int) -> dict:
    conn = get_connection()
    try:
        character = get_character(conn, character_id)
    finally:
        conn.close()
    if character is None:
        raise HTTPException(status_code=404, detail="character not found")
    return character


def _character_root(root_name: str, character: dict) -> Path:
    folder = character.get("trigger_word") or character["name"]
    return _PROJECT_ROOT / "outputs" / _safe_name(root_name) / _safe_name(folder)


def _load_guard(profile_id: int | None) -> dict:
    """上乗せ分のガード。未指定なら空(基本のガードだけが効く)。"""
    if profile_id is None:
        return {"blocked_tags": [], "negative_tags": ""}
    conn = get_guard_connection()
    try:
        profile = get_guard_profile(conn, profile_id)
    finally:
        conn.close()
    if profile is None:
        raise HTTPException(status_code=404, detail="ガードプロファイルが見つかりません。")
    return profile


def _check_tags(character: dict, text: str, guard: dict, r18: bool) -> None:
    """
    全年齢: 基本のガード+プロファイルの追加分に当たるタグを弾く。
    R18:    成人フラグのあるキャラだけ。性的なタグは許す代わりに、キャラシートと text に
            未成年を示すタグがあれば弾く(プロファイルの追加分は引き続き効く)。
    """
    # 成人キャラだけに使うタグ(妊娠など)は、全年齢でも R18 でも、成人キャラで、未成年を示すタグと一緒でないときだけ
    adult_only = adult_only_tags(text)
    if adult_only:
        if not character.get("is_adult"):
            raise HTTPException(
                status_code=403,
                detail=f"成人キャラ(キャラシートの成人フラグ)にだけ使えるタグがあります: {', '.join(adult_only)}",
            )
        found = sorted(set(character_minor_tags(character)) | set(minor_tags(text)))
        if found:
            raise HTTPException(
                status_code=422,
                detail=f"{', '.join(adult_only)} は、未成年を示すタグと一緒には使えません: {', '.join(found)}",
            )
    if r18:
        if not character.get("is_adult"):
            raise HTTPException(status_code=403, detail="R18は成人キャラ(キャラシートの成人フラグ)でのみ使えます。")
        found = sorted(set(character_minor_tags(character)) | set(minor_tags(text)))
        if found:
            raise HTTPException(status_code=422, detail=f"R18では未成年を示すタグは使えません: {', '.join(found)}")
        blocked = blocked_tags(text, guard["blocked_tags"], include_core=False)
    else:
        blocked = blocked_tags(text, guard["blocked_tags"])
    if blocked:
        label = "ガードで禁止されたタグ" if r18 else "全年齢向けでは使えないタグ"
        raise HTTPException(status_code=422, detail=f"{label}: {', '.join(blocked)}")


@router.get("/guards/core", response_model=GuardCoreResponse)
async def get_core_guard() -> dict:
    return {"blocked_tags": core_blocked_tags(), "negative_tags": safe_negative()}


@router.get("/guards", response_model=list[GuardProfileResponse])
async def get_guard_profiles() -> list[dict]:
    conn = get_guard_connection()
    try:
        return list_guard_profiles(conn)
    finally:
        conn.close()


# ガードプロファイルは multi では運営者が全員分を決める(全体の DB。変えられるのは管理者だけ。ステップ 4b)
@router.post("/guards", response_model=GuardProfileResponse, dependencies=[Depends(require_admin)])
async def post_guard_profile(req: GuardProfileSaveRequest, token: str | None = Depends(guard_token)) -> dict:
    """
    ガードプロファイルを保存する(同名は上書き)。基本のガードと重複するタグは保存しない。上書きで禁止タグ・ネガティブの
    語を減らすのは守りを緩める変更なので、コンテンツガードのパスワードの合言葉が要る。
    """
    from .content_guard import _profile_direction

    core = {t.lower() for t in core_blocked_tags()}
    tags: list[str] = []
    for tag in (t.strip() for t in req.blocked_tags):
        if tag and tag.lower() not in core and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    conn = get_guard_connection()
    try:
        current = next((p for p in list_guard_profiles(conn) if p["name"] == req.name.strip()), None)
        if _profile_direction(current, tags, req.negative_tags.strip())["direction"] == "looser":
            guard_password.require_unlocked(token)
        return save_guard_profile(conn, req.name.strip(), tags, req.negative_tags.strip())
    finally:
        conn.close()


@router.delete("/guards/{profile_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_guard_profile_endpoint(profile_id: int, token: str | None = Depends(guard_token)) -> None:
    """ガードプロファイルを消すのは守りを緩める変更なので、コンテンツガードのパスワードの合言葉が要る。"""
    guard_password.require_unlocked(token)
    conn = get_guard_connection()
    try:
        delete_guard_profile(conn, profile_id)
    finally:
        conn.close()


def _variations() -> dict[str, list[str]]:
    conn = get_connection()
    try:
        return list_dataset_variations(conn)
    finally:
        conn.close()


@router.get("/variations")
async def get_variations() -> dict[str, list[str]]:
    """キャラ別データセットのバリエーションの候補(軸ごと。足した順)。画面や API で足したり消したりできる。"""
    return _variations()


@router.post("/variations")
async def post_variations(req: DatasetVariationsAddRequest) -> dict[str, list[str]]:
    """候補を足して、候補の一覧を返す。すでにあるものは増えない。"""
    values = list(dict.fromkeys(v for v in (clean_value(v) for v in req.values) if v))
    if not values:
        raise HTTPException(status_code=422, detail="足すキーワードを入れてください。")
    conn = get_connection()
    try:
        add_dataset_variations(conn, req.axis, values)
    finally:
        conn.close()
    return _variations()


@router.delete("/variations")
async def remove_variation(axis: str, value: str) -> dict[str, list[str]]:
    """候補を消して、候補の一覧を返す。"""
    conn = get_connection()
    try:
        if not delete_dataset_variation(conn, axis, value):
            raise HTTPException(status_code=404, detail="variation not found")
    finally:
        conn.close()
    return _variations()


# ---- シチュエーション・状態 ----


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _shot_states(states: list[dict[str, Any]], req: Any) -> list[dict[str, Any] | None] | None:
    """
    プロンプトで作る1枚ごとの状態の並び。インペイントで作るときは、部位のある状態はここに入れず(ふだんの体の絵から
    描き直す)、ふだんの体の絵はいつも作る(描き直しの元になるため)。
    """
    if not states:
        return None
    if req.state_method == "inpaint" and any(s.get("part", "none") in PARTS for s in states):
        return [None, *[s for s in states if s.get("part", "none") not in PARTS]]
    return [*states, *([None] if req.include_plain_state else [])]


def _load_scenes(req: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """生成で使うシチュエーションと状態(選んだ順)。無いIDは 404。"""
    conn = get_connection()
    try:
        situations = [get_dataset_situation(conn, i) for i in dict.fromkeys(req.situation_ids)]
        states = [get_dataset_state(conn, i) for i in dict.fromkeys(req.state_ids)]
    finally:
        conn.close()
    if any(s is None for s in situations) or any(s is None for s in states):
        raise HTTPException(status_code=404, detail="選んだシチュエーションか状態が見つかりません(消された可能性があります)。")
    return [s for s in situations if s], [s for s in states if s]


def _scene_tags(situations: list[dict[str, Any]], states: list[dict[str, Any]]) -> list[str]:
    """シチュエーションと状態が足すタグ(検査用)。"""
    tags: list[str] = []
    for situation in situations:
        for axis in ("framings", "poses", "outfits", "expressions", "locations"):
            tags.extend(situation[axis])
        tags.append(situation["extra_tags"])
    tags.extend(state["add_tags"] for state in states)
    return [t for t in tags if t]


def _situation_response(situation: dict[str, Any]) -> dict[str, Any]:
    return {**situation, "adult_only": adult_only_tags(", ".join(_scene_tags([situation], [])))}


def _state_response(state: dict[str, Any]) -> dict[str, Any]:
    return {**state, "adult_only": adult_only_tags(state["add_tags"])}


@router.get("/body-parts")
async def get_body_parts() -> list[dict[str, str]]:
    """状態で描き直せる部位(キーと画面での名前)。"""
    return [{"key": key, "label": value[3]} for key, value in PARTS.items()]


def _clean_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in (clean_value(v) for v in values) if v))


@router.get("/situations")
async def get_situations() -> list[dict[str, Any]]:
    """
    シチュエーション(場所・服装・ポーズなどをひとまとまりにしたもの)の一覧。adult_only は、成人キャラにだけ使える
    タグ(コンテンツガードの dataset.adult_only_tags)。
    """
    conn = get_connection()
    try:
        return [_situation_response(s) for s in list_dataset_situations(conn)]
    finally:
        conn.close()


def _save_situation(situation_id: int | None, req: DatasetSituationRequest) -> dict[str, Any]:
    axes = {axis: _clean_list(getattr(req, axis)) for axis in ("framings", "poses", "outfits", "expressions", "locations")}
    extra = clean_value(req.extra_tags) if req.extra_tags.strip() else ""
    if not any(axes.values()) and not extra:
        raise HTTPException(status_code=422, detail="場所・服装・ポーズなど、どれか1つは入れてください。")
    conn = get_connection()
    try:
        if situation_id is not None and get_dataset_situation(conn, situation_id) is None:
            raise HTTPException(status_code=404, detail="situation not found")
        try:
            return _situation_response(save_dataset_situation(conn, situation_id, req.name.strip(), axes, extra))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"「{req.name.strip()}」というシチュエーションはもうあります。")
    finally:
        conn.close()


@router.post("/situations")
async def post_situation(req: DatasetSituationRequest) -> dict[str, Any]:
    return _save_situation(None, req)


@router.put("/situations/{situation_id}")
async def put_situation(situation_id: int, req: DatasetSituationRequest) -> dict[str, Any]:
    return _save_situation(situation_id, req)


@router.delete("/situations/{situation_id}", status_code=204)
async def remove_situation(situation_id: int) -> None:
    conn = get_connection()
    try:
        if not delete_dataset_situation(conn, situation_id):
            raise HTTPException(status_code=404, detail="situation not found")
    finally:
        conn.close()


@router.get("/states")
async def get_states() -> list[dict[str, Any]]:
    """状態(容姿を一時的に変えるもの)の一覧。adult_only は、成人キャラにだけ使えるタグ。"""
    conn = get_connection()
    try:
        return [_state_response(s) for s in list_dataset_states(conn)]
    finally:
        conn.close()


def _save_state(state_id: int | None, req: DatasetStateRequest) -> dict[str, Any]:
    add = ", ".join(_clean_list(req.add_tags.split(",")))
    remove = ", ".join(_clean_list(req.remove_tags.split(",")))
    if not add and not remove:
        raise HTTPException(status_code=422, detail="足すタグか外すタグを入れてください。")
    conn = get_connection()
    try:
        if state_id is not None and get_dataset_state(conn, state_id) is None:
            raise HTTPException(status_code=404, detail="state not found")
        try:
            return _state_response(save_dataset_state(conn, state_id, req.name.strip(), add, remove, req.part))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"「{req.name.strip()}」という状態はもうあります。")
    finally:
        conn.close()


@router.post("/states")
async def post_state(req: DatasetStateRequest) -> dict[str, Any]:
    return _save_state(None, req)


@router.put("/states/{state_id}")
async def put_state(state_id: int, req: DatasetStateRequest) -> dict[str, Any]:
    return _save_state(state_id, req)


@router.delete("/states/{state_id}", status_code=204)
async def remove_state(state_id: int) -> None:
    conn = get_connection()
    try:
        if not delete_dataset_state(conn, state_id):
            raise HTTPException(status_code=404, detail="state not found")
    finally:
        conn.close()


@router.post("/variations/extract")
async def extract_variations(req: DatasetVariationsExtractRequest) -> dict[str, Any]:
    """
    プロンプトから、バリエーションの候補にできるキーワードを取り出して、軸ごとに分ける(足しはしない)。
    axes: 軸ごとのタグ、sheet: キャラシートの側のタグ(appearance: 容姿、style: 品質・画風)、other: どの軸か
    分からないタグ、adult: 性的なタグ、skipped: 取り出さなかったタグ(人数)、suggested: 軸が分からなかったタグの、
    hints から付けた見当(軸か、appearance / style)。
    分け方は dataset_variations.py。
    """
    return extract_variation_tags(req.prompts, req.hints)


@router.get("/character/{character_id}/sheet-prompt", response_model=CharacterSheetPromptResponse)
async def get_character_sheet_prompt(character_id: int) -> dict:
    """
    キャラシートから組み立てた生成用のプロンプト・ネガティブ・基準シード。画像生成ページで
    キャラシートを読み込むのに使う。キャラ別データセット(全年齢)からトリガーワードを除いた
    組み立てなので、データセットと同じ見た目を単発で試せる(参照画像は画像生成ページでは使わない)。
    """
    character = _load_character(character_id)
    return {
        "character_id": character["id"],
        "name": character["name"],
        "prompt": sheet_prompt(character),
        "negative_prompt": sheet_negative(character),
        "seed": character.get("seed"),
    }


@router.post("/character/{character_id}/generate")
async def generate_character_dataset(
    character_id: int, req: CharacterDatasetRequest, client: ClientDep
) -> StreamingResponse:
    """
    キャラシートの容姿を固定し、ポーズ/服装/表情/場所を差し替えて1枚ずつ生成する。
    Anlas節約のため並列にせず、前の1枚が終わってから次をリクエストする。
    """
    character = _load_character(character_id)
    guard = _load_guard(req.guard_profile_id)
    r18 = req.rating == "r18"
    situations, states = _load_scenes(req)
    variations = req.framings + req.poses + req.outfits + req.expressions + req.locations
    _check_tags(
        character,
        ", ".join([
            *variations,
            *_scene_tags(situations, states),
            character.get("outfit_tags") or "",
            character.get("style_tags") or "",
        ]),
        guard,
        r18,
    )

    references = None
    ref_path = character.get("reference_image_path")
    ref_bytes = (_PROJECT_ROOT / ref_path).read_bytes() if ref_path and (_PROJECT_ROOT / ref_path).is_file() else None
    if req.max_attempts > 1 and ref_bytes is None:
        raise HTTPException(status_code=422, detail="引き直しには参照画像が必要です(類似度を測る基準になります)。")
    ref_image = Image.open(io.BytesIO(ref_bytes)) if ref_bytes else None
    if req.use_reference and ref_bytes:
        references = [CharacterReference(
            image=ref_bytes,
            type=req.reference_type,
            fidelity=req.reference_fidelity,
            strength=req.reference_strength,
        )]

    output_root = _character_root(req.root_name, character)
    try:
        cfg = GenConfig(
            output_root=output_root,
            model=req.model,
            steps=req.steps,
            scale=req.scale,
            sampler=req.sampler,
            noise_schedule=req.noise_schedule,
            cfg_rescale=req.cfg_rescale,
            quality=req.quality,
            uc_preset=req.uc_preset,
            variety_boost=req.variety_boost,
            negative_prompt=sheet_negative(character, r18=r18, extra=guard["negative_tags"]),
            seed=character.get("seed"),
            character_references=references,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    shots = build_variation_shots(
        character,
        framings=req.framings,
        poses=req.poses,
        outfits=req.outfits,
        expressions=req.expressions,
        locations=req.locations,
        count=req.count,
        r18=r18,
        situations=situations or None,
        states=_shot_states(states, req),
    )
    # インペイントで作る状態(部位のあるもの)。ふだんの体の1枚ごとに、その部位だけを描き直した絵を足す
    inpaint_states = [s for s in states if s.get("part", "none") in PARTS] if req.state_method == "inpaint" else []
    plain_shots = sum(1 for shot in shots if not shot.state)
    total = len(shots) + plain_shots * len(inpaint_states)
    # R18 は全年齢のデータセットと混ざらないよう別フォルダにする
    suffix = "_r18" if r18 else ""
    out_dir = output_root / f"generated{suffix}"

    async def event_gen():
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        out_dir.mkdir(parents=True, exist_ok=True)
        rejected_dir = output_root / f"rejected{suffix}"
        faces_dir = output_root / f"faces{suffix}"
        # 既存ファイルを上書きしないよう、連番は今ある枚数の続きから振る
        number = len(list(out_dir.glob("*.png")))
        current = 0
        succeeded = failed = 0
        face_caption = join_tags(character.get("trigger_word"), character.get("appearance_tags"), "portrait, close-up")

        def save(image: Image.Image, stem: str, prompt: str) -> dict:
            """データセットに1枚保存する(キャプション・顔の切り抜きも)。画面に返す分を返す。"""
            image.save(out_dir / f"{stem}.png", "PNG")
            (out_dir / f"{stem}.txt").write_text(prompt, encoding="utf-8")
            face = None
            if req.save_face_crops:
                face = face_crop(image)
                if face is not None:
                    faces_dir.mkdir(parents=True, exist_ok=True)
                    face.save(faces_dir / f"{stem}.png", "PNG")
                    (faces_dir / f"{stem}.txt").write_text(face_caption, encoding="utf-8")
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return {
                "status": "ok",
                "prompt": prompt,
                "image_b64": base64.b64encode(buf.getvalue()).decode(),
                "face_crop": None if not req.save_face_crops else face is not None,
            }

        for shot in shots:
            number += 1
            current += 1
            stem = f"{number:04d}"
            event: dict = {
                "current": current, "total": total, "category": out_dir.name, "file": f"{stem}.png",
                "situation": shot.situation, "state": shot.state,
            }
            best = None
            seed = None
            try:
                candidates: list[tuple[float, Image.Image]] = []
                for attempt in range(req.max_attempts):
                    # 同じシード・同じプロンプトだと同じ絵になるので、引き直しはシードをずらす
                    seed = None if cfg.seed is None else (cfg.seed + shot.seed_offset + attempt) % 4294967296
                    if attempt > 0:
                        best_so_far = max(score for score, _ in candidates)
                        yield _sse("retry", {**event, "attempt": attempt + 1, "score": round(best_so_far, 3)})
                        await asyncio.sleep(2)
                    image = await generate_one(client, shot.prompt, (req.width, req.height), cfg, seed=seed)
                    score = await similarity(image, ref_image, req.scorer) if ref_image is not None else 1.0
                    candidates.append((score, image))
                    if score >= req.similarity_threshold:
                        break
                best_index = max(range(len(candidates)), key=lambda k: candidates[k][0])
                best_score, best = candidates[best_index]
                # 外れもAnlasを使った画像なので捨てずに rejected/ に残す(手動で拾えるように)
                for k, (score, image) in enumerate(candidates):
                    if k != best_index:
                        rejected_dir.mkdir(parents=True, exist_ok=True)
                        image.save(rejected_dir / f"{stem}_try{k + 1}_{score:.2f}.png", "PNG")
                event |= {
                    **save(best, stem, shot.prompt),
                    "attempts": len(candidates),
                    "score": round(best_score, 3) if ref_image is not None else None,
                }
                seed = None if cfg.seed is None else (cfg.seed + shot.seed_offset + best_index) % 4294967296
                succeeded += 1
            except Exception as exc:
                event |= {"status": "error", "message": str(exc)}
                failed += 1
            yield _sse("progress", event)
            await asyncio.sleep(2)

            # ふだんの体の絵から、状態の部位だけを描き直す(顔・服・背景が同じで、体だけが違う組)
            base_file = f"{stem}.png"
            for state in inpaint_states if not shot.state else []:
                number += 1
                current += 1
                stem = f"{number:04d}"
                prompt = shot.prompt.replace(shot.look, look_tags(character, state), 1)
                event = {
                    "current": current, "total": total, "category": out_dir.name, "file": f"{stem}.png",
                    "situation": shot.situation, "state": state["name"], "base": base_file,
                }
                try:
                    if best is None:
                        raise RuntimeError("元のふだんの体の絵が作れなかったので、描き直せません")
                    found = await asyncio.to_thread(part_mask, best, state["part"])
                    if found is None:
                        # 頭が見つからない(寝ている絵など)ときは、状態のタグで生成し直す
                        image = await generate_one(client, prompt, (req.width, req.height), cfg, seed=seed)
                        event["method"] = "prompt"
                    else:
                        mask, _ = found
                        params = InpaintParams(
                            image=_png(best), mask=_png(mask), strength=req.inpaint_strength, seed=(seed or 0) + 1,
                        )
                        image = await generate_one(client, prompt, (req.width, req.height), cfg, seed=seed, inpaint=params)
                        event["method"] = "inpaint"
                    event |= save(image, stem, prompt)
                    succeeded += 1
                except Exception as exc:
                    event |= {"status": "error", "message": str(exc)}
                    failed += 1
                yield _sse("progress", event)
                await asyncio.sleep(2)
        yield _sse("complete", {
            "total": total, "succeeded": succeeded, "failed": failed, "output_path": str(output_root),
        })

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- 顔・表情シート(face_sheet.py) ---


def _face_dir(character: dict, root_name: str) -> Path:
    return _character_root(root_name, character) / "face_sheet"


def _face_entries(character: dict, root_name: str) -> list[dict[str, Any]]:
    folder = _face_dir(character, root_name)
    result = []
    for item in FACE_ITEMS.values():
        path = folder / f"{item.key}.png"
        result.append({
            "key": item.key,
            "label": item.label,
            "group": item.group,
            "path": path.relative_to(_PROJECT_ROOT).as_posix() if path.is_file() else None,
            "updated": path.stat().st_mtime if path.is_file() else None,
        })
    return result


@router.get("/face-sheet/presets")
async def get_face_sheet_presets() -> list[dict[str, str]]:
    """顔・表情シートの項目(キー・名前・顔の向きか表情か・足すタグ)。"""
    return face_presets()


@router.get("/character/{character_id}/face-sheet")
async def get_face_sheet(character_id: int, root_name: str = "training_data") -> list[dict[str, Any]]:
    """顔・表情シートの項目ごとの、描いた絵のパス(描いていなければ null)。"""
    return _face_entries(_load_character(character_id), root_name)


@router.post("/character/{character_id}/face-sheet")
async def generate_face_sheet(character_id: int, req: FaceSheetRequest, client: ClientDep) -> StreamingResponse:
    """
    顔・表情シートの項目を1枚ずつ描く(SSE で1枚ごとに知らせる)。キャラシートの見た目+向き・表情のタグ+無地の背景。
    描いた絵は <データセットのフォルダ>/face_sheet/<キー>.png に、LoRA 用のキャプション(.txt)と一緒に置く(上書き)。
    """
    character = _load_character(character_id)
    unknown = [k for k in req.keys if k not in FACE_ITEMS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"顔・表情シートに無い項目です: {unknown}")
    items = [FACE_ITEMS[k] for k in dict.fromkeys(req.keys)]
    _check_tags(character, ", ".join(i.tags for i in items), _load_guard(None), False)

    references = None
    ref_path = character.get("reference_image_path")
    if req.use_reference and ref_path and (_PROJECT_ROOT / ref_path).is_file():
        references = [CharacterReference(
            image=(_PROJECT_ROOT / ref_path).read_bytes(), type="character", fidelity=1.0, strength=req.reference_strength,
        )]
    folder = _face_dir(character, req.root_name)
    try:
        cfg = GenConfig(
            output_root=folder.parent,
            model=req.model,
            steps=req.steps,
            scale=req.scale,
            negative_prompt=sheet_negative(character),
            seed=character.get("seed") if character.get("seed") is not None else req.seed,
            character_references=references,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    async def event_gen():
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        folder.mkdir(parents=True, exist_ok=True)
        succeeded = failed = 0
        for index, item in enumerate(items, start=1):
            prompt = join_tags(sheet_prompt(character), item.tags, FACE_BACKGROUND)
            event: dict[str, Any] = {"current": index, "total": len(items), "key": item.key, "label": item.label}
            try:
                image = await generate_one(client, prompt, FACE_SIZES[item.size], cfg, seed=cfg.seed)
                path = folder / f"{item.key}.png"
                image.save(path, "PNG")
                caption = join_tags(character.get("trigger_word"), character.get("appearance_tags"), item.tags)
                path.with_suffix(".txt").write_text(caption, encoding="utf-8")
                event |= {"status": "ok", "path": path.relative_to(_PROJECT_ROOT).as_posix()}
                succeeded += 1
            except Exception as exc:
                event |= {"status": "error", "message": str(exc)}
                failed += 1
            yield _sse("progress", event)
            await asyncio.sleep(2)
        yield _sse("complete", {"total": len(items), "succeeded": succeeded, "failed": failed})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/character/{character_id}/import")
async def import_character_image(character_id: int, req: DatasetImportRequest) -> dict:
    """手元の画像をデータセットの manual/ に取り込み、キャプション(.txt)を添える。"""
    character = _load_character(character_id)
    r18 = req.rating == "r18"
    caption = req.caption if req.caption and req.caption.strip() else caption_for(character)
    _check_tags(character, caption, _load_guard(req.guard_profile_id), r18)

    data = req.image.split(",", 1)[1] if req.image.startswith("data:") else req.image
    try:
        image = Image.open(io.BytesIO(base64.b64decode(data)))
        image.load()
    except (binascii.Error, ValueError, UnidentifiedImageError):
        raise HTTPException(status_code=400, detail="画像を読み取れませんでした。")

    out_dir = _character_root(req.root_name, character) / ("manual_r18" if r18 else "manual")
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_name(Path(req.filename).stem) if req.filename else "image"
    stem = base
    n = 1
    while (out_dir / f"{stem}.png").exists():
        n += 1
        stem = f"{base}_{n}"
    # 学習ツールが扱いやすいよう PNG に揃える(メタデータは引き継がない)
    image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB").save(out_dir / f"{stem}.png", "PNG")
    (out_dir / f"{stem}.txt").write_text(caption, encoding="utf-8")
    return {"file": f"{out_dir.name}/{stem}.png", "caption": caption}
