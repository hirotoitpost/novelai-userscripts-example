from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from .client import close_client, init_client
from .llm_client import close_llm_clients, init_llm_clients
from .routes.admin import router as admin_router
from .routes.auth import router as auth_router
from .routes.batch import router as batch_router
from .routes.chunks import router as chunks_router
from .routes.image import router as image_router
from .routes.llm import router as llm_router
from .routes.manga_draft import router as manga_draft_router
from .routes.manga_import import router as manga_import_router
from .routes.manga_v2 import router as manga_v2_router
from .routes.library import router as library_router
from .routes.lora_dataset import router as lora_dataset_router
from .routes.metadata import router as metadata_router
from .routes.ocr import router as ocr_router
from .routes.push import router as push_router
from .routes.series import router as series_router
from .routes.story import router as story_router
from .routes.user import router as user_router
from .routes.works import router as works_router
from .routes.writer import router as writer_router

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_env_path = _PROJECT_ROOT / ".env"
load_dotenv(_env_path, override=True)



@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_client()
    await init_llm_clients()
    yield
    await close_client()
    await close_llm_clients()


app = FastAPI(
    title="NovelAI Userscripts Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(batch_router)
app.include_router(chunks_router)
app.include_router(image_router)
app.include_router(llm_router)
app.include_router(manga_draft_router)
app.include_router(manga_import_router)
app.include_router(manga_v2_router)
app.include_router(library_router)
app.include_router(lora_dataset_router)
app.include_router(metadata_router)
app.include_router(ocr_router)
app.include_router(push_router)
app.include_router(series_router)
app.include_router(works_router)
app.include_router(story_router)
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
