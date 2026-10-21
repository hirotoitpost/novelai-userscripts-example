"""
物語の編集画面: コマ割り(シーンの並び・追加・削除・ページの区切り)・セリフ(1行ずつ・話し手つき)・タグ・
登場人物をまとめて直し、元の物語に上書き保存するか、新しい物語として別名保存する。

- 読み込み(GET /{id}/edit)は、今のシーンを編集用の形にして返す。セリフは、編集画面で決めたものがなければ
  本文の「」から取り出し、合成と同じ方法で推定した話し手を付ける。
- 保存は、送られた並びをそのままシーンの順番にする。並びに無い既存のシーンは消える。
- コマの絵は、変えていないシーンはそのまま使う。タグ・登場人物を変えたシーンの絵も消さず「描き直し待ち」にする
  (manga_panels.stale)。別名保存では、絵のファイルを写して新しい物語のコマにする。
- 検査(キャラ・状態・成人キャラだけの状態)は書き込む前にすべて行う。弾いたときは何も変わらない。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from ..db import (
    get_character,
    get_connection,
    get_dataset_state,
    get_manga_v2_compose_settings,
    get_manga_v2_overrides,
    get_manga_v2_page_layouts,
    get_manga_v2_ugoira,
    get_story,
    list_manga_pages,
    list_manga_panels,
    set_manga_v2_overrides,
    set_manga_v2_ugoira,
    set_scene_characters,
)
from ..models import StoryEditRequest, StoryEditScene, StorySaveAsRequest
from ..manga_v2.layout import TEMPLATES
from .manga_v2 import _PANEL_DIR, _PROJECT_ROOT, _lettering, _scene_speakers, panel_size_kinds
from .story import _check_adult_only

router = APIRouter(prefix="/api/story", tags=["story-edit"])

# manga_v2_overrides の大きさのキーの接頭辞(routes/manga_v2.py の _SCALE_PREFIX と同じ)
_SCALE_PREFIX = "scale:"


def _scene_text(scene: dict[str, Any]) -> str:
    return scene["novelai_text"] or scene["draft_text"] or ""


def _template(conn: sqlite3.Connection, story_id: int) -> str:
    """自動の生成サイズを決めるテンプレート(最後に合成したときのもの。まだなら既定の grid4)。"""
    template = (get_manga_v2_compose_settings(conn, story_id) or {}).get("template")
    return template if template in TEMPLATES else "grid4"


def _sizes(conn: sqlite3.Connection, story_id: int, custom_pages: bool, scenes: list[tuple[int, str | None]]) -> list[str]:
    """シーン(ページ番号, 決めた生成サイズ)の並びごとの、実際に使う生成サイズ。決めていなければ入る枠の形から。"""
    story = {"custom_pages": custom_pages, "scenes": [{"id": i, "page_index": page} for i, (page, _) in enumerate(scenes)]}
    auto = panel_size_kinds(story, _template(conn, story_id), get_manga_v2_page_layouts(conn, story_id))
    return [size or auto[i] for i, (_, size) in enumerate(scenes)]


def _snapshot_scene(scene: dict[str, Any], panel: dict[str, Any] | None, auto_size: str) -> dict[str, Any]:
    if scene.get("dialogue") is not None:
        dialogue = [
            {"text": str(line.get("text") or ""), "speaker_id": line.get("speaker_id")} for line in scene["dialogue"]
        ]
    else:
        lines = _lettering(scene)[0]
        speakers, _ = _scene_speakers(scene)
        speakers = (speakers + [None] * len(lines))[: len(lines)]
        dialogue = [{"text": line, "speaker_id": speaker} for line, speaker in zip(lines, speakers)]
    return {
        "id": scene["id"],
        "title": scene["draft_title"],
        "text": _scene_text(scene),
        "prompt_tags": scene["draft_prompt_tags"] or "",
        "narration": scene.get("narration"),
        "sfx": scene.get("sfx"),
        "dialogue": dialogue,
        "dialogue_custom": scene.get("dialogue") is not None,
        "page_index": scene["page_index"],
        "panel_size": scene.get("panel_size"),
        "auto_size": auto_size,
        "characters": [
            {
                "id": c["id"],
                "name": c["name"],
                "action_tags": c.get("action_tags") or "",
                "outfit_tags": c.get("scene_outfit_tags") or "",
                "state_id": c.get("state_id"),
            }
            for c in scene.get("characters") or []
        ],
        "panel": {"image_path": panel["image_path"], "stale": bool(panel.get("stale"))} if panel else None,
    }


@router.get("/{story_id}/edit")
async def get_story_edit(story_id: int) -> dict[str, Any]:
    """編集画面に読み込む内容(物語の設定と、シーンごとのタグ・セリフ・登場人物・コマの絵)。"""
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        if story is None:
            raise HTTPException(status_code=404, detail="story not found")
        panels = {p["scene_id"]: p for p in list_manga_panels(conn, story_id)}
        auto = _sizes(conn, story_id, bool(story.get("custom_pages")), [(s["page_index"], None) for s in story["scenes"]])
        page_layouts = bool(get_manga_v2_page_layouts(conn, story_id))
    finally:
        conn.close()
    return {
        "id": story["id"],
        "title": story["title"],
        "panels_per_page": story["panels_per_page"],
        "custom_pages": bool(story.get("custom_pages")),
        # 漫画v2のページごとのコマ割りを決めてあるか(あれば、合成はここでのページの区切りではなくそれに従う)
        "page_layouts": page_layouts,
        "scenes": [
            _snapshot_scene(scene, panels.get(scene["id"]), size) for scene, size in zip(story["scenes"], auto)
        ],
    }


def _page_indexes(req: StoryEditRequest) -> list[int]:
    """シーンごとのページ番号。区切りを決めていれば、前より小さい番号を詰めて 0 から連番にする。"""
    if not req.custom_pages:
        return [i // req.panels_per_page for i in range(len(req.scenes))]
    raw: list[int] = []
    for scene in req.scenes:
        raw.append(max(scene.page_index, raw[-1] if raw else 0))
    numbers = {value: index for index, value in enumerate(dict.fromkeys(raw))}
    return [numbers[value] for value in raw]


def _image_key(prompt_tags: str, characters: list[tuple[int, str, str, int | None]]) -> tuple[Any, ...]:
    """絵に効く内容(タグ・登場人物とその動作・服装・状態)。これが変わったら描き直し待ちにする。"""
    return (prompt_tags.strip(), tuple(sorted((i, a.strip(), o.strip(), s) for i, a, o, s in characters)))


def _edited_key(scene: StoryEditScene) -> tuple[Any, ...]:
    return _image_key(scene.prompt_tags, [(c.id, c.action_tags, c.outfit_tags, c.state_id) for c in scene.characters])


def _stored_key(scene: dict[str, Any]) -> tuple[Any, ...]:
    return _image_key(
        scene["draft_prompt_tags"] or "",
        [
            (c["id"], c.get("action_tags") or "", c.get("scene_outfit_tags") or "", c.get("state_id"))
            for c in scene.get("characters") or []
        ],
    )


def _validate(conn: sqlite3.Connection, req: StoryEditRequest, old: dict[int, dict[str, Any]]) -> None:
    """書き込む前の検査。シーンIDはこの物語のもの、キャラ・状態はあるもの、成人キャラだけの状態は成人に。"""
    ids = [s.id for s in req.scenes if s.id is not None]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="同じシーンが2回入っています。")
    unknown = [i for i in ids if i not in old]
    if unknown:
        raise HTTPException(status_code=422, detail=f"この物語に無いシーンです: {unknown}")
    characters: dict[int, dict[str, Any]] = {}
    for scene in req.scenes:
        cast_ids = [c.id for c in scene.characters]
        if len(cast_ids) != len(set(cast_ids)):
            raise HTTPException(status_code=422, detail="同じキャラが1つのシーンに2回入っています。")
        for member in scene.characters:
            if member.id not in characters:
                character = get_character(conn, member.id)
                if character is None:
                    raise HTTPException(status_code=404, detail=f"キャラが見つかりません(id {member.id})。")
                characters[member.id] = character
            if member.state_id is not None:
                state = get_dataset_state(conn, member.state_id)
                if state is None:
                    raise HTTPException(status_code=404, detail="その状態が見つかりません。")
                _check_adult_only([characters[member.id]], state["add_tags"])


def _dialogue_json(scene: StoryEditScene) -> str | None:
    """セリフを保存する形に。話し手がそのシーンにいなければ話し手なしにする。"""
    if scene.dialogue is None:
        return None
    cast = {c.id for c in scene.characters}
    lines = [
        {"text": line.text.strip(), "speaker_id": line.speaker_id if line.speaker_id in cast else None}
        for line in scene.dialogue
        if line.text.strip()
    ]
    return json.dumps(lines, ensure_ascii=False)


def _write_scene(
    conn: sqlite3.Connection,
    story_id: int,
    scene: StoryEditScene,
    index: int,
    page_index: int,
    stored: dict[str, Any] | None,
) -> int:
    """シーンを1つ書く(stored が無ければ新しく作る)。シーンIDを返す。"""
    sfx = json.dumps([w.strip() for w in scene.sfx if w.strip()], ensure_ascii=False) if scene.sfx is not None else None
    values = (
        scene.title or None, scene.prompt_tags.strip(), scene.narration, sfx, _dialogue_json(scene), scene.panel_size,
        index, page_index,
    )
    if stored is None:
        scene_id = conn.execute(
            """
            INSERT INTO story_scenes
                (story_id, draft_title, draft_prompt_tags, narration, sfx, dialogue, panel_size, scene_index, page_index,
                 draft_text, novelai_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (story_id, *values, scene.text, scene.text),
        ).lastrowid
    else:
        scene_id = stored["id"]
        conn.execute(
            """
            UPDATE story_scenes SET draft_title = ?, draft_prompt_tags = ?, narration = ?, sfx = ?, dialogue = ?,
                panel_size = ?, scene_index = ?, page_index = ?
            WHERE id = ?
            """,
            (*values, scene_id),
        )
        # 本文は変えたときだけ書く(書いていない本文 novelai_text を、下書きで埋めてしまわないように)
        if scene.text != _scene_text(stored):
            conn.execute(
                "UPDATE story_scenes SET draft_text = ?, novelai_text = ? WHERE id = ?", (scene.text, scene.text, scene_id)
            )
    return int(scene_id)


