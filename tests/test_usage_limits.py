"""APP_MODE=multi の GPU の利用上限(docs/multi_user_plan_jp.md のステップ 6b)。"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import httpx
import openai
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, sessions, usage, video_remote  # noqa: E402


@pytest.fixture()
def multi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, int]:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.setenv("APP_MASTER_KEY", base64.b64encode(b"m" * 32).decode())
    monkeypatch.setenv("APP_LIMIT_LLM_PER_DAY", "2")
    monkeypatch.setenv("APP_LIMIT_VIDEO_PER_DAY", "1")
    ids = {}
    for name in ("alice", "bob", "admin"):
        email = f"{name}@example.com"
        sessions.add_invite(email)
        sessions.set_admin(email, name == "admin")
        sessions.create_session(email, "eyJ", None)
        ids[name] = sessions.user_id_of(email)
    return ids


def _client(calls: list[str], *, for_openai: bool = False) -> httpx.AsyncClient:
    transport = usage.LimitedTransport("llm", as_response=for_openai)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    transport._inner = httpx.MockTransport(handler)  # type: ignore[assignment]
    return httpx.AsyncClient(transport=transport, base_url="http://ollama.test")


def test_llm_calls_are_counted_per_user_per_day(multi: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def run(user: str, n: int) -> list[int]:
        with db.as_user(multi[user]):
            async with _client(calls) as client:
                codes = []
                for _ in range(n):
                    try:
                        codes.append((await client.post("/api/chat")).status_code)
                    except usage.LimitExceeded as exc:
                        assert "今日の上限(2 回)" in str(exc)  # 呼ぶ側の except にそのまま届く
                        codes.append(429)
                return codes

    assert asyncio.run(run("alice", 3)) == [200, 200, 429]
    assert asyncio.run(run("bob", 2)) == [200, 200]  # ほかの人の分は別
    assert asyncio.run(run("admin", 5)) == [200] * 5  # 管理者には上限をかけない
    assert len(calls) == 9  # 上限のときは Ollama に送らない
    assert usage.used_today(multi["alice"], "llm") == 2
    monkeypatch.setattr(usage, "_today", lambda: "2099-01-01")  # 次の日はまた使える
    assert asyncio.run(run("alice", 1)) == [200]


def test_the_limit_message_reaches_openai_callers(multi: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LIMIT_LLM_PER_DAY", "1")
    calls: list[str] = []

    async def run() -> str:
        with db.as_user(multi["alice"]):
            client = openai.AsyncOpenAI(base_url="http://ollama.test/v1", api_key="EMPTY", http_client=_client(calls, for_openai=True))
            await client.models.list()
            try:
                await client.models.list()
            except openai.RateLimitError as exc:
                return str(exc)
            return "no error"

    message = asyncio.run(run())
    assert "今日の上限(1 回)" in message
    assert len(calls) == 1  # 429 をやり直さない(x-should-retry: false)


def test_one_at_a_time_per_user(multi: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LIMIT_LLM_PER_DAY", "0")  # 上限なし(同時に 1 つだけを見る)
    order: list[str] = []

    async def job(user: str, label: str) -> None:
        with db.as_user(multi[user]):
            async with usage.slot("llm"):
                order.append(f"{label} start")
                await asyncio.sleep(0.05)
                order.append(f"{label} end")

    async def run() -> None:
        await asyncio.gather(job("alice", "a1"), job("alice", "a2"), job("bob", "b1"))

    asyncio.run(run())
    # 同じ人は前の処理が終わるまで待つ。ほかの人は待たない
    assert order.index("a1 end") < order.index("a2 start")
    assert order.index("b1 start") < order.index("a1 end")


def test_streamed_responses_hold_the_slot_until_closed(multi: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LIMIT_LLM_PER_DAY", "0")
    calls: list[str] = []

    async def run() -> int:
        with db.as_user(multi["alice"]):
            async with _client(calls) as client:
                async with client.stream("POST", "/api/chat") as response:
                    await response.aread()
                # 閉じたので、次の呼び出しは待たずに通る(閉じ忘れなら、ここで止まる)
                return (await asyncio.wait_for(client.post("/api/chat"), timeout=5)).status_code

    assert asyncio.run(run()) == 200


def test_videos_are_counted_per_video(multi: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_generate(*args: object, **kwargs: object) -> list[str]:
        return ["frame"]

    monkeypatch.setattr(video_remote, "_generate", fake_generate)

    async def run() -> None:
        with db.as_user(multi["alice"]):
            assert await video_remote.generate("source", "prompt") == ["frame"]
            with pytest.raises(usage.LimitExceeded, match="動画の生成"):
                await video_remote.generate("source", "prompt")

    asyncio.run(run())


def test_single_has_no_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LIMIT_LLM_PER_DAY", "1")
    calls: list[str] = []

    async def run() -> list[int]:
        async with _client(calls) as client:
            return [(await client.post("/api/chat")).status_code for _ in range(3)]  # single は上限なし

    assert asyncio.run(run()) == [200, 200, 200]
