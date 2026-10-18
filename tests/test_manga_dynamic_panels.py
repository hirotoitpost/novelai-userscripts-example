"""ページごとにコマ数を変える漫画: コマ数に合わせたコマ割りと、大枠・台本のコマ数。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.manga_draft import DraftCharacter, episode_prompts, outline_prompts, parse_outlines  # noqa: E402
from python.manga_v2.compose import plan_pages  # noqa: E402
from python.manga_v2.layout import MAX_DYNAMIC_PANELS, _COUNT_LAYOUTS, dynamic_layout, layout_rects  # noqa: E402


@pytest.mark.parametrize("count", range(1, MAX_DYNAMIC_PANELS + 1))
def test_layouts_have_the_count_and_read_right_to_left(count: int) -> None:
    for variant in range(len(_COUNT_LAYOUTS[count])):
        layout = dynamic_layout(count, variant)
        assert len(layout) == count
        rects = layout_rects(layout)
        for i, (x0, y0, x1, y1) in enumerate(rects):
            assert 0 <= x0 < x1 <= 1200 and 0 <= y0 < y1 <= 1700
            # 重ならない
            for a0, b0, a1, b1 in rects[i + 1 :]:
                assert x1 <= a0 or a1 <= x0 or y1 <= b0 or b1 <= y0
        # 読む順: 上の段から、同じ段では右から
        for (x0, y0, _, _), (a0, b0, _, _) in zip(rects, rects[1:]):
            assert b0 > y0 or (b0 == y0 and a0 < x0)


def test_variant_changes_the_layout_and_pages_follow_the_counts() -> None:
    assert dynamic_layout(4, 0) != dynamic_layout(4, 1)
    assert dynamic_layout(4, 0) == dynamic_layout(4, len(_COUNT_LAYOUTS[4]))
    with pytest.raises(ValueError):
        dynamic_layout(7)
    layouts = [dynamic_layout(n, i) for i, n in enumerate((3, 6, 4))]
    plans = plan_pages("vertical4", 13, layouts)
    assert [(p.start, p.end) for p in plans] == [(0, 3), (3, 9), (9, 13)]


def test_outline_with_panel_counts() -> None:
    cast = [DraftCharacter(id=1, name="会長", profile="")]
    system, _ = outline_prompts("生徒会", "日常", cast, 3, panel_range=(3, 6))
    assert "1つが漫画の1ページ" in system and "3〜6" in system
    data = {"outlines": [{"title": "生徒会", "logline": "", "episodes": [
        {"content": "会議", "panels": 9}, {"content": "準備", "panels": "x"}, "本番",
    ]}]}
    (outline,) = parse_outlines(data, 3, (3, 6))
    # 範囲の外は収め、数でなければ範囲の真ん中
    assert outline["episodes"] == ["会議", "準備", "本番"] and outline["panel_counts"] == [6, 4, 4]
    assert "panel_counts" not in parse_outlines(data, 3)[0]


def test_page_script_follows_the_count_and_only_the_last_page_ends() -> None:
    cast = [DraftCharacter(id=1, name="会長", profile="")]
    outline = {"title": "生徒会", "logline": "", "episodes": ["会議", "準備", "本番"]}
    first, _ = episode_prompts(outline, 0, cast, count=5)
    last, _ = episode_prompts(outline, 2, cast, count=3)
    assert "ちょうど5コマ" in first and "次のページへつながる引き" in first
    assert "ちょうど3コマ" in last and "話の結末" in last
    # コマ数を渡さなければ、これまでどおり4コマでオチ
    plain, _ = episode_prompts(outline, 0, cast)
    assert "4コマ目にオチ" in plain


def test_page_script_sees_all_earlier_lines_and_is_told_not_to_repeat() -> None:
    cast = [DraftCharacter(id=1, name="会長", profile=""), DraftCharacter(id=2, name="怜", profile="")]
    outline = {"title": "夏期講習", "logline": "", "episodes": ["休憩", "花火大会の案", "ホタルの案"]}

    def panel(speaker: str, text: str) -> dict:
        return {"lines": [{"speaker": speaker, "text": text}]}

    # 1ページ目の最初のセリフ(直前の4コマより前)も渡す
    earlier = [panel("会長", "花火大会に行こう!"), panel("怜", "混雑するし無理")] + [panel("会長", f"…{i}") for i in range(5)]
    _, user = episode_prompts(outline, 2, cast, previous_panels=earlier, count=4)
    assert "花火大会に行こう!" in user and "繰り返さず" in user
    # 1ページ目には、繰り返さないの決まりは要らない
    _, first = episode_prompts(outline, 0, cast, count=4)
    assert "繰り返さず" not in first
    # コマ数を変えない(4コマずつの)漫画は、これまでどおり直前の4コマだけ
    _, plain = episode_prompts(outline, 2, cast, previous_panels=earlier)
    assert "花火大会に行こう!" not in plain and "…4" in plain and "繰り返さず" not in plain
    system, _ = outline_prompts("夏期講習", "日常", cast, 3, panel_range=(3, 6))
    assert "前のページと同じ出来事・提案を繰り返さない" in system
