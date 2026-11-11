"""
コマの絵の精度を測る(怪しいコマを1枚ずつ知らせる panel_check とは別に、全体でどれだけ指定どおりに描けたかを数字にする)。

判定モデル(image_tagger の WD Tagger)で各コマのタグを推定し、次の3つを集計する。Anlas は使わない。

1. 指定したタグの的中率を、種類ごと(髪・目などの容姿 / 服・小物 / ポーズ・動作 / 構図 / 場所・背景 / 表情 / 体型 /
   性的な描写 / その他)に出す。指定は作画タグ・動作・その場面の服装・キャラシートの容姿と体型。塗り・画質・光の当たり方
   のタグ(anime coloring, painterly など)は絵の中身ではないので数えない。
2. 出てはいけない物(シーンのネガティブ・absent_tags・状態で外したタグ・キャラシートのネガティブ)が描かれていないか。
3. 年齢区分(判定モデルの general / sensitive / questionable / explicit)を、物語・コマの意図と比べる。全年齢の物語に
   explicit のコマが無いか、性的な描写を指定したコマが実際にそう描かれたか。中身に関係なく同じ物差しで測る。

docs/panel_accuracy_jp.md を参照。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from . import vocabulary as vocab

# 描かれているとみなす確率(panel_check._PRESENT と同じ)と、出てはいけない物が描かれているとみなす確率(_UNWANTED と同じ)
PRESENT = 0.2
UNWANTED = 0.35

# 種類(dataset_variations.axis_of の軸)→ 表示名
CATEGORIES: dict[str, str] = {
    "appearance": "容姿(髪・目・顔)",
    "outfits": "服・小物",
    "poses": "ポーズ・動作",
    "framings": "構図",
    "locations": "場所・背景",
    "expressions": "表情",
    "body": "体型",
    "adult": "性的な描写",
    "other": "その他",
}

# 塗り・画質・光の当たり方のタグ(絵の中身ではないので、的中率にも、出てはいけない物にも数えない)
_RENDER = re.compile(
    r"\b(colou?ring|colou?rs?|shading|shaded|lineart|line art|outlines?|illustration|painterly|painting|realistic|"
    r"aesthetic|masterpiece|quality|resolution|lowres|highres|absurdres|detailed|detail|complexity|texture|pores|"
    r"subsurface|scattering|lighting|rim light|bloom|neon|lens flare|chromatic|aberration|saturat\w*|"
    r"style|sketch|sumi-e|ink wash|watercolor|anime|webtoon|cel|flat colou?r|matte|glossy|shiny skin|3d|photorealistic|"
    r"jpeg|artifacts?|blurry|bad (anatomy|hands|proportions)|extra (fingers|limbs|digits)|watermark|signature|"
    r"username|logo|text|letters?|typography|speech bubble|comic|panels?|border|frame|palette)\b"
)
# 人数・人の有無のタグ(人数は panel_check で別に見る)
_PEOPLE = re.compile(r"^(\d\+?(girl|boy|other)s?|multiple (girls|boys|others|people)|no humans|solo( focus)?)$")
# 「〜しない」の言い方(no makeup など)。判定モデルの語に無いことが多く、あっても意味が逆になる
_NEGATED = re.compile(r"^(no|without|not)\b")
# 年齢区分(判定モデルの rating)
RATINGS = ("general", "sensitive", "questionable", "explicit")


def is_render_tag(tag: str) -> bool:
    """塗り・画質・光の当たり方のタグか(絵の中身ではないもの)。"""
    return bool(_RENDER.search(tag.lower()))


def category_of(tag: str) -> str:
    """タグの種類(CATEGORIES のキー)。塗りのタグは "style"、人数は "skip"。"""
    from .dataset_variations import axis_of

    if is_render_tag(tag):
        return "style"
    axis = axis_of(tag)
    if axis in ("style", "skip"):
        return axis
    if axis == "other":
        words = set(tag.lower().replace("(", " ").replace(")", " ").split())
        if words & vocab.words("accuracy.expression_words"):
            return "expressions"
        if words & vocab.words("accuracy.makeup_words"):
            return "appearance"
        if words & vocab.words("accuracy.outfit_words"):
            return "outfits"
    return axis if axis in CATEGORIES else "other"


def _tags(*texts: str | None) -> list[str]:
    """プロンプトを、強調・抑制の書き方を外した小文字のタグの並びにする(重複なし。抑制したタグは除く)。"""
    from .dataset_variations import prompt_tags

    result: list[str] = []
    for text in texts:
        for tag in prompt_tags(text or ""):
            tag = tag.lower()
            if tag not in result:
                result.append(tag)
    return result


def _measurable(tag: str, vocabulary: set[str]) -> bool:
    return (
        tag in vocabulary and not _PEOPLE.match(tag) and not _NEGATED.match(tag) and not is_render_tag(tag)
        and tag != "looking at viewer"
    )


def expected_tags(scene: dict[str, Any], members: list[dict[str, Any]], vocabulary: set[str]) -> dict[str, str]:
    """
    そのコマで描かれるはずのタグ → 種類。作画タグ・動作・服装(その場面の服装があればそれ)・容姿・体型のうち、
    判定モデルで見分けられ、塗り・人数・「〜しない」でないもの。
    """
    texts = [scene.get("draft_prompt_tags")]
    for member in members:
        texts += [member.get("appearance_tags"), member.get("body_tags"), member.get("outfit_tags"), member.get("action_tags")]
    result: dict[str, str] = {}
    for tag in _tags(*texts):
        if _measurable(tag, vocabulary):
            category = category_of(tag)
            if category not in ("style", "skip"):
                result[tag] = category
    return result


def forbidden_tags(
    scene: dict[str, Any], members: list[dict[str, Any]], vocabulary: set[str], expected: dict[str, str] | None = None
) -> list[str]:
    """
    そのコマに出てはいけないタグ。シーンのネガティブ・absent_tags・状態で外したタグ・キャラシートのネガティブのうち、
    判定モデルで見分けられ、塗り・画質・人数でなく、描くはずのタグとぶつからないもの。
    """
    texts = [scene.get("negative_prompt"), scene.get("absent_tags")]
    for member in members:
        texts += [member.get("state_remove_tags"), member.get("negative_tags")]
    # 描くはずのタグに含まれるもの(キャラシートのネガティブは、その人だけのもの。栄子の glasses は、同じコマの
    # 先生の black-framed glasses とぶつかる)は除く
    drawn = _tags(scene.get("draft_prompt_tags"), *(
        m.get(k) for m in members for k in ("appearance_tags", "body_tags", "outfit_tags", "action_tags")
    ))
    wanted = set(expected or {})

    def conflicts(tag: str) -> bool:
        return tag in wanted or any(re.search(rf"\b{re.escape(tag)}\b", d) for d in drawn)

    return [t for t in _tags(*texts) if _measurable(t, vocabulary) and not conflicts(t)]


def rating_of(ratings: dict[str, float]) -> str | None:
    """判定モデルの年齢区分のうち、確率の最も高いもの。"""
    found = {k: v for k, v in ratings.items() if k in RATINGS}
    return max(found, key=found.__getitem__) if found else None


def wants_adult(scene: dict[str, Any], members: list[dict[str, Any]]) -> bool:
    """
    性的な描写を指定したコマか(作画タグ・動作に、性的なタグがある)。服装は見ない(入浴の裸など、全年齢の話でも
    服装に nude を入れることがあり、それだけでは性的な場面ではない)。
    """
    from . import content_guard

    texts = [scene.get("draft_prompt_tags") or "", *(m.get("action_tags") or "" for m in members)]
    return content_guard.is_sexual(", ".join(texts))


@dataclass
class PanelAccuracy:
    """1コマの測定結果。"""

    story_id: int
    scene_index: int
    expected: dict[str, str]
    hits: list[str]
    unwanted: list[tuple[str, float]]
    rating: str | None
    ratings: dict[str, float]
    wants_adult: bool
    rating_problem: str | None = None

    @property
    def missing(self) -> list[str]:
        return [t for t in self.expected if t not in self.hits]

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_id": self.story_id,
            "scene_index": self.scene_index,
            "checked": len(self.expected),
            "hit": len(self.hits),
            "missing": [{"tag": t, "category": self.expected[t]} for t in self.missing],
            "unwanted": [{"tag": t, "probability": round(p, 3)} for t, p in self.unwanted],
            "rating": self.rating,
            "ratings": {k: round(v, 3) for k, v in self.ratings.items()},
            "wants_adult": self.wants_adult,
            "rating_problem": self.rating_problem,
        }


def measure_panel(
    story_id: int,
    scene: dict[str, Any],
    members: list[dict[str, Any]],
    vocabulary: set[str],
    probabilities: dict[str, float],
    ratings: dict[str, float],
    story_adult: bool,
) -> PanelAccuracy:
    """
    1コマを測る。probabilities は一般タグ → 確率、ratings は年齢区分 → 確率。story_adult は成人向けの物語か。
    年齢区分の外れ: 全年齢の物語で explicit / 性的な描写を指定したのに general か sensitive。
    """
    expected = expected_tags(scene, members, vocabulary)
    hits = [t for t in expected if probabilities.get(t, 0.0) >= PRESENT]
    unwanted = [
        (t, probabilities[t]) for t in forbidden_tags(scene, members, vocabulary, expected)
        if probabilities.get(t, 0.0) >= UNWANTED
    ]
    rating = rating_of(ratings)
    adult = wants_adult(scene, members)
    problem = None
    if not story_adult and rating == "explicit":
        problem = "全年齢の物語なのに explicit と判定された"
    elif adult and rating in ("general", "sensitive"):
        problem = f"性的な描写を指定したのに {rating} と判定された(描写が描かれていない)"
    return PanelAccuracy(story_id, scene["scene_index"], expected, hits, unwanted, rating, ratings, adult, problem)


@dataclass
class AccuracyReport:
    """物語(複数可)の測定結果の集計。"""

    panels: list[PanelAccuracy] = field(default_factory=list)
    adult_stories: set[int] = field(default_factory=set)

    def categories(self) -> dict[str, dict[str, Any]]:
        counts: dict[str, list[int]] = {}
        for panel in self.panels:
            for tag, category in panel.expected.items():
                checked_hit = counts.setdefault(category, [0, 0])
                checked_hit[0] += 1
                checked_hit[1] += tag in panel.hits
        return {
            key: {"label": label, "checked": counts[key][0], "hit": counts[key][1], "rate": round(counts[key][1] / counts[key][0], 3)}
            for key, label in CATEGORIES.items() if key in counts
        }

    def tags(self, min_checked: int = 2) -> list[dict[str, Any]]:
        """タグごとの的中率(min_checked 回以上確かめたもの。外れの多い順)。"""
        counts: dict[str, list[Any]] = {}
        for panel in self.panels:
            for tag, category in panel.expected.items():
                row = counts.setdefault(tag, [category, 0, 0])
                row[1] += 1
                row[2] += tag in panel.hits
        rows = [
            {"tag": tag, "category": c, "checked": n, "hit": h, "rate": round(h / n, 3)}
            for tag, (c, n, h) in counts.items() if n >= min_checked
        ]
        return sorted(rows, key=lambda r: (r["rate"], -r["checked"], r["tag"]))

    def to_dict(self, include_panels: bool = True) -> dict[str, Any]:
        checked = sum(len(p.expected) for p in self.panels)
        hit = sum(len(p.hits) for p in self.panels)
        ratings = {r: 0 for r in RATINGS}
        for panel in self.panels:
            if panel.rating in ratings:
                ratings[panel.rating] += 1
        result: dict[str, Any] = {
            "panels_checked": len(self.panels),
            "tags_checked": checked,
            "tags_hit": hit,
            "rate": round(hit / checked, 3) if checked else None,
            "categories": self.categories(),
            "weakest_tags": self.tags()[:20],
            "unwanted": [
                {"story_id": p.story_id, "scene_index": p.scene_index, "tag": t, "probability": round(prob, 3)}
                for p in self.panels for t, prob in p.unwanted
            ],
            "ratings": ratings,
            "adult_stories": sorted(self.adult_stories),
            "rating_problems": [
                {"story_id": p.story_id, "scene_index": p.scene_index, "rating": p.rating, "problem": p.rating_problem}
                for p in self.panels if p.rating_problem
            ],
        }
        if include_panels:
            result["panels"] = [p.to_dict() for p in self.panels]
        return result