def _resized(
    conn: sqlite3.Connection, story_id: int, story: dict[str, Any], req: StoryEditRequest, pages: list[int]
) -> list[bool]:
    """
    編集した並びのシーンごとに、生成サイズ(決めたもの、無ければ入る枠の形から)が今と変わるか。
    変わるコマの絵は描き直し待ちにする(縦長の絵を横長の枠に入れると大きく切れるため)。
    """
    before_sizes = _sizes(
        conn, story_id, bool(story.get("custom_pages")), [(s["page_index"], s.get("panel_size")) for s in story["scenes"]]
    )
    before = {s["id"]: size for s, size in zip(story["scenes"], before_sizes)}
    after = _sizes(conn, story_id, req.custom_pages, [(page, s.panel_size) for page, s in zip(pages, req.scenes)])
    return [s.id is not None and s.id in before and before[s.id] != size for s, size in zip(req.scenes, after)]


def _write_cast(conn: sqlite3.Connection, scene_id: int, scene: StoryEditScene) -> None:
    set_scene_characters(
        conn,
        scene_id,
        [c.id for c in scene.characters],
        actions={c.id: c.action_tags for c in scene.characters},
        outfits={c.id: c.outfit_tags for c in scene.characters},
        states={c.id: c.state_id for c in scene.characters},
    )


