"""
Windows で接続の受け付けの一時的な失敗(WinError 64)で待ち受けが閉じないようにする差し替え(win_accept.py)のテスト。

実行方法:
  uv run pytest tests/test_win_accept.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.win_accept import MAX_RETRIES, is_transient, resilient_accept  # noqa: E402


def _winerror(code: int) -> OSError:
    exc = OSError(22, "error")
    exc.winerror = code  # type: ignore[attr-defined]
    return exc


class _Listener:
    def __init__(self) -> None:
        self.fd = 3

    def fileno(self) -> int:
        return self.fd


class _Proactor:
    """accept を呼ぶたびに、決めておいた結果(例外か値)の future を返す。"""

    def __init__(self, loop: asyncio.AbstractEventLoop, outcomes: list[object]) -> None:
        self._loop = loop
        self.outcomes = outcomes
        self.calls: list[asyncio.Future] = []

    def original(self, _self: object, listener: object) -> asyncio.Future:
        future = self._loop.create_future()
        self.calls.append(future)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                future.set_exception(outcome)
            elif outcome is not None:
                future.set_result(outcome)
        return future


def test_transient_errors() -> None:
    assert is_transient(_winerror(64)) and is_transient(_winerror(10054))
    assert not is_transient(_winerror(995)) and not is_transient(OSError("x")) and not is_transient(ValueError())


def test_retries_transient_failures_and_returns_the_connection() -> None:
    async def run() -> None:
        proactor = _Proactor(asyncio.get_running_loop(), [_winerror(64), _winerror(1236), ("conn", "addr")])
        accept = resilient_accept(proactor.original)
        result = await accept(proactor, _Listener())
        assert result == ("conn", "addr") and len(proactor.calls) == 3

    asyncio.run(run())


def test_other_errors_are_passed_through() -> None:
    async def run() -> None:
        proactor = _Proactor(asyncio.get_running_loop(), [_winerror(995)])
        with pytest.raises(OSError):
            await resilient_accept(proactor.original)(proactor, _Listener())
        assert len(proactor.calls) == 1
        # 待ち受けが閉じていれば、一時的なエラーでもやり直さない
        closed = _Listener()
        closed.fd = -1
        proactor = _Proactor(asyncio.get_running_loop(), [_winerror(64)])
        with pytest.raises(OSError):
            await resilient_accept(proactor.original)(proactor, closed)

    asyncio.run(run())


def test_retries_are_capped() -> None:
    async def run() -> None:
        proactor = _Proactor(asyncio.get_running_loop(), [_winerror(64)] * (MAX_RETRIES + 5))
        with pytest.raises(OSError):
            await resilient_accept(proactor.original)(proactor, _Listener())
        assert len(proactor.calls) == MAX_RETRIES + 1

    asyncio.run(run())


def test_cancelling_the_outer_future_cancels_the_pending_accept() -> None:
    async def run() -> None:
        proactor = _Proactor(asyncio.get_running_loop(), [])
        outer = resilient_accept(proactor.original)(proactor, _Listener())
        outer.cancel()
        await asyncio.sleep(0)
        assert proactor.calls[0].cancelled()

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows の ProactorEventLoop だけの不具合")
def test_server_keeps_serving_after_winerror_64(monkeypatch: pytest.MonkeyPatch) -> None:
    """実際の ProactorEventLoop で、1回目の accept が WinError 64 で失敗しても、次の接続に応答する。"""
    from asyncio import windows_events

    real = windows_events.IocpProactor.accept
    real = getattr(real, "__wrapped__", real)
    failed = [False]

    def flaky(self: object, listener: object) -> asyncio.Future:
        if not failed[0]:
            failed[0] = True
            future = self._loop.create_future()  # type: ignore[attr-defined]
            future.set_exception(_winerror(64))
            return future
        return real(self, listener)

    monkeypatch.setattr(windows_events.IocpProactor, "accept", resilient_accept(flaky))

    async def run() -> bytes:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"ok")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 5)
            data = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            return data
        finally:
            server.close()
            await server.wait_closed()

    loop = windows_events.ProactorEventLoop()
    try:
        assert loop.run_until_complete(run()) == b"ok"
        assert failed[0]
    finally:
        loop.close()
