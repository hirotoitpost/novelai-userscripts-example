"""LoRA学習用データセット生成の共通ロジック。

顔アップ/上半身/全身のショット構成は固定テンプレートとし、トリガーワード・
キャラクター外見タグ・追加タグ・衣装タグ・AI生成設定を差し替えられるようにする。
CLI (scripts/generate_lora_dataset.py) と FastAPI ルート (routes/lora_dataset.py)
の両方からこのモジュールを利用する。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

from novelai import AsyncNovelAI, RateLimitError, ServerError
from novelai.types import GenerateImageParams
from PIL.Image import Image

from .models import ImageModelLiteral, NoiseScheduleLiteral, SamplerLiteral

QUALITY_TAGS = "anime style, masterpiece, best quality"
NEGATIVE_PROMPT_DEFAULT = (
    "worst quality, low quality, blurry, bad anatomy, "
    "extra limbs, missing fingers, ugly, duplicate"
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


async def generate_one(client: AsyncNovelAI, prompt: str, size: tuple[int, int], cfg: GenConfig) -> Image:
    params = GenerateImageParams(
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
) -> AsyncGenerator[dict, None]:
    """画像を1枚ずつ生成して保存し、進捗イベントをyieldする。"""
    total = sum(s.count for s in shots)
    done = 0

    for shot in shots:
        out_dir = cfg.output_root / shot.category
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(shot.count):
            done += 1
            stem = f"{shot.subcategory}_{i + 1:02d}"
            try:
                image = await generate_one(client, shot.prompt, shot.size, cfg)
                image.save(out_dir / f"{stem}.png", "PNG")
                (out_dir / f"{stem}.txt").write_text(shot.prompt, encoding="utf-8")
                yield {
                    "current": done, "total": total,
                    "category": shot.category, "file": f"{stem}.png",
                    "status": "ok",
                }
            except Exception as e:
                yield {
                    "current": done, "total": total,
                    "category": shot.category, "file": f"{stem}.png",
                    "status": "error", "message": str(e),
                }
            await asyncio.sleep(2)
