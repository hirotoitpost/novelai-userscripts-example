"""
開発管理者のページの API(/api/admin)のテスト: この PC からだけ使えること、.env の伏せ字と書き換え、
アプリの既定値、掃除、バックアップ。DB とファイルの場所は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_admin.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import app_settings, db  # noqa: E402
from python.routes import admin  # noqa: E402

ENV = """# NovelAI のトークン
NOVELAI_API_TOKEN=pst-abcdefghijklmnopqrstuvwxyz1234
VLLM_MODEL=qwen2.5:7b
# PUSH_MIN_SECONDS=15
"""
EXAMPLE = """# NovelAI API トークン(ログインの代わり)
# NOVELAI_API_TOKEN=pst-...

# 通知を出す最短の秒数
# PUSH_MIN_SECONDS=15
# HF_TOKEN=hf_...
"""


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(admin, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(admin, "_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(admin, "_ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(admin, "_BACKUP_DIR", tmp_path / "data" / "backups")
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    (tmp_path / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    application = FastAPI()
    application.include_router(admin.router)
    return application


def local(app: FastAPI) -> TestClient:
    """この PC から、バックエンドへ直接開いたブラウザ。"""
    return TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))


def test_only_this_pc_can_use_it(app: FastAPI) -> None:
    assert local(app).get("/api/admin/settings").status_code == 200
    # LAN のほかの端末
    lan = TestClient(app, base_url="http://192.168.0.9:8000", client=("192.168.0.20", 50000))
    assert lan.get("/api/admin/settings").status_code == 403
    # フロントの中継を通ったもの(接続元はこの PC に見えるが、中継の印が付く)
    proxied = local(app).get("/api/admin/env", headers={"X-Forwarded-For": "192.168.0.20"})
    assert proxied.status_code == 403
    # この PC のブラウザで開いた、ほかのサイトのページから
    assert local(app).get("/api/admin/env", headers={"Origin": "https://evil.example"}).status_code == 403
    # 名前を 127.0.0.1 に向けたサイト(DNS rebinding)から
    rebind = TestClient(app, base_url="http://evil.example:8000", client=("127.0.0.1", 50000))
    assert rebind.get("/api/admin/env").status_code == 403
    # この PC のフロント(localhost:5173)のページからは使える
    assert local(app).get("/api/admin/env", headers={"Origin": "https://localhost:5173"}).status_code == 200


def test_env_masks_secrets_and_edits_in_place(app: FastAPI, tmp_path: Path) -> None:
    client = local(app)
    entries = {e["key"]: e for e in client.get("/api/admin/env").json()["entries"]}
    # 秘密は末尾だけ。全体は返さない
    assert entries["NOVELAI_API_TOKEN"]["value"] == "…1234" and entries["NOVELAI_API_TOKEN"]["secret"]
    assert entries["VLLM_MODEL"]["value"] == "qwen2.5:7b"
    # .env.example にあって未設定の項目も、説明つきで出す
    assert entries["PUSH_MIN_SECONDS"]["set"] is False and "最短" in entries["PUSH_MIN_SECONDS"]["description"]
    assert entries["HF_TOKEN"]["set"] is False

    res = client.put("/api/admin/env", json={"key": "VLLM_MODEL", "value": "qwen2.5:14b"})
    assert res.status_code == 200 and res.json()["restart_required"]
    client.put("/api/admin/env", json={"key": "PUSH_CONTACT", "value": "mailto:me@example.com # 連絡先"})
    client.put("/api/admin/env", json={"key": "NOVELAI_API_TOKEN", "value": None})
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    # 並びとコメントはそのまま。消した項目はコメントになり、足した項目は末尾に(# を含む値は引用符で囲む)
    assert text.splitlines() == [
        "# NovelAI のトークン",
        "# NOVELAI_API_TOKEN=pst-abcdefghijklmnopqrstuvwxyz1234",
        "VLLM_MODEL=qwen2.5:14b",
        "# PUSH_MIN_SECONDS=15",
        'PUSH_CONTACT="mailto:me@example.com # 連絡先"',
    ]
    assert (tmp_path / ".env.bak").exists()
    entries = {e["key"]: e for e in client.get("/api/admin/env").json()["entries"]}
    assert entries["NOVELAI_API_TOKEN"]["set"] is False
    assert entries["PUSH_CONTACT"]["value"] == "mailto:me@example.com # 連絡先"
    # 変な項目名・改行は受け付けない
    assert client.put("/api/admin/env", json={"key": "bad key", "value": "x"}).status_code == 422
    assert client.put("/api/admin/env", json={"key": "A", "value": "x\nB=1"}).status_code == 422


def test_settings_override_and_reset(app: FastAPI) -> None:
    client = local(app)
    key = "reverse.general_threshold"
    assert app_settings.get(key) == 0.5
    res = client.put(f"/api/admin/settings/{key}", json={"value": 0.35})
    assert res.status_code == 200
    assert app_settings.get(key) == 0.35
    item = next(s for s in res.json()["settings"] if s["key"] == key)
    assert item["overridden"] and item["default"] == 0.5
    # 範囲の外・知らない設定
    assert client.put(f"/api/admin/settings/{key}", json={"value": 5}).status_code == 422
    assert client.put("/api/admin/settings/nope", json={"value": 1}).status_code == 404
    # 既定に戻す
    client.put(f"/api/admin/settings/{key}", json={"value": None})
    assert app_settings.get(key) == 0.5


def test_cleanup_keeps_referenced_files(app: FastAPI, tmp_path: Path) -> None:
    refs = tmp_path / "outputs" / "manga" / "refs"
    (refs / "candidates").mkdir(parents=True)
    for name in ("used.png", "orphan.png", "candidates/c1.png", "candidates/c2.png"):
        (refs / name).write_bytes(b"x" * 10)
    conn = db.get_connection()
    try:
        character = db.save_character(conn, "テスト", "1girl")
        db.update_character_reference(conn, character["id"], "outputs/manga/refs/used.png")
    finally:
        conn.close()
    client = local(app)
    cleanup = {c["category"]: c for c in client.get("/api/admin/storage").json()["cleanup"]}
    assert cleanup["candidates"]["files"] == 2 and cleanup["references"]["files"] == 1
    assert client.post("/api/admin/cleanup/references").json()["deleted"] == 1
    assert client.post("/api/admin/cleanup/candidates").json()["deleted"] == 2
    # 使われている参照画像は残る
    assert (refs / "used.png").exists() and not (refs / "orphan.png").exists()
    assert client.post("/api/admin/cleanup/unknown").status_code == 404


def test_backup(app: FastAPI, tmp_path: Path) -> None:
    client = local(app)
    db.get_connection().close()  # DB を作る
    created = client.post("/api/admin/backups").json()
    name = created["created"]
    assert (tmp_path / "data" / "backups" / name).stat().st_size > 0
    assert [b["name"] for b in client.get("/api/admin/backups").json()["backups"]] == [name]
    # 名前を偽ってほかのファイルは消せない
    assert client.delete("/api/admin/backups/..%2F..%2Fapp.db").status_code == 404
    assert client.delete(f"/api/admin/backups/{name}").json()["backups"] == []
