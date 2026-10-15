"""
未成年キャラの安全対策: 性的な場面(コマ・ページ)に、成人でないキャラがいれば止める。

キャラの年齢は3つに分ける。
- 未成年: キャラシートで未成年と定義したか、未成年の手がかりがある(手がかりは成人の定義より優先する)
  - 手がかり: キャラシートの未成年を示すタグ(dataset.minor_tags)/ キャラのメモ・物語の本文とメモリで、そのキャラの
    名前と同じ文にある未成年を示す語(story.minor_words)
- 成人: キャラシートで成人と定義し、手がかりが無い
- 未定義: どちらでもない

性的な場面(場面のタグかキャラの動作のタグが性的)の扱い:
- 未成年がいれば止める(生成しない)
- 未定義のキャラがいる、またはキャラが割り当てられていなければ、safety.undefined_age に従う(止める / 全年齢で描く)。
  全年齢で描くときは性的なタグを外し、未成年対策のネガティブを足す
- 成人だけなら、そのまま

止めた・全年齢にしたことは guard_events に残し、管理ページで見られる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import content_guard
from .db import get_connection

MINOR, ADULT, UNDEFINED = "minor", "adult", "undefined"
OK, BLOCK, ALL_AGES = "ok", "block", "all_ages"
_SENTENCE_RE = re.compile(r"[^。！？!?\n]+")
_NAME_SPLIT_RE = re.compile(r"[ 　・]+")
_PAREN_RE = re.compile(r"[（(].*?[）)]")


def minor_words(text: str) -> list[str]:
    """本文の中の未成年を示す語(story.minor_words)。"""
    return content_guard.find("story.minor_words", text or "")


def _names(character: dict[str, Any]) -> list[str]:
    """本文での呼ばれ方: 名前全体・空白で区切った各部分・括弧を除いた名前(2文字以上)。"""
    name = str(character.get("name") or "").strip()
    plain = _PAREN_RE.sub("", name).strip()
    names = {name, plain, plain.replace(" ", "").replace("　", ""), *_NAME_SPLIT_RE.split(plain)}
    return sorted((n for n in names if len(n) >= 2), key=len, reverse=True)


def minor_cues(character: dict[str, Any], story_text: str = "") -> list[str]:
    """そのキャラが未成年だと思わせる手がかり(画面に出す説明)。無ければ空。"""
    from .character_sheet import character_minor_tags

    cues = [f"キャラシートのタグ: {tag}" for tag in character_minor_tags(character)]
    notes = str(character.get("notes") or "")
    cues += [f"キャラのメモ: {word}" for word in minor_words(notes)]
    names = _names(character)
    found: dict[str, str] = {}
    for sentence in _SENTENCE_RE.findall(story_text or ""):
        if any(n in sentence for n in names):
            for word in minor_words(sentence):
                found.setdefault(word, sentence.strip()[:60])
    cues += [f"本文「{sentence}」の「{word}」" for word, sentence in found.items()]
    return cues


@dataclass
class Age:
    state: str
    reasons: list[str] = field(default_factory=list)


def character_age(character: dict[str, Any], story_text: str = "") -> Age:
    cues = minor_cues(character, story_text)
    if character.get("is_minor"):
        return Age(MINOR, ["キャラシートで未成年と定義", *cues])
    if cues:
        return Age(MINOR, cues)
    if character.get("is_adult"):
        return Age(ADULT)
    return Age(UNDEFINED, ["キャラシートで年齢区分が未定義"])


@dataclass
class Decision:
    action: str
    reasons: list[str] = field(default_factory=list)


def is_sexual_scene(tags: str, members: list[dict[str, Any]]) -> bool:
    actions = ", ".join(str(m.get("action_tags") or m.get("scene_action_tags") or "") for m in members)
    return content_guard.is_sexual(f"{tags}, {actions}")


def decide(tags: str, members: list[dict[str, Any]], story_text: str = "") -> Decision:
    """性的な場面かと、出てくるキャラから、生成してよいか(ok / block / all_ages)と、その理由。"""
    if not is_sexual_scene(tags, members):
        return Decision(OK)
    minors: list[str] = []
    undefined: list[str] = []
    for member in members:
        age = character_age(member, story_text)
        if age.state == MINOR:
            minors.append(f"{member.get('name')}({'、'.join(age.reasons[:2])})")
        elif age.state == UNDEFINED:
            undefined.append(str(member.get("name")))
    if minors:
        return Decision(BLOCK, [f"性的な場面に未成年のキャラ: {', '.join(minors)}"])
    if not members:
        undefined_reason = "性的な場面にキャラが割り当てられていない(年齢が分からない)"
    elif undefined:
        undefined_reason = f"性的な場面に年齢区分が未定義のキャラ: {', '.join(undefined)}"
    else:
        return Decision(OK)
    policy = content_guard.get("safety.undefined_age")
    return Decision(ALL_AGES if policy == ALL_AGES else BLOCK, [undefined_reason])


# 判定モデルで、未成年を思わせるタグをこの確率以上なら手がかりにする(当たり外れがあるので、低めに拾って止める側に倒す)
_IMAGE_MINOR_THRESHOLD = 0.35
# 判定モデルの評価(rating)でこの確率以上なら、性的な画像とみなす
_IMAGE_SEXUAL_THRESHOLD = 0.5


def image_decision(prompt: str, scores: list[Any]) -> Decision:
    """
    ギャラリーの画像の画質を上げるとき(範囲・画像全体)の判定。画像にはキャラの年齢区分が無いので、プロンプトと
    判定モデルの結果(image_tagger.TagScore)だけで見る: 性的な画像に未成年を思わせるタグがあれば止める。
    """
    rating = {s.tag: s.probability for s in scores if s.category == "rating"}
    sexual = content_guard.is_sexual(prompt) or rating.get("explicit", 0) + rating.get("questionable", 0) >= _IMAGE_SEXUAL_THRESHOLD
    if not sexual:
        return Decision(OK)
    tags = [t.strip() for t in (prompt or "").split(",") if t.strip()]
    found = [t for t in tags if content_guard.search("scene.minor_tags", t)]
    found += [
        f"{s.tag}(判定 {s.probability:.2f})"
        for s in scores
        if s.category == "general" and s.probability >= _IMAGE_MINOR_THRESHOLD and content_guard.search("scene.minor_tags", s.tag)
    ]
    if found:
        return Decision(BLOCK, [f"性的な画像に未成年を思わせるタグ: {', '.join(dict.fromkeys(found))}"])
    return Decision(OK)


def all_ages_tags(tags: str) -> str:
    """性的なタグ(scene.sexual_tags・性器のタグ)を外したタグ。"""
    items = [t.strip() for t in (tags or "").split(",") if t.strip()]
    kept = [
        t for t in items
        if not content_guard.is_sexual(t)
        and not content_guard.search("scene.genital_tags", t)
        and t.lower() not in ("explicit", "uncensored")
    ]
    return ", ".join(kept)


def all_ages_members(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """キャラの動作のタグからも性的なタグを外したもの(コピー)。"""
    result = []
    for member in members:
        copy = dict(member)
        for key in ("action_tags", "scene_action_tags"):
            if copy.get(key):
                copy[key] = all_ages_tags(copy[key])
        result.append(copy)
    return result


def all_ages_negative() -> str:
    """全年齢で描くときに足すネガティブ(全年齢のネガティブと、未成年対策のネガティブ)。"""
    return ", ".join(
        t for t in (content_guard.text("general.safe_negative"), content_guard.text("scene.adult_safety_negative")) if t
    )


def record(action: str, place: str, reasons: list[str], story_id: int | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO guard_events (created_at, action, story_id, place, detail) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), action, story_id, place, " / ".join(reasons)),
        )
        conn.commit()
    finally:
        conn.close()


def recent_events(limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM guard_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def story_text(story: dict[str, Any] | None) -> str:
    """手がかりを探す本文: 取り込んだ原文(無ければシーンの本文)とメモリ。"""
    if not story:
        return ""
    body = story.get("raw_text") or "\n".join(
        str(s.get("novelai_text") or s.get("draft_text") or "") for s in story.get("scenes") or []
    )
    return f"{story.get('memory') or ''}\n{body}"
