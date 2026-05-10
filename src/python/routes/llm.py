from __future__ import annotations

import json
from typing import AsyncGenerator, cast

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from ..llm_client import (
    get_text_client,
    get_text_model,
    get_vision_client,
    get_vision_model,
)
from ..llm_models import (
    AuxTextRequest,
    CharGenRequest,
    MetadataGenRequest,
    PromptFormatRequest,
    ReversePromptRequest,
    StoryDraftRequest,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])


async def _stream_llm(
    client: AsyncOpenAI,
    model: str,
    messages: list[ChatCompletionMessageParam],
    max_tokens: int = 1024,
) -> StreamingResponse:
    async def event_gen() -> AsyncGenerator[str, None]:
        full: list[str] = []
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=max_tokens,
                temperature=0.7,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    full.append(delta)
                    yield f"event: token\ndata: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'full_text': ''.join(full)}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/prompt-format")
async def stream_prompt_format(req: PromptFormatRequest) -> StreamingResponse:
    client = get_text_client()
    system = (
        "あなたはNovelAI Diffusion向けのプロンプト整形AIです。"
        "ユーザーから受け取った曖昧な日本語や英語の説明を、"
        "NovelAIで使用できるコンマ区切りタグリストに変換してください。\n\n"
        "ルール:\n"
        "- 出力はコンマ区切りの英語タグのみ（説明文なし）\n"
        "- 品質ブースター: masterpiece, best quality, very aesthetic を必ず冒頭に含める\n"
        "- キャラクター属性（hair color, eye color, clothing）は詳細なタグに展開する\n"
        "- 感情・ポーズ・構図を具体的なタグで表現する（smile, looking at viewer, upper body）\n"
        "- タグの順序: 品質 → キャラ属性 → 服装 → 表情/ポーズ → 背景\n"
        f"- モデル {req.target_model} の傾向に合わせて調整する\n"
        "- コードブロックや余分な説明は不要"
    )
    messages = cast(list[ChatCompletionMessageParam], [
        {"role": "system", "content": system},
        {"role": "user", "content": req.rough_prompt},
    ])
    return await _stream_llm(client, get_text_model(), messages)


@router.post("/char-gen")
async def stream_char_gen(req: CharGenRequest) -> StreamingResponse:
    client = get_text_client()
    system = (
        "あなたはNovelAI向けキャラクタービジュアルタグ生成AIです。\n"
        "ユーザーのキャラクター概念説明を受け取り、"
        "NovelAIで一貫したキャラを描画するためのタグセットをJSONで生成してください。\n\n"
        "出力形式（JSONのみ、コードブロック不要）:\n"
        '{"name": "キャラ名", "positive_tags": "masterpiece, best quality, 1girl, ...", '
        '"negative_tags": "lowres, bad anatomy, ...", "character_notes": "注意点（日本語）"}\n\n'
        "ルール:\n"
        "- positive_tagsはコンマ区切りの英語タグ\n"
        "- 髪色・髪型・目の色・体型・特徴的な衣装を必ず含める\n"
        f"- {req.style}スタイルに最適化すること"
    )
    messages = cast(list[ChatCompletionMessageParam], [
        {"role": "system", "content": system},
        {"role": "user", "content": req.concept},
    ])
    return await _stream_llm(client, get_text_model(), messages, max_tokens=1024)


@router.post("/story-draft")
async def stream_story_draft(req: StoryDraftRequest) -> StreamingResponse:
    client = get_text_client()
    system = (
        "あなたはNovelAI画像生成シナリオ用の物語ドラフト生成AIです。\n"
        "ユーザーの前提設定から、画像生成に適した情景描写を含む短編物語を生成してください。\n\n"
        "出力形式:\n"
        "各シーンは以下の構造で記述する:\n"
        "【シーンN】タイトル\n"
        "情景テキスト（日本語、3〜5文）\n"
        "[生成プロンプト候補]: masterpiece, best quality, ...\n\n"
        "ルール:\n"
        f"- {req.n_scenes}個のシーンを生成する\n"
        "- 各シーンは画像1枚で表現できる情景に絞る\n"
        "- 情景テキストは日本語で情感豊かに\n"
        "- 生成プロンプト候補は英語タグのみ"
    )
    messages = cast(list[ChatCompletionMessageParam], [
        {"role": "system", "content": system},
        {"role": "user", "content": req.premise},
    ])
    return await _stream_llm(client, get_text_model(), messages, max_tokens=2048)


