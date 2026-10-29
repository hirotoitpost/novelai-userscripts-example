"""
画像からキャラを自動で作る(画像生成ページの生成画像・リバースプロンプトの画像)。

1. 人物ごとのプロンプト: NovelAI で生成した画像なら、埋め込まれたプロンプト(キャラごとのプロンプトがあれば
   1人ずつ)。埋め込みが無ければ、判定モデル(WD Tagger)で推定したタグ(二人以上なら頭の位置で人物ごとに分ける)
2. プロンプトをキャラシートの項目(容姿・服装・画風)に分ける(split_prompt。表情・ポーズ・場所は入れない)
3. 名前・人物像・年齢を文章モデルで考える(manga_similar.name_people)。キャラ名のタグ(既存の作品のキャラ)が
   あれば、そのキャラの名前にする
4. キャラ一覧に作る。1人なら、その画像を参照画像にし、シード値を基準シードにする

未成年と考えた人物・未成年を思わせるタグのある人物は、未成年にする(未成年キャラの安全対策が効く)。成人と考えた
人物も成人にはしない(絵から考えた年齢なので、キャラシートで確かめてから入れる)。
"""

from __future__ import annotations

import asyncio
import io
import re
from dataclasses import dataclass
from typing import Any

from PIL import Image

# 一度に作る人物の数(NovelAI のキャラごとのプロンプトの上限)
MAX_PEOPLE = 4
_GENDERS = ("1girl", "1boy", "1other")
# 一人ずつに分けられない人数のタグ(キャラごとのプロンプトが無いとき)
_MANY = re.compile(r"^(?:[2-6]\+?(?:girls|boys|others)|multiple (?:girls|boys|others))$")


@dataclass
class ImagePerson:
    prompt: str
    negative: str = ""
    # 判定モデルのキャラ名のタグ(既存の作品のキャラ)。分からなければ空
    character: str = ""


@dataclass
class ImagePeople:
    # metadata(埋め込まれたプロンプト)か tagger(判定モデルで推定)
    source: str
    prompt: str
    people: list[ImagePerson]
    seed: int | None = None


def _plain_tags(prompt: str) -> list[str]:
    from .character_sheet import _plain, split_tags

    return [p for p in (_plain(t) for t in split_tags(prompt.replace("\n", ","))) if p]


def _character_in(prompt: str, names: set[str]) -> str:
    return next((t for t in _plain_tags(prompt) if t in names), "")


def read_people(data: bytes) -> ImagePeople:
    """画像の人物ごとのプロンプト(埋め込み、無ければ判定モデル)。人物ごとに分けられなければ ValueError。"""
    from .image_tagger import (
        CHARACTER_THRESHOLD,
        build_prompt,
        character_names,
        embedded_prompt,
        split_characters,
        tag_image,
    )

    embedded = embedded_prompt(data)
    if embedded is not None:
        names = character_names()
        main = embedded["positive"]
        people = [
            ImagePerson(c["prompt"], c.get("negative") or "", _character_in(c["prompt"], names))
            for c in embedded["characters"]
            if str(c.get("prompt") or "").strip()
        ]
        if not people:
            if any(_MANY.match(t) for t in _plain_tags(main)):
                raise ValueError("二人以上の画像ですが、キャラごとのプロンプトが無いので、人物ごとに分けられません。")
            people = [ImagePerson(main, "", _character_in(main, names))]
        return ImagePeople("metadata", main, people[:MAX_PEOPLE], (embedded.get("settings") or {}).get("seed"))

    with Image.open(io.BytesIO(data)) as src:
        image = src.convert("RGB")
    scores = tag_image(image)
    guesses = split_characters(image)
    moved = {tag for g in guesses for tag in g.tags}
    main = build_prompt(scores, exclude=moved)
    if guesses:
        people = [ImagePerson(g.prompt, "", g.characters[0] if g.characters else "") for g in guesses]
    else:
        known = [s.tag for s in scores if s.category == "character" and s.probability >= CHARACTER_THRESHOLD]
        people = [ImagePerson(main, "", known[0] if known else "")]
    return ImagePeople("tagger", main, people[:MAX_PEOPLE])


