from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from novelai import (
    AsyncNovelAI,
    AuthenticationError,
    InvalidRequestError,
    NetworkError,
    RateLimitError,
    ServerError,
)
from novelai.types import (
    Character,
    CharacterReference,
    ControlNet,
    ControlNetImage,
    GenerateImageParams,
    GenerateImageStreamParams,
    I2iParams,
    InpaintParams,
)

from ..client import get_client
from ..sdk_compat import allow_uc_preset_none
from ..db import (
    delete_image_preset,
    get_connection,
    list_image_presets,
    record_generation,
    save_image_preset,
)
from ..models import (
    EmotionToolRequest,
    UpscaleToolRequest,
    AnlasEstimateRequest,
    AnlasEstimateResponse,
    GenerateImageRequest,
    GenerateImageResponse,
    ImagePresetCreateRequest,
    ImagePresetResponse,
)

ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]

router = APIRouter(prefix="/api/image", tags=["image"])
# UC プリセット「なし」を SDK が API へ送れるようにする(sdk_compat.py)
allow_uc_preset_none()

_HISTORY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "outputs" / "history"


def _save_reference_image(image_b64: str) -> str:
    """i2i/キャラクター参照画像を outputs/history/ に保存し、リポジトリルート相対パスを返す。"""
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}_ref.png"
    (_HISTORY_DIR / filename).write_bytes(_decode_b64(image_b64))
    return f"outputs/history/{filename}"


def save_generation(
    gen: GenerateImageRequest,
    images: list[bytes],
    *,
    chunk_ids: list[str] | None = None,
    based_on: int | None = None,
) -> dict[str, Any]:
    """
    生成した画像(PNG)を outputs/history/ に保存し、generation_history に記録する。
    画像生成ページ・ワード選択のどちらから生成しても、ギャラリーに同じように並ぶ。
    i2i・キャラクター参照の元画像も、再生成できるよう一緒に保存する。
    """
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    for data in images:
        filename = f"{uuid4().hex}.png"
        (_HISTORY_DIR / filename).write_bytes(data)
        image_paths.append(f"outputs/history/{filename}")

    character_references = (
        [
            {
                "image_path": _save_reference_image(cr.image),
                "type": cr.type,
                "fidelity": cr.fidelity,
                "strength": cr.strength,
            }
            for cr in gen.character_references
        ]
        if gen.character_references
        else None
    )
    characters = (
        [
            {
                "prompt": c.prompt,
                "negative_prompt": c.negative_prompt,
                "position": c.position,
                "enabled": c.enabled,
            }
            for c in gen.characters
        ]
        if gen.characters
        else None
    )
    conn = get_connection()
    try:
        return record_generation(
            conn,
            prompt=gen.prompt,
            negative_prompt=gen.negative_prompt,
            model=gen.model,
            size=str(gen.size),
            steps=gen.steps,
            scale=gen.scale,
            seed=gen.seed,
            chunk_ids=chunk_ids or [],
            image_paths=image_paths,
            i2i_image_path=_save_reference_image(gen.i2i.image) if gen.i2i else None,
            i2i_strength=gen.i2i.strength if gen.i2i else None,
            i2i_noise=gen.i2i.noise if gen.i2i else None,
            character_references=character_references,
            characters=characters,
            based_on_id=based_on,
        )
    finally:
        conn.close()


@router.get("/presets", response_model=list[ImagePresetResponse])
async def get_image_presets() -> list[dict[str, Any]]:
    """挿絵生成のパラメータプリセット一覧。"""
    conn = get_connection()
    try:
        return list_image_presets(conn)
    finally:
        conn.close()


@router.post("/presets", response_model=ImagePresetResponse)
async def create_image_preset(req: ImagePresetCreateRequest) -> dict[str, Any]:
    """プリセットを保存する。同じ名前なら上書きする。"""
    conn = get_connection()
    try:
        return save_image_preset(conn, req.name, req.settings.model_dump())
    finally:
        conn.close()


