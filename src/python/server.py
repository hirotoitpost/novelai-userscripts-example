from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from .client import app_mode, close_client, init_client
from .sessions import SessionMiddleware, allowed_origins, check_config
from .usage import LimitExceeded
from .llm_client import close_llm_clients, init_llm_clients
from .routes.admin import router as admin_router
from .routes.auth import router as auth_router
from .routes.batch import router as batch_router
from .routes.chunks import router as chunks_router
from .routes.content_guard import router as content_guard_router
from .routes.trash import router as trash_router
from .routes.ugoira import router as ugoira_router
from .routes.gallery_manga import router as gallery_manga_router
from .routes.quick_manga import router as quick_manga_router
from .routes.story_arcs import router as story_arcs_router
from .routes.image import router as image_router
from .routes.llm import router as llm_router
from .routes.manga_draft import router as manga_draft_router
from .routes.manga_import import router as manga_import_router
from .routes.manga_v2 import router as manga_v2_router
from .routes.library import router as library_router
from .routes.lora_dataset import router as lora_dataset_router
from .routes.metadata import router as metadata_router
from .routes.ocr import router as ocr_router
from .routes.publish import router as publish_router
from .routes.push import router as push_router
from .routes.series import router as series_router
from .routes.story import router as story_router
from .routes.story_edit import router as story_edit_router
from .routes.user import router as user_router
from .routes.works import router as works_router
from .routes.writer import router as writer_router

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_env_path = _PROJECT_ROOT / ".env"
# ふだんは .env の値を優先する(PC の環境変数に古い値が残っていても、.env を直せば効くように)。Docker では
# compose の environment(LLM の場所を http://ollama:11434 にする など)を優先したいので、上書きしない
load_dotenv(_env_path, override=os.environ.get("APP_IN_CONTAINER") != "1")



async def _auto_backup_loop() -> None:
    """1時間ごとに、自動バックアップ(1日1回)を取る頃かを見る(backup.py)。"""
    import asyncio

    from . import backup

    while True:
        try:
            await asyncio.to_thread(backup.auto_tick)
        except Exception:  # noqa: BLE001 見回りが止まらないように
            pass
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    import asyncio

    await init_client()
    check_config()
    await init_llm_clients()
    auto_backup = asyncio.create_task(_auto_backup_loop())
    yield
    auto_backup.cancel()
    await close_client()
    await close_llm_clients()


app = FastAPI(
    title="NovelAI Userscripts Backend",
    version="0.11.0",
    lifespan=lifespan,
)



class _CorsByMode:
    """
    single: どこからでも呼べる(今までどおり)。multi: ログインの Cookie を使うので、APP_ALLOWED_ORIGINS
    (カンマ区切り)に書いた配信元だけ。書かなければ同じ配信元だけ(Vite の中継や、バックエンドが配る画面)。
    """

    def __init__(self, app: ASGIApp) -> None:
        common = {"allow_credentials": True, "allow_methods": ["*"], "allow_headers": ["*"]}
        self.single = CORSMiddleware(app, allow_origins=["*"], **common)
        self.multi = CORSMiddleware(app, allow_origins=allowed_origins(), **common)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await (self.multi if app_mode() == "multi" else self.single)(scope, receive, send)


@app.exception_handler(LimitExceeded)
async def _limit_exceeded(request: Request, exc: LimitExceeded) -> JSONResponse:
    """GPU の利用上限(multi。usage.py)に達した。"""
    return JSONResponse(status_code=429, content={"detail": str(exc)})


# 後に加えたものが外側。CORS(前置きの問い合わせに答える)→ ログインのセッション(multi)→ 各ルーター
app.add_middleware(SessionMiddleware)
app.add_middleware(_CorsByMode)

app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(batch_router)
app.include_router(chunks_router)
app.include_router(content_guard_router)
app.include_router(trash_router)
app.include_router(ugoira_router)
app.include_router(gallery_manga_router)
app.include_router(quick_manga_router)
app.include_router(story_arcs_router)
app.include_router(image_router)
app.include_router(llm_router)
app.include_router(manga_draft_router)
app.include_router(manga_import_router)
app.include_router(manga_v2_router)
app.include_router(library_router)
app.include_router(lora_dataset_router)
app.include_router(metadata_router)
app.include_router(ocr_router)
app.include_router(publish_router)
app.include_router(push_router)
app.include_router(series_router)
app.include_router(works_router)
app.include_router(story_router)
app.include_router(story_edit_router)
app.include_router(user_router)
app.include_router(writer_router)

# LAN 用の認証局の証明書(scripts/make_lan_cert.py)。スマホにインストールすると、この PC のサーバーを
# https で警告なしに開け、ホーム画面へのインストール(PWA)や通知が使える。秘密鍵(ca.key)は出さない
_LAN_CA = Path(__file__).resolve().parent.parent.parent / "data" / "certs" / "ca.crt"


@app.get("/api/lan-ca.crt", include_in_schema=False)
async def lan_ca() -> Response:
    if not _LAN_CA.is_file():
        raise HTTPException(status_code=404, detail="LAN 用の証明書がまだありません(scripts/make_lan_cert.py)。")
    # Android は application/x-x509-ca-cert なら CA 証明書としてインストールに進む
    return Response(
        _LAN_CA.read_bytes(),
        media_type="application/x-x509-ca-cert",
        headers={"Content-Disposition": 'attachment; filename="novelai-lan-ca.crt"'},
    )


# ---- ビルドした画面を配る(Docker。SERVE_FRONTEND=1 のときだけ) ----
# 開発中は Vite の開発サーバー(:5173)が画面を配るので、ここでは何もしない。Docker では画面と API を同じ場所(:8000)
# から配る(画面は backendOrigin.ts で、ビルドしたときは同じ場所の API を呼ぶ)。API の道は先に登録してあるので、
# ここに来るのは API 以外の道だけ。知らない道は index.html を返す(画面の中の道 /gallery などは画面が見分ける)。
_DIST = _PROJECT_ROOT / "dist"


def _serve_frontend() -> None:
    import os

    from fastapi.responses import FileResponse

    if os.environ.get("SERVE_FRONTEND") != "1" or not (_DIST / "index.html").is_file():
        return
    root = _DIST.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        file = (root / path).resolve()
        if path and file.is_file() and root in file.parents:
            return FileResponse(file)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})


_serve_frontend()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
