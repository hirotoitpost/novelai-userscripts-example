"""LoRA学習用データセット生成の共通ロジック。

顔アップ/上半身/全身のショット構成は固定テンプレートとし、トリガーワード・
キャラクター外見タグ・追加タグ・衣装タグ・AI生成設定を差し替えられるようにする。
CLI (scripts/generate_lora_dataset.py) と FastAPI ルート (routes/lora_dataset.py)
の両方からこのモジュールを利用する。

バリエーションの作り方（ベースシードを指定した場合に有効、併用も可）:
- seed_offset:          画像ごとに seed+index を使う。構図が大きく変わる。
- micro_variation_tags: シードは固定したまま、末尾に光源/雰囲気タグを1つランダム追加し、
                        微小な差分だけを出す。
- shuffle_tags:         トリガーワードを先頭固定したまま残りのタグ順序をランダム化する。
"""

from __future__ import annotations

import asyncio
import base64
import io
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import AsyncGenerator

from novelai import AsyncNovelAI, RateLimitError, ServerError
from novelai.types import CharacterReference, ControlNet, ControlNetImage, GenerateImageParams
from PIL.Image import Image

from .models import (
    CharacterReferenceRequest,
    ControlNetRequest,
    ImageModelLiteral,
    NoiseScheduleLiteral,
    SamplerLiteral,
)

QUALITY_TAGS = "anime style, masterpiece, best quality"
NEGATIVE_PROMPT_DEFAULT = (
    "worst quality, low quality, blurry, bad anatomy, "
    "extra limbs, missing fingers, ugly, duplicate"
)

_V4_5_MODELS = {
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated",
}

_SEED_MAX = 4294967295


def _offset_seed(base_seed: int, offset: int) -> int:
    """ベースシードに画像インデックスを加算し、構図のバリエーションを作る（変化量は大きめ）。"""
    return (base_seed + offset) % (_SEED_MAX + 1)


def _shuffle_tags(prompt: str) -> str:
    """先頭タグ（トリガーワード）は固定し、残りのタグ順序だけをランダムに並べ替える。"""
    tags = [t.strip() for t in prompt.split(",") if t.strip()]
    if len(tags) <= 2:
        return prompt
    head, rest = tags[0], tags[1:]
    random.shuffle(rest)
    return ", ".join([head, *rest])


# シードを固定したまま末尾に追加する微小タグ。光源/雰囲気系のみに絞り、
# 構図やポーズなど主要タグへの影響を小さく抑える。
_MICRO_VARIATION_TAGS = [
    "soft lighting", "warm lighting", "cool lighting", "rim light",
    "backlighting", "dynamic lighting", "cinematic lighting",
    "depth of field", "bokeh", "ambient occlusion", "subtle shadow",
    "high contrast", "low contrast", "glowing",
]


def _pick_micro_variation_tag() -> str:
    return random.choice(_MICRO_VARIATION_TAGS)


def _decode_b64(b64: str) -> bytes:
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)


def build_character_references(
    req: CharacterReferenceRequest | None,
) -> list[CharacterReference] | None:
    """精密参照画像（Precise Character Reference）。V4.5系モデル限定。"""
    if req is None:
        return None
    return [CharacterReference(
        image=_decode_b64(req.image),
        type=req.type,
        fidelity=req.fidelity,
        strength=req.strength,
    )]


def build_controlnet(req: ControlNetRequest | None) -> ControlNet | None:
    """Vibe Transfer（雰囲気転送）。"""
    if req is None:
        return None
    return ControlNet(
        images=[
            ControlNetImage(
                image=_decode_b64(ci.image),
                info_extracted=ci.info_extracted,
                strength=ci.strength,
                controlnet_model=ci.controlnet_model,
            )
            for ci in req.images
        ],
        strength=req.strength,
    )


@dataclass
class Shot:
    category: str
    subcategory: str
    prompt: str
    size: tuple[int, int]
    count: int


def build_shots(trigger_word: str, base_tags: str, extra_tags: str, outfit_tag: str) -> list[Shot]:
    """固定テンプレート（顔30/上半身40/全身30）にタグを差し込んでショット一覧を作る。"""
    tags = ", ".join(t.strip() for t in (trigger_word, base_tags) if t.strip())
    if extra_tags.strip():
        tags = f"{tags}, {extra_tags.strip()}"
    outfit = f"{outfit_tag.strip()}, " if outfit_tag.strip() else ""

    return [
        # --- 顔アップ 30枚 ---
        Shot("face", "smile",
             f"{tags}, face focus, close-up, looking at viewer, smile, happy, gentle smile, {QUALITY_TAGS}",
             (512, 512), 10),
        Shot("face", "serious_thinking",
             f"{tags}, face focus, close-up, looking at viewer, serious expression, thinking, {QUALITY_TAGS}",
             (512, 512), 10),
        Shot("face", "surprised_shy",
             f"{tags}, face focus, close-up, looking at viewer, surprised, shy, {QUALITY_TAGS}",
             (512, 512), 10),
        # --- 上半身 40枚 ---
        Shot("upper_body", "simple_white_bg",
             f"{tags}, upper body, looking at viewer, {outfit}simple background, white background, {QUALITY_TAGS}",
             (512, 768), 10),
        Shot("upper_body", "outdoors_blue_sky",
             f"{tags}, upper body, looking at viewer, {outfit}outdoors, blue sky, {QUALITY_TAGS}",
             (512, 768), 10),
        Shot("upper_body", "indoors_window",
             f"{tags}, upper body, looking at viewer, {outfit}indoors, window, {QUALITY_TAGS}",
             (512, 768), 10),
        Shot("upper_body", "flower_field",
             f"{tags}, upper body, looking at viewer, {outfit}flower field, {QUALITY_TAGS}",
             (512, 768), 10),
        # --- 全身 30枚 ---
        Shot("full_body", "standing",
             f"{tags}, full body, standing, looking at viewer, {outfit}{QUALITY_TAGS}",
             (512, 768), 6),
        Shot("full_body", "sitting",
             f"{tags}, full body, sitting, looking at viewer, {outfit}{QUALITY_TAGS}",
             (512, 768), 6),
        Shot("full_body", "walking",
             f"{tags}, full body, walking, looking at viewer, {outfit}{QUALITY_TAGS}",
             (512, 768), 6),
        Shot("full_body", "arms_behind_back",
             f"{tags}, full body, arms behind back, looking at viewer, {outfit}{QUALITY_TAGS}",
             (512, 768), 6),
        Shot("full_body", "hands_on_hips",
             f"{tags}, full body, hands on hips, looking at viewer, {outfit}{QUALITY_TAGS}",
             (512, 768), 6),
    ]


