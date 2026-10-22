"""
漫画作成(物語 → 漫画v2)の MCP ツール群。Claude 等が台本を書いてそのまま漫画にできるようにする。

漫画作りで毎回くり返していた作業を道具にしたもの:
- 台本(1シーン=1コマ: セリフ・作画タグ・登場キャラ・ナレーション・効果音)から物語/シリーズの巻を作る
- 合成と同じ規則で、セリフの話し手の判定を確かめる(しっぽの向き先になる)
- コマを生成する。シーンごとにシードをずらせる(同じシードだと全コマ同じ構図になるため)
- 1コマだけ描き直す・コマの絵を見る・合成してページを見る
- シリーズの巻のあらすじを付ける(次の巻を作るときの前提になる)
- ページのコマ割り(パターン・形・斜めの境目・間隔/余白・色・重ねて置くコマ・重なり順)と、コマの背景を消す
- シーンの状態・シチュエーション・この場面の服装、1行ずつのセリフと話し手、コマの生成サイズ

コマの生成ジョブや合成はバックエンド(FastAPI)の処理をそのまま使うため、ここからは HTTP で
バックエンドを呼ぶ。バックエンドを起動しておくこと(.env の MANGA_BACKEND_URL で接続先を変えられる)。
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import re
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent
from PIL import Image

from . import mcp_identity
from .db import user_files

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# バックエンドが返す outputs/ のパスの基準(multi では MCP サーバーの利用者のフォルダ。db.UserPath)
_USER_ROOT = user_files()
# バックエンドは既定で http、dev-ctl.ps1 -Https なら LAN 用の証明書(scripts/make_lan_cert.py)で https で動く
_CA_FILE = _PROJECT_ROOT / "data" / "certs" / "ca.crt"
_SCHEME_FILE = _PROJECT_ROOT / "data" / "run" / "backend.scheme"


def _default_backend() -> str:
    """dev-ctl.ps1 がバックエンドを起動したときの方式(data/run/backend.scheme)。無ければ既定の http。"""
    try:
        scheme = _SCHEME_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        scheme = ""
    if scheme not in ("http", "https"):
        scheme = "http"
    return f"{scheme}://127.0.0.1:8000"
# 同じシードだと構図が似るので、シーンごとにこの間隔でずらす
_SEED_STEP = 37
# プリセットのうち、コマの生成設定(MangaImageSettings)に使う項目
_PRESET_KEYS = ("model", "steps", "scale", "sampler", "noise_schedule", "cfg_rescale", "complexity", "negative_prompt")
_JOB_POLL_SECONDS = 4


def _backend() -> str:
    return os.environ.get("MANGA_BACKEND_URL", _default_backend()).rstrip("/")


def _verify() -> str | bool:
    """https のバックエンドは、自前の認証局(ca.crt)で証明書を確かめる。"""
    return str(_CA_FILE) if _CA_FILE.exists() else True


async def _call(method: str, path: str, body: Any = None, timeout: float = 120) -> Any:
    async with httpx.AsyncClient(base_url=_backend(), timeout=timeout, verify=_verify()) as client:
        try:
            # multi では MCP サーバーの利用者のセッションで呼ぶ(期限が切れていたら作り直して 1 回だけやり直す)
            response = await client.request(method, path, json=body, cookies=mcp_identity.backend_cookies())
            if response.status_code == 401 and mcp_identity.user_id() is not None:
                response = await client.request(
                    method, path, json=body, cookies=mcp_identity.backend_cookies(renew=True)
                )
        except httpx.ConnectError as exc:
            raise RuntimeError(f"バックエンド({_backend()})に接続できません。起動しているか確認してください。") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"{method} {path} が失敗しました({response.status_code}): {detail}")
    return response.json() if response.content else None


async def _call_bytes(method: str, path: str, body: Any = None, timeout: float = 600) -> bytes:
    """JSON でない応答(PDF など)を受け取る。"""
    async with httpx.AsyncClient(base_url=_backend(), timeout=timeout, verify=_verify()) as client:
        response = await client.request(method, path, json=body, cookies=mcp_identity.backend_cookies())
        if response.status_code == 401 and mcp_identity.user_id() is not None:
            response = await client.request(method, path, json=body, cookies=mcp_identity.backend_cookies(renew=True))
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} が失敗しました({response.status_code}): {response.text[:300]}")
    return response.content


# 物語の編集画面の内容(GET /edit のシーン)を、保存(PUT /edit)に送る形にするときに写す項目
_EDIT_KEYS = ("id", "title", "text", "prompt_tags", "narration", "sfx", "page_index", "characters", "panel_size")


def _edit_item(scene: dict[str, Any]) -> dict[str, Any]:
    """GET /edit のシーンを PUT /edit の形に(決めていたセリフ・コマのネガティブはそのまま送り直す)。"""
    item = {k: scene[k] for k in _EDIT_KEYS}
    item["dialogue"] = scene["dialogue"] if scene["dialogue_custom"] else None
    if "negative_prompt" in scene:
        item["negative_prompt"] = scene["negative_prompt"]
    return item


def _script_to_edit(scene: dict[str, Any], page_index: int) -> dict[str, Any]:
    """台本の形のシーン(manga_create_scripted_story)を、PUT /edit の新しいシーンにする。"""
    actions = {int(k): v for k, v in (scene.get("character_actions") or {}).items()}
    outfits = {int(k): v for k, v in (scene.get("character_outfits") or {}).items()}
    item: dict[str, Any] = {
        "id": None,
        "title": scene.get("title"),
        "text": scene.get("text") or "",
        "prompt_tags": scene.get("prompt_tags") or "",
        "narration": scene.get("narration"),
        "sfx": scene.get("sfx") or [],
        "page_index": page_index,
        "panel_size": None,
        "dialogue": scene.get("dialogue"),
        "characters": [
            {"id": cid, "action_tags": actions.get(cid, ""), "outfit_tags": outfits.get(cid, ""), "state_id": None}
            for cid in scene.get("character_ids") or []
        ],
    }
    if scene.get("negative_prompt"):
        item["negative_prompt"] = scene["negative_prompt"]
    return item


async def _put_edit(story_id: int, data: dict[str, Any], scenes: list[dict[str, Any]]) -> dict[str, Any]:
    body = {
        "title": data["title"],
        "panels_per_page": data["panels_per_page"],
        "custom_pages": data["custom_pages"],
        "scenes": scenes,
    }
    return await _call("PUT", f"/api/story/{story_id}/edit", body)


def _image_content(path: Path, max_size: int) -> ImageContent:
    """画像を縮めて JPEG で返す(元の大きさだと応答が重くなる)。"""
    with Image.open(path) as src:
        image = src.convert("RGB")
    image.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return ImageContent(type="image", data=base64.b64encode(buf.getvalue()).decode(), mime_type="image/jpeg")


def _project_file(relative: str) -> Path:
    """バックエンドが返す outputs/ 配下のパスを実ファイルにする(それ以外は拒否)。"""
    path = (_USER_ROOT / relative).resolve()
    path.relative_to((_USER_ROOT / "outputs").resolve())
    if not path.is_file():
        raise ValueError(f"ファイルが見つかりません: {relative}")
    return path


async def _wait_job(story_id: int, timeout_seconds: float) -> dict[str, Any]:
    """物語のジョブ(コマの生成など)が終わるまで待つ。"""
    elapsed = 0.0
    while True:
        job = await _call("GET", f"/api/story/{story_id}/job")
        if not job or job["status"] != "running":
            return job or {}
        if elapsed >= timeout_seconds:
            return job
        await asyncio.sleep(_JOB_POLL_SECONDS)
        elapsed += _JOB_POLL_SECONDS


async def _speaker_report(story_id: int) -> list[dict[str, Any]]:
    """話し手の判定を、読みやすい形(シーン番号・セリフ・話し手の名前)にする。"""
    scenes = await _call("GET", f"/api/manga-v2/{story_id}/speakers")
    return [
        {
            "scene_index": s["scene_index"],
            "lines": [f"{line['speaker_name'] or '(不明)'}: {line['text']}" for line in s["lines"]],
        }
        for s in scenes
        if s["lines"]
    ]


def register_manga_tools(mcp: MCPServer) -> None:
    """漫画作成のツールを MCP サーバーに登録する。"""

    @mcp.tool()
    async def manga_list_characters() -> list[dict[str, Any]]:
        """
        キャラシートの一覧(ID・名前・容姿/服装タグ・基準シード・参照画像の有無・成人/未成年フラグ)。
        成長段階(同じ人物の年齢ごとのキャラシート)は growth_of(元のキャラの ID)・growth_label・growth_order を持つ。
        元のキャラには、その段階の一覧(stages)を付ける。巻の時期に合う段階のキャラをシーンに入れる。
        """
        characters = await _call("GET", "/api/story/characters")
        stages: dict[int, list[dict[str, Any]]] = {}
        for c in characters:
            if c.get("growth_of") is not None:
                stages.setdefault(c["growth_of"], []).append(c)
        result = []
        for c in characters:
            item = {
                "id": c["id"],
                "name": c["name"],
                "appearance_tags": c["appearance_tags"],
                "outfit_tags": c.get("outfit_tags"),
                "seed": c.get("seed"),
                "has_reference_image": bool(c.get("reference_image_path")),
                "is_adult": c.get("is_adult", False),
                "is_minor": c.get("is_minor", False),
                "age": c.get("age"),
                "notes": c.get("notes"),
            }
            if c.get("growth_of") is not None:
                item.update(growth_of=c["growth_of"], growth_label=c.get("growth_label"), growth_order=c.get("growth_order"))
            if c["id"] in stages:
                ordered = sorted(
                    stages[c["id"]],
                    key=lambda s: (s.get("age") is None, s.get("age") or 0, s.get("growth_order") is None, s.get("growth_order") or 0),
                )
                item["stages"] = [
                    {"id": s["id"], "name": s["name"], "growth_label": s.get("growth_label"), "age": s.get("age")} for s in ordered
                ]
            result.append(item)
        return result

    @mcp.tool()
    async def manga_create_character(
        name: str,
        appearance_tags: str = "",
        outfit_tags: str | None = None,
        negative_tags: str | None = None,
        notes: str | None = None,
        is_adult: bool | None = None,
        is_minor: bool | None = None,
        growth_of: int | None = None,
        growth_label: str | None = None,
        growth_order: int | None = None,
        age: int | None = None,
    ) -> dict[str, Any]:
        """
        キャラシートを新しく作る(同じ名前があれば作らない)。age(歳)を入れると、18 歳未満は未成年・以上は成人になる。
        appearance_tags は変えない容姿(顔・髪・目)、outfit_tags はふだんの服装(シーンの character_outfits で差し替えられる。
        眼鏡のように外す場面がある物はこちらに入れる)。is_minor は未成年(性的な場面には描かない)、is_adult は成人。
        growth_of に元のキャラの ID を渡すと、その人物の成長段階(growth_label: 新生児・1歳 など、growth_order: 幼い順の番号)
        になる。元のキャラが未成年なら段階も未成年になる。
        """
        body: dict[str, Any] = {"name": name, "appearance_tags": appearance_tags}
        for key, value in (
            ("outfit_tags", outfit_tags), ("negative_tags", negative_tags), ("notes", notes), ("is_adult", is_adult),
            ("is_minor", is_minor), ("growth_of", growth_of), ("growth_label", growth_label), ("growth_order", growth_order),
            ("age", age),
        ):
            if value is not None:
                body[key] = value
        return await _call("POST", "/api/story/characters/new", body)

    @mcp.tool()
    async def manga_update_character(character_id: int, sheet: dict[str, Any]) -> dict[str, Any]:
        """
        キャラシートを書き換える(sheet に渡した項目だけ)。項目: appearance_tags・outfit_tags・body_tags・style_tags・
        negative_tags・seed・is_adult・is_minor・growth_of(null で成長段階から外す)・growth_label・growth_order・
        age(歳。入れると年齢区分はそれで決まる)。
        未成年の定義を外すこと(年齢を 18 歳以上に上げることを含む)はここではできない(コンテンツガードのパスワードが
        要るので、画面で行う)。
        """
        return await _call("PUT", f"/api/story/characters/{character_id}/sheet", sheet)

    @mcp.tool()
    async def manga_list_series() -> list[dict[str, Any]]:
        """シリーズの一覧。各巻の物語ID・タイトル・状態・シーン数・あらすじと、シリーズのメモリ(設定)を含む。"""
        return await _call("GET", "/api/series")

    @mcp.tool()
    async def manga_get_story(story_id: int) -> dict[str, Any]:
        """
        物語の中身。シーンごとに本文・作画タグ・登場キャラ・ナレーション・効果音と、コマの絵の有無を返す。
        既存の巻の作風(セリフの長さ・タグの書き方)を真似るときに読む。
        """
        story = await _call("GET", f"/api/story/{story_id}")
        panels = {p["scene_id"] for p in await _call("GET", f"/api/manga-v2/{story_id}/panels")}
        return {
            "id": story["id"],
            "title": story["title"],
            "premise": story["premise"],
            "status": story["status"],
            "panels_per_page": story["panels_per_page"],
            "compose_settings": story.get("manga_v2_compose_settings"),
            "final_image_path": story.get("final_image_path"),
            "scenes": [
                {
                    "scene_id": s["id"],
                    "scene_index": s["scene_index"],
                    "title": s["draft_title"],
                    "text": s["novelai_text"] or s["draft_text"],
                    "prompt_tags": s["draft_prompt_tags"],
                    "characters": [
                        {"id": c["id"], "name": c["name"], "action_tags": c.get("action_tags")} for c in s["characters"]
                    ],
                    "narration": s.get("narration"),
                    "sfx": s.get("sfx"),
                    "has_panel": s["id"] in panels,
                }
                for s in story["scenes"]
            ],
        }

    @mcp.tool()
    async def manga_create_scripted_story(
        title: str,
        scenes: list[dict[str, Any]],
        premise: str | None = None,
        panels_per_page: int = 4,
        series_id: int | None = None,
        volume_no: int | None = None,
    ) -> dict[str, Any]:
        """
        台本から物語を作り、合成で使う話し手の判定結果も返す(誤りがあれば manga_update_scene で本文を直す)。

        scenes の各要素(1シーン=1コマ):
          title: 見出し(任意) / text: セリフ「…」・心の声（…）だけの行と、話し手の手がかりになる地の文
          prompt_tags: コマの作画タグ(英語の danbooru タグ。人数・表情・場所・構図。キャラの容姿はキャラシートから入る)
          character_ids: このコマに描くキャラ(コマでは名前順に左から並ぶ) / narration: ナレーション(80字まで)
          character_actions: {キャラID: そのキャラの表情・動作の英語タグ}。キャラごとのプロンプトに入るので、
              表情や仕草は prompt_tags ではなくここに書く(全体に書くと全員に付いてしまう)。
              prompt_tags には人数・場所・時間帯・構図・二人の位置関係など全体のことだけを書く
          sfx: 描き文字の効果音のリスト
        話し手は「〜が言った/〜は笑った」などの地の文・呼びかけ・会話の交互から判定する。地の文は吹き出しにならない。
        series_id と volume_no を渡すと、そのシリーズの巻にする。
        """
        body = {
            "title": title,
            "premise": premise,
            "panels_per_page": panels_per_page,
            "scenes": scenes,
            "series_id": series_id,
            "volume_no": volume_no,
        }
        story = await _call("POST", "/api/story/scripted", body)
        return {
            "story_id": story["id"],
            "scenes": [{"scene_index": s["scene_index"], "scene_id": s["id"]} for s in story["scenes"]],
            "speakers": await _speaker_report(story["id"]),
        }

    @mcp.tool()
    async def manga_update_scene(
        scene_id: int,
        title: str | None = None,
        text: str | None = None,
        prompt_tags: str | None = None,
        character_ids: list[int] | None = None,
        character_actions: dict[int, str] | None = None,
        narration: str | None = None,
        sfx: list[str] | None = None,
        character_outfits: dict[int, str] | None = None,
        character_states: dict[int, int | None] | None = None,
    ) -> str:
        """
        シーンを手直しする(渡した項目だけ)。narration を空文字にするとナレーションを消す。
        character_actions(キャラID → 表情・動作の英語タグ)を変えるときは character_ids も渡す。
        character_outfits(キャラID → この場面の服装の英語タグ。空ならキャラシートのふだんの服装)と
        character_states(キャラID → 状態の ID。manga_list_scene_presets。null でふだん)も同じ。
        絵に効く項目(prompt_tags・character_ids・character_actions・服装・状態)を変えたら manga_generate_panels で描き直す。
        """
        per_character = (character_actions, character_outfits, character_states)
        if any(v is not None for v in per_character) and character_ids is None:
            raise ValueError("キャラごとの項目を変えるときは character_ids も渡してください")
        fields = {"title": title, "text": text, "prompt_tags": prompt_tags}
        if any(v is not None for v in fields.values()):
            await _call("PATCH", f"/api/story/scenes/{scene_id}", {k: v for k, v in fields.items() if v is not None})
        if character_ids is not None:
            body: dict[str, Any] = {"character_ids": character_ids}
            if character_actions is not None:
                body["actions"] = character_actions
            if character_outfits is not None:
                body["outfits"] = character_outfits
            if character_states is not None:
                body["states"] = character_states
            await _call("PUT", f"/api/story/scenes/{scene_id}/characters", body)
        if narration is not None:
            await _call("PUT", f"/api/manga-v2/scenes/{scene_id}/narration", {"narration": narration})
        if sfx is not None:
            await _call("PUT", f"/api/manga-v2/scenes/{scene_id}/sfx", {"sfx": sfx})
        return f"シーン {scene_id} を更新しました"

    @mcp.tool()
    async def manga_check_speakers(story_id: int) -> list[dict[str, Any]]:
        """シーンごとの吹き出しのセリフと、合成で使う話し手の判定(しっぽの向き先)。(不明) は一番近い顔へ向く。"""
        return await _speaker_report(story_id)

    @mcp.tool()
    async def manga_generate_panels(
        story_id: int,
        scene_indexes: list[int] | None = None,
        template: str | None = None,
        color: bool = False,
        use_character_reference: bool = False,
        reference_strength: float = 1.0,
        preset_id: int | None = None,
        model: str | None = None,
        steps: int | None = None,
        scale: float | None = None,
        cfg_rescale: float | None = None,
        negative_prompt: str | None = None,
        seed_base: int | None = None,
        timeout_seconds: int = 1200,
        manga_style: bool = True,
    ) -> dict[str, Any]:
        """
        コマの絵を生成し(既にあれば描き直し)、終わるまで待つ。NovelAI の Anlas を使う。
        生成サイズはコマが入る枠の形(縦長・横長・正方形)か、manga_edit_scenes で決めたもの。

        scene_indexes: 対象のシーン番号(0始まり)。省略で全シーン。
        template: コマの形を決めるテンプレート(vertical4 / grid4 など)。省略時は前回の合成設定、無ければ grid4。
        preset_id: 画像生成のプリセット(/api/image/presets)の値を使う。model 等を渡すとそれで上書き。
        negative_prompt: 全体のネガティブ(省略時は品質タグの既定値)。
        seed_base: 渡すとシーンごとに seed_base + シーン番号×37 のシードで1コマずつ生成する
                   (省略時はキャラの基準シード→物語ごとの既定の順。基準シードだと全コマ似た構図になりやすい)。
        use_character_reference: キャラシートの参照画像を使う(V4.5 で生成、1人あたり +Anlas)。
        manga_style: False ならプロンプトの先頭に manga style を付けない(画風がイラスト寄りになる)。
        """
        story = await _call("GET", f"/api/story/{story_id}")
        indexes = sorted(scene_indexes) if scene_indexes is not None else [s["scene_index"] for s in story["scenes"]]
        settings: dict[str, Any] = {}
        if preset_id is not None:
            presets = {p["id"]: p for p in await _call("GET", "/api/image/presets")}
            if preset_id not in presets:
                raise ValueError(f"プリセット {preset_id} がありません")
            settings |= {k: v for k, v in presets[preset_id]["settings"].items() if k in _PRESET_KEYS and v is not None}
        overrides = {
            "model": model,
            "steps": steps,
            "scale": scale,
            "cfg_rescale": cfg_rescale,
            "negative_prompt": negative_prompt,
        }
        settings |= {k: v for k, v in overrides.items() if v is not None}
        chosen = template or (story.get("manga_v2_compose_settings") or {}).get("template") or "grid4"
        base = {
            "template": chosen,
            "skip_existing": False,
            "color": color,
            "manga_style": manga_style,
            "use_character_reference": use_character_reference,
            "reference_strength": reference_strength,
        }

        # シードをずらすときと、とびとびのシーンを選んだときは1コマずつ。続いた範囲ならまとめて1回
        contiguous = indexes == list(range(indexes[0], indexes[-1] + 1)) if indexes else True
        batches = [[i] for i in indexes] if seed_base is not None or not contiguous else ([indexes] if indexes else [])
        messages: list[str] = []
        for batch in batches:
            batch_settings = dict(settings)
            if seed_base is not None:
                batch_settings["seed"] = (seed_base + batch[0] * _SEED_STEP) % 4294967296
            await _call(
                "POST",
                f"/api/manga-v2/{story_id}/panels",
                {**base, "scene_from": batch[0], "scene_to": batch[-1], "settings": batch_settings},
            )
            job = await _wait_job(story_id, timeout_seconds)
            if job.get("status") == "error":
                raise RuntimeError(f"シーン{batch[0]}〜{batch[-1]}の生成に失敗しました: {job.get('detail')}")
            if job.get("status") == "running":
                return {"status": "running", "message": f"時間内に終わりませんでした({job.get('message')})"}
            messages.append(job.get("message", ""))
        panels = await _call("GET", f"/api/manga-v2/{story_id}/panels")
        return {
            "status": "done",
            "template": chosen,
            "generated_scene_indexes": indexes,
            "messages": messages,
            "panels": [
                {"scene_index": p["scene_index"], "image_path": p["image_path"], "seed": p["seed"]}
                for p in panels
                if p["scene_index"] in indexes
            ],
        }

    @mcp.tool()
    async def manga_get_panel_image(story_id: int, scene_index: int, max_size: int = 768) -> ImageContent:
        """生成したコマの絵(吹き出しなし)を見る。構図・キャラの描き分け・不適切な描写がないかの確認用。"""
        panels = await _call("GET", f"/api/manga-v2/{story_id}/panels")
        panel = next((p for p in panels if p["scene_index"] == scene_index), None)
        if panel is None:
            raise ValueError(f"シーン{scene_index}のコマの絵はまだありません")
        return _image_content(_project_file(panel["image_path"]), max_size)

    @mcp.tool()
    async def manga_compose(
        story_id: int,
        template: str | None = None,
        max_lines_per_panel: int | None = None,
        font: str | None = None,
        text_scale: float | None = None,
        show_pages: bool = True,
        page_max_size: int = 900,
    ) -> list[TextContent | ImageContent]:
        """
        コマの絵をテンプレートに嵌め、吹き出し・ナレーション・効果音を描いてページにする。
        省略した項目は前回の合成設定を使う。show_pages で全ページの画像も返す(読み味・吹き出しの確認用)。
        """
        story = await _call("GET", f"/api/story/{story_id}")
        body = dict(story.get("manga_v2_compose_settings") or {})
        given = {
            "template": template,
            "max_lines_per_panel": max_lines_per_panel,
            "font": font,
            "text_scale": text_scale,
        }
        body |= {k: v for k, v in given.items() if v is not None}
        result = await _call("POST", f"/api/manga-v2/{story_id}/compose", body, timeout=600)
        content: list[TextContent | ImageContent] = [
            TextContent(
                type="text",
                text=f"{len(result['pages'])}ページを合成しました。全体: {result['final_image_path']}\n"
                + "\n".join(result["pages"]),
            )
        ]
        if show_pages:
            content.extend(_image_content(_project_file(p), page_max_size) for p in result["pages"])
        return content

    @mcp.tool()
    async def manga_set_page_layouts(story_id: int, import_id: int | None) -> dict[str, Any]:
        """
        取り込んだ作品(作品の取り込みページ / /api/manga-import)のページごとのコマ割りを、この物語のコマ割りにする。
        シーンは先頭から順に、写したページのコマに入る(足りない分はテンプレート)。import_id が null なら
        テンプレートに戻す。写したコマ割りでは、セリフの多いシーンを寄りのコマに分けない。
        コマの形が変わるので、写した後は manga_generate_panels で描き直してから manga_compose する。
        """
        return await _call("PUT", f"/api/manga-v2/{story_id}/page-layouts", {"import_id": import_id})

    @mcp.tool()
    async def manga_set_recap(story_id: int, recap: str) -> str:
        """シリーズの巻のあらすじを付ける。次の巻を作るときの前提(これまでの話)として使われる。"""
        await _call("PUT", f"/api/series/volumes/{story_id}/recap", {"recap": recap})
        return f"物語 {story_id} のあらすじを保存しました"

    @mcp.tool()
    async def manga_list_scene_presets() -> dict[str, Any]:
        """
        シーンに使える状態(容姿を一時的に変える: 食べ過ぎでお腹が出る など)とシチュエーション(場所・服装・ポーズの
        ひとまとまり)の一覧。adult_only があるものは成人キャラにだけ使える。状態は manga_update_scene の
        character_states で、シチュエーションは manga_apply_situation でシーンに使う。
        """
        states = await _call("GET", "/api/lora-dataset/states")
        situations = await _call("GET", "/api/lora-dataset/situations")
        return {
            "states": [{k: s[k] for k in ("id", "name", "add_tags", "adult_only")} for s in states],
            "situations": [
                {k: s[k] for k in ("id", "name", "framings", "poses", "outfits", "expressions", "locations", "extra_tags", "adult_only")}
                for s in situations
            ],
        }

    @mcp.tool()
    async def manga_apply_situation(scene_id: int, situation_id: int, choices: dict[str, str] | None = None) -> str:
        """
        シチュエーションのタグをシーンに書き込む(構図・場所はシーンのタグに、服装は登場キャラ全員のこの場面の服装に、
        ポーズ・表情は全員の動作に足す)。choices で軸(framings/poses/outfits/expressions/locations)ごとの値を選ぶ
        (省略した軸は最初の値)。書き込んだあとは manga_update_scene で直せる。
        """
        await _call("POST", f"/api/story/scenes/{scene_id}/situation", {"situation_id": situation_id, "choices": choices or {}})
        return f"シーン {scene_id} にシチュエーションを書き込みました"

    @mcp.tool()
    async def manga_edit_scenes(story_id: int, scenes: dict[int, dict[str, Any]]) -> dict[str, Any]:
        """
        シーンごとの、1行ずつのセリフと話し手・コマの生成サイズを決める(編集画面と同じ保存。ほかの項目は変えない)。

        scenes: {シーン番号(0始まり): {"dialogue": [{"text": "…", "speaker_id": キャラID か null}, …],
                                        "panel_size": "portrait" | "landscape" | "square" | null}}
          dialogue を渡すと、本文の「」の代わりにその並びを吹き出しにする(（…）で囲むと心の声)。null で本文に戻す。
          panel_size は生成サイズ(縦長 832×1216 / 横長 1216×832 / 正方形 1024×1024)。null でコマの枠の形から自動。
        生成サイズが変わったコマの絵は描き直し待ちになる(manga_generate_panels で描き直す)。
        """
        data = await _call("GET", f"/api/story/{story_id}/edit")
        keys = ("id", "title", "text", "prompt_tags", "narration", "sfx", "page_index", "characters", "panel_size")
        body_scenes = []
        for index, scene in enumerate(data["scenes"]):
            item = {k: scene[k] for k in keys}
            # 決めていたセリフはそのまま、本文から取り出していたものは本文のまま
            item["dialogue"] = scene["dialogue"] if scene["dialogue_custom"] else None
            change = scenes.get(index) or scenes.get(str(index))  # type: ignore[call-overload]
            if change:
                for key in ("dialogue", "panel_size"):
                    if key in change:
                        item[key] = change[key]
            body_scenes.append(item)
        body = {
            "title": data["title"],
            "panels_per_page": data["panels_per_page"],
            "custom_pages": data["custom_pages"],
            "scenes": body_scenes,
        }
        return await _call("PUT", f"/api/story/{story_id}/edit", body)

    @mcp.tool()
    async def manga_insert_scenes(story_id: int, after_index: int, scenes: list[dict[str, Any]]) -> dict[str, Any]:
        """
        巻の途中にシーン(コマ)を差し込む。after_index のシーンの後ろに入れる(-1 で先頭)。既存のシーンと絵はそのまま。
        scenes の各要素は manga_create_scripted_story と同じ形(title・text・prompt_tags・character_ids・character_actions・
        character_outfits・narration・sfx・dialogue・negative_prompt)。差し込んだシーンの絵は manga_generate_panels で描く。
        ページのコマ割りを決めてある巻は、コマ数が変わるので manga_save_page_layout で割り直す。
        """
        data = await _call("GET", f"/api/story/{story_id}/edit")
        current = [_edit_item(s) for s in data["scenes"]]
        if not -1 <= after_index < len(current):
            raise ValueError(f"after_index は -1〜{len(current) - 1} で指定してください")
        page = current[after_index]["page_index"] if after_index >= 0 else 0
        new = [_script_to_edit(s, page) for s in scenes]
        body_scenes = current[: after_index + 1] + new + current[after_index + 1 :]
        result = await _put_edit(story_id, data, body_scenes)
        return {**result, "inserted_scene_indexes": list(range(after_index + 1, after_index + 1 + len(new)))}

    @mcp.tool()
    async def manga_reorder_scenes(story_id: int, order: list[int]) -> dict[str, Any]:
        """
        シーンの並びを変える。order は今のシーン番号(0始まり)を新しい順に全部並べたもの。絵はシーンについて動く
        (入る枠の形が変わったコマは描き直し待ちになる)。
        """
        data = await _call("GET", f"/api/story/{story_id}/edit")
        current = [_edit_item(s) for s in data["scenes"]]
        if sorted(order) != list(range(len(current))):
            raise ValueError(f"order には 0〜{len(current) - 1} を1回ずつ並べてください")
        return await _put_edit(story_id, data, [current[i] for i in order])

    @mcp.tool()
    async def manga_remove_scenes(story_id: int, scene_indexes: list[int]) -> dict[str, Any]:
        """シーン(コマ)を消す。scene_indexes は今のシーン番号(0始まり)。コマの絵はこの物語から外れる(画像ファイルは残る)。"""
        data = await _call("GET", f"/api/story/{story_id}/edit")
        current = [_edit_item(s) for s in data["scenes"]]
        drop = set(scene_indexes)
        if not drop <= set(range(len(current))):
            raise ValueError(f"scene_indexes は 0〜{len(current) - 1} で指定してください")
        if len(drop) >= len(current):
            raise ValueError("全部のシーンは消せません")
        return await _put_edit(story_id, data, [s for i, s in enumerate(current) if i not in drop])

    @mcp.tool()
    async def manga_reorder_volumes(series_id: int, story_ids: list[int]) -> list[dict[str, Any]]:
        """
        シリーズの巻番号を付け直す。story_ids(シリーズの巻の物語IDをすべて)の順に 1巻・2巻… にする。
        後から作った巻を途中に入れるときは、最後の巻として作ってからこれで並べ替える。
        """
        series = await _call("PUT", f"/api/series/{series_id}/volume-order", {"story_ids": story_ids})
        return [{"volume_no": v["volume_no"], "story_id": v["story_id"], "title": v["title"]} for v in series["volumes"]]

    @mcp.tool()
    async def manga_export_pdf(story_id: int) -> str:
        """
        合成したページ(最後に合成したときの設定)を PDF にして保存し、そのパスを返す(outputs/manga/exports/)。
        先に manga_compose しておく。
        """
        content = await _call_bytes("POST", f"/api/manga-v2/{story_id}/download", {"format": "pdf", "contents": ["pages"]})
        story = await _call("GET", f"/api/story/{story_id}")
        name = re.sub(r'[\\/:*?"<>|\s]+', "_", (story.get("title") or "").strip())[:60] or "manga"
        folder = _USER_ROOT / "outputs" / "manga" / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"story{story_id}_{name}.pdf"
        path.write_bytes(content)
        return str(path)

    @mcp.tool()
    async def manga_check_panels(
        story_id: int,
        max_redraws: int = 2,
        color: bool = False,
        use_character_reference: bool = False,
        reference_strength: float = 1.0,
        template: str | None = None,
        timeout_seconds: int = 1800,
    ) -> str:
        """
        コマの絵を自動で確かめ、外れたコマを描き直して合成し直す(NovelAI の Anlas を使う)。作画タグのうち判定モデルで
        見分けられるもの(人物の有無・表情・物など)と、描く人数(三つ子・描き忘れ)を確かめ、外れたコマは描かれなかった
        タグを強めてシードを変えて描き直す(よくなったときだけ置き換える)。結果の要約を返す。
        color・use_character_reference・reference_strength は描いたときと同じにする。
        """
        story = await _call("GET", f"/api/story/{story_id}")
        compose = dict(story.get("manga_v2_compose_settings") or {})
        chosen = template or compose.get("template") or "grid4"
        compose["template"] = chosen
        panels = {
            "template": chosen,
            "skip_existing": False,
            "color": color,
            "use_character_reference": use_character_reference,
            "reference_strength": reference_strength,
        }
        await _call(
            "POST", f"/api/manga-v2/{story_id}/check-panels", {"panels": panels, "compose": compose, "max_redraws": max_redraws}
        )
        job = await _wait_job(story_id, timeout_seconds)
        if job.get("status") == "error":
            raise RuntimeError(f"確かめに失敗しました: {job.get('detail')}")
        return str(job.get("message", ""))

    @mcp.tool()
    async def manga_layout_patterns(count: int) -> list[dict[str, Any]]:
        """コマ数 count(1〜12)のページのコマ割りの候補(id・名前・分割の木)。manga_save_page_layout の tree に使う。"""
        patterns = await _call("GET", f"/api/manga-v2/layout-patterns?count={count}")
        return [{k: p[k] for k in ("id", "label", "tree")} for p in patterns]

    @mcp.tool()
    async def manga_get_page_layout(story_id: int, template: str = "grid4") -> dict[str, Any]:
        """
        ページごとのコマ割り(合成と同じ割り付け)。ページごとに、入るシーンのID・コマ数・分割の木(tree)・
        間隔と余白(gaps)・地の色と枠線(style)・重ねたコマ(floats)を返す。決めていなければ template で割り付けた結果。
        """
        view = await _call("GET", f"/api/manga-v2/{story_id}/page-layout?template={template}")
        for page in view["pages"]:
            page.pop("panels", None)
        return view

    @mcp.tool()
    async def manga_save_page_layout(story_id: int, pages: list[dict[str, Any]], template: str = "grid4") -> dict[str, Any]:
        """
        ページごとのコマ割りを保存する。シーンは読む順にコマへ入り、pages の後ろに続くシーンは template で続ける。
        保存すると合成・コマの生成サイズが従い、形が変わったコマは描き直し待ちになる(manga_generate_panels で描き直す)。

        pages の各要素: {"tree": 分割の木, "gaps": {...}, "style": {...}, "floats": [...]} か、写したページを残す {"layout": 番号}
          tree: 葉 "panel"(ふつうのコマ)か {"shape": "rect"|"ellipse"|"triangle"|"polygon", "point": "up"|"down"|"left"|"right"(三角),
                 "points": [[x, y], …](多角形、枠に対する割合), "border": false(枠線なし), "border_color": "#rrggbb",
                 "border_width": 0〜20, "z": 重なり順, "cutout": {"outline": bool, "outline_color": "#rrggbb", "outline_width": 1〜40,
                 "fit": "cover"(枠いっぱい・はみ出しは切る)|"contain"(人物全体を収め足元を下にそろえる), "fade": bool(下端をぼかす)}}、
                 分割は {"dir": "rows"(上から)|"cols"(右から左), "sizes": [割合…], "tilts": [境目の傾き -0.25〜0.25…], "items": [子…]}。
                 manga_layout_patterns の木を土台にするとよい
          gaps: {"gutter_x": 横の間隔, "gutter_y": 縦の間隔, "margin": 余白}(ピクセル。0 でコマが接する・裁ち落とし。既定 16/28/50)
          style: {"background": 地の色, "border_color": 枠線の色, "border_width": 太さ}(既定 #ffffff/#000000/4。回想は黒地に白線など)
          floats: マス目とは別に重ねて置くコマ [{"rect": [x0, y0, x1, y1](ページに対する割合), "after": 読む順でマス目の何コマ目の後ろか,
                  "z": 重なり順(既定 1。前面), 葉と同じ形・枠線・cutout}]。cutout(背景を消す)なら人物だけを見せる
        """
        return await _call("PUT", f"/api/manga-v2/{story_id}/page-layout", {"template": template, "pages": pages})

    @mcp.tool()
    async def manga_reset_page_layout(story_id: int, template: str = "grid4") -> dict[str, Any]:
        """ページごとのコマ割りをやめて、テンプレートの割り付けに戻す。"""
        return await _call("DELETE", f"/api/manga-v2/{story_id}/page-layout?template={template}")

    @mcp.tool()
    async def manga_cutout(scene_id: int, method: str = "local", max_size: int = 512) -> list[TextContent | ImageContent]:
        """
        シーンのコマの絵の背景を消した結果を作って見せる(ページのコマ割りで cutout を付けたコマに使われる)。
        method: "local"(この PC、無料。合成のときにも無ければ作る) / "novelai"(NovelAI の背景の削除。約 65 Anlas、
        Opus でも無料にならない。作ると local より優先される)。
        """
        if method not in ("local", "novelai"):
            raise ValueError("method は local か novelai です")
        path = f"/api/manga-v2/scenes/{scene_id}/cutout" + ("/novelai" if method == "novelai" else "")
        result = await _call("POST", path, timeout=600)
        return [
            TextContent(type="text", text=f"{method} で背景を消しました: {result['preview_path']}"),
            _image_content(_project_file(result["preview_path"]), max_size),
        ]

    @mcp.tool()
    async def manga_upscale_panel(scene_id: int) -> str:
        """
        シーンのコマの絵を AI で2倍に拡大して残す(この PC、無料、1枚40秒ほど)。背景を消したコマ(cutout)を合成するときに
        使われ、大きく貼っても輪郭がくっきりする。
        """
        result = await _call("POST", f"/api/manga-v2/scenes/{scene_id}/upscale", timeout=600)
        return f"拡大しました: {result['upscaled_path']}"

    @mcp.tool()
    async def manga_get_job(story_id: int) -> dict[str, Any] | None:
        """物語で動いているジョブ(コマの生成など)の状態。manga_generate_panels が時間切れのときに確かめる。"""
        return await _call("GET", f"/api/story/{story_id}/job")
