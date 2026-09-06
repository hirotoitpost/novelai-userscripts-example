"""
このアプリで作った「ワード選択エンジン」(カテゴリ/シチュエーション/排他グループ/
選択ルール/プリセット/チャンク作成)をMCPツールとして外部(Claude等)に公開するサーバー。

FastAPIバックエンドとは独立したプロセスとして動く。DB操作は db.py を直接呼び出し、
画像生成だけ NovelAI へ実際に接続する（.env の NOVELAI_API_TOKEN を使用）。

起動方法:
    uv run python -m python.mcp_server

MCPクライアント(例: Claude Desktopの mcpServers 設定)からは、このコマンドを
stdio transportで起動するよう登録する。
"""

from __future__ import annotations

import base64
import os
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent
from novelai import AsyncNovelAI
from novelai.types import GenerateImageParams

from .db import (
    create_custom_chunk,
    find_conflicts,
    get_connection,
    get_preset,
    list_generation_history,
    list_presets,
    list_situations,
    record_generation,
    search_prompt_chunks,
    select_by_situation,
    select_random,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

_HISTORY_DIR = _PROJECT_ROOT / "outputs" / "history"

mcp = MCPServer(
    name="novelai-word-selection",
    instructions=(
        "NovelAIのプロンプトチャンク(公式インポート+自作)を、シチュエーション/排他グループ/"
        "選択ルールに基づいて選び、実際に画像生成するためのツール群。"
    ),
)


def _get_novelai_client() -> AsyncNovelAI:
    token = os.environ.get("NOVELAI_API_TOKEN") or os.environ.get("NOVELAI_API_KEY")
    if not token:
        raise RuntimeError("NOVELAI_API_TOKEN が .env に設定されていません")
    return AsyncNovelAI(api_key=token)


@mcp.tool()
def list_situations_tool() -> list[dict[str, Any]]:
    """登録済みのシチュエーション(タグ分類)一覧を返す。"""
    conn = get_connection()
    try:
        return list_situations(conn)
    finally:
        conn.close()


@mcp.tool()
def list_presets_tool() -> list[dict[str, Any]]:
    """登録済みのプリセット(名前付きチャンク組み合わせ)一覧を返す。"""
    conn = get_connection()
    try:
        return list_presets(conn)
    finally:
        conn.close()


@mcp.tool()
def get_preset_tool(preset_id: int) -> dict[str, Any]:
    """プリセットの詳細(含まれるチャンクのlabel/expansion)を返す。"""
    conn = get_connection()
    try:
        preset = get_preset(conn, preset_id)
        if preset is None:
            raise ValueError(f"preset id={preset_id} が見つかりません")
        return preset
    finally:
        conn.close()


@mcp.tool()
def search_chunks_tool(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """チャンクをラベルの部分一致で検索する。select_similar_tool 等で使う chunk_id を探すのに使う。"""
    conn = get_connection()
    try:
        return search_prompt_chunks(conn, query, limit)
    finally:
        conn.close()


@mcp.tool()
def create_chunk_tool(label: str, expansion: str, situation_ids: list[int] | None = None) -> dict[str, Any]:
    """
    NovelAI公式の同期とは独立に、自前でプロンプトチャンクを新規作成する。
    label: 管理用の名前。expansion: 実際にプロンプトへ展開されるタグ文字列。
    """
    conn = get_connection()
    try:
        return create_custom_chunk(conn, label, expansion, situation_ids)
    finally:
        conn.close()


@mcp.tool()
def select_by_situation_tool(situation_id: int) -> list[dict[str, Any]]:
    """
    シナリオベース選択: 指定シチュエーションが付いたチャンクを、排他グループの重複を
    除いて全て返す(そのまま繋げれば1つのプロンプトになる)。
    """
    conn = get_connection()
    try:
        return select_by_situation(conn, situation_id)
    finally:
        conn.close()


@mcp.tool()
def select_random_tool(situation_id: int | None = None, count: int | None = None) -> list[dict[str, Any]]:
    """(任意でシチュエーション絞り込み後)排他グループの重複を除いてランダムにチャンクを選ぶ。"""
    conn = get_connection()
    try:
        return select_random(conn, situation_id, count)
    finally:
        conn.close()


@mcp.tool()
def check_conflicts_tool(chunk_ids: list[str]) -> list[dict[str, Any]]:
    """指定したチャンク群の中に、同じ排他グループのものが複数含まれていないか確認する。"""
    conn = get_connection()
    try:
        return find_conflicts(conn, chunk_ids)
    finally:
        conn.close()


@mcp.tool()
def list_generation_history_tool(limit: int = 20) -> list[dict[str, Any]]:
    """
    過去の生成履歴を返す(画像はファイルパスのみで、埋め込みはしない。ペイロード肥大化を防ぐため)。
    実際の画像が見たい場合は get_generation_image_tool を使う。
    """
    conn = get_connection()
    try:
        return list_generation_history(conn, limit)
    finally:
        conn.close()


@mcp.tool()
def get_generation_image_tool(image_path: str) -> ImageContent:
    """generation_history の image_paths / i2i_image_path 等が指すファイルを画像として取得する。"""
    full_path = (_HISTORY_DIR.parent.parent / image_path).resolve()
    full_path.relative_to(_HISTORY_DIR.resolve())  # outputs/history/ 配下以外は拒否
    if not full_path.is_file():
        raise ValueError(f"画像が見つかりません: {image_path}")
    data = base64.b64encode(full_path.read_bytes()).decode()
    return ImageContent(type="image", data=data, mime_type="image/png")


@mcp.tool()
async def generate_image_tool(
    prompt: str,
    negative_prompt: str | None = None,
    model: str = "nai-diffusion-4-5-full",
    size: str = "portrait",
    steps: int = 23,
    scale: float = 5.0,
    seed: int | None = None,
    chunk_ids: list[str] | None = None,
    based_on: int | None = None,
) -> list[TextContent | ImageContent]:
    """
    実際にNovelAIへ画像を生成させ、生成履歴に記録して画像を返す。
    chunk_ids: この生成に使ったチャンクIDがあれば渡す(履歴の追跡用、無くても生成は可能)。
    based_on: 既存の履歴エントリを元にした再生成の場合、その履歴IDを渡すと系譜を辿れる。
    """
    client = _get_novelai_client()
    try:
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "model": model,
            "size": size,
            "steps": steps,
            "scale": scale,
            "quality": True,
            "uc_preset": "light",
            "n_samples": 1,
        }
        if seed is not None:
            kwargs["seed"] = seed
        params = GenerateImageParams(**kwargs)
        images = await client.image.generate(params)
    finally:
        await client.close()

    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    b64_images: list[str] = []
    for img in images:
        buf = BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        filename = f"{uuid4().hex}_mcp.png"
        (_HISTORY_DIR / filename).write_bytes(raw)
        image_paths.append(f"outputs/history/{filename}")
        b64_images.append(base64.b64encode(raw).decode())

    conn = get_connection()
    try:
        entry = record_generation(
            conn,
            prompt=prompt,
            negative_prompt=negative_prompt,
            model=model,
            size=size,
            steps=steps,
            scale=scale,
            seed=seed,
            chunk_ids=chunk_ids or [],
            image_paths=image_paths,
            based_on_id=based_on,
        )
    finally:
        conn.close()

    result: list[TextContent | ImageContent] = [
        TextContent(type="text", text=f"生成完了。history_id={entry['id']}")
    ]
    result.extend(ImageContent(type="image", data=b64, mime_type="image/png") for b64 in b64_images)
    return result


if __name__ == "__main__":
    mcp.run()
