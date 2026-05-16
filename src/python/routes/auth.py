from __future__ import annotations

from fastapi import APIRouter
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, EmailStr

from ..auth_utils import login_with_credentials

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    try:
        token = await login_with_credentials(req.email, req.password)
        return LoginResponse(access_token=token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")