@router.post("/aux-text")
async def stream_aux_text(req: AuxTextRequest) -> StreamingResponse:
    client = get_text_client()
    system = (
        "あなたはNovelAI画像生成プロンプト最適化AIです。\n"
        "ユーザーのコンセプトから、ポジティブプロンプトとネガティブプロンプトの"
        "最適なペアをJSONで生成してください。\n\n"
        "出力形式（JSONのみ、コードブロック不要）:\n"
        '{"positive": "masterpiece, best quality, very aesthetic, ...", '
        '"negative": "lowres, bad quality, worst quality, bad anatomy, ...", '
        '"explanation": "このプロンプト選択の理由（日本語、2〜3文）"}\n\n'
        "ルール:\n"
        "- positiveは必ず品質ブースターで始める\n"
        "- negativeはNovelAI標準のUCプリセットを参考に拡張する\n"
        f"- モデル {req.target_model} の特性を考慮する"
    )
    messages = cast(list[ChatCompletionMessageParam], [
        {"role": "system", "content": system},
        {"role": "user", "content": req.concept},
    ])
    return await _stream_llm(client, get_text_model(), messages, max_tokens=1024)


@router.post("/metadata-gen")
async def stream_metadata_gen(req: MetadataGenRequest) -> StreamingResponse:
    client = get_text_client()
    system = (
        "あなたはNovelAI画像生成パラメータ提案AIです。\n"
        "ユーザーのコンセプトから最適な生成パラメータの完全セットをJSONで出力してください。\n\n"
        "出力形式（JSONのみ、コードブロック不要）:\n"
        '{"prompt": "masterpiece, best quality, ...", "negative_prompt": "lowres, ...", '
        f'"model": "{req.target_model}", "size": "{req.size}", '
        '"steps": 28, "scale": 6.0, "sampler": "k_euler_ancestral", '
        '"noise_schedule": "karras", "quality": true, "uc_preset": "light", '
        '"cfg_rescale": 0.0, "variety_boost": false, '
        '"reasoning": "パラメータ選択の根拠（日本語）"}\n\n'
        "制約:\n"
        f"- model は {req.target_model}\n"
        f"- size は {req.size}\n"
        "- samplerは k_euler_ancestral または k_dpmpp_2s_ancestral を推奨\n"
        "- stepsは20〜35の範囲"
    )
    messages = cast(list[ChatCompletionMessageParam], [
        {"role": "system", "content": system},
        {"role": "user", "content": req.concept},
    ])
    return await _stream_llm(client, get_text_model(), messages, max_tokens=1024)


@router.post("/reverse-prompt")
async def stream_reverse_prompt(req: ReversePromptRequest) -> StreamingResponse:
    client = get_vision_client()
    system = (
        "あなたはアニメ・イラスト画像解析AIです。\n"
        "アップロードされた画像を詳細に分析し、"
        "この画像をNovelAI Diffusionで再現するための最適なプロンプトをJSONで生成してください。\n\n"
        "出力形式（JSONのみ、コードブロック不要）:\n"
        '{"positive": "masterpiece, best quality, [詳細タグリスト]", '
        '"negative": "lowres, bad anatomy, ...", '
        '"analysis": "画像の主な特徴の説明（日本語、3〜4文）", '
        '"confidence": "high/medium/low"}\n\n'
        "分析項目:\n"
        "- 全体的な画風・タッチ\n"
        "- キャラクターの外見（髪・目・表情・服装・ポーズ）\n"
        "- 構図・アングル（upper body, looking at viewer等）\n"
        "- 背景・環境・照明\n"
        "- 色調・雰囲気\n\n"
        "ルール:\n"
        "- タグは再現性の高い順に並べる\n"
        "- 英語タグのみ、コンマ区切り"
    )
    messages = cast(list[ChatCompletionMessageParam], [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": req.image}},
                {"type": "text", "text": "この画像のNovelAIプロンプトを生成してください。"},
            ],
        },
    ])
    return await _stream_llm(client, get_vision_model(), messages, max_tokens=1024)
