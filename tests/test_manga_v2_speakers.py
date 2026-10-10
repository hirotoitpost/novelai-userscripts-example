"""
漫画v2: セリフの話し手の推定・コマの頭の見分け・吹き出しのしっぽ/置き場所のテスト。

実行方法:
  uv run pytest tests/test_manga_v2_speakers.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.manga_v2.compose import (  # noqa: E402
    PanelContent,
    _offpanel_side,
    _place,
    _reads_after,
    _tail_target,
    split_dense_panels,
)
from python.manga_v2.speakers import (  # noqa: E402
    attribute_speakers,
    cast_member,
    cast_members,
    hair_color,
    identify_heads,
)
from python.novelai_image_v5 import DIALOGUE_RE, _build_character_prompts  # noqa: E402

YURA = {"id": 1, "name": "九条 ゆら", "appearance_tags": "1girl, brown hair, long hair, yellow eyes"}
MIO = {"id": 2, "name": "佐倉 みお", "appearance_tags": "1girl, black hair, short hair, bob cut"}
CAST = [cast_member(YURA), cast_member(MIO)]


def _speakers(text: str, cast=CAST) -> list[str | None]:
    spans = [(m.start(), m.end(), m.group(1)) for m in DIALOGUE_RE.finditer(text)]
    names = {1: "ゆら", 2: "みお"}
    return [names.get(s) if s is not None else None for s in attribute_speakers(text, spans, cast)]


# ---- 話し手の推定 ----


def test_cast_member_names_and_hair() -> None:
    member = cast_member(YURA)
    assert member.names == ("九条ゆら", "ゆら", "九条")
    assert member.hair == hair_color("brown hair")


def test_shared_family_name_adds_short_names() -> None:
    # 「矢野栄子」「矢野先生」のように空白なしで姓が共通なら、「栄子」「先生」でも呼べる
    eiko, sensei = cast_members([{"id": 3, "name": "矢野栄子"}, {"id": 4, "name": "矢野先生"}])
    assert "栄子" in eiko.names and "先生" in sensei.names
    text = "「先生、朝ですよ」\n「栄子さん、あと五分……」\n先生は布団にもぐった。"
    spans = [(m.start(), m.end(), m.group(1)) for m in DIALOGUE_RE.finditer(text)]
    assert attribute_speakers(text, spans, [eiko, sensei]) == [3, 4]
    # 姓が共通でなければ何も足さない
    assert cast_members([YURA, MIO]) == CAST


def test_following_narration_names_the_speaker() -> None:
    text = "「結構、降ってるね」\nみおが呟いた。\n「みお、早く行こう」\nゆらは叫んだ。"
    assert _speakers(text) == ["みお", "ゆら"]


def test_subject_on_the_same_line() -> None:
    text = "みおはくすくす笑った。「はいはい。ゆらって、雨の日だけは素直だよね」"
    assert _speakers(text) == ["みお"]


def test_vocative_means_the_other_person() -> None:
    # 「ゆら、…」はゆらへの呼びかけなので、二人の場面ならみおのセリフ。文中の呼びかけも拾う
    assert _speakers("「ゆら、また怖い顔してる」") == ["みお"]
    assert _speakers("「じゃあ、また明日。ゆら、お風呂入って早く寝てね」") == ["みお"]
    # 名前でセリフが終わる呼びかけ(「ねえ、みお」)と、それに続く返事
    assert _speakers("「ねえ、みお」\n「ん？」") == ["ゆら", "みお"]
    # 「みおは？」は呼びかけではない(前のセリフと交互)
    text = "「そういうときは私の傘を貸すけど」\nみおが言った。\n「でも、みおは？　自転車でしょ？」"
    assert _speakers(text) == ["みお", "ゆら"]


def test_alternates_in_a_two_person_dialogue() -> None:
    text = "「ゆら、また怖い顔してる」\n「いつもどおりだよ」\n「いいけど、どうしたの？」"
    assert _speakers(text) == ["みお", "ゆら", "みお"]


def test_line_with_next_dialogue_does_not_name_the_previous_speaker() -> None:
    # 直後の行の主語(みお)は、その行にある次のセリフの話し手であって、前のセリフの話し手ではない
    text = (
        "「ん？　どうしたの？」\n"
        "「この傘、もしかしてみおの？」\n"
        "みおはぺろりと舌を出した。「朝のうちに、ゆらの鞄にこっそり入れておいたの」"
    )
    assert _speakers(text) == ["みお", "ゆら", "みお"]


def test_unbubbled_speech_in_narration_counts_for_alternation() -> None:
    # 読点の後の「」は吹き出しにしない(セリフとして数えない)が、話したのはみおなので交互の起点になる
    text = (
        "「お母さんに傘を持ってきてもらう」\n"
        "ゆらは小さく震えていた。\n"
        "みおは微笑んで、「私の傘を貸すけど」と言った。\n"
        "「でも、自転車でしょ？」"
    )
    spans = [(m.start(), m.end(), m.group(1)) for m in DIALOGUE_RE.finditer(text)]
    spoken = [spans[0], spans[2]]  # 2つ目は地の文の中の「」
    assert attribute_speakers(text, spoken, CAST) == [1, 1]


def test_interrupted_conversation_uses_the_narration_subject() -> None:
    text = (
        "「うん。みおも、気をつけて帰って」\n"
        "ゆらは微笑んで見送った。\n"
        "玄関で靴を脱ごうとして、ゆらはふと鞄に目を落とした。\n"
        "「あれ？」"
    )
    assert _speakers(text) == ["ゆら", "ゆら"]


def test_single_character_scene_speaks_everything() -> None:
    assert _speakers("「あれ？」\n「誰のかな……」", [CAST[0]]) == ["ゆら", "ゆら"]


# ---- 頭の見分け ----

_BROWN = (120, 80, 55)
_BLACK = (40, 38, 45)
_SKIN = (250, 225, 210)


def _two_heads(left: tuple[int, int, int], right: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (400, 200), _SKIN)
    image.paste(left, (20, 20, 180, 180))
    image.paste(right, (220, 20, 380, 180))
    return image


_HEADS = [(220.0, 20.0, 380.0, 180.0), (20.0, 20.0, 180.0, 180.0)]  # 右・左の順(検出順は不定)


def test_identify_heads_by_hair_color() -> None:
    assert identify_heads(_two_heads(_BLACK, _BROWN), _HEADS, CAST) == [1, 2]


def test_identify_heads_uses_the_generation_order_when_colors_are_unclear() -> None:
    # 暗い場面でゆらの茶髪も黒っぽく描かれたとき、髪色だけだと決まらない。生成時に
    # キャラを名前順(ゆら→みお)に左から並べているので、左がゆら、右がみおになる
    assert identify_heads(_two_heads(_BLACK, _BLACK), _HEADS, CAST) == [2, 1]


def test_identify_heads_needs_two_hair_colors() -> None:
    no_hair = [cast_member({"id": 3, "name": "白黒", "appearance_tags": "1girl"}), CAST[1]]
    assert identify_heads(_two_heads(_BLACK, _BROWN), _HEADS, no_hair) == [None, None]


# ---- 吹き出し ----

_PANEL = (0, 0, 600, 800)
_LEFT_HEAD = (60, 80, 220, 260)
_RIGHT_HEAD = (380, 80, 540, 260)


def test_tail_points_to_the_speakers_head() -> None:
    box = (250, 500, 350, 700)
    tail = _tail_target(box, _PANEL, [_LEFT_HEAD, _RIGHT_HEAD], [1, 2], 2, None, (0, 0))
    assert tail is not None and tail[0] > 300


def test_offpanel_speaker_has_no_tail() -> None:
    box = (400, 500, 500, 700)
    # 写っているのはゆらだけで、話し手のみおは絵にいない
    side = _offpanel_side(box, _PANEL, [_LEFT_HEAD], [1], {}, 2)
    assert side == "right"
    assert _tail_target(box, _PANEL, [_LEFT_HEAD], [1], 2, side, (0, 0)) is None
    # 見分けられていない頭があれば、それが話し手かもしれないので枠外扱いにしない
    assert _offpanel_side(box, _PANEL, [_LEFT_HEAD, _RIGHT_HEAD], [1, None], {}, 2) is None


def test_reads_after_keeps_right_to_left_and_top_to_bottom() -> None:
    earlier = [(300, 20, 400, 200)]
    assert _reads_after((100, 20, 200, 200), earlier)  # 同じ段の左
    assert not _reads_after((450, 20, 550, 200), earlier)  # 同じ段の右
    assert _reads_after((450, 300, 550, 480), earlier)  # 下の段なら右でもよい


def test_bubble_goes_near_the_speaker_and_away_from_the_other() -> None:
    heads = [_LEFT_HEAD, _RIGHT_HEAD]
    size = (120, 200)
    first, _ = _place(size, _PANEL, [], heads=heads, prefer_x=460, earlier=[])
    assert (first[0] + first[2]) / 2 > 300  # みおのセリフはみお(右)の近く
    second, _ = _place(size, _PANEL, [first], heads=heads, prefer_x=140, earlier=[first], rivals=[first])
    assert (second[0] + second[2]) / 2 < 300  # ゆらの返事はゆら(左)の近く
    assert _reads_after(second, [first])


def test_split_dense_panels_keeps_speakers_and_focus() -> None:
    panel = PanelContent(None, ["a", "b", "c", "d"], speakers=[1, 2, 2, 2], cast=CAST)
    split = split_dense_panels([panel], 2)
    assert [p.speakers for p in split] == [[1, 2], [2, 2]]
    assert split[0].focus == []  # 1コマ目は寄らない
    assert split[1].focus == [2]  # 寄りのコマはそこで話す人へ


# ---- キャラごとのネガティブ ----


def test_character_negatives_go_to_each_character() -> None:
    prompts, captions, negatives = _build_character_prompts(["yura tags", "mio tags"], ["black hair", "brown hair"])
    assert [p["uc"] for p in prompts] == ["black hair", "brown hair"]
    assert [n["char_caption"] for n in negatives] == ["black hair", "brown hair"]
    assert [n["centers"] for n in negatives] == [c["centers"] for c in captions]


def test_cast_prompt_puts_actions_in_each_character() -> None:
    from python.routes.manga_v2 import _cast_prompt

    characters = [
        {"appearance_tags": "1girl, solo, brown hair", "outfit_tags": "apron", "action_tags": "blush, looking away"},
        {"appearance_tags": "1boy, solo, glasses", "outfit_tags": None, "action_tags": "hand on own chin"},
    ]
    tags, _, count = _cast_prompt(characters)
    assert tags == ["1girl, brown hair, apron, blush, looking away", "1boy, glasses, hand on own chin"]
    assert count == "1girl, 1boy"
