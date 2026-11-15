"""MCP の generate_image_tool で使う、キャラシートの参照画像(Character Reference)。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .db import get_character, get_connection, user_files


@dataclass
class Reference:
    """generate_image_tool に渡すキャラ参照(キャラシートの参照画像)。"""

    image: bytes
    type: str
    fidelity: float
    strength: float

    def record(self, history_dir: Path) -> dict[str, Any]:
        """履歴に残す形(画像は outputs/history/ に保存。ギャラリーの「似た画像を生成」で引き継げる)。"""
        history_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid4().hex}_ref.png"
        (history_dir / filename).write_bytes(self.image)
        return {"image_path": f"outputs/history/{filename}", "type": self.type, "fidelity": self.fidelity,
                "strength": self.strength}


def character_reference(character_id: int | None, ref_type: str, fidelity: float, strength: float) -> "Reference | None":
    """キャラシートの参照画像を読み込む。ID が無ければ None。参照画像が無いキャラはエラー。"""
    if character_id is None:
        return None
    if ref_type not in ("character", "character&style"):
        raise ValueError('reference_type は "character" か "character&style" にしてください')
    if not (0 <= fidelity <= 1 and 0 <= strength <= 1):
        raise ValueError("reference_strength / reference_fidelity は 0〜1 にしてください")
    conn = get_connection()
    try:
        character = get_character(conn, character_id)
    finally:
        conn.close()
    if character is None:
        raise ValueError(f"キャラシート {character_id} が見つかりません")
    path = character.get("reference_image_path")
    full = user_files() / path if path else None
    if full is None or not full.is_file():
        raise ValueError(f"「{character['name']}」には参照画像がありません(キャラシートで登録してください)")
    return Reference(full.read_bytes(), ref_type, fidelity, strength)
