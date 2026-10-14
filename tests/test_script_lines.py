"""
台本の書き方(名前：「セリフ」/ 名前：（効果音）)と、登場人物の抽出で判定に誰も残らなかったときの切り替えのテスト。
文章モデルは差し替える。

実行方法:
  uv run pytest tests/test_script_lines.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.manga_v2.speakers import CastMember, attribute_speakers  # noqa: E402
from python.routes import story as story_routes  # noqa: E402
from python.routes.manga_v2 import _lettering, _spoken_spans  # noqa: E402
from python.script_lines import script_names, script_sfx, speaker_before  # noqa: E402

TEXT = "\n".join([
    "雨が降っていた。",
    "みお：「結構、降ってるね」",
    "ゆら: 「うん」",
    "ゆら：（ザーッ、ポツポツ）",
    "（帰りたくないな）",
    "看板には「時雨堂」とあった。",
])


def test_script_lines_name_the_speaker_and_sfx() -> None:
    assert script_names(TEXT) == ["みお", "ゆら"]
    assert script_sfx(TEXT) == ["ザーッ", "ポツポツ"]
    start = TEXT.index("「結構")
    assert speaker_before(TEXT, start) == "みお"
    # 地の文の中の「」には名前が付かない
    assert speaker_before(TEXT, TEXT.index("「時雨堂")) is None


def test_lettering_reads_script_lines() -> None:
    spoken, sfx = _spoken_spans(TEXT)
    # 台本のセリフは吹き出しになり、名前：（…）は心の声ではなく効果音になる。名前の無い（…）は心の声のまま
    assert [line for _, _, line in spoken] == ["結構、降ってるね", "うん", "（帰りたくないな）"]
    assert sfx == ["ザーッ", "ポツポツ"]
    dialogue, all_sfx = _lettering({"novelai_text": TEXT, "draft_text": TEXT, "sfx": ["ガタッ", "ザーッ"]})
    assert all_sfx == ["ザーッ", "ポツポツ", "ガタッ"] and len(dialogue) == 3


def test_script_speaker_wins_over_guessing() -> None:
    cast = [CastMember(1, ("みお",)), CastMember(2, ("ゆら",))]
    # 交互に話すとみなすと逆になる並び(どちらも ゆら)でも、書いた名前に従う
    text = "ゆら：「ねえ」\nゆら：「聞いてる？」\nみお：「うん」"
    spans = [(m, m + 1, "") for m in (text.index("「ねえ"), text.index("「聞い"), text.index("「うん"))]
    assert attribute_speakers(text, spans, cast) == [2, 2, 1]


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    connection = db.get_connection()
    yield connection
    connection.close()


def _story(conn, text: str) -> dict:
    story = db.create_story(conn, "[漫画] x", 1, 4)
    db.add_story_scenes(conn, story["id"], [{"draft_title": "1", "draft_text": text, "draft_prompt_tags": "", "novelai_text": text}])
    return db.get_story(conn, story["id"])


def _extract(monkeypatch: pytest.MonkeyPatch, story: dict, candidates: list[str], confirmed: list[str], asked: list[str]):
    calls = {"confirm": [], "ask": 0}
    monkeypatch.setattr(story_routes, "_collect_name_candidates", lambda text: [{"name": n, "aliases": [n]} for n in candidates])

    async def confirm(names):
        calls["confirm"].append(list(names))
        return confirmed

    async def ask(text):
        calls["ask"] += 1
        return [{"name": n, "aliases": [n]} for n in asked]

    async def describe(name, passages):
        return "1girl"

    monkeypatch.setattr(story_routes, "_confirm_people", confirm)
    monkeypatch.setattr(story_routes, "_ask_people_names", ask)
    monkeypatch.setattr(story_routes, "_describe_appearance", describe)
    job = story_routes._Job(story_id=story["id"], kind="characters")
    asyncio.run(story_routes._run_extract_characters(job, story))
    return job, calls


def test_nobody_confirmed_switches_to_reading_the_text(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    story = _story(conn, "陽介は時雨堂の前で立ち止まった。")
    job, calls = _extract(monkeypatch, story, ["時雨堂"], [], ["陽介"])
    assert calls["ask"] == 1
    assert [c["name"] for c in db.story_characters(conn, story["id"])] == ["陽介"]
    assert "1人を登録し" in job.message


def test_script_names_skip_the_judgement(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    story = _story(conn, "こはく：「おはよう」\n商店街は静かだった。")
    _, calls = _extract(monkeypatch, story, ["商店街"], [], [])
    # 台本の名前(こはく)は判定にかけず、判定にかけるのは本文から拾った候補だけ。誰かは残るので本文は読ませない
    assert calls["confirm"] == [["商店街"]] and calls["ask"] == 0
    assert [c["name"] for c in db.story_characters(conn, story["id"])] == ["こはく"]
