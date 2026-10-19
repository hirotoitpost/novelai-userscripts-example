"""
キャラシートの書き出し・取り込み(ZIP)。別の PC に移したり、人に渡したりするためのもの。

ZIP の中身:
- characters.json: {"format", "version", "exported_at", "characters": [{名前・キャラシートの項目・メモ・年齢区分,
  "reference": 参照画像の ZIP の中の名前(無ければ null)}]}
- refs/{番号}.png: 参照画像

取り込むときは、キャラごとに「新しく入れる / 別名で入れる / 上書き / 入れない」を選ぶ(同じ名前があれば、はじめは
別名)。キャラシートの検査は、画面から保存するときと同じ(routes/story.py の _checked_sheet。未成年を示すタグがあると
成人にできない・未成年の定義を外すのはパスワードが要る)。
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from .db import user_files

FORMAT = "novelai-userscripts-example/characters"
VERSION = 1
MAX_ZIP_BYTES = 100 * 1024 * 1024
SHEET_KEYS = (
    "appearance_tags", "body_tags", "trigger_word", "outfit_tags", "style_tags", "negative_tags", "seed", "is_adult", "is_minor",
)
# 利用者のデータのファイルの基準(single はプロジェクトのフォルダ、multi はその人のフォルダ。db.UserPath)
_PROJECT_ROOT = user_files()
_REF_DIR = "outputs/manga/refs"


class TransferError(ValueError):
    """読めない ZIP・形の違うファイル。"""


def export_zip(characters: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    entries = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, character in enumerate(characters, start=1):
            entry: dict[str, Any] = {"name": character["name"], "notes": character.get("notes")}
            for key in SHEET_KEYS:
                value = character.get(key)
                entry[key] = bool(value) if key in ("is_adult", "is_minor") else value
            entry["reference"] = None
            ref = character.get("reference_image_path")
            if ref and (_PROJECT_ROOT / ref).is_file():
                name = f"refs/{index}{Path(ref).suffix or '.png'}"
                zf.write(_PROJECT_ROOT / ref, name)
                entry["reference"] = name
            entries.append(entry)
        manifest = {
            "format": FORMAT, "version": VERSION, "exported_at": datetime.now(timezone.utc).isoformat(), "characters": entries,
        }
        zf.writestr("characters.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buf.getvalue()


def read_zip(data: bytes) -> tuple[list[dict[str, Any]], zipfile.ZipFile]:
    """ZIP を読んで、キャラの並びと ZIP(参照画像を取り出す用)を返す。読めなければ TransferError。"""
    if len(data) > MAX_ZIP_BYTES:
        raise TransferError(f"ファイルが大きすぎます({MAX_ZIP_BYTES // 1024 // 1024}MB まで)。")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(zf.read("characters.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferError("キャラシートを書き出した ZIP ではありません(characters.json が読めません)。") from exc
    if manifest.get("format") != FORMAT or not isinstance(manifest.get("characters"), list):
        raise TransferError("このアプリでキャラシートを書き出した ZIP ではありません。")
    characters = []
    for raw in manifest["characters"]:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            raise TransferError("名前の無いキャラがあります。")
        entry = {"name": str(raw["name"]).strip()[:100], "notes": raw.get("notes")}
        for key in SHEET_KEYS:
            value = raw.get(key)
            if key in ("is_adult", "is_minor"):
                entry[key] = bool(value)
            elif key == "seed":
                entry[key] = int(value) if isinstance(value, int) and 0 <= value <= 4294967295 else None
            else:
                entry[key] = str(value) if value is not None else None
        reference = raw.get("reference")
        entry["reference"] = reference if isinstance(reference, str) and reference in zf.namelist() else None
        characters.append(entry)
    return characters, zf


def save_reference(zf: zipfile.ZipFile, name: str, character_id: int) -> str:
    """参照画像を取り出して置き、リポジトリのルートからのパスを返す。"""
    from PIL import Image

    data = zf.read(name)
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:  # noqa: BLE001 画像として読めないものは置かない
        raise TransferError(f"参照画像が読めません: {name}") from exc
    rel = f"{_REF_DIR}/char{character_id}_{uuid4().hex[:8]}{Path(name).suffix or '.png'}"
    target = _PROJECT_ROOT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return rel


def free_name(name: str, taken: set[str]) -> str:
    """使われていない名前(「みお(2)」のように番号を足す)。"""
    if name not in taken:
        return name
    number = 2
    while f"{name}({number})" in taken:
        number += 1
    return f"{name}({number})"
