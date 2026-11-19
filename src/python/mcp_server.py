"""
このアプリで作った「ワード選択エンジン」(カテゴリ/シチュエーション/排他グループ/
選択ルール/プリセット/チャンク作成)をMCPツールとして外部(Claude等)に公開するサーバー。

FastAPIバックエンドとは独立したプロセスとして動く。DB操作は db.py を直接呼び出し、
画像生成だけ NovelAI へ実際に接続する（.env の NOVELAI_API_TOKEN を使用）。
APP_MODE=multi では、.env の APP_MCP_USER の利用者として動く(その人の DB・フォルダ・永続トークン。mcp_identity.py)。

起動方法:
    uv run python -m python.mcp_server

MCPクライアント(例: Claude Desktopの mcpServers 設定)からは、このコマンドを
stdio transportで起動するよう登録する。
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent
from novelai import AsyncNovelAI
from novelai.types import CharacterReference, GenerateImageParams

from . import mcp_identity, title_styles
from .db import user_files
from .mcp_manga import register_manga_tools
from .mcp_reference import character_reference, image_preset_settings, style_record, style_reference
from .db import (
    create_custom_chunk,
    find_conflicts,
    get_connection,
    get_preset,
    list_generation_history,
    list_image_presets,
    list_presets,
    list_situations,
    record_generation,
    search_prompt_chunks,
    select_by_situation,
    select_random,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)
# multi では APP_MCP_USER の利用者として動く(DB とフォルダはその人のもの)
mcp_identity.setup()

_HISTORY_DIR = user_files() / "outputs" / "history"  # 今の利用者のフォルダ(db.UserPath)

mcp = MCPServer(
    name="novelai-word-selection",
    instructions=(
        "NovelAIのプロンプトチャンク(公式インポート+自作)を、シチュエーション/排他グループ/"
        "選択ルールに基づいて選び、実際に画像生成するためのツール群。"
        "manga_ で始まるツールは漫画作成用: 台本(1シーン=1コマ)から物語/シリーズの巻を作り、"
        "話し手の判定を確かめ、コマを生成・描き直し、合成してページを見る(バックエンドの起動が必要)。"
    ),
)
register_manga_tools(mcp)


def _get_novelai_client() -> AsyncNovelAI:
    # single は .env のトークン、multi は APP_MCP_USER の人の永続トークン
    return AsyncNovelAI(api_key=mcp_identity.novelai_token())


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
    model: str | None = None,
    size: str | None = None,
    steps: int | None = None,
    scale: float | None = None,
    seed: int | None = None,
    preset_id: int | None = None,
    sampler: str | None = None,
    noise_schedule: str | None = None,
    cfg_rescale: float | None = None,
    chunk_ids: list[str] | None = None,
    based_on: int | None = None,
    reference_character_id: int | None = None,
    reference_strength: float = 0.7,
    reference_fidelity: float = 1.0,
    reference_type: str = "character",
    style_set_id: int | None = None,
    use_style_set: bool = True,
    title_style: str | None = None,
    title_outfit: bool = True,
) -> list[TextContent | ImageContent]:
    """
    実際にNovelAIへ画像を生成させ、生成履歴に記録して画像を返す。
    chunk_ids: この生成に使ったチャンクIDがあれば渡す(履歴の追跡用、無くても生成は可能)。
    based_on: 既存の履歴エントリを元にした再生成の場合、その履歴IDを渡すと系譜を辿れる。
    reference_character_id: キャラシートの ID を渡すと、そのキャラの参照画像をキャラ参照(Character Reference)
    として使い、顔・髪を揃える(V4.5 だけ。Anlas が増える)。生成した画像はそのキャラの画像にも並ぶ。
    reference_strength / reference_fidelity: 参照の強さと忠実度(0〜1)。強いほど顔が参照に近くなるが、
    参照画像の絵柄にも引っ張られる。絵柄を寄せたいときは 0.5 / 0.6 くらいに下げる。
    reference_type: "character"(顔・髪だけ)か "character&style"(参照の絵柄も寄せる)。
    style_set_id: 絵柄セット(manga_list_style_sets)の ID。そのセットの絵柄だけの参照(style)と絵柄タグ・ネガティブを足す。
    use_style_set: reference_character_id のキャラが絵柄セットを選んでいれば、style_set_id が無くてもそれを使う
    (False で使わない)。絵柄の参照は1枚ぶん Anlas が増える。
    title_style: 作品風テンプレートのキー(manga_title_styles)。作品タグ・立ち絵の雰囲気・衣装の例をプロンプトに、
    混ざりやすい物をネガティブに足す(Anlas は増えない)。title_outfit=False なら衣装の例は足さない。
    preset_id: 画像生成のプリセット(画像生成ページで保存したもの。list_image_presets_tool)の値を使う
    (モデル・サイズ・ステップ・Scale・サンプラー・ノイズスケジュール・CFG Rescale・ネガティブ・シード)。
    model・size・steps・scale・sampler・noise_schedule・cfg_rescale・negative_prompt・seed を渡すと、それで上書き。
    渡さなければ V4.5 Full・portrait・27 ステップ・Scale 5。
    """
    preset = image_preset_settings(preset_id)
    model = model or preset.get("model") or "nai-diffusion-4-5-full"
    size = size or preset.get("size") or "portrait"
    steps = steps if steps is not None else int(preset.get("steps") or 27)
    scale = scale if scale is not None else float(preset.get("scale") or 5.0)
    negative_prompt = negative_prompt if negative_prompt is not None else preset.get("negative_prompt")
    seed = seed if seed is not None else preset.get("seed")
    sampling = {k: v for k, v in {
        "sampler": sampler or preset.get("sampler"),
        "noise_schedule": noise_schedule or preset.get("noise_schedule"),
        "cfg_rescale": cfg_rescale if cfg_rescale is not None else preset.get("cfg_rescale"),
        "variety_boost": preset.get("variety_boost"),
    }.items() if v is not None}
    prompt, negative_prompt = title_styles.apply(prompt, negative_prompt, title_style, title_outfit)
    reference = character_reference(reference_character_id, reference_type, reference_fidelity, reference_strength)
    style = style_reference(style_set_id, reference_character_id if use_style_set else None)
    if style is not None:
        prompt = ", ".join(t for t in (prompt, style.style_tags) if t.strip())
        negative_prompt = ", ".join(t for t in (negative_prompt or "", style.negative_tags) if t.strip()) or None
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
            **sampling,
        }
        if seed is not None:
            kwargs["seed"] = seed
        refs = [] if reference is None else [
            CharacterReference(image=reference.image, type=reference.type, fidelity=reference.fidelity,
                               strength=reference.strength)
        ]
        if style is not None:
            refs.append(CharacterReference(image=style.image, type="style", fidelity=style.fidelity, strength=style.strength))
        if refs:
            kwargs["character_references"] = refs
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
            size=size if isinstance(size, str) else f"{size[0]}x{size[1]}",
            steps=steps,
            scale=scale,
            seed=seed,
            chunk_ids=chunk_ids or [],
            image_paths=image_paths,
            character_references=[
                *([reference.record(_HISTORY_DIR)] if reference else []),
                *([style_record(style, _HISTORY_DIR)] if style else []),
            ] or None,
            based_on_id=based_on,
            character_id=reference_character_id,
        )
    finally:
        conn.close()

    note = f"(絵柄セット「{style.name}」を使いました)" if style else ""
    result: list[TextContent | ImageContent] = [
        TextContent(type="text", text=f"生成完了。history_id={entry['id']}{note}")
    ]
    result.extend(ImageContent(type="image", data=b64, mime_type="image/png") for b64 in b64_images)
    return result


@mcp.tool()
async def list_image_presets_tool() -> list[dict[str, Any]]:
    """画像生成のプリセット(画像生成ページで保存したもの)の一覧。generate_image_tool の preset_id に渡す。"""
    conn = get_connection()
    try:
        return [{"id": p["id"], "name": p["name"], "settings": p["settings"]} for p in list_image_presets(conn)]
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
