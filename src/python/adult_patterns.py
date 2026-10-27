"""
R18 のパターン: 成人向けの作品で使うプレイ(体位・行為・構図のタグ)と、シナリオの型(導入から展開の流れ)。
DB(adult_patterns)に持ち、R18 のパターンのページで足す・直す・消す。

パターンには表現も持たせる: 描き文字(擬音)と、セリフの言い回しの例。漫画ドラフトの台本づくりで文章モデルに渡し、
取り込んだ R18 の作品の描き文字の読みの候補にも足す(場面のタグがプレイのタグと重なるコマ)。
プレイには参照画像を何枚でも付けられる。付けたときに判定モデルで構図・行為のタグを読み取り、使うたびに1枚ずつ
順に、そのタグをプレイのタグに足す(同じプレイでも描き方に幅が出る)。

使えるのは、登場するキャラが全員、年齢(18 歳以上)を入れた成人のときだけ(content_guard.is_defined_adult)。
未成年を思わせるタグ・語は、タグにも表現にも入れられず、参照画像も判定モデルで未成年の手がかりが出たら受け付けない。
"""

from __future__ import annotations

from typing import Any

from . import content_guard
from .character_sheet import minor_tags, split_tags

KINDS: dict[str, str] = {"play": "プレイ", "scenario": "シナリオの型"}

# はじめのパターン(初めて表を作るときだけ入れる)。どれも成人どうしの、合意のある関係
DEFAULTS: list[dict[str, str]] = [
    {"kind": "play", "name": "キス", "tags": "kiss, french kiss, saliva trail, closed eyes, hug",
     "sfx": "ちゅっ\nちゅぷ\nれろ", "lines": "んっ…\nもっと…", "description": ""},
    {"kind": "play", "name": "正常位", "tags": "sex, missionary, lying, on back, spread legs, vaginal, sweat",
     "sfx": "ぱんっ\nずちゅ\nぎしっ", "lines": "あっ、あっ…\nそこ…っ", "description": ""},
    {"kind": "play", "name": "騎乗位", "tags": "sex, cowgirl position, girl on top, straddling, vaginal, sweat",
     "sfx": "ぱちゅっ\nずぷっ\nぎしぎし", "lines": "動いちゃう…\n見ないで…っ", "description": ""},
    {"kind": "play", "name": "後背位", "tags": "sex, doggystyle, from behind, all fours, vaginal, sweat",
     "sfx": "ぱんっぱんっ\nずちゅ", "lines": "奥…っ\nだめ…っ", "description": ""},
    {"kind": "play", "name": "口での愛撫", "tags": "fellatio, oral, kneeling, looking up, saliva",
     "sfx": "じゅぽ\nちゅぱ\nれろ", "lines": "ん…む…\n気持ちいい?", "description": ""},
    {"kind": "play", "name": "抱き合ったまま", "tags": "sex, hug, mating press, face-to-face, vaginal, sweat",
     "sfx": "ぎゅっ\nずちゅ\nどくん", "lines": "離さないで…\n一緒に…っ", "description": ""},
    {"kind": "scenario", "name": "幼なじみと再会", "tags": "",
     "description": "社会人になった幼なじみと久しぶりに再会する → 昔話で距離が縮まる → 互いの気持ちに気づく → 合意のうえで一夜を過ごす → 翌朝の照れくさいやり取り",
     "sfx": "", "lines": "ずっと好きだった\n大人になったね"},
    {"kind": "scenario", "name": "残業のオフィス", "tags": "office, night",
     "description": "同じ部署の先輩と後輩(どちらも社会人)が残業で二人きりになる → 仕事の悩みを打ち明ける → 惹かれ合っていたことを確かめる → 合意のうえで関係を持つ → 何事もなかったように翌日を迎える",
     "sfx": "", "lines": "誰も来ないよ\n今だけ…"},
    {"kind": "scenario", "name": "新婚の夜", "tags": "bedroom, night",
     "description": "結婚したばかりの夫婦 → 新居での初めての夜にぎこちなくなる → 笑い合って緊張がほどける → 愛情をたしかめ合う → 寄り添って眠る",
     "sfx": "", "lines": "これからよろしくね\n恥ずかしい…"},
    {"kind": "scenario", "name": "温泉旅行", "tags": "onsen, yukata",
     "description": "恋人どうしの温泉旅行 → 浴衣で宿の夜を楽しむ → 雰囲気が高まる → 布団の上で愛し合う → 朝風呂で余韻にひたる",
     "sfx": "", "lines": "浴衣、似合ってる\n声、聞こえちゃう…"},
]

