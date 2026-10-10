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
