"""
MCP の generate_image_tool のキャラ参照: キャラシートの参照画像を読み込み、履歴に残す形にする。

実行方法:
  uv run pytest tests/test_mcp_reference.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.mcp_reference import character_reference  # noqa: E402


@pytest.fixture()
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """利用者のフォルダ(single ではプロジェクトのフォルダ)を一時フォルダにする。"""
    monkeypatch.setattr(db, "_PROJECT_ROOT", tmp_path)
    return tmp_path


def _character(files: Path, *, with_reference: bool) -> int:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "ひより", "1girl, pink hair")
        if with_reference:
            ref = files / "outputs" / "manga" / "refs" / "char_ref.png"
            ref.parent.mkdir(parents=True)
            ref.write_bytes(b"png-bytes")
            db.update_character_reference(conn, character["id"], "outputs/manga/refs/char_ref.png")
        return character["id"]
    finally:
        conn.close()


def test_no_character_means_no_reference(files: Path) -> None:
    assert character_reference(None, "character", 1.0, 0.7) is None


def test_reads_sheet_reference_and_records_it(files: Path) -> None:
    character_id = _character(files, with_reference=True)
    reference = character_reference(character_id, "character", 0.6, 0.5)
    assert reference is not None
    assert reference.image == b"png-bytes"
    assert (reference.type, reference.fidelity, reference.strength) == ("character", 0.6, 0.5)

    history = files / "outputs" / "history"
    record = reference.record(history)
    assert record["image_path"].startswith("outputs/history/")
    assert (files / record["image_path"]).read_bytes() == b"png-bytes"
    assert (record["type"], record["fidelity"], record["strength"]) == ("character", 0.6, 0.5)


def test_character_without_reference_is_an_error(files: Path) -> None:
    character_id = _character(files, with_reference=False)
    with pytest.raises(ValueError, match="参照画像がありません"):
        character_reference(character_id, "character", 1.0, 0.7)


def test_unknown_character_and_bad_values(files: Path) -> None:
    with pytest.raises(ValueError, match="見つかりません"):
        character_reference(9999, "character", 1.0, 0.7)
    character_id = _character(files, with_reference=True)
    with pytest.raises(ValueError, match="reference_type"):
        character_reference(character_id, "style", 1.0, 0.7)
    with pytest.raises(ValueError, match="0〜1"):
        character_reference(character_id, "character", 1.0, 1.5)