@router.delete("/presets/{preset_id}", status_code=204)
async def delete_image_preset_endpoint(preset_id: int) -> None:
    conn = get_connection()
    try:
        delete_image_preset(conn, preset_id)
    finally:
        conn.close()


def _decode_b64(b64: str) -> bytes:
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)


def _pil_to_b64(images: list, fmt: str = "PNG") -> list[str]:
    result = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format=fmt.upper())
        result.append(base64.b64encode(buf.getvalue()).decode())
    return result


def _build_kwargs(req: GenerateImageRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "prompt": req.prompt,
        "model": req.model,
        "quality": req.quality,
        "uc_preset": req.uc_preset,
        "steps": req.steps,
        "scale": req.scale,
        "sampler": req.sampler,
        "noise_schedule": req.noise_schedule,
        "n_samples": req.n_samples,
        "cfg_rescale": req.cfg_rescale,
        "variety_boost": req.variety_boost,
        "size": tuple(req.size) if isinstance(req.size, list) else req.size,
    }

    if req.negative_prompt is not None:
        kwargs["negative_prompt"] = req.negative_prompt
    if req.seed is not None:
        kwargs["seed"] = req.seed
    if req.image_format is not None:
        kwargs["image_format"] = req.image_format

    if req.i2i:
        i2i_kw: dict[str, Any] = {
            "image": _decode_b64(req.i2i.image),
            "strength": req.i2i.strength,
            "noise": req.i2i.noise,
        }
        if req.i2i.seed is not None:
            i2i_kw["seed"] = req.i2i.seed
        kwargs["i2i"] = I2iParams(**i2i_kw)

    if req.inpaint:
        ip_kw: dict[str, Any] = {
            "image": _decode_b64(req.inpaint.image),
            "mask": _decode_b64(req.inpaint.mask),
            "strength": req.inpaint.strength,
        }
        if req.inpaint.seed is not None:
            ip_kw["seed"] = req.inpaint.seed
        kwargs["inpaint"] = InpaintParams(**ip_kw)

    if req.controlnet:
        kwargs["controlnet"] = ControlNet(
            images=[
                ControlNetImage(
                    image=_decode_b64(ci.image),
                    info_extracted=ci.info_extracted,
                    strength=ci.strength,
                    controlnet_model=ci.controlnet_model,
                )
                for ci in req.controlnet.images
            ],
            strength=req.controlnet.strength,
        )

    if req.character_references:
        kwargs["character_references"] = [
            CharacterReference(
                image=_decode_b64(cr.image),
                type=cr.type,
                fidelity=cr.fidelity,
                strength=cr.strength,
            )
            for cr in req.character_references
        ]

    if req.characters:
        kwargs["characters"] = [
            Character(
                prompt=c.prompt,
                negative_prompt=c.negative_prompt,
                position=c.position,
                enabled=c.enabled,
            )
            for c in req.characters
        ]

    return kwargs


def _http_status(exc: Exception) -> int:
    if isinstance(exc, AuthenticationError):
        return 401
    if isinstance(exc, InvalidRequestError):
        return 422
    if isinstance(exc, RateLimitError):
        return 429
    if isinstance(exc, ServerError):
        return 502
    if isinstance(exc, NetworkError):
        return 503
    return 500


# ---- ディレクターツール(表情の変更・拡大) ----


