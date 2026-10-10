"""
画像生成ページに読み込むキャラシートのプロンプト・ネガティブのテスト。
キャラ別データセットと同じ組み立てになっていることを確かめる。

実行方法:
  uv run pytest tests/test_character_sheet_prompt.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.character_sheet import (  # noqa: E402
    DEFAULT_NEGATIVE,
    r18_negative,
    safe_negative,
    build_variation_shots,
    sheet_negative,
    sheet_prompt,
    split_tags,
)

YURA = {
    "id": 1,
    "name": "九条 ゆら",
    "trigger_word": "kujo_yura",
    "appearance_tags": "1girl, solo, brown hair, two side up",
    "outfit_tags": "school uniform, grey cardigan",
    "style_tags": "best quality, masterpiece",
    "negative_tags": "short hair, black hair",
    "seed": 3257661879,
}


def test_sheet_prompt_matches_a_dataset_shot_without_variations() -> None:
    # 差し替え(構図・ポーズ・服装・表情・場所)を選ばないデータセットの1枚から、
    # トリガーワードだけを除いたプロンプトになる
    shot = build_variation_shots(YURA, poses=[], outfits=[], expressions=[], locations=[], count=1)[0]
    assert shot.prompt == f"kujo_yura, {sheet_prompt(YURA)}"
    assert "kujo_yura" not in sheet_prompt(YURA)
    assert sheet_prompt(YURA).startswith("1girl, solo, brown hair")


def test_sheet_prompt_skips_missing_fields() -> None:
    assert sheet_prompt({"appearance_tags": "1girl, black hair"}) == "1girl, black hair"


def test_sheet_negative_is_the_general_dataset_negative() -> None:
    tags = split_tags(sheet_negative(YURA))
    for part in (DEFAULT_NEGATIVE, YURA["negative_tags"], safe_negative()):
        assert set(split_tags(part)) <= set(tags)
    assert not set(split_tags(r18_negative())) & set(tags) - set(split_tags(safe_negative()))


def test_sheet_negative_adds_guard_tags_once() -> None:
    tags = split_tags(sheet_negative(YURA, extra="black hair, gore"))
    assert tags.count("black hair") == 1
    assert "gore" in tags
