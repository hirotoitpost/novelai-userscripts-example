"""
Windows で、接続の受け付けの一時的な失敗でバックエンドが止まらないようにする。

Python の asyncio(Windows の ProactorEventLoop)は、待ち受けのソケットの accept が OSError で失敗すると、
「Accept failed on a socket」とログに出して、待ち受けのソケットそのものを閉じる(proactor_events._start_serving)。
以後は1件も接続を受け付けなくなり、プロセスは生きたまま応答しなくなる。

2026-11-06〜07 の実機: スマホ(LAN)がギャラリーのサムネイルを大量に読み込んでいる最中に、接続の途中で相手が切れる
(画面を消す・Wi-Fi が切り替わる)と WinError 64(ERROR_NETNAME_DELETED)で accept が失敗し、再起動するまで
止まった。これは接続1件の失敗で、待ち受けを閉じる理由にはならない。

ここでは IocpProactor.accept を包み、こうした一時的なエラーのときは同じ待ち受けで accept をやり直す。
それ以外のエラーは、これまでどおり呼び出し元(asyncio)に返す。
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 相手側の事情で、その接続1件だけが失敗したときのエラー(Windows のエラー番号)
TRANSIENT_WINERRORS = frozenset({
    64,     # ERROR_NETNAME_DELETED: 受け付けの途中で相手が切れた
    121,    # ERROR_SEM_TIMEOUT
    1236,   # ERROR_CONNECTION_ABORTED
    10053,  # WSAECONNABORTED
    10054,  # WSAECONNRESET
})
# 1回の accept で、一時的なエラーのやり直しを続ける上限(待ち受けが本当に壊れたときに回り続けないように)
MAX_RETRIES = 100


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in TRANSIENT_WINERRORS


def resilient_accept(original: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
    """IocpProactor.accept を包む。一時的なエラーで失敗したら、同じ待ち受けで accept をやり直す。"""

    def accept(self: Any, listener: Any) -> Any:
        outer = self._loop.create_future()
        current: list[Any] = [None]
        retries = [0]

        def start() -> None:
            try:
                inner = original(self, listener)
            except OSError as exc:
                if not outer.done():
                    outer.set_exception(exc)
                return
            current[0] = inner
            inner.add_done_callback(finished)

        def finished(inner: Any) -> None:
            if outer.done():
                return
            if inner.cancelled():
                outer.cancel()
                return
            exc = inner.exception()
            if exc is None:
                outer.set_result(inner.result())
                return
            if is_transient(exc) and listener.fileno() != -1 and retries[0] < MAX_RETRIES:
                retries[0] += 1
                logger.warning("接続の受け付けに失敗したので、やり直します(待ち受けは続けます): %s", exc)
                start()
                return
            outer.set_exception(exc)

        def outer_done(future: Any) -> None:
            # 待ち受けをやめる(stop_serving)ときは、外側が取り消される。中の accept も取り消して接続を閉じさせる
            inner = current[0]
            if future.cancelled() and inner is not None and not inner.done():
                inner.cancel()

        outer.add_done_callback(outer_done)
        start()
        return outer

    accept.__wrapped__ = original  # type: ignore[attr-defined]
    return accept


def install() -> bool:
    """Windows なら IocpProactor.accept を差し替える(2回目以降は何もしない)。差し替えたら True。"""
    if sys.platform != "win32":
        return False
    from asyncio import windows_events

    proactor = windows_events.IocpProactor
    if getattr(proactor.accept, "__wrapped__", None) is not None:
        return False
    proactor.accept = resilient_accept(proactor.accept)
    return True