def _tool_history_request(source: bytes, label: str) -> GenerateImageRequest:
    """
    ツールで作った画像をギャラリーに残すときの記録。元の画像に NovelAI の設定が入っていれば、そのプロンプトを
    引き継ぐ(ギャラリーの検索や成人向けの判定が、元の画像と同じように働く)。先頭に、何をした画像かを付ける。
    """
    from ..nai_metadata import generation_settings

    settings = generation_settings(source) or {}
    prompt = f"[{label}] {settings.get('prompt') or ''}".strip()
    fields: dict[str, Any] = {"prompt": prompt, "negative_prompt": settings.get("negative_prompt") or None}
    if settings.get("width") and settings.get("height"):
        fields["size"] = [settings["width"], settings["height"]]
    for key in ("model", "steps", "scale", "sampler", "seed"):
        if settings.get(key) is not None:
            fields[key] = settings[key]
    try:
        return GenerateImageRequest(**fields)
    except ValueError:
        return GenerateImageRequest(prompt=prompt)


async def _is_opus(client: AsyncNovelAI) -> bool:
    """Opus の契約か(ディレクターツールの Anlas の見積もりに使う)。分からなければ False(高い方の見積もり)。"""
    try:
        subscription = await client.user.get_subscription()
    except Exception:  # noqa: BLE001 見積もりのためだけなので、取れなければ高い方で出す
        return False
    return bool(subscription.active) and int(subscription.tier) >= 3


@router.get("/tools/anlas")
async def estimate_tool_anlas(width: int, height: int, client: ClientDep, tool: str = "emotion") -> dict[str, Any]:
    """ディレクターツール(表情の変更など)の Anlas の見積もり。Opus なら 1MP 以内は 0。"""
    from typing import get_args

    from novelai.types.user.tools import DirectorTool
    from novelai.utils.anlas import calculate_augment_anlas

    if tool not in get_args(DirectorTool):
        raise HTTPException(status_code=422, detail=f"知らないツールです: {tool}")
    if not (64 <= width <= 4096 and 64 <= height <= 4096):
        raise HTTPException(status_code=422, detail="画像の大きさが範囲の外です。")
    opus = await _is_opus(client)
    estimate = calculate_augment_anlas(tool, width, height, is_opus=opus)  # type: ignore[arg-type]
    return {"tool": tool, "total_anlas": estimate.total_anlas, "is_opus": opus}


@router.post("/tools/emotion", response_model=GenerateImageResponse)
async def change_emotion(req: EmotionToolRequest, client: ClientDep) -> GenerateImageResponse:
    """絵はそのままで、人物の表情だけを変える。結果はギャラリーにも残す。"""
    source = _decode_b64(req.image)
    try:
        images = await client.tools.emotion(source, req.emotion, prompt=req.prompt.strip(), defry=req.defry)
    except Exception as exc:
        raise HTTPException(status_code=_http_status(exc), detail=str(exc))
    encoded = _pil_to_b64(images, "png")
    save_generation(_tool_history_request(source, f"表情: {req.emotion}"), [base64.b64decode(b) for b in encoded])
    return GenerateImageResponse(images=encoded, format="png")


@router.post("/tools/upscale", response_model=GenerateImageResponse)
async def upscale_image(req: UpscaleToolRequest, client: ClientDep) -> GenerateImageResponse:
    """画像を縦横2倍に拡大する。結果はギャラリーにも残す。"""
    from novelai.types import UpscaleParams

    source = _decode_b64(req.image)
    try:
        images = await client.tools.upscale(UpscaleParams(image=source))
    except Exception as exc:
        raise HTTPException(status_code=_http_status(exc), detail=str(exc))
    encoded = _pil_to_b64(images, "png")
    save_generation(_tool_history_request(source, "2倍に拡大"), [base64.b64decode(b) for b in encoded])
    return GenerateImageResponse(images=encoded, format="png")


# タグの候補は同じ問い合わせが続くので、少しだけ覚えておく(NovelAI への問い合わせを減らす)
_SUGGEST_CACHE: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
_SUGGEST_CACHE_MAX = 500
_SUGGEST_MAX_ITEMS = 12


def _suggest_model(model: str) -> str:
    """タグの候補を引けるモデル名。知らないモデルなら V4.5 Full。"""
    from typing import get_args

    from novelai.types import SuggestTagsParams

    allowed = get_args(SuggestTagsParams.model_fields["model"].annotation)
    return model if model in allowed else "nai-diffusion-4-5-full"


