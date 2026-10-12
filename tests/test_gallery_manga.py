"""
ギャラリーで選んだ画像から漫画を作る機能(gallery_manga)のテスト。文章モデルと画像生成は使わず、答えや生成を
差し替えて確かめる。

実行方法:
  uv run pytest tests/test_gallery_manga.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, gallery_manga  # noqa: E402
from python.gallery_manga import (  # noqa: E402
    apply_tag_changes,
    clean_source_tags,
    fallback_script,
    panel_scene,
    parse_script,
    script_prompts,
    settings_tags,
)
from python.routes import gallery_manga as routes  # noqa: E402
from python.routes import library  # noqa: E402

SOURCE = "1girl, smile, brown hair, school uniform, classroom"


def test_source_prompt_becomes_panel_tags() -> None:
    prompt = "1girl, {{smile}}, 1.3::brown hair::, very aesthetic, masterpiece, no text, school_uniform, classroom, -1::blurry::, rating:general, year 2024"
    # 強調の書き方を外し、品質・絵柄のタグと、出さない指定(マイナスの重み)を除く
    assert clean_source_tags(prompt) == SOURCE
    assert clean_source_tags("") == ""
    # ギャラリーの記録の見出し(タグではないもの)は使わない
    assert clean_source_tags("[表情: embarrassed] 1girl, park bench, [smile]") == "1girl, park bench, smile"
    assert clean_source_tags("[2倍に拡大]") == ""
    # 埋め込まれた設定: 本体にキャラごとのプロンプトをつなげる。無ければギャラリーの記録
    settings = {"prompt": "2girls, classroom, masterpiece", "characters": [{"prompt": "girl, brown hair"}, {"prompt": "girl, black hair"}]}
    assert settings_tags(settings) == "2girls, classroom, girl, brown hair, black hair"
    assert settings_tags(None, "1boy, park, best quality") == "1boy, park"
    assert settings_tags({"prompt": "masterpiece", "characters": []}, "1boy, park") == "1boy, park"


def test_model_may_adjust_the_picture_but_not_who_is_in_it() -> None:
    changed = apply_tag_changes(SOURCE, "blush, looking_away, masterpiece", "smile, brown hair, 1girl, school uniform, nothing")
    # 外せるのは元にある表情・動作など。人数と見た目(髪・服)は残す。足すタグからも品質タグは除く
    assert changed == "1girl, brown hair, school uniform, classroom, blush, looking away"
    # 文章モデルは、タグを配列で返すこともある
    assert apply_tag_changes(SOURCE, ["blush", "looking away"], ["smile"]) == changed
    # 足せる数には上限がある
    many = apply_tag_changes(SOURCE, "a1, a2, a3, a4, a5, a6, a7, a8", "")
    assert many.count("a") - SOURCE.count("a") == gallery_manga.MAX_ADDED_TAGS
    # ほとんど外すような答えは聞かない
    assert apply_tag_changes("smile, running, park, night", "", "smile, running, park") == "smile, running, park, night"


def test_script_is_checked_and_trimmed() -> None:
    system, user = script_prompts("学園コメディ", "テスト前のどたばた", [SOURCE, ""])
    assert "panels はちょうど2つ" in system and "ジャンル: 学園コメディ" in user
    assert "話の方向・補足: テスト前のどたばた" in user and "2コマ目: (プロンプトなし)" in user
    answer = {
        "title": "テスト前夜",
        "panels": [
            {
                "lines": [{"kind": "speech", "text": "あ" * 60}, {"kind": "thought", "text": "まずい"}, {"text": "三つ目"}, {"text": " "}],
                "sfx": ["ガーン", "ドン"],
                "narration": "放課後の教室、だれもいない時間に",
                "add_tags": "sweat",
                "remove_tags": "smile",
            },
            "こわれた答え",
        ],
    }
    script = parse_script(answer, [SOURCE, "park"])
    first, second = script["panels"]
    assert script["title"] == "テスト前夜"
    assert [line["kind"] for line in first["lines"]] == ["speech", "thought"] and len(first["lines"][0]["text"]) == 40
    assert first["sfx"] == ["ガーン"] and len(first["narration"]) == 15
    assert first["prompt_tags"] == "1girl, brown hair, school uniform, classroom, sweat"
    assert second == {"lines": [], "sfx": [], "narration": "", "prompt_tags": "park"}
    with pytest.raises(ValueError):
        parse_script({"panels": [{}]}, [SOURCE, "park"])
    with pytest.raises(ValueError):
        parse_script("こわれた答え", [SOURCE])
    # 考えさせられなかったときは、絵だけ
    assert fallback_script([SOURCE])["panels"][0] == {"lines": [], "sfx": [], "narration": "", "prompt_tags": SOURCE}


def test_panel_becomes_a_scene_without_speakers() -> None:
    panel = {"lines": [{"kind": "speech", "text": "おはよう"}, {"kind": "thought", "text": "ねむい"}], "sfx": ["ふあ"], "narration": "朝", "prompt_tags": SOURCE}
    scene = panel_scene(panel, "1ページ1")
    assert scene["text"] == "「おはよう」\n（ねむい）" and scene["character_ids"] == [] and scene["sfx"] == ["ふあ"]
    assert scene["narration"] == "朝" and scene["prompt_tags"] == SOURCE
    silent = panel_scene({"lines": [], "sfx": [], "narration": "", "prompt_tags": "park"}, "1ページ2")
    assert silent["text"] == "(セリフなし)" and silent["narration"] is None


# ---- API ----


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    db.get_connection().close()
    monkeypatch.setattr(
        routes, "_source_tags", lambda keys: [{"a": SOURCE, "b": "1boy, park", "empty": ""}[k] for k in dict.fromkeys(keys) if k != "gone"]
    )
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _run(client: TestClient, body: dict) -> dict:
    res = client.post("/api/gallery-manga", json=body, headers={"Authorization": "Bearer t"})
    assert res.status_code == 200, res.text
    for _ in range(200):
        job = client.get(f"/api/gallery-manga/jobs/{res.json()['id']}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("終わりませんでした")


def test_makes_a_story_from_selected_images(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: dict = {}

    async def fake_ask(api_key, system, user, *, max_tokens, temperature):
        asked.update(system=system, user=user)
        return {
            "title": "公園の約束",
            "panels": [
                {"lines": [{"kind": "speech", "text": "待った？"}], "sfx": ["タッ"], "narration": "", "add_tags": "running", "remove_tags": "smile"},
                {"lines": [], "sfx": [], "narration": "夕方の公園", "add_tags": "", "remove_tags": ""},
            ],
        }

    monkeypatch.setattr(gallery_manga, "_ask_json", fake_ask)
    job = _run(client, {"image_keys": ["a", "b", "a", "gone"], "genre": "ラブコメ", "theme": "待ち合わせ", "make_images": False})
    assert job["status"] == "done", job
    assert "ジャンル: ラブコメ" in asked["user"] and "2コマ目: 1boy, park" in asked["user"]
    conn = db.get_connection()
    try:
        story = db.get_story(conn, job["story_id"])
        scenes = db.list_story_scenes(conn, job["story_id"])
    finally:
        conn.close()
    assert story["title"] == "公園の約束" and len(scenes) == 2
    assert scenes[0]["draft_prompt_tags"] == "1girl, brown hair, school uniform, classroom, running"
    assert scenes[0]["draft_text"] == "「待った？」" and scenes[0]["sfx"] == ["タッ"]
    assert scenes[1]["narration"] == "夕方の公園" and scenes[1]["sfx"] == []
    assert "コマの絵はまだ生成していません" in job["message"]


def test_generates_and_composes_when_asked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from python.routes import manga_v2

    async def broken_ask(*args, **kwargs):
        raise RuntimeError("答えが読めません")

    calls: dict = {}

    async def fake_panels(job, story_id, req, api_key, targets):
        calls["panels"] = (req.color, req.template, len(targets))

    def fake_compose(story_id, req):
        calls["compose"] = story_id
        return {"pages": ["p1"]}

    monkeypatch.setattr(gallery_manga, "_ask_json", broken_ask)
    monkeypatch.setattr(manga_v2, "_run_panels", fake_panels)
    monkeypatch.setattr(manga_v2, "compose", fake_compose)
    job = _run(client, {"image_keys": ["a", "b"], "title": "自分で付けた題", "color": False})
    assert job["status"] == "done", job
    # 文章モデルが答えられなくても、絵だけの漫画にはなる
    assert calls == {"panels": (False, "grid4", 2), "compose": job["story_id"]}
    assert "自分で付けた題" in job["message"] and "セリフは考えられませんでした" in job["message"]


def test_rejects_what_cannot_become_a_manga(client: TestClient) -> None:
    assert _run(client, {"image_keys": ["gone"]})["detail"] == "選んだ画像が見つかりません。"
    assert "プロンプトがありません" in _run(client, {"image_keys": ["empty"]})["detail"]
    # プロンプトの無い画像が混じっていたら、それだけ外して作る
    job = _run(client, {"image_keys": ["a", "empty", "b"], "make_images": False})
    assert job["status"] == "done" and "プロンプトの無い画像 1 枚は外しました" in job["message"]
    many = [f"k{i}" for i in range(gallery_manga.MAX_IMAGES + 1)]
    res = client.post("/api/gallery-manga", json={"image_keys": many}, headers={"Authorization": "Bearer t"})
    assert res.status_code == 422
    assert client.post("/api/gallery-manga", json={"image_keys": []}, headers={"Authorization": "Bearer t"}).status_code == 422
    assert client.get("/api/gallery-manga/jobs/nope").status_code == 404


def test_source_tags_read_embedded_settings_then_the_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(b"x")
    items = [
        library.GalleryImage(key="with", source="generate", path="a.png", created_at="", prompt="record prompt"),
        library.GalleryImage(key="without", source="generate", path="a.png", created_at="", prompt="1boy, park, masterpiece"),
        library.GalleryImage(key="missing", source="generate", path="nope.png", created_at="", prompt="1girl, beach"),
    ]
    monkeypatch.setattr(library, "_gallery_images", lambda: items)

    def served(path: str) -> Path:
        if path == "nope.png":
            raise library.HTTPException(status_code=404, detail="not found")
        return image

    monkeypatch.setattr(library, "_served_path", served)
    answers = iter([{"prompt": "1girl, classroom", "characters": [{"prompt": "girl, brown hair"}]}, None])
    monkeypatch.setattr(routes, "generation_settings", lambda data: next(answers))
    assert routes._source_tags(["with", "without", "missing", "unknown"]) == [
        "1girl, classroom, girl, brown hair",
        "1boy, park",
        "1girl, beach",
    ]
