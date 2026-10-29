"""
取り込んだ漫画に似た漫画を作るときの、人物のまとめ方とタグの選び方のテスト(判定モデルは使わない)。

実行方法:
  uv run pytest tests/test_manga_similar.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import image_tagger, manga_similar  # noqa: E402
from python.image_tagger import CharacterGuess, TagScore  # noqa: E402


def test_cluster_people_by_gender_and_hair() -> None:
    guesses = [
        ("1girl", ["black hair", "long hair", "school uniform", "red eyes"]),
        ("1girl", ["black hair", "long hair", "school uniform"]),
        ("1girl", ["black hair", "ponytail", "school uniform"]),
        ("1boy", ["brown hair", "glasses"]),
        ("1boy", ["brown hair", "glasses", "necktie"]),
        ("1girl", ["blonde hair"]),
    ]
    people = manga_similar.cluster_people(guesses)
    # 多い順に2人まで。見た目は、その人物の絵の4割以上に出たタグ
    assert [(p.gender, p.count) for p in people] == [("1girl", 3), ("1boy", 2)]
    assert set(people[0].tags) == {"black hair", "long hair", "school uniform"}
    assert set(people[1].tags) == {"brown hair", "glasses", "necktie"}


def test_cluster_people_keeps_both_genders_and_ignores_colour_in_black_and_white() -> None:
    # 白黒の作品: 同じ女性がコマによって金髪にも黒髪にも読まれる。髪の色で分けず、髪の色は一番多いものだけ
    guesses = [
        ("1girl", ["blonde hair", "ponytail", "large breasts"]),
        ("1girl", ["black hair", "ponytail", "large breasts"]),
        ("1girl", ["black hair", "ponytail"]),
        ("1girl", ["black hair", "blonde hair", "large breasts"]),
        ("1girl", ["black hair", "large breasts"]),
        ("1boy", ["bald"]),
    ]
    people = manga_similar.cluster_people(guesses, monochrome=True)
    assert [(p.gender, p.count) for p in people] == [("1girl", 5), ("1boy", 1)]
    assert set(people[0].tags) == {"black hair", "ponytail", "large breasts"}
    # カラーでは髪の色で分ける。それでも、両方の性別が出てくれば性別ごとに1人ずつ選ぶ
    people = manga_similar.cluster_people(guesses)
    assert sorted(p.gender for p in people) == ["1boy", "1girl"]


def test_read_pages_keeps_setting_and_drops_unsafe_tags(monkeypatch) -> None:
    scores = [
        TagScore("comic", 0.9, "general"),
        TagScore("cafe", 0.8, "general"),
        TagScore("pancake", 0.7, "general"),
        TagScore("breasts", 0.9, "general"),
        TagScore("nude", 0.6, "general"),
        TagScore("smile", 0.9, "general"),
        TagScore("hatsune miku", 0.95, "character"),
        TagScore("indoors", 0.3, "general"),
    ]
    monkeypatch.setattr(image_tagger, "tag_image", lambda image: scores)
    monkeypatch.setattr(image_tagger, "colour_profile", lambda image: (0.95, 0.0))
    monkeypatch.setattr(
        image_tagger,
        "split_characters",
        lambda image: [CharacterGuess("1girl", ["black hair", "large breasts", "smile", "skirt"], (0.3, 0.5))],
    )
    features = manga_similar.read_pages([Image.new("RGB", (8, 8))] * 2)
    # 舞台は場所・小物だけ(絵柄・人物・体つき・露出・キャラ名・確率の低いものは入れない)
    assert features.setting == ["cafe", "pancake"]
    assert features.monochrome
    # 見た目は髪・服だけ(体つき・表情は入れない)
    assert features.people[0].gender == "1girl"
    assert set(features.people[0].tags) == {"black hair", "skirt"}
    assert "cafe" in manga_similar.theme_of(features)


def test_adult_import_keeps_body_and_sexual_tags_but_not_minor_ones(monkeypatch) -> None:
    scores = [TagScore("bedroom", 0.8, "general"), TagScore("bed sheet", 0.7, "general")]
    monkeypatch.setattr(image_tagger, "tag_image", lambda image: scores)
    monkeypatch.setattr(image_tagger, "colour_profile", lambda image: (0.95, 0.0))
    monkeypatch.setattr(
        image_tagger,
        "split_characters",
        lambda image: [CharacterGuess("1girl", ["black hair", "large breasts", "school uniform", "skirt", "cleavage"], (0.3, 0.5))],
    )
    pages = [Image.new("RGB", (8, 8))] * 2
    # 全年齢: 体つき・露出の語は使わない(制服は使う)
    assert set(manga_similar.read_pages(pages).people[0].tags) == {"black hair", "school uniform", "skirt"}
    # R18: 体つき・露出も使う。作る人物は成人なので、制服は衣装として使う
    tags = set(manga_similar.read_pages(pages, adult=True).people[0].tags)
    assert {"black hair", "skirt", "large breasts", "school uniform"} <= tags

    script = [{"prompt_tags": "bedroom", "characters": ["A"], "actions": {}, "lines": ["…"]}]
    imported = [{"detailed": True, "scene_tags": ["bed", "sex", "loli", "child"], "action_tags": ["kiss"], "lines": ["x"]}]
    kept = manga_similar.apply_import_panels([dict(script[0])], imported, adult=True)[0]["prompt_tags"]
    assert "sex" in kept and "loli" not in kept and "child" not in kept
    assert "sex" not in manga_similar.apply_import_panels([dict(script[0])], imported)[0]["prompt_tags"]


def test_adult_import_makes_only_adult_people(monkeypatch) -> None:
    import asyncio

    from python import manga_draft

    answers = iter([
        {"characters": [{"name": "佐藤 花", "age": 16, "profile": "高校生", "look": ""}]},
        {"characters": [{"name": "佐藤 花", "age": 27, "profile": "会社員", "look": "large breasts"}]},
    ])
    systems: list[str] = []

    async def ask(api_key, system, user, *, max_tokens, temperature):  # noqa: ANN001
        systems.append(system)
        return next(answers)

    monkeypatch.setattr(manga_draft, "_ask_json", ask)
    people = [manga_similar.Person("1girl", ["black hair"], 3)]
    named = asyncio.run(manga_similar.name_people("k", people, "ラブコメ", [], adult=True))
    # 18 歳未満の答えは作り直させる。R18 の決まりを指示文に入れる
    assert named == [{"name": "佐藤 花", "age": 27, "profile": "会社員", "look": "large breasts"}] and len(systems) == 2
    assert "18歳以上" in systems[0]

    monkeypatch.setattr(manga_draft, "_ask_json", lambda *a, **k: _answer({"characters": [{"name": "x", "age": 15}]}))
    try:
        asyncio.run(manga_similar.name_people("k", people, "ラブコメ", [], adult=True))
    except RuntimeError:
        pass
    else:
        raise AssertionError("18 歳未満の人物しか出なければ止める")


async def _answer(value):  # noqa: ANN001, ANN202
    return value