# 参照画像から読み取るタグの確率と、使わないタグ(人物の見た目・絵柄・画面の作り。プレイの描き方だけを取り出す)
_IMAGE_TAG_PROB = 0.45
_IMAGE_TAGS_MAX = 20
_SKIP_WORDS = {
    "hair", "eyes", "bangs", "sidelocks", "ahoge", "ponytail", "twintails", "braid", "skin", "mole", "freckles",
    "comic", "monochrome", "greyscale", "text", "speech", "bubble", "censored", "mosaic", "watermark", "signature",
    "translated", "artist", "username", "solo", "1girl", "1boy", "2girls", "2boys", "multiple", "highres",
}
# 判定モデルで未成年の手がかりとみなす確率(minor_guard と同じく、低めに拾って止める側に倒す)
_IMAGE_MINOR_PROB = 0.35


def problems(*texts: str) -> list[str]:
    """
    パターンに入れられない語: 未成年を示すタグ(成人の衣装の制服などは除く)と、本文の未成年を示す語
    (〇〇歳の 18 未満・中学生 など)。
    """
    text = "\n".join(texts)
    found = set(minor_tags(text, adult=True))
    found |= {t.strip() for t in split_tags(text.replace("\n", ",")) if content_guard.search("scene.minor_tags", t.strip())
              and not content_guard.is_costume(t.strip())}
    found |= set(content_guard.find("story.minor_words", text))
    return sorted(found)


def image_tags(scores: list[Any]) -> tuple[list[str], list[str]]:
    """
    参照画像の判定結果(image_tagger.TagScore)から、(プレイに足すタグ, 未成年の手がかり)。手がかりが1つでもあれば、
    その画像は受け付けない。
    """
    minors = sorted({
        s.tag for s in scores
        if s.probability >= _IMAGE_MINOR_PROB
        and (content_guard.search("scene.minor_tags", s.tag) or minor_tags(s.tag, adult=True))
        and not content_guard.is_costume(s.tag)
    })
    tags = [
        s.tag for s in sorted(scores, key=lambda s: -s.probability)
        if s.category == "general" and s.probability >= _IMAGE_TAG_PROB and not (set(s.tag.split()) & _SKIP_WORDS)
    ]
    return tags[:_IMAGE_TAGS_MAX], minors


def lines_of(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def play_tags(pattern: dict[str, Any], turn: int = 0) -> str:
    """プレイのタグ。参照画像があれば、turn 番目(順に回す)の画像から読み取ったタグを足す。"""
    images = [i for i in pattern.get("images") or [] if i.get("tags")]
    extra = images[turn % len(images)]["tags"] if images else ""
    return ", ".join(dict.fromkeys(split_tags(pattern.get("tags")) + split_tags(extra)))


def draft_notes(patterns: list[dict[str, Any]]) -> str:
    """漫画ドラフトの文章モデルに渡す補足(シナリオの流れ・使うプレイ・描き文字と言い回しの例)。"""
    parts = []
    for p in patterns:
        if p["kind"] == "scenario":
            parts.append(f"シナリオの型「{p['name']}」: {p['description']}")
        else:
            parts.append(f"プレイ「{p['name']}」(prompt_tags に入れるタグ: {p['tags']})")
        if lines_of(p.get("sfx", "")):
            parts.append(f"  描き文字(sfx)の例: {'、'.join(lines_of(p['sfx']))}")
        if lines_of(p.get("lines", "")):
            parts.append(f"  セリフの言い回しの例: {'、'.join(lines_of(p['lines']))}")
    return "\n".join(parts)


def sfx_vocabulary(patterns: list[dict[str, Any]], tags: str) -> list[str]:
    """場面のタグとタグが重なるプレイの描き文字(取り込んだ作品の描き文字の読みの候補に足す)。"""
    scene = {t.lower() for t in split_tags(tags)}
    words: list[str] = []
    for p in patterns:
        if p["kind"] == "play" and scene & {t.lower() for t in split_tags(play_tags(p))}:
            words.extend(lines_of(p.get("sfx", "")))
    return list(dict.fromkeys(words))
