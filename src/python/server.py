from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .client import close_client, init_client
from .llm_client import close_llm_clients, init_llm_clients
from .routes.auth import router as auth_router
from .routes.image import router as image_router
from .routes.llm import router as llm_router
from .routes.metadata import router as metadata_router
from .routes.user import router as user_router

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

app.include_router(auth_router)
app.include_router(image_router)
app.include_router(llm_router)
app.include_router(metadata_router)
app.include_router(user_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
