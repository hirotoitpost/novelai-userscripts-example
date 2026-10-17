"""
ComfyUI の前に置く、合言葉(Bearer トークン)を確かめるだけの中継(Colab・GCP のマシンで動かす)。

ComfyUI 自体には認証が無い。cloudflared のトンネルなどで外に出すと、URL を知った人が誰でも GPU を使えてしまうので、
これを前に置き、Authorization: Bearer <トークン> が合っているときだけ ComfyUI へ通す。アプリ側は .env の
VIDEO_GEN_TOKEN に同じトークンを書く。

最後に通した時刻を --activity-file に書く(GCP で、使っていない時間が続いたら VM を止める scripts/gcp/idle_shutdown.sh が読む)。
接続の確認(/system_stats)は数えない。

合言葉は --token か、環境変数 COMFY_TOKEN で渡す(systemd では EnvironmentFile にして、ps に出ないようにする)。

  python comfy_auth_proxy.py --token <トークン> --listen 0.0.0.0:8189 --upstream http://127.0.0.1:8188

aiohttp だけを使う(ComfyUI が使っているので、同じ環境なら入っている)。
"""

from __future__ import annotations

import argparse
import hmac
import os
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

# 中継しないヘッダー(つなぎ方に関するもの)
_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

# 使ったうちに数えない問い合わせ
_NOT_ACTIVITY = {"/system_stats"}


def make_app(token: str, upstream: str, activity_file: Path | None) -> web.Application:
    expected = f"Bearer {token}".encode()

    async def handle(request: web.Request) -> web.StreamResponse:
        given = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(given, expected):
            return web.json_response({"error": "unauthorized"}, status=401)
        # 接続の確認(画面の「確かめ直す」)は、使ったうちに数えない(自動停止までの時間が延びないように)
        if activity_file is not None and request.rel_url.path not in _NOT_ACTIVITY:
            activity_file.write_text(str(time.time()))
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP and k.lower() != "authorization"
        }
        body = await request.read()
        session: ClientSession = request.app["session"]
        async with session.request(
            request.method,
            upstream + request.rel_url.path_qs,
            headers=headers,
            data=body or None,
        ) as upstream_response:
            payload = await upstream_response.read()
            out_headers = {
                k: v
                for k, v in upstream_response.headers.items()
                if k.lower() not in _HOP and k.lower() != "content-encoding"
            }
            return web.Response(
                status=upstream_response.status, headers=out_headers, body=payload
            )

    async def start(app: web.Application) -> None:
        app["session"] = ClientSession(
            timeout=ClientTimeout(total=600), auto_decompress=True
        )

    async def stop(app: web.Application) -> None:
        await app["session"].close()

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--token",
        default=os.environ.get("COMFY_TOKEN", ""),
        help="合言葉(アプリの VIDEO_GEN_TOKEN と同じもの。無ければ環境変数 COMFY_TOKEN)",
    )
    parser.add_argument("--listen", default="127.0.0.1:8189")
    parser.add_argument("--upstream", default="http://127.0.0.1:8188")
    parser.add_argument("--activity-file", type=Path, default=None)
    args = parser.parse_args()
    if len(args.token) < 16:
        parser.error(
            "トークンは16文字以上にしてください(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
        )
    host, _, port = args.listen.rpartition(":")
    web.run_app(
        make_app(args.token, args.upstream.rstrip("/"), args.activity_file),
        host=host or "0.0.0.0",
        port=int(port),
    )


if __name__ == "__main__":
    main()
