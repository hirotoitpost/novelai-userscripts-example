"""
作品・画像の「弱い関連」(似ている度合い)の計算。保存せず、表示のたびに計算する。

保存しないのは、作品や画像が増えるほど組み合わせが爆発的に増え、タグや登場人物を直しても
古い値が残ってしまうため。数百件なら毎回計算しても十分に速い。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

# どの作品・画像にも付きやすく、似ている手がかりにならないタグ。珍しさ(IDF)でも自然に軽くなるが、
# 画風・品質の指定は件数が少ないうちは珍しく見えてしまうので、最初から外す。
_IGNORED_TAGS = {
    "masterpiece",
    "best quality",
    "very aesthetic",
    "absurdres",
    "high complexity",
    "medium complexity",
    "low complexity",
    "ultra complexity",
    "manga style",
    "monochrome",
    "greyscale",
    "screentone",
    "no text",
    "highres",
    "nsfw",
}
# 「1.2::tag::」(強調の重み)、{tag} [tag] (tag:1.2) などの書き方を外して、タグの語だけにする
_WEIGHT_PREFIX_RE = re.compile(r"^-?\d+(\.\d+)?::")
_BRACKETS_RE = re.compile(r"[{}\[\]()]")
_TRAILING_WEIGHT_RE = re.compile(r":-?\d+(\.\d+)?$")


def normalize_tags(text: str | None) -> set[str]:
    tags = set()
    for raw in (text or "").split(","):
        tag = _BRACKETS_RE.sub(
            "", _WEIGHT_PREFIX_RE.sub("", raw.strip().lower())
        ).strip()
        tag = _TRAILING_WEIGHT_RE.sub("", tag).rstrip(":").strip()
        if tag and tag not in _IGNORED_TAGS and len(tag) <= 60:
            tags.add(tag)
    return tags


def title_bigrams(title: str) -> set[str]:
    """題名の2文字ずつの組(日本語は単語に区切らなくても、共通の語がおおよそ拾える)。"""
    text = re.sub(r"[\s\W_]+", "", title.lower())
    return {text[i : i + 2] for i in range(len(text) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def idf_weights(tag_sets: Iterable[set[str]]) -> dict[str, float]:
    """タグの珍しさ。多くの作品に付いているタグほど軽い(全部に付いていれば0)。"""
    sets = list(tag_sets)
    df = Counter(tag for tags in sets for tag in tags)
    n = len(sets)
    return {tag: math.log(n / count) for tag, count in df.items()}


def _days_between(a: str, b: str) -> float | None:
    try:
        return (
            abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds())
            / 86400
        )
    except ValueError:
        return None


@dataclass
class Item:
    """似ている度合いを比べる対象(作品か画像)。"""

    key: str
    created_at: str
    tags: set[str] = field(default_factory=set)
    characters: dict[int, str] = field(default_factory=dict)
    authors: dict[int, str] = field(default_factory=dict)
    title: str = ""
    # 画像用: 同じ物語の画像どうしは似ている
    story_id: int | None = None


@dataclass
class Match:
    key: str
    score: float
    reasons: list[str]


# 重み。登場人物・作者は「同じ世界の話」の強い手がかり、タグは中くらい、日付は同点の差を付ける程度。
_W_CHARACTER = 3.0
_W_CHARACTER_MAX = 6.0
_W_AUTHOR = 4.0
_W_TAGS = 4.0
_W_TITLE = 3.0
_W_SAME_STORY = 2.0
_W_DATE_SAME_DAY = 0.6
_W_DATE_SAME_WEEK = 0.3
# これより低いものは「似ている」と出さない
MIN_SCORE = 1.0
_TAGS_SHOWN = 4


def score(target: Item, other: Item, idf: dict[str, float]) -> Match:
    reasons: list[str] = []
    total = 0.0

    shared_characters = target.characters.keys() & other.characters.keys()
    if shared_characters:
        total += min(_W_CHARACTER * len(shared_characters), _W_CHARACTER_MAX)
        names = sorted(target.characters[c] for c in shared_characters)
        reasons.append(
            f"登場人物: {'、'.join(names[:3])}{' ほか' if len(names) > 3 else ''}"
        )

    shared_authors = target.authors.keys() & other.authors.keys()
    if shared_authors:
        total += _W_AUTHOR
        reasons.append(
            f"同じ作者: {'、'.join(sorted(target.authors[a] for a in shared_authors))}"
        )

    shared_tags = target.tags & other.tags
    if shared_tags:
        # 共通のタグの珍しさの合計を、両方のタグの珍しさの合計で割る(重み付きの Jaccard)
        union = target.tags | other.tags
        weight = sum(idf.get(t, 0.0) for t in shared_tags) / (
            sum(idf.get(t, 0.0) for t in union) or 1.0
        )
        if weight > 0:
            total += _W_TAGS * weight
            top = sorted(shared_tags, key=lambda t: idf.get(t, 0.0), reverse=True)[
                :_TAGS_SHOWN
            ]
            if idf.get(top[0], 0.0) > 0:
                reasons.append(f"共通のタグ: {', '.join(top)}")

    title = jaccard(title_bigrams(target.title), title_bigrams(other.title))
    if title >= 0.3:
        total += _W_TITLE * title
        reasons.append("題名が同じ" if title >= 0.95 else "題名が似ている")

    if target.story_id is not None and target.story_id == other.story_id:
        total += _W_SAME_STORY
        reasons.append("同じ物語")

    days = _days_between(target.created_at, other.created_at)
    if days is not None and days <= 1:
        total += _W_DATE_SAME_DAY
        reasons.append("作成日が近い")
    elif days is not None and days <= 7:
        total += _W_DATE_SAME_WEEK

    return Match(other.key, round(total, 3), reasons)


def rank(
    target: Item, others: Iterable[Item], idf: dict[str, float], limit: int
) -> list[Match]:
    """target に似ているものを、似ている順に limit 件。MIN_SCORE 未満は出さない。"""
    matches = [score(target, other, idf) for other in others if other.key != target.key]
    matches = [m for m in matches if m.score >= MIN_SCORE and m.reasons]
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]
