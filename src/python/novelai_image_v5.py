"""
NovelAI Diffusion V5 (nai-diffusion-5-full) で「1回の生成でコマ割り済みの漫画ページ」を
作るための薄いラッパー。

V5のコマ割り機能に専用APIパラメータは無く、通常の /ai/generate-image に対して
自然言語でコマ割りを記述するだけでよい(NovelAI公式journal記事で確認済み)。
ただし現在の novelai-sdk (0.8.1) は "nai-diffusion-5-full" を model の Literal 型に
含んでおらず、GenerateImageParams に渡すとpydanticバリデーションで弾かれるため、
ここでは novelai-sdk を経由せず直接 httpx でリクエストを組み立てる
(chunks.py の /promptmacros と同じ「生httpx + Bearer」パターン)。

リクエストボディの形は、実際にSDK経由で送られる正常なV4.5リクエストを一度キャプチャして
確認した上で、V5用に params_version=4 / noise_schedule=karras に変更して組み立てている
(実機で200 OK・コマ割り画像が返ることを確認済み)。
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import httpx
from novelai import AsyncNovelAI

_IMAGE_API_ADDRESS = "https://image.novelai.net"

V5_MODEL = "nai-diffusion-5-full"

_DEFAULT_NEGATIVE_PROMPT = (
    ", lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, "
    "multiple views, very displeasing, too many watermarks, negative space, blank page, "
)

# 1コマの説明にそのまま載せる本文の上限文字数。
_MAX_PANEL_TEXT_CHARS = 200


def build_manga_page_prompt(panels: list[dict[str, Any]]) -> str:
    """
    シーン(ページ内の各コマ)のリストから、V5に渡す「コマ割り指示付き」自然言語プロンプトを組み立てる。

    panels: [{"draft_text": str, "draft_prompt_tags": str}, ...] (ページに含まれる順)
    """

    # 実機検証: novelai_text(セリフを含む本文)をそのままコマ内容として渡すと、
    # V5はそのセリフを実際に読める吹き出しとして描画してくれる("no text"指定は逆効果だった)。
    # そのためタグは絵柄指定、本文はセリフ素材として両方渡す。
    n = len(panels)
    layout = f"{n}-panel comic layout" if n > 1 else "single panel manga illustration"
    parts = [f"manga page, monochrome, comic panels with speech bubbles, {layout}"]

    for i, panel in enumerate(panels, start=1):
        tags = panel.get("draft_prompt_tags", "").strip()
        # 本文はセリフ素材として渡すだけなので、長いシーンをそのまま載せない。
        # 分割条件によっては1シーンが数千字になり得るが、その長さのプロンプトは
        # トークン上限を超えて後続のコマ指定ごと無視されてしまう。
        text = panel.get("draft_text", "").strip()[:_MAX_PANEL_TEXT_CHARS]
        description = ", ".join(d for d in (tags, text) if d)
        if description:
            parts.append(f"panel {i}: {description}")

    parts.append("very aesthetic, masterpiece")
    return ", ".join(parts)


def _build_v5_body(prompt: str, negative_prompt: str, *, width: int, height: int, steps: int, scale: float, seed: int) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "steps": steps,
        "scale": scale,
        "sampler": "k_euler_ancestral",
        "seed": seed,
        "n_samples": 1,
        "negative_prompt": negative_prompt,
        "ucPreset": 1,
        "qualityToggle": True,
        "v4_prompt": {
            "caption": {"base_caption": prompt, "char_captions": []},
            "use_coords": False,
            "use_order": True,
        },
        "v4_negative_prompt": {
            "caption": {"base_caption": negative_prompt, "char_captions": []},
            "legacy_uc": False,
        },
        "sm": False,
        "sm_dyn": False,
        "autoSmea": False,
        "dynamic_thresholding": False,
        "cfg_rescale": 0.0,
        "noise_schedule": "karras",
        "legacy": False,
        "legacy_uc": False,
        "legacy_v3_extend": False,
        "deliberate_euler_ancestral_bug": False,
        "prefer_brownian": True,
        "strength": 0.7,
        "add_original_image": False,
        "controlnet_strength": 1.0,
        "normalize_reference_strength_multiple": False,
        "characterPrompts": [],
        "params_version": 4,
        "use_coords": False,
    }
    return {
        "action": "generate",
        "input": prompt,
        "model": V5_MODEL,
        "use_new_shared_trial": True,
        "parameters": parameters,
    }


async def generate_manga_page(
    client: AsyncNovelAI,
    panels: list[dict[str, Any]],
    *,
    width: int = 1216,
    height: int = 1728,
    steps: int = 28,
    scale: float = 7.0,
    seed: int = 0,
    negative_prompt: str = _DEFAULT_NEGATIVE_PROMPT,
) -> bytes:
    """
    panelsからコマ割りプロンプトを組み立て、V5で1枚の漫画ページ画像(PNGバイト列)を生成する。

    :param client: 認証済みのAsyncNovelAIクライアント(api_keyの取り出しにのみ使う)
    """

    prompt = build_manga_page_prompt(panels)
    body = _build_v5_body(prompt, negative_prompt, width=width, height=height, steps=steps, scale=scale, seed=seed)

    headers = {"Authorization": f"Bearer {client.api_key}"}
    async with httpx.AsyncClient(headers=headers, timeout=180) as http_client:
        response = await http_client.post(f"{_IMAGE_API_ADDRESS}/ai/generate-image", json=body)
        if response.status_code != 200:
            raise RuntimeError(f"NovelAI V5 image API error {response.status_code}: {response.text}")
        content = response.content

    zf = zipfile.ZipFile(io.BytesIO(content))
    names = zf.namelist()
    if not names:
        raise RuntimeError("NovelAI V5 image API returned an empty archive")
    return zf.read(names[0])