@router.get("/suggest-tags")
async def suggest_tags(q: str, client: ClientDep, model: str = "nai-diffusion-4-5-full") -> dict[str, Any]:
    """
    打っている途中のタグから、NovelAI のタグの候補を返す(プロンプト欄の補完用)。英数字だけなら英語のタグの候補、
    日本語が入っていれば、日本語のタグ名から英語のタグを引く。items の tag がプロンプトに入れる英語のタグ、
    label は日本語で引いたときの日本語のタグ名、count は使われている多さの目安。
    """
    from novelai.types import SuggestTagsParams

    query = q.strip()
    if not query or len(query) > 60:
        return {"lang": "en", "items": []}
    lang = "en" if query.isascii() else "jp"
    model = _suggest_model(model)
    key = (lang, model, query.lower())
    if key not in _SUGGEST_CACHE:
        params = SuggestTagsParams(prompt=query, model=model)  # type: ignore[arg-type]
        try:
            if lang == "jp":
                found = await client.image.suggest_tags_jp(params)
                items = [{"tag": s.en_tag, "label": s.jp_tag, "count": s.power} for s in found]
            else:
                found = await client.image.suggest_tags(params)
                items = [{"tag": s.tag, "label": None, "count": s.count} for s in found]
        except Exception as exc:
            raise HTTPException(status_code=_http_status(exc), detail=str(exc))
        if len(_SUGGEST_CACHE) >= _SUGGEST_CACHE_MAX:
            _SUGGEST_CACHE.clear()
        _SUGGEST_CACHE[key] = items[:_SUGGEST_MAX_ITEMS]
    return {"lang": lang, "items": _SUGGEST_CACHE[key]}


@router.post("/generate", response_model=GenerateImageResponse)
async def generate_image(
    req: GenerateImageRequest,
    client: ClientDep,
) -> GenerateImageResponse:
    try:
        params = GenerateImageParams(**_build_kwargs(req))
        images = await client.image.generate(params)
    except Exception as exc:
        raise HTTPException(status_code=_http_status(exc), detail=str(exc))
    # ギャラリーに残すため、返す形式(webp 等)とは別に PNG で保存する
    save_generation(req, [base64.b64decode(b) for b in _pil_to_b64(images, "png")])
    fmt = req.image_format or "png"
    return GenerateImageResponse(images=_pil_to_b64(images, fmt), format=fmt)


@router.post("/generate/stream")
async def generate_image_stream(
    req: GenerateImageRequest,
    client: ClientDep,
) -> StreamingResponse:
    async def event_gen():
        finals: list[bytes] = []
        try:
            params = GenerateImageStreamParams(**_build_kwargs(req))
            async for chunk in client.image.generate_stream(params):
                if chunk.event_type == "final" and chunk.image:
                    finals.append(base64.b64decode(chunk.image))
                payload = json.dumps(
                    {
                        "event_type": chunk.event_type,
                        "samp_ix": chunk.samp_ix,
                        "step_ix": chunk.step_ix,
                        "gen_id": chunk.gen_id,
                        "sigma": chunk.sigma,
                        "image": chunk.image,
                    }
                )
                yield f"event: {chunk.event_type}\ndata: {payload}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
        # 完成した画像だけをギャラリー用に保存する(途中経過の画像は残さない)。
        # 途中で失敗しても、それまでに完成した分は残す。
        if finals:
            save_generation(req, finals)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/anlas", response_model=AnlasEstimateResponse)
async def estimate_anlas(req: AnlasEstimateRequest) -> AnlasEstimateResponse:
    try:
        params = GenerateImageParams(**_build_kwargs(req.params))
        est = params.calculate_anlas(is_opus=req.is_opus)
        return AnlasEstimateResponse(**est.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
