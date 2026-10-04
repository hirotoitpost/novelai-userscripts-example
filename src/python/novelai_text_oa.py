"""
NovelAI の新しい文章生成モデル(GLM-4.6 系)を、OpenAI 互換のエンドポイントで呼ぶラッパー。

Xialong / GLM-4.6 は従来の /ai/generate(Kayra 等。novelai_text.py)では使えず、
text.novelai.net の /oa/v1/ 以下でだけ提供されている(SillyTavern の対応 PR
https://github.com/SillyTavern/SillyTavern/pull/5967 と、2026-10 の実機で確認)。
物語の「続きを書く」用途なので、チャット形式ではなく /oa/v1/completions に本文を
そのまま渡す(公式エディタと同じ、地の文の続きを生成する使い方)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import httpx

_TEXT_API = "https://text.novelai.net/oa/v1/completions"
_CHAT_API = "https://text.novelai.net/oa/v1/chat/completions"
# タグ付けなどの指示に従わせる用途は、調整なしの汎用モデル(全プラン)を使う
INSTRUCT_MODEL = "glm-4-6"


@dataclass(frozen=True)
class TextModel:
    id: str
    label: str
    note: str


# 先頭が既定(その時点の最新)。Xialong は 2026-03-31 公開の GLM-4.6 ファインチューン(Opus 限定)。
TEXT_MODELS: list[TextModel] = [
    TextModel("xialong-v1", "Xialong", "最新・物語向けに調整(Opus プラン限定)"),
    TextModel("glm-4-6", "GLM-4.6", "調整なしの汎用モデル(全プラン)"),
]
DEFAULT_TEXT_MODEL = TEXT_MODELS[0].id

# モデルの文脈長は 28,672 トークン(公式ドキュメント)。日本語は1文字1トークン前後になる
# ことがあるので、本文は末尾から文字数で切って余裕を残す。
_MAX_BODY_CHARS = 16000
# 作者メモ(Author's Note)を差し込む位置(本文の末尾から何段落前か)。公式エディタと同じく
# 末尾の少し手前に置くと、直近の展開に効きやすい。
_AUTHOR_NOTE_DEPTH = 3


def build_prompt(memory: str, text: str, author_note: str) -> str:
    """メモリ(冒頭)+ 本文(末尾から)+ 作者メモ(末尾の少し手前)でプロンプトを組む。"""
    body = text[-_MAX_BODY_CHARS:]
    if len(text) > _MAX_BODY_CHARS and "\n" in body:
        # 段落の途中から始まらないよう、最初の改行までを捨てる
        body = body.split("\n", 1)[1]
    if author_note.strip():
        paragraphs = body.split("\n")
        at = max(len(paragraphs) - _AUTHOR_NOTE_DEPTH, 0)
        paragraphs.insert(at, f"[ {author_note.strip()} ]")
        body = "\n".join(paragraphs)
    parts = [memory.strip(), body] if memory.strip() else [body]
    return "\n".join(parts)


async def stream_completion(
    api_key: str,
    prompt: str,
    *,
    model: str = DEFAULT_TEXT_MODEL,
    max_tokens: int = 200,
    temperature: float = 1.0,
    top_p: float = 0.95,
    stop: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """本文の続きを少しずつ返す。"""
    body: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": True,
    }
    if stop:
        body["stop"] = stop
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as client:
        async with client.stream("POST", _TEXT_API, json=body, headers=headers) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode("utf-8", "replace")
                raise RuntimeError(f"NovelAI text API error {response.status_code}: {detail[:300]}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                choices = json.loads(payload).get("choices") or []
                if choices and choices[0].get("text"):
                    yield choices[0]["text"]


async def stream_chat(
    api_key: str,
    messages: list[dict[str, str]],
    *,
    model: str = INSTRUCT_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.4,
) -> AsyncGenerator[str, None]:
    """チャット形式で指示に答えさせる(ストリーミング)。非ストリーミングは本文が空で返るため使わない。"""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": True}
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as client:
        async with client.stream("POST", _CHAT_API, json=body, headers=headers) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode("utf-8", "replace")
                raise RuntimeError(f"NovelAI chat API error {response.status_code}: {detail[:300]}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                choices = json.loads(payload).get("choices") or []
                content = (choices[0].get("delta") or {}).get("content") if choices else None
                if content:
                    yield content
