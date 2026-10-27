"""
キャラごとの生成した画像の一覧と、古い画像の整理。

一覧に出す画像(種類 kind):
- candidate: 参照画像の候補(outputs/manga/refs/candidates/char<ID>_<シード>_*.png)
- reference: 参照画像(outputs/manga/refs/char<ID>_*.png。いま使っているもの・前に使っていたもの)
- panel: 漫画のコマ(そのキャラが出るシーンの、いまのコマの絵)
- old_panel: 描き直す前のコマ(そのキャラが出るシーンの、どのシーンにも使われていない絵)
- dataset / face_sheet / body_base: キャラ別データセット・顔と表情のシート・素体(outputs/<ルート>/<キャラのフォルダ>/)
- history: 生成履歴(プロンプトにキャラのトリガーワードがある画像。トリガーワードが無いキャラは出ない)

どれも参照画像に選び直せる(候補だけは基準シードも)。整理(cleanup_targets)できるのは、候補・どのキャラにも
使われていない参照画像・描き直す前のコマ・生成履歴(ブックマークしたものは除く)だけ。整理はごみ箱(trash.py)に
移すので、30日のあいだは元に戻せる。消すのは、一覧で見せて選ばれたパスのうち、いま整理してよいものだけ。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import get_connection, user_files

KINDS: dict[str, str] = {
    "candidate": "参照画像の候補",
    "reference": "参照画像",
    "panel": "漫画のコマ",
    "old_panel": "描き直す前のコマ",
    "dataset": "データセット",
    "face_sheet": "顔・表情シート",
    "body_base": "素体",
    "history": "生成履歴",
}
# 整理できる種類
CLEANABLE: tuple[str, ...] = ("candidate", "reference", "old_panel", "history")

_ROOT = user_files()
_CANDIDATE_RE = re.compile(r"char(\d+)_(\d+)_[0-9a-f]+\.png")
_REFERENCE_RE = re.compile(r"char(\d+)_[0-9a-f]+\.png")
_PANEL_RE = re.compile(r"story(\d+)_scene(\d+)_[0-9a-f]+\.png")
# データセットのフォルダの中の、種類ごとのフォルダ(配信できるもの。library._DATASET_SUBDIRS・_SERVED_ONLY_SUBDIRS)
_DATASET_SUBDIRS: dict[str, str] = {
    "generated": "dataset", "generated_r18": "dataset", "manual": "dataset", "manual_r18": "dataset",
    "face_sheet": "face_sheet", "body_base": "body_base", "body_base_r18": "body_base",
}
# outputs の直下で、データセットのルートではないフォルダ
_NOT_DATASET_ROOTS = {"history", "manga", "ugoira", "publications", "adult_patterns"}


def _outputs() -> Path:
    return _ROOT / "outputs"


def _refs() -> Path:
    return _outputs() / "manga" / "refs"


def _candidates() -> Path:
    return _refs() / "candidates"


def _panels() -> Path:
    return _outputs() / "manga" / "panels"


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


def _file_item(kind: str, path: Path, **extra: Any) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "kind": kind, "path": _rel(path), "bytes": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "seed": None, "in_use": False, "note": "", **extra,
    }


def _used_references(conn: Any) -> set[str]:
    return {Path(r[0]).name for r in conn.execute(
        "SELECT reference_image_path FROM characters WHERE reference_image_path IS NOT NULL"
    )}


def _bookmarked_history(conn: Any) -> set[str]:
    """ブックマークした生成履歴の画像(history:<履歴のID>:<ファイル名>)。整理しない。"""
    return {r[0] for r in conn.execute("SELECT item_key FROM bookmarks WHERE kind = 'image' AND item_key LIKE 'history:%'")}


def _panel_files() -> dict[tuple[int, int], list[Path]]:
    """コマの絵のファイル(物語ID, シーン番号)ごと。"""
    files: dict[tuple[int, int], list[Path]] = {}
    if _panels().is_dir():
        for path in _panels().glob("story*_scene*.png"):
            match = _PANEL_RE.fullmatch(path.name)
            if match:
                files.setdefault((int(match.group(1)), int(match.group(2))), []).append(path)
    return files


def _history_rows(conn: Any, trigger: str | None) -> list[dict[str, Any]]:
    """生成履歴の行。trigger を渡せば、プロンプト(キャラごとのプロンプトも)にその語があるものだけ。"""
    rows = [dict(r) for r in conn.execute("SELECT id, prompt, characters, seed, image_paths, created_at FROM generation_history")]
    if trigger is None:
        return rows
    word = re.compile(rf"(?<![\w]){re.escape(trigger)}(?![\w])", re.IGNORECASE)
    return [r for r in rows if word.search(f"{r['prompt']}\n{r.get('characters') or ''}")]


def _history_items(rows: list[dict[str, Any]], bookmarked: set[str]) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        for path in json.loads(row["image_paths"] or "[]"):
            name = Path(path).name
            full = _ROOT / path
            if not full.is_file():
                continue
            items.append({
                "kind": "history", "path": Path(path).as_posix(), "bytes": full.stat().st_size,
                "created_at": row["created_at"], "seed": row["seed"],
                "in_use": f"history:{row['id']}:{name}" in bookmarked,
                "note": (row["prompt"] or "")[:80], "history_id": row["id"],
            })
    return items


def _dataset_folders(character: dict[str, Any]) -> list[Path]:
    """キャラ別データセットのフォルダ(どのルートでも)。フォルダ名はトリガーワードか名前(lora_dataset._character_root)。"""
    from .routes.lora_dataset import _safe_name

    folder = _safe_name(character.get("trigger_word") or character["name"])
    if not _outputs().is_dir():
        return []
    return [
        root / folder for root in sorted(_outputs().iterdir())
        if root.is_dir() and not root.name.startswith(".") and root.name not in _NOT_DATASET_ROOTS and (root / folder).is_dir()
    ]


def collect(character: dict[str, Any]) -> list[dict[str, Any]]:
    """そのキャラの画像の一覧(新しい順)。in_use はいま使われている(整理しない)もの。"""
    cid = int(character["id"])
    current = character.get("reference_image_path") or ""
    items: list[dict[str, Any]] = []
    conn = get_connection()
    try:
        used = _used_references(conn)
        scenes = [dict(r) for r in conn.execute(
            "SELECT s.id, s.story_id, s.scene_index, st.title, p.image_path, p.seed FROM scene_characters sc"
            " JOIN story_scenes s ON s.id = sc.scene_id JOIN stories st ON st.id = s.story_id"
            " LEFT JOIN manga_panels p ON p.scene_id = s.id WHERE sc.character_id = ?",
            (cid,),
        )]
        all_panels = {Path(r[0]).name for r in conn.execute("SELECT image_path FROM manga_panels")}
        trigger = (character.get("trigger_word") or "").strip()
        history = _history_rows(conn, trigger) if len(trigger) >= 3 else []
        bookmarked = _bookmarked_history(conn)
    finally:
        conn.close()

    if _candidates().is_dir():
        for path in _candidates().glob(f"char{cid}_*.png"):
            match = _CANDIDATE_RE.fullmatch(path.name)
            if match and int(match.group(1)) == cid:
                item = _file_item("candidate", path, seed=int(match.group(2)))
                if item:
                    items.append(item)
    if _refs().is_dir():
        for path in _refs().glob(f"char{cid}_*.png"):
            match = _REFERENCE_RE.fullmatch(path.name)
            if match and int(match.group(1)) == cid:
                note = "いまの参照画像" if Path(current).name == path.name else ""
                item = _file_item("reference", path, in_use=path.name in used, note=note)
                if item:
                    items.append(item)
    files = _panel_files()
    for scene in scenes:
        title = f"{scene['title'] or '物語' + str(scene['story_id'])} のシーン{scene['scene_index'] + 1}"
        if scene["image_path"]:
            item = _file_item("panel", _ROOT / scene["image_path"], seed=scene["seed"], in_use=True, note=title)
            if item:
                items.append(item)
        for path in files.get((scene["story_id"], scene["scene_index"]), []):
            if path.name not in all_panels:
                item = _file_item("old_panel", path, note=title)
                if item:
                    items.append(item)
    for folder in _dataset_folders(character):
        for sub, kind in _DATASET_SUBDIRS.items():
            for path in (folder / sub).glob("*.png") if (folder / sub).is_dir() else []:
                item = _file_item(kind, path, in_use=kind != "dataset", note=f"{folder.parent.name}/{sub}")
                if item:
                    items.append(item)
    items += _history_items(history, bookmarked)
    items.sort(key=lambda i: i["created_at"], reverse=True)
    return items


def find(character: dict[str, Any], path: str) -> dict[str, Any] | None:
    """一覧にある画像(パスで)。一覧の外のパスは扱わない。"""
    wanted = Path(path).as_posix()
    return next((i for i in collect(character) if i["path"] == wanted), None)


def cleanup_targets(
    kinds: list[str], older_than_days: int, character: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    整理してよい画像(古い順)。character を渡せばそのキャラの画像だけ、無ければ全部(生成履歴はキャラに関係なく全部)。
    いま使われている参照画像・いまのコマ・ブックマークした生成履歴は入れない。older_than_days より新しいものも入れない。
    """
    kinds = [k for k in kinds if k in CLEANABLE]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(0, older_than_days))).isoformat()
    if character is not None:
        items = [i for i in collect(character) if i["kind"] in kinds]
    else:
        items = _all_cleanable(kinds)
    return sorted(
        (i for i in items if not i["in_use"] and i["created_at"] < cutoff),
        key=lambda i: i["created_at"],
    )