def _remap_overrides(overrides: dict[str, Any], mapping: dict[int, int]) -> dict[str, Any]:
    """吹き出し・描き文字の手動配置と大きさ。キーの先頭のシーンIDを付け替え、無くなったシーンの分は捨てる。"""
    result: dict[str, Any] = {}
    for key, value in overrides.items():
        prefix = _SCALE_PREFIX if key.startswith(_SCALE_PREFIX) else ""
        scene, _, rest = key[len(prefix) :].partition(":")
        if scene.isdigit() and int(scene) in mapping:
            new_scene = str(mapping[int(scene)])
            result[prefix + new_scene + (":" + rest if rest or key.endswith(":") else "")] = value
    return result


def _update_story(conn: sqlite3.Connection, story_id: int, req: StoryEditRequest, n_scenes: int) -> None:
    conn.execute(
        "UPDATE stories SET panels_per_page = ?, custom_pages = ?, n_scenes = ? WHERE id = ?",
        (req.panels_per_page, int(req.custom_pages), n_scenes, story_id),
    )
    if req.title is not None and req.title.strip():
        conn.execute("UPDATE stories SET title = ? WHERE id = ?", (req.title.strip(), story_id))


@router.put("/{story_id}/edit")
async def put_story_edit(story_id: int, req: StoryEditRequest) -> dict[str, Any]:
    """上書き保存。並びに無いシーンは消し、絵に効く内容を変えたシーンのコマは描き直し待ちにする。"""
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        if story is None:
            raise HTTPException(status_code=404, detail="story not found")
        old = {s["id"]: s for s in story["scenes"]}
        _validate(conn, req, old)
        pages = _page_indexes(req)
        sizes = _resized(conn, story_id, story, req, pages)
        keep = {s.id for s in req.scenes if s.id is not None}
        for scene_id in old:
            if scene_id not in keep:
                # 登場人物・コマの絵の行は外部キーで一緒に消える(絵のファイルは残す)
                conn.execute("DELETE FROM story_scenes WHERE id = ?", (scene_id,))
        # 並べ替えで (story_id, scene_index) が一時的にぶつからないよう、いったん負の番号に逃がす
        conn.execute("UPDATE story_scenes SET scene_index = -1 - scene_index WHERE story_id = ?", (story_id,))
        panels = {p["scene_id"] for p in list_manga_panels(conn, story_id)}
        page_scenes: dict[int, list[int]] = {}
        stale = 0
        for index, scene in enumerate(req.scenes):
            stored = old.get(scene.id) if scene.id is not None else None
            scene_id = _write_scene(conn, story_id, scene, index, pages[index], stored)
            _write_cast(conn, scene_id, scene)
            if stored is not None and scene_id in panels and (_stored_key(stored) != _edited_key(scene) or sizes[index]):
                conn.execute("UPDATE manga_panels SET stale = 1 WHERE scene_id = ?", (scene_id,))
                stale += 1
            page_scenes.setdefault(pages[index], []).append(scene_id)
        _update_story(conn, story_id, req, len(req.scenes))
        # 消したシーンの手動配置・うごイラは捨てる
        same = {i: i for i in keep}
        set_manga_v2_overrides(conn, story_id, _remap_overrides(get_manga_v2_overrides(conn, story_id), same))
        set_manga_v2_ugoira(conn, story_id, {k: v for k, v in get_manga_v2_ugoira(conn, story_id).items() if k in keep})
        # 挿絵(ページごとの絵)は、そのページのシーンが変わったら外す(ファイルは残す)
        dropped = 0
        for page in list_manga_pages(conn, story_id):
            if page_scenes.get(page["page_index"]) != page["scene_ids"]:
                conn.execute("DELETE FROM manga_pages WHERE id = ?", (page["id"],))
                dropped += 1
        conn.commit()
    finally:
        conn.close()
    return {"story_id": story_id, "stale_panels": stale, "dropped_pages": dropped}


