"""
台本と絵のずれを減らす(漫画のコマ)。

1. 生成の前: 台本の見直し(review_script)。セリフと話の筋から、そのコマで見えていないと話が通じない物・状態(小物、
   食べ物、透明・浮いているなど)を、作画タグに足す。台本の作画タグに肝心の物が入っていないと、絵は別の物になる
   (2026-10 実機: 「コーヒー寒天」のコマでパンケーキが描かれた。作画タグに寒天が無かった)。
2. 生成の後: 絵の確かめ(check_panel)。判定モデル(image_tagger の WD Tagger)で、作画タグのうちモデルが見分けられる
   ものが描かれているか、描く人数が合っているかを見る。外れたコマは描き直す(routes/manga_v2.py の check-panels)。
   判定モデルに無い語(「透明」など)は確かめられない。

docs/trials/2026-10-14_panel-check.md を参照。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .manga_draft import _ask_json, _clean_tags

# 描かれているとみなす確率と、点数で判断するのに要る、確かめるタグの数
_PRESENT = 0.2
_MIN_CHECKED = 2
# 確かめに使わないタグ(絵柄・構図・時間帯など、描かれていても判定が当てにならないもの)
_SKIP = re.compile(
    r"^(manga style|monochrome|greyscale|screentone|very aesthetic|masterpiece|best quality|.*complexity|"
    r"close-up|upper body|full body|cowboy shot|portrait|from (side|above|below|behind)|dutch angle|pov|"
    r"day|daytime|night|evening|morning|afternoon|indoors|outdoors|solo|male focus|female focus)$"
)
# 人数のタグ → 人数
_PEOPLE = re.compile(r"^(\d)(girl|boy|other)s?$")
# 判定モデルが返す人数のタグ
_COUNTS = {"1girl": (1, 0), "2girls": (2, 0), "3girls": (3, 0), "1boy": (0, 1), "2boys": (0, 2), "3boys": (0, 3)}


def tagger_vocabulary() -> set[str]:
    """判定モデルが見分けられる一般タグ(_ は空白に)。"""
    from .image_tagger import _GENERAL, _get_session

    _, tags = _get_session()
    return {name.replace("_", " ") for name, category in tags if category == _GENERAL}


def checkable_tags(
    prompt_tags: str, actions: list[str], vocabulary: set[str], skip: set[str] | None = None
) -> list[str]:
    """
    作画タグ・動作のタグのうち、判定モデルで確かめられるもの。人数・絵柄・構図・時間帯と、表情(驚き・得意げ など。
    判定モデルの当たり外れが大きく、確かめると正しい絵まで外れにした)は除く。
    """
    from .dataset_variations import axis_of

    tags = [t for t in _clean_tags(", ".join([prompt_tags, *actions])).split(", ") if t]
    return [
        t for t in dict.fromkeys(t.lower() for t in tags)
        if t in vocabulary and not _SKIP.match(t) and not _PEOPLE.match(t) and axis_of(t) != "expressions"
        and t not in (skip or set())
    ]


def is_person(appearance: str) -> bool:
    """人として数えるキャラか(動物などは、判定モデルの 1girl / 1boy に出ないので数えない)。"""
    words = set(re.split(r"[,\s]+", (appearance or "").lower()))
    return "animal" not in words


@dataclass
class PanelCheck:
    """1コマの確かめの結果。score は確かめたタグのうち描かれていた割合(確かめるものが無ければ 1)。"""

    score: float
    people_ok: bool
    missing: list[str] = field(default_factory=list)
    people_seen: int | None = None

    @property
    def ok(self) -> bool:
        return self.people_ok and self.score >= 0.5

    def key(self) -> tuple[bool, float]:
        """よい順に並べるための値(人数が合っていることを先に見る)。"""
        return (self.people_ok, self.score)


def check_panel(probabilities: dict[str, float], expected: list[str], people: int) -> PanelCheck:
    """
    判定モデルの結果(タグ → 確率)で、1コマを確かめる。people は描くはずの人数(台本の登場人物の数)。
    人数は、判定モデルの 1girl / 2boys などから数える。人がいないはずのコマに人が描かれた(または逆)ときと、
    2人以上のはずが1人しか描かれていないときを外れとみなす(3人以上の数え分けは当てにならないので見ない)。
    """
    present = [t for t in expected if probabilities.get(t, 0.0) >= _PRESENT]
    # 確かめるタグが1つだけのときは、その1つで外れにしない(場所の語などは、描かれていても判定が低いことがある)
    score = len(present) / len(expected) if len(expected) >= _MIN_CHECKED else 1.0
    girls = max((g for tag, (g, _) in _COUNTS.items() if probabilities.get(tag, 0.0) >= 0.5), default=0)
    boys = max((b for tag, (_, b) in _COUNTS.items() if probabilities.get(tag, 0.0) >= 0.5), default=0)
    seen = girls + boys
    if probabilities.get("multiple girls", 0.0) >= 0.5 or probabilities.get("multiple boys", 0.0) >= 0.5:
        seen = max(seen, 2)
    if people == 0:
        people_ok = seen == 0 or probabilities.get("no humans", 0.0) >= 0.5
    elif people == 1:
        people_ok = seen >= 1
    else:
        people_ok = seen >= 2
    return PanelCheck(score, people_ok, [t for t in expected if t not in present], seen)


def tag_probabilities(image: Any) -> dict[str, float]:
    from .image_tagger import tag_image

    return {s.tag: s.probability for s in tag_image(image) if s.category == "general"}


# ---- 生成の前: 台本の見直し ----

_REVIEW_SYSTEM = """あなたは漫画の作画監督です。台本の各コマについて、セリフと話の筋から「そのコマの絵に見えていないと話が通じないもの」を探し、
作画タグに足す英語のdanbooruタグを決めてください。JSONだけを返してください(前置きや```は不要):
{"panels":[{"add_tags":"英語のdanbooruタグ"}]}
- panels は渡されたコマと同じ数・同じ順
- 足すのは、セリフや筋に出てくる物(食べ物・道具・小物)、場所、状態(浮いている、透明、ずぶ濡れ など)、人物の目立つ動作だけ
- 絵にしにくい状態は、絵で描ける言い方に言い換えて足す(例: 透明になった → translucent body, transparent, empty clothes /
  浮いている → floating, levitation / 縮んだ → chibi ではなく tiny, minigirl など)
- すでに作画タグにあるものは書かない。足すものが無ければ空文字
- 人物の髪・目・服装は書かない(別に指定する)。1コマに足すのは5個まで"""


def review_prompts(episode: str, panels: list[dict[str, Any]]) -> tuple[str, str]:
    rows = [f"この話の筋: {episode}"]
    for index, panel in enumerate(panels, start=1):
        lines = " / ".join(f"{line.get('speaker') or '?'}「{line.get('text')}」" for line in panel.get("lines") or [])
        rows.append(
            f"{index}コマ目: 人物 {', '.join(panel.get('characters') or []) or 'なし'} | セリフ {lines or 'なし'} | "
            f"効果音 {', '.join(panel.get('sfx') or []) or 'なし'} | 作画タグ {panel.get('prompt_tags') or 'なし'}"
        )
    return _REVIEW_SYSTEM, "\n".join(rows)


_MAX_ADDED = 5


def apply_review(data: Any, panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """見直しの答えを台本に入れる(作画タグに足す)。コマ数が合わなければ ValueError。足した内容は fixes に書く。"""
    from . import content_guard

    raw = data.get("panels") if isinstance(data, dict) else None
    if not isinstance(raw, list) or len(raw) < len(panels):
        raise ValueError("コマ数が合いません")
    result = []
    for panel, item in zip(panels, raw):
        add = item.get("add_tags") if isinstance(item, dict) else ""
        add = ", ".join(add) if isinstance(add, list) else str(add or "")
        current = [t.lower() for t in _clean_tags(panel.get("prompt_tags") or "").split(", ") if t]
        new = [
            t for t in content_guard.sanitize_scene_tags(_clean_tags(add), adult=False).split(", ")
            if t and t.lower() not in current and not _PEOPLE.match(t.lower())
        ][:_MAX_ADDED]
        updated = dict(panel)
        if new:
            updated["prompt_tags"] = _clean_tags(", ".join([panel.get("prompt_tags") or "", *new]))
            updated["fixes"] = [*(panel.get("fixes") or []), f"見直しで作画タグに足した: {', '.join(new)}"]
        result.append(updated)
    return result


async def review_script(api_key: str, episode: str, panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1話分の台本を見直して、作画タグを足す。うまく答えが得られなければ(つながらないときも)、元の台本のまま。"""
    system, user = review_prompts(episode, panels)
    for _ in range(2):
        try:
            return apply_review(await _ask_json(api_key, system, user, max_tokens=1200, temperature=0.2), panels)
        except (ValueError, RuntimeError, httpx.HTTPError):
            continue
    return panels


# ---- 描き直しのプロンプト ----

# 描き直しで、描かれなかったタグを強める重み(1回目, 2回目 …)。強めすぎると絵が崩れるので、2 までにする
REDRAW_WEIGHTS = (1.5, 2.0)


def redraw_tags(check: PanelCheck, people: int, attempt: int) -> list[str]:
    """
    描き直しで作画タグに足すもの(NovelAI の強調・抑制の書き方)。描かれなかったタグは「重み::タグ::」で強め、
    人がいないはずのコマに人が描かれたときは人を抑える(-1::1girl:: など)。人数が足りないときは複数人を強める。
    """
    weight = REDRAW_WEIGHTS[min(attempt, len(REDRAW_WEIGHTS)) - 1]
    tags = [f"{weight:g}::{t}::" for t in check.missing]
    if not check.people_ok:
        if people == 0:
            tags += ["no humans", "-1::1girl::", "-1::1boy::"]
        elif people >= 2:
            tags.append(f"{weight:g}::multiple people::")
    return tags
