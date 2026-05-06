import os

from novelai import AsyncNovelAI

_client: AsyncNovelAI | None = None


async def init_client() -> None:
    global _client
    api_key = os.environ.get("NOVELAI_API_KEY") or os.environ.get("NOVELAI_API_TOKEN")
    _client = AsyncNovelAI(api_key=api_key)


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_client() -> AsyncNovelAI:
    if _client is None:
        raise RuntimeError("NovelAI client not initialized")
    return _client
