"""
絵柄セット(style_sets)。絵柄だけの参照画像(Character Reference の style)と強さ・忠実度、絵柄タグ・ネガティブを
名前を付けて保存し、キャラ(characters.style_set_id)に選ばせる。

タグだけでは同じ絵柄にならない(細い線・柔らかい塗りなどのタグは方向を少し動かすだけ)ので、気に入った絵を
「絵柄だけ」の参照として毎回渡すと、外見の違うキャラでも同じタッチ・線・色使いで描ける(2026-11 実機)。
キャラの絵(参照画像の候補・画像生成ページ・MCP の generate_image_tool)に効かせる。漫画のコマは、キャラの参照画像が
すでに絵柄を運ぶので足さない(参照1枚ぶん Anlas が増えるため)。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from .db import get_connection, get_style_set, user_files

# 絵柄セットの画像の置き場所(利用者のフォルダ。outputs/manga はギャラリーの配信の対象)
_RELATIVE_DIR = Path("outputs") / "manga" / "style_sets"


def style_dir() -> Path:
    return user_files() / _RELATIVE_DIR


def save_image(data: bytes) -> str:
    """画像を PNG にして絵柄セットの置き場所へ写し、利用者のフォルダからの相対パスを返す(元の画像を整理しても残る)。"""
    with Image.open(io.BytesIO(data)) as opened:
        image = opened.convert("RGB")
    folder = style_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = f"style_{uuid4().hex[:12]}.png"
    image.save(folder / name, format="PNG")
    return (_RELATIVE_DIR / name).as_posix()


def image_bytes(style_set: dict[str, Any]) -> bytes:
    path = user_files() / style_set["image_path"]
    if not path.is_file():
        raise FileNotFoundError(f"絵柄セット「{style_set['name']}」の画像が見つかりません: {style_set['image_path']}")
    return path.read_bytes()


@dataclass
class StyleReference:
    """生成に足す絵柄の参照(type は style)と、絵柄タグ・ネガティブ。"""

    name: str
    image: bytes
    strength: float
    fidelity: float
    style_tags: str
    negative_tags: str


def load(style_set_id: int | None) -> StyleReference | None:
    """ID の絵柄セットを読み込む。ID が無い・セットが無い・画像が無いときは None(生成は止めない)。"""
    if style_set_id is None:
        return None
    conn = get_connection()
    try:
        style_set = get_style_set(conn, style_set_id)
    finally:
        conn.close()
    if style_set is None:
        return None
    try:
        data = image_bytes(style_set)
    except FileNotFoundError:
        return None
    return StyleReference(
        name=style_set["name"], image=data, strength=float(style_set["strength"]),
        fidelity=float(style_set["fidelity"]), style_tags=style_set["style_tags"] or "",
        negative_tags=style_set["negative_tags"] or "",
    )


def for_character(character: dict[str, Any] | None) -> StyleReference | None:
    """キャラの選んだ絵柄セット(無ければ None)。"""
    return load(character.get("style_set_id")) if character else None
