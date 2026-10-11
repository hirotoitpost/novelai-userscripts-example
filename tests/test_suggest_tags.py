"""
タグの候補の API(GET /api/image/suggest-tags)のテスト。NovelAI へは問い合わせず、クライアントを差し替える。

実行方法:
  uv run pytest tests/test_suggest_tags.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.client import get_client  # noqa: E402
from python.routes import image as image_routes  # noqa: E402


class FakeImage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def suggest_tags(self, params: Any) -> list[Any]:
        self.calls.append(("en", params.prompt, params.model))
        return [SimpleNamespace(tag=f"{params.prompt} tag {i}", count=1000 - i, confidence=0.5) for i in range(20)]

    async def suggest_tags_jp(self, params: Any) -> list[Any]:
        self.calls.append(("jp", params.prompt, params.model))
        return [SimpleNamespace(jp_tag="ねこみみ", en_tag="cat ears", power=10000)]


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeImage]:
    fake = FakeImage()
    monkeypatch.setattr(image_routes, "_SUGGEST_CACHE", {})
    app = FastAPI()
    app.include_router(image_routes.router)
    app.dependency_overrides[get_client] = lambda: SimpleNamespace(image=fake)
    return TestClient(app), fake


def test_english_and_japanese_queries(api: tuple[TestClient, FakeImage]) -> None:
    client, fake = api
    body = client.get("/api/image/suggest-tags", params={"q": " school uni "}).json()
    assert body["lang"] == "en" and len(body["items"]) == 12
    assert body["items"][0] == {"tag": "school uni tag 0", "label": None, "count": 1000}
    # 日本語が入っていれば、日本語のタグ名から英語のタグを引く
    body = client.get("/api/image/suggest-tags", params={"q": "ねこみみ"}).json()
    assert body == {"lang": "jp", "items": [{"tag": "cat ears", "label": "ねこみみ", "count": 10000}]}
    assert fake.calls == [
        ("en", "school uni", "nai-diffusion-4-5-full"),
        ("jp", "ねこみみ", "nai-diffusion-4-5-full"),
    ]


def test_same_query_is_asked_once_and_model_is_checked(api: tuple[TestClient, FakeImage]) -> None:
    client, fake = api
    for query in ("twin", "TWIN", "twin"):
        assert client.get("/api/image/suggest-tags", params={"q": query}).status_code == 200
    assert len(fake.calls) == 1
    # モデルごとに語彙が違うので、モデルが変われば引き直す。知らないモデルは V4.5 Full として引く
    client.get("/api/image/suggest-tags", params={"q": "twin", "model": "nai-diffusion-5-full"})
    client.get("/api/image/suggest-tags", params={"q": "twin", "model": "something-else"})
    assert [c[2] for c in fake.calls] == ["nai-diffusion-4-5-full", "nai-diffusion-5-full"]


def test_empty_and_overlong_queries_are_not_sent(api: tuple[TestClient, FakeImage]) -> None:
    client, fake = api
    assert client.get("/api/image/suggest-tags", params={"q": "   "}).json()["items"] == []
    assert client.get("/api/image/suggest-tags", params={"q": "x" * 61}).json()["items"] == []
    assert fake.calls == []


def test_upstream_errors_are_reported(api: tuple[TestClient, FakeImage], monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake = api

    async def boom(params: Any) -> list[Any]:
        raise RuntimeError("upstream down")

    monkeypatch.setattr(fake, "suggest_tags", boom)
    res = client.get("/api/image/suggest-tags", params={"q": "twin"})
    assert res.status_code == 500 and "upstream down" in res.json()["detail"]
    # 失敗は覚えない(次は引き直す)
    assert image_routes._SUGGEST_CACHE == {}
