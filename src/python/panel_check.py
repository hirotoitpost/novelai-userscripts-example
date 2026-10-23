"""
台本と絵のずれを減らす(漫画のコマ)。

1. 生成の前: 台本の見直し(review_script)。セリフと話の筋から、そのコマで見えていないと話が通じない物・状態(小物、
   食べ物、透明・浮いているなど)を、作画タグに足す。台本の作画タグに肝心の物が入っていないと、絵は別の物になる
   (2026-10 実機: 「コーヒー寒天」のコマでパンケーキが描かれた。作画タグに寒天が無かった)。
2. 生成の後: 絵の確かめ(check_panel)。判定モデル(image_tagger の WD Tagger)で、作画タグのうちモデルが見分けられる
   ものが描かれているか、描く人数が合っているかを見る。外れたコマは描き直す(routes/manga_v2.py の check-panels)。
   判定モデルに無い語(「透明」など)は確かめられない。
   あわせて、会話のコマで人物がこちら(読者)を見ていないか(目線)と、そのコマに出てはいけない物(まだもらっていない
   小物など。シーンの absent_tags と、状態で外したタグ)が描かれていないか(続き具合)も見る。

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


# 会話のコマで、こちらを見ているとみなす確率(2026-10 実機: 0.5 以上のコマは見返しても、ほぼ全部カメラ目線だった)
_GAZE = 0.5
_VIEWER = "looking at viewer"
# こちらを見ていてよいコマ(作画タグ・動作にこれがあれば、目線は確かめない)
_GAZE_EXEMPT = re.compile(r"\b(looking at viewer|pov|selfie|eye contact with viewer|looking at camera|mirror)\b")
# 出てはいけない物が描かれているとみなす確率(描かれている物を見落とすより、余計な描き直しを減らす側に寄せる)
_UNWANTED = 0.35


def is_conversation(has_dialogue: bool, people: int, tags: str) -> bool:
    """
    目線を確かめるコマか。セリフがあって人物が描かれるコマ(相手が別のコマにいる1人のコマも含む)。
    作画タグ・動作で、こちらを見ること(looking at viewer / pov / 鏡・自撮り)を決めてあるコマは除く。
    """
    return has_dialogue and people >= 1 and not _GAZE_EXEMPT.search(tags.lower())


def unwanted_tags(absent: str, removed: list[str], vocabulary: set[str]) -> list[str]:
    """
    そのコマに出てはいけないもの(判定モデルで確かめられるものだけ)。シーンの「出てはいけない物」と、登場人物の状態で
    外したタグ(髪を下ろす → シュシュ など)。体型の語(slim など)は、状態で外しても判定が当てにならないので除く。
    """
    from .character_sheet import BODY_SHAPE_WORDS

    tags = [t.lower() for t in _clean_tags(", ".join([absent, *removed])).split(", ") if t]
    return [
        t for t in dict.fromkeys(tags)
        if t in vocabulary and t != _VIEWER and not _SKIP.match(t) and not _PEOPLE.match(t)
        and not (set(t.split()) & BODY_SHAPE_WORDS)
    ]


@dataclass
class PanelCheck:
    """
    1コマの確かめの結果。score は確かめたタグのうち描かれていた割合(確かめるものが無ければ 1)。
    gaze_ok は会話のコマでこちらを見ていないか、unwanted は出てはいけないのに描かれていたもの。
    """

    score: float
    people_ok: bool
    missing: list[str] = field(default_factory=list)
    people_seen: int | None = None
    gaze_ok: bool = True
    unwanted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.people_ok and self.score >= 0.5 and self.gaze_ok and not self.unwanted

    def key(self) -> tuple[bool, bool, int, float]:
        """よい順に並べるための値(人数 → 出てはいけない物 → 目線 → 描かれたタグの割合の順に見る)。"""
        return (self.people_ok, not self.unwanted, self.gaze_ok, self.score)

    def problems(self) -> list[str]:
        """そろわなかったものの説明(描かれなかったタグ、目線、出てはいけない物)。"""
        return [
            *self.missing,
            *([] if self.gaze_ok else ["目線(こちらを見ている)"]),
            *(f"出てはいけない: {t}" for t in self.unwanted),
        ]


def check_panel(
    probabilities: dict[str, float],
    expected: list[str],
    people: int,
    conversation: bool = False,
    absent: list[str] | None = None,
) -> PanelCheck:
    """
    判定モデルの結果(タグ → 確率)で、1コマを確かめる。people は描くはずの人数(台本の登場人物の数)。
    人数は、判定モデルの 1girl / 2boys などから数える。人がいないはずのコマに人が描かれた(または逆)ときと、
    2人以上のはずが1人しか描かれていないときを外れとみなす(3人以上の数え分けは当てにならないので見ない)。
    conversation(会話のコマ)なら、こちらを見ている(looking at viewer)ときを外れにする。absent は出てはいけないタグ。
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
    gaze_ok = not conversation or probabilities.get(_VIEWER, 0.0) < _GAZE
    unwanted = [t for t in absent or [] if probabilities.get(t, 0.0) >= _UNWANTED]
    return PanelCheck(score, people_ok, [t for t in expected if t not in present], seen, gaze_ok, unwanted)


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
    目線が外れたら、1人のコマは横・斜めを向かせ、2人以上は見つめ合わせて、こちらを見るのを抑える。2回目は横顔にする。
    出てはいけない物は抑える(-1::タグ::、2回目は -2)。
    """
    weight = REDRAW_WEIGHTS[min(attempt, len(REDRAW_WEIGHTS)) - 1]
    tags = [f"{weight:g}::{t}::" for t in check.missing]
    if not check.people_ok:
        if people == 0:
            tags += ["no humans", "-1::1girl::", "-1::1boy::"]
        elif people >= 2:
            tags.append(f"{weight:g}::multiple people::")
    if not check.gaze_ok:
        if people >= 2:
            tags += ["looking at each other", "eye contact", "from side" if attempt >= 2 else "three-quarter view"]
        else:
            tags += ["looking to the side", "profile" if attempt >= 2 else "three-quarter view"]
        tags.append(f"-{weight:g}::{_VIEWER}::")
    tags += [f"-{attempt}::{t}::" for t in check.unwanted]
    return tags
