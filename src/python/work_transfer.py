"""
作品(本棚の物語1つ)の書き出し・取り込み(ZIP)。別の PC に移したり、人に渡したりするためのもの。

ZIP の中身:
- work.json: {"format", "version", "exported_at", "story": 物語の行, "scenes": シーンの行, "panels": コマの行,
  "pages": 挿絵ページの行, "cast": [{"scene": シーンの番号, "character": 登場キャラの番号, "action_tags"}],
  "characters": キャラシート(character_transfer と同じ形), "composed": 合成したページの ZIP の中の名前}
- panels/・pages/・composed/: 画像。refs/: 登場キャラの参照画像

取り込むと新しい物語になる(元の id・シリーズの巻には付けない)。シーンの id が変わるので、吹き出しの位置などの
シーンごとの設定(manga_v2_overrides)は新しい id に付け替える。登場キャラは、同じ名前のキャラがあればそれを使うか、
別名で新しく入れるかを選ぶ。

未成年キャラの安全対策(minor_guard): 性的な場面に成人でないキャラがいるコマ・ページの画像は、書き出しにも取り込みにも
入れない(本文とタグは入る)。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import minor_guard
from .character_transfer import SHEET_KEYS, TransferError
from .db import user_files

FORMAT = "novelai-userscripts-example/work"
VERSION = 1
MAX_ZIP_BYTES = 1024 * 1024 * 1024
# 利用者のデータのファイルの基準(single はプロジェクトのフォルダ、multi はその人のフォルダ。db.UserPath)
_PROJECT_ROOT = user_files()
# 書き出さない物語の列(この PC だけの意味のもの)
# 動かすコマ(manga_v2_ugoira)は、この PC のうごイラを指すので書き出さない
_STORY_SKIP = {"id", "series_id", "volume_no", "final_image_path", "manga_v2_sfx_stamps", "manga_v2_ugoira"}
_SCALE_PREFIX = "scale:"


def _members(scene_id: int, cast: list[dict[str, Any]], characters: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**characters[c["character_id"]], "action_tags": c.get("action_tags")} for c in cast if c["scene_id"] == scene_id and c["character_id"] in characters]


def blocked_scenes(story: dict[str, Any], scenes: list[dict[str, Any]], members: dict[int, list[dict[str, Any]]]) -> set[int]:
    """未成年キャラの安全対策で止めるシーン(画像を入れない)。"""
    text = minor_guard.story_text({**story, "scenes": scenes})
    return {
        s["id"] for s in scenes
        if minor_guard.decide(s.get("draft_prompt_tags") or "", members.get(s["id"], []), text).action == minor_guard.BLOCK
    }


def export_zip(conn: Any, story_id: int) -> tuple[bytes, str, int]:
    """(ZIP, 題名, 安全対策で入れなかったコマ・ページの数)。"""
    from .trash import rows

    story = rows(conn, "SELECT * FROM stories WHERE id = ?", (story_id,))
    if not story:
        raise TransferError("物語が見つかりません。")
    story = story[0]
    scenes = rows(conn, "SELECT * FROM story_scenes WHERE story_id = ? ORDER BY scene_index", (story_id,))
    panels = rows(conn, "SELECT * FROM manga_panels WHERE story_id = ?", (story_id,))
    pages = rows(conn, "SELECT * FROM manga_pages WHERE story_id = ?", (story_id,))
    scene_ids = [s["id"] for s in scenes]
    placeholders = ",".join("?" for _ in scene_ids) or "NULL"
    cast = rows(conn, f"SELECT * FROM scene_characters WHERE scene_id IN ({placeholders})", tuple(scene_ids))
    character_ids = sorted({c["character_id"] for c in cast})
    characters = {
        r["id"]: r for r in rows(conn, f"SELECT * FROM characters WHERE id IN ({','.join('?' for _ in character_ids) or 'NULL'})", tuple(character_ids))
    }
    members = {sid: _members(sid, cast, characters) for sid in scene_ids}
    blocked = blocked_scenes(story, scenes, members)
    scene_index = {s["id"]: i for i, s in enumerate(scenes)}
    character_index = {cid: i for i, cid in enumerate(character_ids)}
    skipped = 0

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        def add(rel: str | None, folder: str) -> str | None:
            if not rel or not (_PROJECT_ROOT / rel).is_file():
                return None
            name = f"{folder}/{len(zf.namelist())}_{Path(rel).name}"
            zf.write(_PROJECT_ROOT / rel, name)
            return name

        panel_entries = []
        for panel in panels:
            if panel["scene_id"] in blocked:
                skipped += 1
                continue
            panel_entries.append({
                "scene": scene_index[panel["scene_id"]], "file": add(panel["image_path"], "panels"),
                **{k: panel[k] for k in ("seed", "width", "height", "created_at")},
            })
        page_entries = []
        for page in pages:
            page_scene_ids = json.loads(page.get("scene_ids") or "[]")
            if any(sid in blocked for sid in page_scene_ids):
                skipped += 1
                continue
            page_entries.append({
                "page_index": page["page_index"], "scenes": [scene_index[sid] for sid in page_scene_ids if sid in scene_index],
                "file": add(page["image_path"], "pages"), "seed": page.get("seed"), "created_at": page["created_at"],
            })
        # 合成したページ(いまの版だけ。止めるシーンがあれば入れない。合成し直せば作り直せる)
        composed = []
        final = re.fullmatch(r"outputs/manga/v2/story\d+_final_([0-9a-f]+)\.png", story.get("final_image_path") or "")
        if not blocked and final:
            token = final.group(1)
            for path in sorted((_PROJECT_ROOT / "outputs" / "manga" / "v2").glob(f"story{story_id}_*_{token}.png")):
                composed.append(add(path.relative_to(_PROJECT_ROOT).as_posix(), "composed"))
        character_entries = []
        for cid in character_ids:
            c = characters[cid]
            entry = {"name": c["name"], "notes": c.get("notes"), **{k: c.get(k) for k in SHEET_KEYS}}
            entry["is_adult"], entry["is_minor"] = bool(c.get("is_adult")), bool(c.get("is_minor"))
            entry["reference"] = add(c.get("reference_image_path"), "refs")
            character_entries.append(entry)
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "story": {k: v for k, v in story.items() if k not in _STORY_SKIP},
            "scenes": [{k: v for k, v in s.items() if k not in ("id", "story_id")} | {"old_id": s["id"]} for s in scenes],
            "panels": panel_entries,
            "pages": page_entries,
            "cast": [
                {"scene": scene_index[c["scene_id"]], "character": character_index[c["character_id"]], "action_tags": c.get("action_tags")}
                for c in cast if c["character_id"] in character_index
            ],
            "characters": character_entries,
            "composed": [name for name in composed if name],
        }
        zf.writestr("work.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    title = str(story.get("title") or story.get("premise") or f"物語{story_id}").splitlines()[0][:40]
    return buf.getvalue(), title, skipped


def read_zip(data: bytes) -> tuple[dict[str, Any], zipfile.ZipFile]:
    if len(data) > MAX_ZIP_BYTES:
        raise TransferError("ファイルが大きすぎます(1GB まで)。")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(zf.read("work.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferError("作品を書き出した ZIP ではありません(work.json が読めません)。") from exc
    if manifest.get("format") != FORMAT:
        raise TransferError("このアプリで作品を書き出した ZIP ではありません。")
    for key in ("story", "scenes", "panels", "pages", "cast", "characters"):
        if key not in manifest:
            raise TransferError(f"work.json に {key} がありません。")
    return manifest, zf


def _insert(conn: Any, table: str, row: dict[str, Any]) -> int:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    values = {k: v for k, v in row.items() if k in existing and k != "id"}
    cursor = conn.execute(
        f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", tuple(values.values())
    )
    return int(cursor.lastrowid)


def _extract(zf: zipfile.ZipFile, name: str | None, folder: str, prefix: str) -> str | None:
    if not name or name not in zf.namelist():
        return None
    rel = f"{folder}/{prefix}_{uuid4().hex[:8]}{Path(name).suffix or '.png'}"
    target = _PROJECT_ROOT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(zf.read(name))
    return rel


def _remap_overrides(raw: str | None, mapping: dict[int, int]) -> str | None:
    """吹き出しの位置などのシーンごとの設定(キーの頭がシーンの id)を、新しい id に付け替える。"""
    if not raw:
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    result = {}
    for key, value in data.items():
        prefix = _SCALE_PREFIX if key.startswith(_SCALE_PREFIX) else ""
        head, _, rest = key[len(prefix):].partition(":")
        if head.isdigit() and int(head) in mapping:
            result[f"{prefix}{mapping[int(head)]}:{rest}" if rest else f"{prefix}{mapping[int(head)]}"] = value
    return json.dumps(result, ensure_ascii=False)


def import_work(conn: Any, manifest: dict[str, Any], zf: zipfile.ZipFile, character_ids: list[int]) -> dict[str, Any]:
    """
    新しい物語として入れる。character_ids は manifest の characters と同じ順の、使うキャラの id(呼ぶ側が、同じ名前の
    キャラを使うか新しく入れるかを決めて作っておく)。戻り値は {story_id, title, skipped}。
    """
    now = datetime.now(timezone.utc).isoformat()
    story_row = dict(manifest["story"])
    story_row["created_at"] = now
    story_id = _insert(conn, "stories", story_row)
    mapping: dict[int, int] = {}
    new_scenes = []
    for scene in manifest["scenes"]:
        old_id = scene.get("old_id")
        new_id = _insert(conn, "story_scenes", {**{k: v for k, v in scene.items() if k != "old_id"}, "story_id": story_id})
        if isinstance(old_id, int):
            mapping[old_id] = new_id
        new_scenes.append({**scene, "id": new_id})
    for entry in manifest["cast"]:
        if 0 <= entry["scene"] < len(new_scenes) and 0 <= entry["character"] < len(character_ids):
            conn.execute(
                "INSERT OR IGNORE INTO scene_characters (scene_id, character_id, action_tags) VALUES (?, ?, ?)",
                (new_scenes[entry["scene"]]["id"], character_ids[entry["character"]], entry.get("action_tags")),
            )
    # 安全対策: 取り込んだキャラ(この PC のキャラシート)で判定し直し、止めるシーンの画像は入れない
    characters = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM characters WHERE id IN ({','.join('?' for _ in character_ids) or 'NULL'})", tuple(character_ids))}
    cast = [dict(r) for r in conn.execute(
        f"SELECT * FROM scene_characters WHERE scene_id IN ({','.join('?' for _ in new_scenes) or 'NULL'})",
        tuple(s["id"] for s in new_scenes))]
    members = {s["id"]: _members(s["id"], cast, characters) for s in new_scenes}
    blocked = blocked_scenes({**story_row, "id": story_id}, new_scenes, members)
    skipped = 0
    for panel in manifest["panels"]:
        scene = new_scenes[panel["scene"]] if 0 <= panel.get("scene", -1) < len(new_scenes) else None
        if scene is None:
            continue
        if scene["id"] in blocked:
            skipped += 1
            continue
        path = _extract(zf, panel.get("file"), "outputs/manga/panels", f"story{story_id}_scene{scene['scene_index']}")
        if path:
            conn.execute(
                "INSERT INTO manga_panels (story_id, scene_id, image_path, seed, width, height, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (story_id, scene["id"], path, panel.get("seed"), panel.get("width") or 0, panel.get("height") or 0, panel.get("created_at") or now),
            )
    for page in manifest["pages"]:
        scene_ids = [new_scenes[i]["id"] for i in page.get("scenes", []) if 0 <= i < len(new_scenes)]
        if any(sid in blocked for sid in scene_ids):
            skipped += 1
            continue
        path = _extract(zf, page.get("file"), "outputs/manga", f"story{story_id}_page{page['page_index']}")
        if path:
            conn.execute(
                "INSERT INTO manga_pages (story_id, page_index, image_path, scene_ids, created_at, seed) VALUES (?, ?, ?, ?, ?, ?)",
                (story_id, page["page_index"], path, json.dumps(scene_ids), page.get("created_at") or now, page.get("seed")),
            )
    if not blocked:
        # 合成したページは、この物語の版として置き直す(本棚は final_image_path の版のページを見る)
        token = uuid4().hex[:8]
        folder = _PROJECT_ROOT / "outputs" / "manga" / "v2"
        for name in manifest.get("composed") or []:
            match = re.search(r"story\d+_(page\d+|final)_[0-9a-f]+\.png$", name or "")
            if not match or name not in zf.namelist():
                continue
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"story{story_id}_{match.group(1)}_{token}.png").write_bytes(zf.read(name))
            if match.group(1) == "final":
                conn.execute(
                    "UPDATE stories SET final_image_path = ? WHERE id = ?", (f"outputs/manga/v2/story{story_id}_final_{token}.png", story_id)
                )
    conn.execute(
        "UPDATE stories SET manga_v2_overrides = ? WHERE id = ?", (_remap_overrides(story_row.get("manga_v2_overrides"), mapping), story_id)
    )
    conn.commit()
    if blocked:
        minor_guard.record(minor_guard.BLOCK, "作品の取り込み", [f"性的な場面に成人でないキャラがいるため、{skipped}件の画像を入れませんでした"], story_id)
    title = str(story_row.get("title") or story_row.get("premise") or "").splitlines()[0][:40] if (story_row.get("title") or story_row.get("premise")) else ""
    return {"story_id": story_id, "title": title, "skipped": skipped}
