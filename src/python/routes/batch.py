from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import AsyncGenerator

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field

from novelai._utils.nai_meta import extract_image_metadata

router = APIRouter(prefix="/api/batch", tags=["batch"])

# 類似度グループ化から除外する品質系タグ
_QUALITY_TAGS: frozenset[str] = frozenset({
    "masterpiece", "best quality", "very aesthetic", "amazing quality",
    "absurdres", "highres", "ultra-detailed", "ultra detailed",
    "extremely detailed", "perfect face", "official art", "wallpaper",
    "8k", "4k", "2k", "illustration", "digital art", "anime", "manga",
    "newest", "sensitive", "rating: general", "year 2024", "year 2025",
})


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _extract_prompt(meta: dict) -> str:
    comment = meta.get("Comment", {})
    if isinstance(comment, str):
        try:
            comment = json.loads(comment)
        except Exception:
            return comment.lower().strip()
    if isinstance(comment, dict):
        return str(comment.get("prompt", "")).lower().strip()
    return ""


def _meaningful_tags(prompt: str, max_tags: int = 4) -> list[str]:
    result: list[str] = []
    for tag in (t.strip() for t in prompt.split(",")):
        if tag and tag not in _QUALITY_TAGS and len(tag) > 1:
            result.append(tag)
        if len(result) >= max_tags:
            break
    return result


def _safe_name(name: str, max_len: int = 60) -> str:
    name = re.sub(r"[^\w\s\-]", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", "_", name).strip("_")[:max_len]
    return name or "group"


def _group_folder_name(prompt: str) -> str:
    tags = _meaningful_tags(prompt, 4)
    return _safe_name("_".join(tags)) if tags else "no_prompt"


def _file_date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def _try_extract(path: Path) -> dict | None:
    try:
        img = Image.open(path).convert("RGBA")
        return extract_image_metadata(np.asarray(img, dtype=np.uint8))
    except Exception:
        return None


def _collect_pngs(input_path: str) -> list[Path]:
    p = Path(input_path)
    if p.is_dir():
        return sorted(p.rglob("*.png"))
    if p.is_file() and p.suffix.lower() == ".png":
        return [p]
    return []


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 9999):
        candidate = dest.parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return dest


# ---------------------------------------------------------------------------
# グループ化
# ---------------------------------------------------------------------------

def _fuzzy_group(
    items: list[tuple[Path, dict]],
    threshold: float,
) -> list[tuple[str, str, list[tuple[Path, dict]]]]:
    groups: list[tuple[str, str, list[tuple[Path, dict]]]] = []

    for path, meta in items:
        prompt = _extract_prompt(meta)
        best_ratio, best_idx = 0.0, -1
        for i, (_, repr_prompt, _) in enumerate(groups):
            ratio = SequenceMatcher(None, prompt, repr_prompt).ratio()
            if ratio > best_ratio:
                best_ratio, best_idx = ratio, i

        if best_ratio >= threshold and best_idx >= 0:
            groups[best_idx][2].append((path, meta))
        else:
            gname = _group_folder_name(prompt)
            groups.append((gname, prompt, [(path, meta)]))

    return groups


# ---------------------------------------------------------------------------
# リクエスト / レスポンス スキーマ
# ---------------------------------------------------------------------------

class PreviewRequest(BaseModel):
    input_path: str
    similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class FileInfo(BaseModel):
    path: str
    prompt: str
    date: str
    has_metadata: bool


class GroupInfo(BaseModel):
    group_name: str
    representative_prompt: str
    files: list[FileInfo]
    file_count: int


class PreviewResponse(BaseModel):
    groups: list[GroupInfo]
    no_metadata_files: list[FileInfo]
    total_files: int


class OrganizeRequest(BaseModel):
    input_path: str
    output_path: str
    operation: str = Field(default="copy", pattern="^(copy|move)$")
    similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    save_metadata_json: bool = True


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------