def build_preview_shots(shots: list[Shot]) -> list[Shot]:
    """カテゴリ（顔/上半身/全身）ごとに先頭のショットを1枚だけ取り出す。"""
    seen: set[str] = set()
    preview: list[Shot] = []
    for shot in shots:
        if shot.category in seen:
            continue
        seen.add(shot.category)
        preview.append(replace(shot, count=1))
    return preview


@dataclass
class GenConfig:
    output_root: Path
    model: ImageModelLiteral = "nai-diffusion-3"
    steps: int = 23
    scale: float = 5.0
    sampler: SamplerLiteral = "k_euler_ancestral"
    noise_schedule: NoiseScheduleLiteral = "karras"
    cfg_rescale: float = 0.0
    negative_prompt: str = field(default=NEGATIVE_PROMPT_DEFAULT)
    seed: int | None = None
    seed_offset: bool = False
    micro_variation_tags: bool = False
    shuffle_tags: bool = False
    character_references: list[CharacterReference] | None = None
    controlnet: ControlNet | None = None

    def __post_init__(self) -> None:
        if self.character_references and self.model not in _V4_5_MODELS:
            raise ValueError("精密参照画像（character reference）はV4.5系モデルでのみ使用できます")


async def generate_one(
    client: AsyncNovelAI,
    prompt: str,
    size: tuple[int, int],
    cfg: GenConfig,
    seed: int | None = None,
) -> Image:
    kwargs: dict = dict(
        prompt=prompt,
        model=cfg.model,
        size=size,
        negative_prompt=cfg.negative_prompt,
        quality=True,
        uc_preset="light",
        steps=cfg.steps,
        scale=cfg.scale,
        sampler=cfg.sampler,
        noise_schedule=cfg.noise_schedule,
        cfg_rescale=cfg.cfg_rescale,
        n_samples=1,
    )
    if seed is not None:
        kwargs["seed"] = seed
    if cfg.character_references:
        kwargs["character_references"] = cfg.character_references
    if cfg.controlnet:
        kwargs["controlnet"] = cfg.controlnet
    params = GenerateImageParams(**kwargs)
    for attempt in range(5):
        try:
            images = await client.image.generate(params)
            return images[0]
        except (RateLimitError, ServerError):
            if attempt == 4:
                raise
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


async def run_dataset(
    client: AsyncNovelAI,
    shots: list[Shot],
    cfg: GenConfig,
    include_image_data: bool = False,
) -> AsyncGenerator[dict, None]:
    """画像を1枚ずつ生成して保存し、進捗イベントをyieldする。

    include_image_data=True の場合、生成画像をbase64化して"image_b64"に含める
    （プレビュー生成用。通常の100枚生成ではSSEペイロードを肥大化させないためFalse）。
    """
    total = sum(s.count for s in shots)
    done = 0

    for shot in shots:
        out_dir = cfg.output_root / shot.category
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(shot.count):
            done += 1
            stem = f"{shot.subcategory}_{i + 1:02d}"
            if cfg.seed is None:
                seed = None
            elif cfg.seed_offset:
                seed = _offset_seed(cfg.seed, done - 1)
            else:
                seed = cfg.seed

            prompt = _shuffle_tags(shot.prompt) if cfg.shuffle_tags else shot.prompt
            if cfg.micro_variation_tags:
                prompt = f"{prompt}, {_pick_micro_variation_tag()}"
            try:
                image = await generate_one(client, prompt, shot.size, cfg, seed=seed)
                image.save(out_dir / f"{stem}.png", "PNG")
                (out_dir / f"{stem}.txt").write_text(prompt, encoding="utf-8")
                event: dict = {
                    "current": done, "total": total,
                    "category": shot.category, "file": f"{stem}.png",
                    "status": "ok",
                }
                if include_image_data:
                    buf = io.BytesIO()
                    image.save(buf, format="PNG")
                    event["image_b64"] = base64.b64encode(buf.getvalue()).decode()
                yield event
            except Exception as e:
                yield {
                    "current": done, "total": total,
                    "category": shot.category, "file": f"{stem}.png",
                    "status": "error", "message": str(e),
                }
            await asyncio.sleep(2)
