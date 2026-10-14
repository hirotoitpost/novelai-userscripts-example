"""
描き直しで作る人物の状態(透明・幽霊・石化 など)。

最初から「透明」と指定して描くと、キャラのプロンプトがその人物を描くので、透明にならない(強調しても出なかった)。
そこで、ふつうに描いたコマから、その人物の範囲だけをインペイントで描き直す(例: 透明 → 空の服が浮いている)。
範囲の外(ほかの人物・背景)は変わらない。docs/trials/2026-10-14_panel-check.md を参照。

- 状態の決まりは DB(panel_redraw_states)にあり、画面から足したり直したりできる: 反応する動作のタグ(match_tags)、
  描き直しのタグ(redraw_tags)、その人物の見た目をどこまで入れるか(look: none / outfit / full)。
- どの人物か: 台本の登場人物は、コマの中で名前順に左から並ぶ(コマの生成でそう指定している)。頭の検出の結果を左から並べて、
  名前順の人物と対応させる。頭の数と、人として描く登場人物の数が合わないときは、どの人か分からないので描き直さない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .character_sheet import _plain, character_prompt_tags, join_tags, split_tags
from .panel_check import is_person

LOOKS = ("none", "outfit", "full")
# 範囲: 頭の幅を単位にした左右の広がり(片側)と、頭の上端の少し上から画面の下まで
_HALF_WIDTH = 1.5
_ABOVE_HEAD = 0.1
_FEATHER = 10


def matched_state(action_tags: str, states: list[dict[str, Any]]) -> dict[str, Any] | None:
    """人物の動作のタグに当たる状態(先に登録したものから見る)。"""
    tags = {_plain(t) for t in split_tags(action_tags)}
    for state in states:
        if tags & {_plain(t) for t in split_tags(state["match_tags"])}:
            return state
    return None


def state_match_tags(states: list[dict[str, Any]]) -> set[str]:
    """どれかの状態に反応するタグ(絵の確かめでは、描き直しで扱うので確かめない)。"""
    return {_plain(t) for state in states for t in split_tags(state["match_tags"])}


@dataclass
class Target:
    """描き直す人物: 登場人物、当たった状態、範囲(x0, y0, x1, y1)。"""

    character: dict[str, Any]
    state: dict[str, Any]
    box: tuple[int, int, int, int]


def find_targets(
    members: list[dict[str, Any]], heads: list[tuple[float, float, float, float]], size: tuple[int, int],
    states: list[dict[str, Any]],
) -> tuple[list[Target], str | None]:
    """
    コマの登場人物(名前順)と頭の検出から、描き直す人物とその範囲。戻り値の2つ目は、描き直せない理由(無ければ None)。
    """
    people = [m for m in members if is_person(m.get("appearance_tags") or "")]
    wanted = [(m, matched_state(m.get("action_tags") or "", states)) for m in people]
    if not any(state for _, state in wanted):
        return [], None
    ordered = sorted(heads, key=lambda h: (h[0] + h[2]) / 2)
    if len(ordered) != len(people):
        return [], f"頭が{len(ordered)}つ見つかり、登場人物({len(people)}人)と合わないので、どの人か分かりません"
    centres = [(h[0] + h[2]) / 2 for h in ordered]
    targets = []
    for index, ((member, state), head) in enumerate(zip(wanted, ordered)):
        if state is None:
            continue
        x0, y0, x1, y1 = head
        w = x1 - x0
        left = max(centres[index] - _HALF_WIDTH * w, 0)
        right = min(centres[index] + _HALF_WIDTH * w, size[0])
        # 隣の人の範囲に入らないよう、となりとの中間で止める
        if index > 0:
            left = max(left, (centres[index - 1] + centres[index]) / 2)
        if index + 1 < len(centres):
            right = min(right, (centres[index] + centres[index + 1]) / 2)
        box = (int(left), int(max(y0 - _ABOVE_HEAD * w, 0)), int(right), size[1])
        targets.append(Target(member, state, box))
    return targets, None


def target_mask(size: tuple[int, int], box: tuple[int, int, int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(_FEATHER)).point(lambda v: 255 if v > 40 else 0).convert("RGB")


def redraw_scene_tags(scene_tags: str, target: Target) -> str:
    """描き直しの作画タグ: コマの作画タグ(状態に反応するタグは除く)+ 状態の描き直しのタグ + 見た目(look の分だけ)。"""
    match = {_plain(t) for t in split_tags(target.state["match_tags"])}
    kept = ", ".join(t for t in split_tags(scene_tags) if _plain(t) not in match)
    look = target.state.get("look") or "none"
    character = target.character
    looks = {"none": "", "outfit": character.get("outfit_tags") or "", "full": character_prompt_tags(character)}
    return join_tags(kept, target.state["redraw_tags"], looks.get(look, ""))