@router.post("/preview", response_model=PreviewResponse)
async def batch_preview(req: PreviewRequest) -> PreviewResponse:
    """フォルダをスキャンしてグループ分けのプレビューを返す（ファイル変更なし）。"""
    png_files = _collect_pngs(req.input_path)
    if not png_files and not Path(req.input_path).exists():
        raise HTTPException(status_code=404, detail=f"パスが見つかりません: {req.input_path}")

    with_meta: list[tuple[Path, dict]] = []
    no_meta: list[FileInfo] = []

    for p in png_files:
        meta = _try_extract(p)
        if meta:
            with_meta.append((p, meta))
        else:
            no_meta.append(FileInfo(path=str(p), prompt="", date=_file_date(p), has_metadata=False))

    raw_groups = _fuzzy_group(with_meta, req.similarity_threshold)

    groups = [
        GroupInfo(
            group_name=gname,
            representative_prompt=repr_prompt,
            file_count=len(files),
            files=[
                FileInfo(path=str(p), prompt=_extract_prompt(m), date=_file_date(p), has_metadata=True)
                for p, m in files
            ],
        )
        for gname, repr_prompt, files in raw_groups
    ]

    return PreviewResponse(
        groups=groups,
        no_metadata_files=no_meta,
        total_files=len(png_files),
    )


@router.post("/organize")
async def batch_organize(req: OrganizeRequest) -> StreamingResponse:
    """バッチ整理を実行し、SSE で進捗をストリーミングする。"""

    async def event_gen() -> AsyncGenerator[str, None]:
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        input_dir = Path(req.input_path)
        output_dir = Path(req.output_path)

        png_files = _collect_pngs(req.input_path)
        if not png_files:
            yield _sse("error", {"detail": f"PNG ファイルが見つかりません: {req.input_path}"})
            return

        total = len(png_files)

        # --- フェーズ1: スキャン & メタデータ抽出 ---
        with_meta: list[tuple[Path, dict]] = []
        no_meta_files: list[Path] = []

        for i, p in enumerate(png_files):
            meta = _try_extract(p)
            if meta:
                with_meta.append((p, meta))
            else:
                no_meta_files.append(p)
            yield _sse("scan", {"current": i + 1, "total": total, "file": p.name})

        # --- フェーズ2: グループ化 ---
        raw_groups = _fuzzy_group(with_meta, req.similarity_threshold)

        # --- フェーズ3: ファイル操作 ---
        organized = skipped_no_meta = errors = current = 0

        def _do_op(src: Path, dest: Path) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if req.operation == "move":
                shutil.move(str(src), dest)
            else:
                shutil.copy2(str(src), dest)

        for gname, _, files in raw_groups:
            for src_path, meta in files:
                current += 1
                dest = _unique_dest(output_dir / gname / _file_date(src_path) / src_path.name)
                try:
                    _do_op(src_path, dest)
                    if req.save_metadata_json:
                        dest.with_suffix(".json").write_text(
                            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    organized += 1
                    yield _sse("progress", {
                        "current": current, "total": total,
                        "file": src_path.name,
                        "dest": str(dest.relative_to(output_dir)),
                        "status": "ok",
                    })
                except Exception as e:
                    errors += 1
                    yield _sse("progress", {
                        "current": current, "total": total,
                        "file": src_path.name, "dest": "",
                        "status": "error", "message": str(e),
                    })

        for src_path in no_meta_files:
            current += 1
            dest = _unique_dest(output_dir / "_no_metadata" / _file_date(src_path) / src_path.name)
            try:
                _do_op(src_path, dest)
                skipped_no_meta += 1
                yield _sse("progress", {
                    "current": current, "total": total,
                    "file": src_path.name,
                    "dest": str(dest.relative_to(output_dir)),
                    "status": "no_metadata",
                })
            except Exception as e:
                errors += 1
                yield _sse("progress", {
                    "current": current, "total": total,
                    "file": src_path.name, "dest": "",
                    "status": "error", "message": str(e),
                })

        yield _sse("complete", {
            "organized": organized,
            "skipped_no_meta": skipped_no_meta,
            "errors": errors,
        })

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
