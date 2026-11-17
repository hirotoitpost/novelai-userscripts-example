"""MCP の generate_image_tool で使う、キャラシートの参照画像(Character Reference)。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .db import get_character, get_connection, list_image_presets, user_files
from .style_sets import StyleReference, load as load_style_set


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


def style_reference(style_set_id: int | None, character_id: int | None) -> StyleReference | None:
    """
    使う絵柄セット。style_set_id があればそれ、無ければ character_id のキャラが選んだセット(どちらも無ければ None)。
    style_set_id を指定したのにセットが無いときはエラー。
    """
    if style_set_id is not None:
        style = load_style_set(style_set_id)
        if style is None:
            raise ValueError(f"絵柄セット {style_set_id} が見つかりません(画像が無い場合も)")
        return style
    if character_id is None:
        return None
    conn = get_connection()
    try:
        character = get_character(conn, character_id)
    finally:
        conn.close()
    return load_style_set(character.get("style_set_id")) if character else None


def style_record(style: StyleReference, history_dir: Path) -> dict[str, Any]:
    """履歴に残す形(絵柄の参照画像も outputs/history/ に写す。ギャラリーの「似た画像を生成」で引き継げる)。"""
    return Reference(style.image, "style", style.fidelity, style.strength).record(history_dir)


_SIZE_NAMES = {(832, 1216): "portrait", (1216, 832): "landscape", (1024, 1024): "square",
               (1024, 1536): "large_portrait", (1536, 1024): "large_landscape"}


def image_preset_settings(preset_id: int | None) -> dict[str, Any]:
    """画像生成のプリセットの値(size は名前か (幅, 高さ))。preset_id が無ければ空。"""
    if preset_id is None:
        return {}
    conn = get_connection()
    try:
        presets = {p["id"]: p for p in list_image_presets(conn)}
    finally:
        conn.close()
    if preset_id not in presets:
        raise ValueError(f"プリセット {preset_id} がありません(list_image_presets_tool で一覧を見られます)")
    settings = dict(presets[preset_id]["settings"])
    width, height = settings.get("width"), settings.get("height")
    if width and height:
        settings["size"] = _SIZE_NAMES.get((int(width), int(height)), (int(width), int(height)))
    return settings