@router.post("/{story_id}/save-as")
async def save_story_as(story_id: int, req: StorySaveAsRequest) -> dict[str, Any]:
    """
    別名保存: 編集した内容で新しい物語を作る。元の物語は変えない。合成の設定・効果音のフォント/スタンプ・写した
    コマ割り・手動配置・うごイラは引き継ぎ、コマの絵はファイルを写して使う(絵に効く内容を変えたコマは描き直し待ち)。
    シリーズには入れない(別の作品として扱う)。
    """
    conn = get_connection()
    try:
        story = get_story(conn, story_id)
        if story is None:
            raise HTTPException(status_code=404, detail="story not found")
        old = {s["id"]: s for s in story["scenes"]}
        _validate(conn, req, old)
        pages = _page_indexes(req)
        sizes = _resized(conn, story_id, story, req, pages)
        new_id = conn.execute(
            """
            INSERT INTO stories (premise, title, n_scenes, panels_per_page, status, created_at, raw_text, memory,
                manga_v2_sfx_fonts, manga_v2_sfx_stamps, manga_v2_compose_settings, manga_v2_page_layouts, custom_pages)
            SELECT premise, ?, ?, ?, status, ?, raw_text, memory,
                manga_v2_sfx_fonts, manga_v2_sfx_stamps, manga_v2_compose_settings, manga_v2_page_layouts, ?
            FROM stories WHERE id = ?
            """,
            (
                req.title.strip(),
                len(req.scenes),
                req.panels_per_page,
                datetime.now(timezone.utc).isoformat(),
                int(req.custom_pages),
                story_id,
            ),
        ).lastrowid
        panels = {p["scene_id"]: p for p in list_manga_panels(conn, story_id)}
        mapping: dict[int, int] = {}
        copied = stale = 0
        _PANEL_DIR.mkdir(parents=True, exist_ok=True)
        for index, scene in enumerate(req.scenes):
            # 中身は編集した内容で、新しいシーンとして作る(本文は元の書き方のまま写す)
            source = old.get(scene.id) if scene.id is not None else None
            scene_id = _write_scene(conn, new_id, scene, index, pages[index], None)
            if source is not None:
                mapping[source["id"]] = scene_id
                conn.execute("UPDATE story_scenes SET seed_cue = ? WHERE id = ?", (source.get("seed_cue"), scene_id))
                if scene.text == _scene_text(source):
                    conn.execute(
                        "UPDATE story_scenes SET draft_text = ?, novelai_text = ? WHERE id = ?",
                        (source["draft_text"], source["novelai_text"], scene_id),
                    )
            _write_cast(conn, scene_id, scene)
            panel = panels.get(source["id"]) if source is not None else None
            if panel is not None and (_PROJECT_ROOT / panel["image_path"]).is_file():
                filename = f"story{new_id}_scene{index}_{uuid4().hex[:8]}.png"
                shutil.copyfile(_PROJECT_ROOT / panel["image_path"], _PANEL_DIR / filename)
                changed = bool(panel.get("stale")) or _stored_key(source) != _edited_key(scene) or sizes[index]
                conn.execute(
                    """
                    INSERT INTO manga_panels (story_id, scene_id, image_path, seed, width, height, created_at, stale)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id,
                        scene_id,
                        f"outputs/manga/panels/{filename}",
                        panel["seed"],
                        panel["width"],
                        panel["height"],
                        datetime.now(timezone.utc).isoformat(),
                        int(changed),
                    ),
                )
                copied += 1
                stale += int(changed)
        set_manga_v2_overrides(conn, new_id, _remap_overrides(get_manga_v2_overrides(conn, story_id), mapping))
        set_manga_v2_ugoira(
            conn, new_id, {mapping[k]: v for k, v in get_manga_v2_ugoira(conn, story_id).items() if k in mapping}
        )
        conn.commit()
    finally:
        conn.close()
    return {"story_id": new_id, "copied_panels": copied, "stale_panels": stale}
