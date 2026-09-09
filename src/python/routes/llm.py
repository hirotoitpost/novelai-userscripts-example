from __future__ import annotations

import json
from typing import Any, AsyncGenerator, cast

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from ..llm_client import (
    get_text_base_url,
    get_text_client,
    get_text_model,
    get_vision_base_url,
    get_vision_client,
    get_vision_model,
    is_ollama_text,
    is_ollama_vision,
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


def _ollama_base(url: str) -> str:
    """Convert OpenAI-compat base URL to Ollama native API base."""
    return url.rstrip("/").removesuffix("/v1")


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks that Qwen3 may emit in the content stream."""
    import re
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


async def _stream_llm_ollama(
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 1024,
) -> StreamingResponse:
    """Ollama native /api/chat with think=False — filters Qwen3 reasoning from content."""
    ollama_base = _ollama_base(base_url)

    async def run_once() -> str:
        raw: list[str] = []
        async with httpx.AsyncClient(timeout=600) as client:
            async with client.stream(
                "POST",
                f"{ollama_base}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "think": False,
                    # デフォルトのrepeat_penalty(1.1)だと同じ文を丸ごと繰り返すことが
            # 実機で頻発したため、やや強めに設定する。
            "options": {"num_predict": max_tokens, "repeat_penalty": 1.3, "repeat_last_n": 256},
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        raw.append(delta)
                    if data.get("done"):
                        break
        return strip_think_tags("".join(raw))

    async def event_gen() -> AsyncGenerator[str, None]:
        try:
            # qwen3の思考モデルはたまに回答全体を message.thinking 側に出してしまい、
            # content が空になることがある(実機で確認済みの既知の不安定挙動)。
            # 空の場合は一度だけ再試行する。
            full_text = await run_once()
            if not full_text:
                full_text = await run_once()
            # Emit the cleaned text as a single token (avoids streaming partial think tags)
            if full_text:
                yield f"event: token\ndata: {json.dumps({'delta': full_text}, ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'full_text': full_text}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


async def _stream_text(
    messages: list[dict[str, Any]],
    max_tokens: int = 1024,
) -> StreamingResponse:
    if is_ollama_text():
        return await _stream_llm_ollama(get_text_base_url(), get_text_model(), messages, max_tokens)
    client = get_text_client()
    typed = cast(list[ChatCompletionMessageParam], messages)
    return await _stream_llm(client, get_text_model(), typed, max_tokens)


def sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_llm_text(
    messages: list[dict[str, Any]],
    request: Request | None = None,
    max_tokens: int = 1024,
    json_schema: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """
    _stream_text と同じテキストLLM(通常はOllama)から、内容のデルタを逐次yieldする。
    サーバー内部でLLM呼び出しを連鎖させる物語生成パイプラインが、進捗表示と
    途中キャンセル(request.is_disconnected())の両方に対応できるようにするための版。
    request=None なら切断チェックを行わない(バックグラウンドジョブからの呼び出し用)。

    json_schema: 指定するとOllamaの構造化出力(format)機能でJSON Schemaに適合する
    出力だけをサンプリングさせる。ローカルの小型LLMは指示した自由形式のフォーマット
    (見出し記法や番号付け)に従わないことが多く実機で複数回確認したため、機械的に
    パースする必要がある呼び出しは自由形式のプロンプト整形ではなくこちらを使う。
    """
    if is_ollama_text():
        ollama_base = _ollama_base(get_text_base_url())
        options: dict[str, Any] = {"num_predict": max_tokens}
        if json_schema is None:
            # デフォルトのrepeat_penalty(1.1)だと同じ文を丸ごと繰り返すことが実機で
            # 頻発したため、やや強めに設定する。ただしJSON Schema制約下では、必要な
            # 構造トークン(引用符・カンマ・波括弧)の反復まで抑制してしまい、自然に
            # 停止できず上限まで暴走する不具合を実機で確認したため、そちらには適用しない。
            options["repeat_penalty"] = 1.3
            options["repeat_last_n"] = 256
        body: dict[str, Any] = {
            "model": get_text_model(),
            "messages": messages,
            "stream": True,
            "think": False,
            "options": options,
        }
        if json_schema is not None:
            body["format"] = json_schema
        async with httpx.AsyncClient(timeout=600) as client:
            async with client.stream("POST", f"{ollama_base}/api/chat", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if request is not None and await request.is_disconnected():
                        return
                    if not line:
                        continue
                    data = json.loads(line)
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    if data.get("done"):
                        break
        return

    client = get_text_client()
    typed = cast(list[ChatCompletionMessageParam], messages)
    stream = await client.chat.completions.create(
        model=get_text_model(), messages=typed, stream=True, max_tokens=max_tokens, temperature=0.7
    )
    async for chunk in stream:
        if request is not None and await request.is_disconnected():
            return
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta


async def collect_llm_text(messages: list[dict[str, Any]], request: Request, max_tokens: int = 1024) -> str:
    """stream_llm_text を最後まで消費して、思考タグを除いた全文だけを返す(進捗不要な内部利用向け)。"""
    parts = [delta async for delta in stream_llm_text(messages, request, max_tokens)]
    return strip_think_tags("".join(parts))


async def _stream_vision(
    messages: list[dict[str, Any]],
    max_tokens: int = 1024,
) -> StreamingResponse:
    if is_ollama_vision():
        return await _stream_llm_ollama(get_vision_base_url(), get_vision_model(), messages, max_tokens)
    client = get_vision_client()
    typed = cast(list[ChatCompletionMessageParam], messages)
    return await _stream_llm(client, get_vision_model(), typed, max_tokens)


@router.post("/prompt-format")
async def stream_prompt_format(req: PromptFormatRequest) -> StreamingResponse:
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
    return await _stream_text([
        {"role": "system", "content": system},
        {"role": "user", "content": req.rough_prompt},
    ])


@router.post("/char-gen")
async def stream_char_gen(req: CharGenRequest) -> StreamingResponse:
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
    return await _stream_text([
        {"role": "system", "content": system},
        {"role": "user", "content": req.concept},
    ], max_tokens=1024)


def story_draft_system_prompt(n_scenes: int) -> str:
    return (
        "あなたはNovelAI画像生成シナリオ用の物語ドラフト生成AIです。\n"
        "ユーザーの前提設定から、画像生成に適した情景描写を含む短編物語を生成してください。\n\n"
        "出力形式:\n"
        "各シーンは以下の構造で記述する:\n"
        "【シーンN】タイトル\n"
        "情景テキスト（日本語、3〜5文）\n"
        "[生成プロンプト候補]: masterpiece, best quality, ...\n\n"
        "ルール:\n"
        f"- {n_scenes}個のシーンを生成する\n"
        "- 各シーンは画像1枚で表現できる情景に絞る\n"
        "- 情景テキストは日本語で情感豊かに\n"
        "- 生成プロンプト候補は英語タグのみ"
    )


@router.post("/story-draft")
async def stream_story_draft(req: StoryDraftRequest) -> StreamingResponse:
    return await _stream_text([
        {"role": "system", "content": story_draft_system_prompt(req.n_scenes)},
        {"role": "user", "content": req.premise},
    ], max_tokens=2048)


@router.post("/aux-text")
async def stream_aux_text(req: AuxTextRequest) -> StreamingResponse:
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
    return await _stream_text([
        {"role": "system", "content": system},
        {"role": "user", "content": req.concept},
    ], max_tokens=1024)


@router.post("/metadata-gen")
async def stream_metadata_gen(req: MetadataGenRequest) -> StreamingResponse:
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
    return await _stream_text([
        {"role": "system", "content": system},
        {"role": "user", "content": req.concept},
    ], max_tokens=1024)


@router.post("/reverse-prompt")
async def stream_reverse_prompt(req: ReversePromptRequest) -> StreamingResponse:
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
    if is_ollama_vision():
        # Ollama native API: image must be base64 (without data URL prefix)
        image_data = req.image
        if image_data.startswith("data:"):
            image_data = image_data.split(",", 1)[1]
        return await _stream_llm_ollama(
            get_vision_base_url(),
            get_vision_model(),
            [
                {"role": "system", "content": system},
                {"role": "user", "content": system + "\nこの画像のNovelAIプロンプトを生成してください。", "images": [image_data]},
            ],
            max_tokens=1024,
        )
    client = get_vision_client()
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