def _all_cleanable(kinds: list[str]) -> list[dict[str, Any]]:
    """全キャラ(と、キャラに関係しない生成履歴)の整理できる種類の画像。"""
    items: list[dict[str, Any]] = []
    conn = get_connection()
    try:
        used = _used_references(conn)
        all_panels = {Path(r[0]).name for r in conn.execute("SELECT image_path FROM manga_panels")}
        history = _history_rows(conn, None) if "history" in kinds else []
        bookmarked = _bookmarked_history(conn)
    finally:
        conn.close()
    if "candidate" in kinds and _candidates().is_dir():
        for path in _candidates().glob("*.png"):
            match = _CANDIDATE_RE.fullmatch(path.name)
            item = _file_item("candidate", path, seed=int(match.group(2)) if match else None)
            if item:
                items.append(item)
    if "reference" in kinds and _refs().is_dir():
        for path in _refs().glob("*.png"):
            item = _file_item("reference", path, in_use=path.name in used)
            if item:
                items.append(item)
    if "old_panel" in kinds:
        for paths in _panel_files().values():
            for path in paths:
                if path.name not in all_panels:
                    item = _file_item("old_panel", path)
                    if item:
                        items.append(item)
    items += _history_items(history, bookmarked)
    return items


def move_to_trash(targets: list[dict[str, Any]], label: str) -> dict[str, Any]:
    """
    整理する(ごみ箱へ)。生成履歴は1枚ずつ履歴の行と一緒に(trash.history_image)、ほかのファイルはまとめて1件で預ける。
    """
    from . import trash

    files = [t for t in targets if t["kind"] != "history"]
    history = [t for t in targets if t["kind"] == "history"]
    moved = 0
    size = 0
    trash_ids: list[int] = []
    if files:
        trash_ids.append(trash.put("cleanup", f"{label}: {len(files)}件", {"category": "character_images"},
                                   [t["path"] for t in files]))
        for t in files:
            if not (_ROOT / t["path"]).exists():
                moved += 1
                size += t["bytes"]
    for t in history:
        entry = trash.history_image(int(t["history_id"]), Path(t["path"]).name)
        if entry is not None:
            trash_ids.append(entry)
            moved += 1
            size += t["bytes"]
    return {"deleted": moved, "bytes": size, "trash_ids": trash_ids}
