"""
物語の登場人物をそろえる: 本文の人物を登録済みのキャラに対応させ、参照画像の無いキャラには
キャラシートから候補を生成して、一番キャラシートに近い1枚を自動で参照画像にする。
"""

from __future__ import annotations

import re
from typing import Any

from PIL import Image

# 名前の一部での対応に使う最短の長さ(1文字の名前は別人と重なりやすい)
_MIN_PARTIAL = 2


def _compact(name: str) -> str:
    return re.sub(r"[ 　]+", "", name)


def match_existing(name: str, aliases: list[str], characters: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    本文の人物(名前と呼び名)に当たる登録済みのキャラ。同じ名前があればそれ。無ければ、名前の一部
    (名だけ「栄子」→「矢野栄子」、姓だけ「九条」→「九条 ゆら」)で一人に絞れるときだけそのキャラ。
    二人以上に当てはまるときは決めない(別のキャラに付けてしまうより、新しく作る方がよい)。
    """
    names = [n for n in dict.fromkeys([name, *aliases]) if n]
    by_name = {_compact(c["name"]): c for c in characters}
    for n in names:
        if _compact(n) in by_name:
            return by_name[_compact(n)]
    for n in sorted(names, key=len, reverse=True):
        key = _compact(n)
        if len(key) < _MIN_PARTIAL:
            continue
        hits = [
            c
            for c in characters
            if (full := _compact(c["name"])) != key and (full.endswith(key) or full.startswith(key))
        ]
        # 名前の区切り(「九条 ゆら」の空白)があれば、区切りごとの一致も見る
        hits += [
            c
            for c in characters
            if c not in hits and key in [p for p in re.split(r"[ 　]+", c["name"]) if p]
        ]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


def sheet_tags(character: dict[str, Any]) -> list[str]:
    """参照画像の候補の採点に使うキャラシートのタグ(容姿・普段の服装)。"""
    from .character_sheet import character_prompt_tags

    return [t.strip() for t in character_prompt_tags(character).split(",") if t.strip()]


def score_candidate(image: Image.Image, tags: list[str]) -> float:
    """
    候補の絵がキャラシートのタグをどれだけ描けているか(0〜1)。リバースプロンプトの判定モデルで、
    キャラシートのタグのうち danbooru の語彙にあるものの確率を平均する(語彙に無い "adult man" などは見ない)。
    """
    from .image_tagger import tag_image, vocabulary

    vocab = vocabulary()
    known = [t for t in tags if t in vocab]
    if not known:
        return 0.0
    probs = {s.tag: s.probability for s in tag_image(image)}
    return sum(probs.get(t, 0.0) for t in known) / len(known)


async def auto_reference(api_key: str, character: dict[str, Any], count: int | None = None) -> dict[str, Any]:
    """
    キャラシートから参照画像の候補を count 枚生成し、score_candidate が一番高い1枚を参照画像と基準シードに
    登録する。戻り値は {path, seed, score, scores}。
    """
    from .routes.manga_v2 import _PROJECT_ROOT, make_reference_candidates, register_reference

    from . import app_settings

    tags = sheet_tags(character)
    count = count or int(app_settings.get("reference.auto_count"))
    candidates = await make_reference_candidates(api_key, character, count)
    scored = []
    for candidate in candidates:
        with Image.open(_PROJECT_ROOT / candidate["path"]) as image:
            scored.append((score_candidate(image.convert("RGB"), tags), candidate))
    best_score, best = max(scored, key=lambda pair: pair[0])
    path = register_reference(
        int(character["id"]), (_PROJECT_ROOT / best["path"]).read_bytes(), int(best["seed"])
    )
    return {"path": path, "seed": best["seed"], "score": best_score, "scores": [round(s, 3) for s, _ in scored]}
