from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from novelai import AsyncNovelAI
from novelai.types import CharacterReference
from PIL import Image, UnidentifiedImageError

from ..character_sheet import (
    CORE_BLOCKED_TAGS,
    CORE_NEGATIVE,
    DEFAULT_NEGATIVE,
    EXPRESSIONS,
    FRAMINGS,
    LOCATIONS,
    OUTFITS,
    POSES,
    R18_NEGATIVE,
    SAFE_NEGATIVE,
    blocked_tags,
    build_variation_shots,
    caption_for,
    character_minor_tags,
    join_tags,
    minor_tags,
)
from ..client import get_client
from ..db import (
    delete_guard_profile,
    get_character,
    get_connection,
    get_guard_profile,
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
    DatasetImportRequest,
    GuardCoreResponse,
    GuardProfileResponse,
    GuardProfileSaveRequest,
    LoraDatasetRequest,
)
from ..similarity import similarity

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/lora-dataset", tags=["lora-dataset"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
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
    conn = get_connection()
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
    return {"blocked_tags": CORE_BLOCKED_TAGS, "negative_tags": CORE_NEGATIVE}


@router.get("/guards", response_model=list[GuardProfileResponse])
async def get_guard_profiles() -> list[dict]:
    conn = get_connection()
    try:
        return list_guard_profiles(conn)
    finally:
        conn.close()


@router.post("/guards", response_model=GuardProfileResponse)
async def post_guard_profile(req: GuardProfileSaveRequest) -> dict:
    """ガードプロファイルを保存する(同名は上書き)。基本のガードと重複するタグは保存しない。"""
    core = {t.lower() for t in CORE_BLOCKED_TAGS}
    tags: list[str] = []
    for tag in (t.strip() for t in req.blocked_tags):
        if tag and tag.lower() not in core and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    conn = get_connection()
    try:
        return save_guard_profile(conn, req.name.strip(), tags, req.negative_tags.strip())
    finally:
        conn.close()


@router.delete("/guards/{profile_id}", status_code=204)
async def delete_guard_profile_endpoint(profile_id: int) -> None:
    conn = get_connection()
    try:
        delete_guard_profile(conn, profile_id)
    finally:
        conn.close()


@router.get("/variations")
async def get_variations() -> dict[str, list[str]]:
    """キャラ別データセットのバリエーション既定リスト(全年齢向け)。"""
    return {"framings": FRAMINGS, "poses": POSES, "outfits": OUTFITS, "expressions": EXPRESSIONS, "locations": LOCATIONS}


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
    variations = req.framings + req.poses + req.outfits + req.expressions + req.locations
    _check_tags(
        character,
        ", ".join([*variations, character.get("outfit_tags") or "", character.get("style_tags") or ""]),
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
            negative_prompt=join_tags(
                DEFAULT_NEGATIVE,
                character.get("negative_tags"),
                R18_NEGATIVE if r18 else SAFE_NEGATIVE,
                guard["negative_tags"],
            ),
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
    )
    # R18 は全年齢のデータセットと混ざらないよう別フォルダにする
    suffix = "_r18" if r18 else ""
    out_dir = output_root / f"generated{suffix}"

    async def event_gen():
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        out_dir.mkdir(parents=True, exist_ok=True)
        rejected_dir = output_root / f"rejected{suffix}"
        # 既存ファイルを上書きしないよう、連番は今ある枚数の続きから振る
        start = len(list(out_dir.glob("*.png")))
        succeeded = failed = 0
        for i, shot in enumerate(shots, start=1):
            stem = f"{start + i:04d}"
            event: dict = {"current": i, "total": len(shots), "category": out_dir.name, "file": f"{stem}.png"}
            try:
                candidates: list[tuple[float, Image.Image]] = []
                for attempt in range(req.max_attempts):
                    # 同じシード・同じプロンプトだと同じ絵になるので、引き直しはシードをずらす
                    seed = None if cfg.seed is None else (cfg.seed + attempt) % 4294967296
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
                best.save(out_dir / f"{stem}.png", "PNG")
                (out_dir / f"{stem}.txt").write_text(shot.prompt, encoding="utf-8")
                buf = io.BytesIO()
                best.save(buf, format="PNG")
                event |= {
                    "status": "ok",
                    "prompt": shot.prompt,
                    "attempts": len(candidates),
                    "score": round(best_score, 3) if ref_image is not None else None,
                    "image_b64": base64.b64encode(buf.getvalue()).decode(),
                }
                succeeded += 1
            except Exception as exc:
                event |= {"status": "error", "message": str(exc)}
                failed += 1
            yield _sse("progress", event)
            await asyncio.sleep(2)
        yield _sse("complete", {
            "total": len(shots), "succeeded": succeeded, "failed": failed, "output_path": str(output_root),
        })

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
