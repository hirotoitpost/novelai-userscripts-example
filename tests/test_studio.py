"""対話生成(studio.py): モデルの答えの読み取り、場面のタグの掃除、プロンプトの組み立て。"""

from __future__ import annotations

import pytest

from python import studio

ADULT = {
    "name": "夏川つぐみ", "age": 20, "is_minor": 0,
    "appearance_tags": "1girl, adult woman, 1.2::light honey blonde hair::, long hair, golden amber eyes",
    "outfit_tags": "oversized cream knit cardigan, beige plaid pleated mini skirt",
    "style_tags": "illustration, soft painterly shading",
    "negative_tags": "anime screenshot, thick lineart",
    "body_tags": None, "notes": "",
}
MINOR = {**ADULT, "name": "ことは", "age": 16, "is_minor": 1}


def test_parse_reply_reads_json_with_surrounding_text():
    text = '了解です。\n```json\n{"say": "描きます", "action": "generate", "scene": "white bikini, from below", ' \
           '"negative": "kneeling", "size": "portrait", "use_outfit": false, "changes": ""}\n```'
    p = studio.parse_reply(text, None)
    assert p is not None
    assert (p.action, p.scene, p.negative, p.size, p.use_outfit) == ("generate", "white bikini, from below", "kneeling", "portrait", False)


def test_parse_reply_keeps_current_tags_for_explanations():
    current = {"scene": "all fours, from below", "negative": "kneeling", "size": "portrait", "use_outfit": False}
    p = studio.parse_reply('{"say": "all fours は四つん這いです", "action": "none"}', current)
    assert p is not None and p.action == "none"
    assert p.scene == "all fours, from below" and p.negative == "kneeling"


def test_parse_reply_rejects_non_json_and_fixes_bad_fields():
    assert studio.parse_reply("描きました!", None) is None
    p = studio.parse_reply('{"say": "", "action": "draw", "scene": "smile", "size": "wide"}', None)
    assert p is not None and p.action == "generate" and p.size == "portrait"


def test_clean_scene_drops_character_tags_and_name():
    scene = "夏川つぐみ, light honey blonde hair, long hair, 1.3::white bikini::, from below, from below"
    assert studio.clean_scene(ADULT, scene) == "1.3::white bikini::, from below"


def test_build_prompt_puts_character_sheet_around_scene():
    prompt, negative = studio.build_prompt(ADULT, "white bikini, from below", "kneeling", use_outfit=False)
    assert prompt.startswith("1girl, adult woman")
    assert "white bikini, from below" in prompt and prompt.endswith("soft painterly shading")
    assert "cardigan" not in prompt
    assert negative == "kneeling, anime screenshot, thick lineart"
    with_outfit, _ = studio.build_prompt(ADULT, "smile", "", use_outfit=True)
    assert "oversized cream knit cardigan" in with_outfit


def test_build_prompt_refuses_sexual_scene_for_minor_or_no_character():
    with pytest.raises(ValueError):
        studio.build_prompt(MINOR, "nude, nipples", "", use_outfit=False)
    with pytest.raises(ValueError):
        studio.build_prompt(None, "1girl, nude, nipples", "", use_outfit=False)
    prompt, _ = studio.build_prompt(MINOR, "school uniform, smile", "", use_outfit=False)
    assert "smile" in prompt


def test_scene_from_prompt_and_tag_diff():
    full = "1girl, adult woman, long hair, white bikini, all fours, illustration"
    assert studio.scene_from_prompt(ADULT, full) == "white bikini, all fours"
    diff = studio.tag_diff("all fours, from below, smile", "1.3::all fours::, from below, fisheye")
    assert diff == {"added": ["fisheye"], "removed": ["smile"], "changed": ["1.3::all fours::"]}


def test_build_messages_keeps_recent_history_and_current_tags():
    history = [{"role": "user", "content": "x" * 5000}, {"role": "assistant", "content": "y" * 5000},
               {"role": "user", "content": "z" * 5000}]
    msgs = studio.build_messages(ADULT, history, "顔が逆さま", {"scene": "all fours", "negative": "", "size": "portrait"})
    assert msgs[0]["role"] == "system"
    assert "downward dog" in msgs[0]["content"]  # タグの効き方のメモを渡す
    assert [m["content"][0] for m in msgs[1:-1]] == ["y", "z"]  # 古いものから落とす
    assert "scene: all fours" in msgs[-1]["content"] and msgs[-1]["content"].endswith("顔が逆さま")


def test_panel_prompt_covers_whole_cast():
    other = {**ADULT, "name": "先生", "appearance_tags": "1boy, black hair, glasses"}
    msgs = studio.build_messages([ADULT, other], [], "振り返って", {"scene": "standing"}, {"title": "ブレザー", "text": "まずは定番"})
    system = msgs[0]["content"]
    assert "漫画のコマの描き直し" in system and "ブレザー" in system
    assert "夏川つぐみ" in system and "先生" in system
    assert studio.clean_scene([ADULT, other], "glasses, black hair, peace sign, 先生") == "peace sign"
