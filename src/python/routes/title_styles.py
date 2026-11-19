"""作品風テンプレートの API(title_styles.py)。一覧だけ(テンプレートはコードに持つ)。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import title_styles

router = APIRouter(prefix="/api/title-styles", tags=["title-styles"])


@router.get("")
def list_title_styles() -> list[dict[str, Any]]:
    return title_styles.catalog()