# V4 のキャラごとのプロンプトでは、性別を girl・boy とだけ書くことが多い
_GENDER_WORDS = {"girl": "1girl", "boy": "1boy", "other": "1other"}


def _gender(prompt: str, main: str) -> str:
    """人物の性別(1girl・1boy・1other)。キャラのプロンプトに無ければ全体のプロンプトから。"""
    for tags in (_plain_tags(prompt), _plain_tags(main)):
        found = next((_GENDER_WORDS.get(t, t) for t in tags if t in _GENDERS or t in _GENDER_WORDS), "")
        if found:
            return found
    return "1girl"


def _without_gender_words(tags: str) -> str:
    from .character_sheet import _plain, split_tags

    return ", ".join(t for t in split_tags(tags) if _plain(t) not in _GENDER_WORDS)


async def create_from_image(api_key: str, data: bytes) -> dict[str, Any]:
    """画像からキャラを作り、{source, characters} を返す(characters は作ったキャラ)。"""
    from .character_sheet import join_tags, split_prompt
    from .db import get_connection, list_characters, save_character, update_character_sheet
    from .manga_similar import ADULT_AGE, Person, _is_minor, name_people

    # 判定モデルは数秒かかるので、イベントループを塞がないようにスレッドで
    found = await asyncio.to_thread(read_people, data)
    main = split_prompt(found.prompt)
    sheets = []
    for person in found.people:
        own = split_prompt(person.prompt)
        if len(found.people) == 1:
            # 1人なら、キャラのプロンプトに無い服装・容姿は全体のプロンプトから(服を全体に書くことが多い)
            own = {**own, **{k: join_tags(own[k], main[k]) for k in ("appearance", "outfit")}}
        gender = _gender(person.prompt, found.prompt)
        sheets.append(
            {
                "gender": gender,
                # 性別 → キャラ名 → そのほか(NovelAI の並び)
                "appearance": join_tags(gender, person.character, _without_gender_words(own["appearance"])),
                "outfit": own["outfit"],
                "negative": person.negative.strip(),
                "character": person.character,
            }
        )
    people = [
        Person(
            s["gender"], [t for t in _plain_tags(f"{s['appearance']}, {s['outfit']}") if t not in _GENDERS], 1, s["character"]
        )
        for s in sheets
    ]
    known = {p.character for p in found.people}
    setting = [t for t in _plain_tags(main["other"]) if t not in known][:12]
    named = await name_people(api_key, people, "イラスト(1枚絵のキャラ)", setting)

    created: list[dict[str, Any]] = []
    conn = get_connection()
    try:
        taken = {c["name"] for c in list_characters(conn)}
        for sheet, info in zip(sheets, named):
            name, suffix = info["name"], 2
            while name in taken:
                name = f"{info['name']}{suffix}"
                suffix += 1
            taken.add(name)
            appearance = sheet["appearance"]
            character = save_character(conn, name, appearance, f"{info['profile']}(画像から自動で作ったキャラ)")
            fields: dict[str, Any] = {"outfit_tags": sheet["outfit"], "style_tags": main["style"]}
            if sheet["negative"]:
                fields["negative_tags"] = sheet["negative"]
            age = info.get("age")
            minor_tags = [t for t in _plain_tags(f"{appearance}, {sheet['outfit']}") if _is_minor(t)]
            if (age is not None and age < ADULT_AGE) or minor_tags:
                fields.update({"is_minor": 1, "is_adult": 0, "age": age if age is not None and age < ADULT_AGE else None})
            created.append(update_character_sheet(conn, character["id"], fields) or character)
    finally:
        conn.close()

    if len(created) == 1:
        # 1人の画像は、そのまま参照画像にする(シード値があれば基準シードにも)
        from .routes.manga_v2 import register_reference

        register_reference(created[0]["id"], data, found.seed)
        conn = get_connection()
        try:
            from .db import get_character

            created[0] = get_character(conn, created[0]["id"]) or created[0]
        finally:
            conn.close()
    return {"source": found.source, "characters": created}
