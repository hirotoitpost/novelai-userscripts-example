"""
ヘルプのチャット(help_chat.py と /api/help/chat)のテスト。文章モデルは呼ばない(差し替える)。

実行方法:
  uv run pytest tests/test_help_chat.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import app_settings, db, help_chat  # noqa: E402
from python.routes import help as help_route  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    app_settings.reset_cache()
    db.get_connection().close()
    yield
    app_settings.reset_cache()


def test_articles_are_loaded_with_titles() -> None:
    articles = {a.slug: a for a in help_chat.load_articles()}
    assert "backup-add-disk" in articles and articles["backup-add-disk"].title == "写し先(ディスク)を足す"
    assert not articles["backup"].body.startswith("---")


@pytest.mark.parametrize(("question", "expected"), [
    ("バックアップのディスクを足すには?", "backup-add-disk"),
    ("写し先を外したい", "backup-remove-disk"),
    ("ベルのお知らせを消したい", "notices"),
    ("ドライブの空きが少ない、片付け方は", "storage"),
    ("前の状態に戻すには", "backup-restore"),
])
def test_pick_finds_the_right_article(question: str, expected: str) -> None:
    picked = help_chat.pick(question, help_chat.load_articles())
    assert expected in [a.slug for a in picked], [a.slug for a in picked]


def test_pick_puts_the_open_article_first() -> None:
    picked = help_chat.pick("どうすればいい?", help_chat.load_articles(), current="storage")
    assert picked[0].slug == "storage"


def test_messages_contain_only_picked_articles_and_recent_history() -> None:
    articles = help_chat.load_articles()
    picked = [a for a in articles if a.slug == "backup-add-disk"]
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(10)]
    messages = help_chat.build_messages("足すには?", history, picked)
    assert messages[0]["role"] == "system" and "写し先を足す" in messages[0]["content"]
    assert "backup-remove-disk" not in messages[0]["content"]
    assert [m["content"] for m in messages[1:-1]] == [f"turn {i}" for i in range(4, 10)]
    assert messages[-1] == {"role": "user", "content": "足すには?"}


@pytest.mark.parametrize(("question", "article", "section"), [
    ("バックアップのディスクを増やしたいです。どうすればいいですか?", "backup-add-disk", "手順"),
    ("ベルを消したい", "notices", "隠す"),
    ("写し先を外す手順は?", "backup-remove-disk", "手順"),
    ("戻すとファイルは消えますか", "backup-restore", "戻すときの動き"),
])
def test_excerpt_quotes_the_right_section(question: str, article: str, section: str) -> None:
    answer = help_chat.excerpt(question, help_chat.pick(question, help_chat.load_articles()))
    title = next(a.title for a in help_chat.load_articles() if a.slug == article)
    assert answer.startswith(f"「{title}」の「{section}」より"), answer[:80]
    assert f"(/help/{article})" in answer


def test_excerpt_without_articles() -> None:
    assert "ヘルプには書いてありません" in help_chat.excerpt("天気は?", [])


def test_flag_unknown_names() -> None:
    articles = [a for a in help_chat.load_articles() if a.slug == "backup-add-disk"]
    ok = help_chat.flag_unknown_names("**写し先を足す** を押します。", articles)
    assert "※" not in ok
    flagged = help_chat.flag_unknown_names("**write-to-disks** を押し、「足す迪斯克」に入れます。ديسك", articles)
    assert "write-to-disks、足す迪斯克" in flagged and "ほかの言語の文字" in flagged


def test_choose_engine() -> None:
    assert help_chat.choose_engine("default", "excerpt", True) == "excerpt"
    assert help_chat.choose_engine("default", "ollama", True) == "ollama"
    assert help_chat.choose_engine("novelai", "ollama", False) == "novelai"
    assert help_chat.choose_engine("default", "auto", True) == "novelai"
    assert help_chat.choose_engine("default", "auto", False) == "ollama"
    assert help_chat.choose_engine("default", "broken", True) == "excerpt"


def test_choice_setting(fresh_db: None) -> None:
    assert app_settings.get("help.chat_model") == "excerpt"
    assert app_settings.set_value("help.chat_model", "novelai") == "novelai"
    with pytest.raises(ValueError):
        app_settings.set_value("help.chat_model", "gpt")
    row = next(s for s in app_settings.describe() if s["key"] == "help.chat_model")
    assert row["kind"] == "choice" and {c["value"] for c in row["choices"]} == {"excerpt", "ollama", "novelai", "auto"}


def test_novelai_without_token_is_not_401() -> None:
    # 401 だと画面がログアウトしてしまう
    with pytest.raises(HTTPException) as caught:
        asyncio.run(help_route._answer("novelai", [], None))
    assert caught.value.status_code == 422


def test_api(fresh_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[dict[str, str]], str | None]] = []

    async def fake_answer(engine: str, messages: list[dict[str, str]], api_key: str | None) -> str:
        calls.append((engine, messages, api_key))
        return "<think>考え</think>1. 「写し先を足す」を押します。"

    async def fake_key(authorization: str | None) -> str | None:
        return "token" if authorization else None

    monkeypatch.setattr(help_route, "_answer", fake_answer)
    monkeypatch.setattr(help_route, "_novelai_key", fake_key)
    app = FastAPI()
    app.include_router(help_route.router)
    client = TestClient(app)

    # 既定は記事の抜き出し(文章モデルを呼ばない)
    res = client.post("/api/help/chat", json={"question": "ディスクを足すには?"}).json()
    assert res["engine"] == "excerpt" and "記事のまま" in res["answer"] and not calls
    assert res["sources"][0]["slug"] == "backup-add-disk"
    res = client.post("/api/help/chat", json={"question": "ディスクを足すには?", "engine": "ollama"}).json()
    assert res["engine"] == "ollama" and res["answer"] == "1. 「写し先を足す」を押します。"
    res = client.post(
        "/api/help/chat", json={"question": "外すには?", "engine": "novelai", "history": [{"role": "user", "content": "前の質問"}]},
        headers={"Authorization": "Bearer x"},
    ).json()
    assert res["engine"] == "novelai" and calls[-1][2] == "token"
    assert calls[-1][1][1] == {"role": "user", "content": "前の質問"}
    engines = client.get("/api/help/chat/engines").json()
    assert engines["default"] == "excerpt" and engines["novelai_ready"] is False
    assert client.post("/api/help/chat", json={"question": ""}).status_code == 422
