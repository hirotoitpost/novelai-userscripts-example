from __future__ import annotations

import logging
import os

from fastapi import HTTPException
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_text_client: AsyncOpenAI | None = None
_vision_client: AsyncOpenAI | None = None
_text_model: str = "Qwen/Qwen2-VL-7B-Instruct"
_vision_model: str = "Qwen/Qwen2-VL-7B-Instruct"


async def init_llm_clients() -> None:
    global _text_client, _vision_client, _text_model, _vision_model

    text_base_url = os.environ.get("VLLM_BASE_URL", "")
    vision_base_url = os.environ.get("VLLM_VISION_BASE_URL", "")
    _text_model = os.environ.get("VLLM_MODEL", "Qwen/Qwen2-VL-7B-Instruct")
    _vision_model = os.environ.get("VLLM_VISION_MODEL", "Qwen/Qwen2-VL-7B-Instruct")

    if text_base_url:
        _text_client = AsyncOpenAI(base_url=text_base_url, api_key="EMPTY")
        logger.info("vLLM text client initialized: %s (model: %s)", text_base_url, _text_model)
    else:
        logger.warning("VLLM_BASE_URL not set — /api/llm/* endpoints will return 503")

    if vision_base_url:
        _vision_client = AsyncOpenAI(base_url=vision_base_url, api_key="EMPTY")
        logger.info("vLLM vision client initialized: %s (model: %s)", vision_base_url, _vision_model)
    else:
        logger.warning("VLLM_VISION_BASE_URL not set — /api/llm/reverse-prompt will return 503")


async def close_llm_clients() -> None:
    global _text_client, _vision_client
    if _text_client is not None:
        await _text_client.close()
        _text_client = None
    if _vision_client is not None:
        await _vision_client.close()
        _vision_client = None


def get_text_client() -> AsyncOpenAI:
    if _text_client is None:
        raise HTTPException(
            status_code=503,
            detail="vLLM テキストサーバーが設定されていません。.env に VLLM_BASE_URL を設定してください。",
        )
    return _text_client


def get_vision_client() -> AsyncOpenAI:
    if _vision_client is None:
        raise HTTPException(
            status_code=503,
            detail="vLLM ビジョンサーバーが設定されていません。.env に VLLM_VISION_BASE_URL を設定してください。",
        )
    return _vision_client


def get_text_model() -> str:
    return _text_model


def get_vision_model() -> str:
    return _vision_model
