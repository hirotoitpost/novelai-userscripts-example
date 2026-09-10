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
import re
import zipfile
from typing import Any

import httpx

_IMAGE_API_ADDRESS = "https://image.novelai.net"

V5_MODEL = "nai-diffusion-5-full"

_DEFAULT_NEGATIVE_PROMPT = (
    ", lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, "
    "multiple views, very displeasing, too many watermarks, negative space, blank page, "
)

# 1コマに載せるセリフの上限文字数。
_MAX_PANEL_TEXT_CHARS = 100

_DIALOGUE_RE = re.compile(r"[「『]([^」』]*)[」』]")


def _panel_dialogue(text: str) -> str:
    """
    コマに描かせるセリフだけを取り出す。地の文は使わない。

    実機検証: 1コマ200字の地の文を4コマ分載せるとプロンプトが約1,500字になり、
    その大半が日本語の散文になる。すると絵の指示である英語タグもコマ割り指示も
    埋もれてしまい、4コマ指定が8コマで描かれ、全コマが同じ構図(同じバストショット)に
    なった。セリフだけに絞るとタグが相対的に効くようになる。
    """
    lines = [match.group(1).strip() for match in _DIALOGUE_RE.finditer(text)]
    return " ".join(line for line in lines if line)[:_MAX_PANEL_TEXT_CHARS]


def build_manga_page_prompt(panels: list[dict[str, Any]]) -> str:
    """
    シーン(ページ内の各コマ)のリストから、V5に渡す「コマ割り指示付き」自然言語プロンプトを組み立てる。

    panels: [{"draft_text": str, "draft_prompt_tags": str}, ...] (ページに含まれる順)
    """

    # 実機検証: セリフをコマ内容として渡すと、V5はそれを実際に読める吹き出しとして
    # 描画してくれる("no text"指定は逆効果だった)。そのためタグは絵柄指定、
    # セリフは吹き出しの素材として両方渡す。地の文は渡さない(_panel_dialogue 参照)。
    n = len(panels)
    layout = f"{n}-panel comic layout" if n > 1 else "single panel manga illustration"
    parts = [f"manga page, monochrome, comic panels with speech bubbles, {layout}"]

    for i, panel in enumerate(panels, start=1):
        tags = panel.get("draft_prompt_tags", "").strip()
        text = _panel_dialogue(panel.get("draft_text", ""))
        description = ", ".join(d for d in (tags, text) if d)
        if description:
            parts.append(f"panel {i}: {description}")

    parts.append("very aesthetic, masterpiece")
    return ", ".join(parts)


# V5に渡せるキャラクター指定の上限。1ページに複数コマが入るため登場人物が増えがちだが、
# characterPrompts は画像全体に効く指定でコマごとには分けられないので、多すぎると
# かえって混ざる。V4系の上限に合わせて絞る。
_MAX_CHARACTER_PROMPTS = 4


def _build_character_prompts(
    character_tags: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    (characterPrompts, char_captions) を組み立てる。V4形式のリクエストでは同じ内容を
    この2箇所に入れる必要がある(SDKのCharacter指定が生成するボディと同じ形)。
    位置は指定せず中央固定にしている(コマ割りはbase側のプロンプトで指示しているため)。
    """
    center = {"x": 0.5, "y": 0.5}
    prompts: list[dict[str, Any]] = []
    captions: list[dict[str, Any]] = []
    for tags in character_tags[:_MAX_CHARACTER_PROMPTS]:
        prompts.append({"prompt": tags, "uc": "", "center": center, "enabled": True})
        captions.append({"char_caption": tags, "centers": [center]})
    return prompts, captions


def _build_v5_body(
    prompt: str,
    negative_prompt: str,
    *,
    model: str,
    width: int,
    height: int,
    steps: int,
    scale: float,
    sampler: str,
    noise_schedule: str,
    cfg_rescale: float,
    seed: int,
    character_tags: list[str],
) -> dict[str, Any]:
    character_prompts, char_captions = _build_character_prompts(character_tags)
    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "steps": steps,
        "scale": scale,
        "sampler": sampler,
        "seed": seed,
        "n_samples": 1,
        "negative_prompt": negative_prompt,
        "ucPreset": 1,
        "qualityToggle": True,
        "v4_prompt": {
            "caption": {"base_caption": prompt, "char_captions": char_captions},
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
        "cfg_rescale": cfg_rescale,
        "noise_schedule": noise_schedule,
        "legacy": False,
        "legacy_uc": False,
        "legacy_v3_extend": False,
        "deliberate_euler_ancestral_bug": False,
        "prefer_brownian": True,
        "strength": 0.7,
        "add_original_image": False,
        "controlnet_strength": 1.0,
        "normalize_reference_strength_multiple": False,
        "characterPrompts": character_prompts,
        "params_version": 4,
        "use_coords": False,
    }
    return {
        "action": "generate",
        "input": prompt,
        "model": model,
        "use_new_shared_trial": True,
        "parameters": parameters,
    }


async def generate_manga_page(
    api_key: str,
    panels: list[dict[str, Any]],
    *,
    model: str = V5_MODEL,
    width: int = 1216,
    height: int = 1728,
    steps: int = 28,
    scale: float = 7.0,
    sampler: str = "k_euler_ancestral",
    noise_schedule: str = "karras",
    cfg_rescale: float = 0.0,
    seed: int = 0,
    negative_prompt: str = _DEFAULT_NEGATIVE_PROMPT,
    character_tags: list[str] | None = None,
) -> bytes:
    """
    panelsからコマ割りプロンプトを組み立て、1枚の漫画ページ画像(PNGバイト列)を生成する。

    :param api_key: NovelAIのアクセストークン。バックグラウンドジョブから呼ぶため、
        リクエスト終了時に閉じられるクライアントではなくキーだけを受け取る。
    """

    prompt = build_manga_page_prompt(panels)
    body = _build_v5_body(
        prompt,
        negative_prompt,
        model=model,
        width=width,
        height=height,
        steps=steps,
        scale=scale,
        sampler=sampler,
        noise_schedule=noise_schedule,
        cfg_rescale=cfg_rescale,
        seed=seed,
        character_tags=character_tags or [],
    )

    headers = {"Authorization": f"Bearer {api_key}"}
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
