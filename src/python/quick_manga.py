"""
おまかせ漫画: テーマ1行(とジャンル)から、登場人物の案 → 大枠シナリオ → 1話ずつ台本 → 物語 → コマの生成と合成まで、
1つの処理で作る(routes/quick_manga.py)。題材の候補も出す(外部の作品DBの今の傾向から)。

- 登場人物の案: 文章モデル(NovelAI の GLM)に、テーマに合う人物(名前・人物像・見た目のタグ)を考えさせる。
  見た目のタグは、容姿(顔・髪・目)・標準の体型・普段の服装に分けて、そのままキャラシートにする。
- 題材の候補: 小説家になろう(なろう小説API)と AniList(GraphQL)の、そのジャンルの今の上位作品のキーワード・タグを集め、
  それを手がかりに新しいテーマを考えさせる。特定の作品の筋は使わない(キーワードの傾向だけを渡す)。
  外部に問い合わせられなければ、ジャンルだけから考える。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

from . import content_guard
from .manga_draft import _ask_json, _clean_tags, _content_rule

# 登場人物の案の数の上限、題材の候補の数
MAX_CAST = 4
THEME_COUNT = 5
# 外部の傾向の取り置き(秒)。同じジャンルで何度も問い合わせない
_TREND_TTL = 6 * 3600
_trend_cache: dict[str, tuple[float, dict[str, Any]]] = {}
# AniList は、名乗り(User-Agent)の無い問い合わせを 403 で断る(2026-10 実機)
_USER_AGENT = "novelai-userscripts-example (personal manga drafting)"

# 漫画のジャンル → なろうの探し方と、AniList のジャンル。なろうは、ジャンルの番号(https://dev.syosetu.com/man/api/)より、
# その雰囲気の語をキーワードに持つ作品から集めた方が、雰囲気に合う(「ほのぼの」をヒューマンドラマのジャンル全体で探すと、
# 婚約破棄・ざまぁ が上位に来た。2026-10 実機)。語が無いものはジャンルの番号で探す
_GENRE_MAP: list[tuple[tuple[str, ...], int, str | None, str]] = [
    (("ラブコメ", "恋愛", "ラブ"), 102, "ラブコメ", "Romance"),
    (("ホラー", "怪談"), 305, "ホラー", "Horror"),
    (("ミステリー", "推理", "謎"), 304, "ミステリー", "Mystery"),
    (("ファンタジー", "魔法", "異世界"), 201, None, "Fantasy"),
    (("ほのぼの",), 302, "ほのぼの", "Slice of Life"),
    (("いい話", "ちょっといい", "感動"), 302, "ハートフル", "Slice of Life"),
    (("学園",), 307, "学園", "Comedy"),
    (("日常",), 307, "日常", "Slice of Life"),
    (("コメディ", "ギャグ", "ドタバタ"), 307, "コメディ", "Comedy"),
]
_DEFAULT_GENRE: tuple[int, str | None, str] = (307, "コメディ", "Comedy")

_CAST_SYSTEM = """あなたは漫画のキャラクターデザイナーです。渡されたテーマとジャンルの短編漫画に出る登場人物を{count}人考えます。
JSONだけを返してください(前置きや```は不要):
{"characters":[{"name":"フルネーム","profile":"人物像・口調・ほかの人との関係(60文字程度)","appearance":"英語のdanbooruタグ","body":"英語のdanbooruタグ","outfit":"英語のdanbooruタグ"}]}
- name は日本語の名前。登場人物どうしで、見た目と性格をはっきり変える
- appearance は人数と性別(1girl / 1boy)、髪の色・長さ・髪型、目の色、顔の特徴。最初に 1girl か 1boy を書く
- body は体型(slim, tall など。2〜3語)
- outfit はふだんの服装(3〜5語)
- 動物などの人でない登場人物は、appearance に animal と種類を書く{content_rule}"""

_THEME_SYSTEM = """あなたは漫画の編集者です。指定のジャンルで、4コマ漫画の短編にできる新しいテーマを{count}つ考えます。
JSONだけを返してください(前置きや```は不要):
{"themes":["テーマ(40〜70文字。だれが・どこで・何をして・どうなりそうか)"]}
- 手がかりのキーワードは、いま人気の作品の傾向です。組み合わせて使ってよいが、特定の作品の筋・人物・設定は使わない
- {count}つは、舞台・人物・笑いどころ(または見どころ)をはっきり変える
- 4コマを2〜4話で描ける大きさにする
- 日本語だけで書く(英語の語や記号を混ぜない){content_rule}"""


def _genre_keys(genre: str) -> tuple[int, str | None, str]:
    """(なろうのジャンルの番号, なろうで探す語, AniList のジャンル)"""
    for words, narou, word, anilist in _GENRE_MAP:
        if any(w in genre for w in words):
            return narou, word, anilist
    return _DEFAULT_GENRE


def _get_json(url: str, *, data: bytes | None = None) -> Any:
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=20) as res:
        return json.loads(res.read())


def narou_keywords(genre_id: int, word: str | None = None, limit: int = 50) -> list[str]:
    """
    なろう小説API: 週間上位の作品のキーワード(多い順)。word があれば、その語をキーワードに持つ作品から、無ければ
    そのジャンルの作品から集める。探した語そのものは除く。
    """
    params: dict[str, Any] = {"out": "json", "order": "weekly", "lim": limit, "of": "k"}
    params |= {"word": word, "keyword": 1} if word else {"genre": genre_id}
    data = _get_json(f"https://api.syosetu.com/novelapi/api/?{urllib.parse.urlencode(params)}")
    counts = Counter(w for item in data[1:] for w in (item.get("keyword") or "").split() if w and w != word)
    # 作品の注意書き・賞の名前・掲載の都合は、題材の手がかりにならない
    skip = ("R15", "残酷な描写", "書籍化", "コミカライズ", "大賞", "AI", "ネトコン", "ESN", "ＥＳＮ", "連載", "完結")
    return [w for w, _ in counts.most_common(40) if not any(s in w for s in skip)][:15]


def anilist_tags(genre: str, limit: int = 50) -> list[str]:
    """AniList: そのジャンルの今人気の全年齢の漫画のタグ(多い順。成人向けのタグは除く)。"""
    query = (
        "query ($genre: String, $limit: Int) { Page(perPage: $limit) { media(type: MANGA, sort: TRENDING_DESC, "
        "isAdult: false, genre: $genre) { tags { name rank isAdult } } } }"
    )
    body = json.dumps({"query": query, "variables": {"genre": genre, "limit": limit}}).encode()
    media = _get_json("https://graphql.anilist.co", data=body)["data"]["Page"]["media"]
    counts = Counter(t["name"] for m in media for t in m["tags"] if t["rank"] >= 60 and not t["isAdult"])
    return [name for name, _ in counts.most_common(15)]


def trends(genre: str) -> dict[str, Any]:
    """ジャンルの今の傾向(なろうのキーワード、AniList のタグ)。問い合わせられなかったものは errors に。"""
    narou_id, narou_word, anilist_genre = _genre_keys(genre)
    key = f"{narou_id}:{narou_word}:{anilist_genre}"
    cached = _trend_cache.get(key)
    if cached and time.time() - cached[0] < _TREND_TTL:
        return cached[1]
    result: dict[str, Any] = {"narou": [], "anilist": [], "errors": []}
    for name, fetch in (("narou", lambda: narou_keywords(narou_id, narou_word)), ("anilist", lambda: anilist_tags(anilist_genre))):
        try:
            result[name] = fetch()
        except Exception as exc:  # noqa: BLE001 外部が使えなくても、ジャンルだけから考えられる
            result["errors"].append(f"{name}: {exc}")
    if result["narou"] or result["anilist"]:
        _trend_cache[key] = (time.time(), result)
    return result


def theme_prompts(genre: str, found: dict[str, Any] | None) -> tuple[str, str]:
    system = _THEME_SYSTEM.replace("{count}", str(THEME_COUNT)).replace("{content_rule}", _content_rule())
    user = [f"ジャンル: {genre}"]
    if found and found.get("narou"):
        user.append("手がかり(日本のWeb小説の人気作のキーワード): " + "、".join(found["narou"]))
    if found and found.get("anilist"):
        user.append("手がかり(人気の漫画のタグ・英語): " + ", ".join(found["anilist"]))
    return system, "\n".join(user)


# 文章モデルが混ぜることのある、_で囲んだ英語の語(_slapstick_)と、中国語の字
_STRAY_WORD = re.compile(r"_[A-Za-z][A-Za-z ]*_|(?<![A-Za-z])[A-Z]{4,}(?![A-Za-z])")
_HANZI = str.maketrans({"话": "話", "们": "たち", "这": "この", "说": "説"})


def _clean_theme(text: str) -> str:
    return re.sub(r"\s+", " ", _STRAY_WORD.sub("", text).translate(_HANZI)).strip()


def parse_themes(data: Any) -> list[str]:
    themes = data.get("themes") if isinstance(data, dict) else None
    if not isinstance(themes, list):
        raise ValueError("テーマが読めません")
    result = [_clean_theme(str(t))[:120] for t in themes]
    result = [t for t in result if t]
    if not result:
        raise ValueError("テーマがありません")
    return result[:THEME_COUNT]


async def suggest_themes(api_key: str, genre: str, use_trends: bool) -> dict[str, Any]:
    """題材の候補と、手がかりにした傾向。"""
    import asyncio

    found = await asyncio.to_thread(trends, genre) if use_trends else None
    system, user = theme_prompts(genre, found)
    themes = parse_themes(await _ask_json(api_key, system, user, max_tokens=1200, temperature=0.9))
    return {"themes": themes, "trends": found or {"narou": [], "anilist": [], "errors": []}}


def cast_prompts(theme: str, genre: str, count: int) -> tuple[str, str]:
    system = _CAST_SYSTEM.replace("{count}", str(count)).replace("{content_rule}", _content_rule())
    return system, f"テーマ: {theme}\nジャンル: {genre}"


def _tags(value: Any) -> str:
    text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value or "")
    return content_guard.sanitize_scene_tags(_clean_tags(text), adult=False)


def parse_cast(data: Any, count: int) -> list[dict[str, str]]:
    """文章モデルの答えから、キャラシートにする登場人物の案。名前の無いもの・重なる名前は除く。"""
    raw = data.get("characters") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise ValueError("登場人物が読めません")
    cast: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:40]
        appearance = _tags(item.get("appearance"))
        if not name or not appearance or name in {c["name"] for c in cast}:
            continue
        # 人数のタグが無ければ、1人の絵として足す(キャラシートの容姿は1人分)
        if "animal" not in appearance and not any(t in appearance.split(", ") for t in ("1girl", "1boy", "1other")):
            appearance = f"1girl, {appearance}" if "girl" in appearance or "woman" in appearance else f"1other, {appearance}"
        cast.append(
            {
                "name": name,
                "notes": str(item.get("profile") or "").strip()[:300],
                "appearance_tags": appearance,
                "body_tags": _tags(item.get("body")),
                "outfit_tags": _tags(item.get("outfit")),
            }
        )
    if not cast:
        raise ValueError("登場人物がいません")
    return cast[:count]


async def propose_cast(api_key: str, theme: str, genre: str, count: int) -> list[dict[str, str]]:
    system, user = cast_prompts(theme, genre, count)
    last: Exception | None = None
    for _ in range(3):
        try:
            return parse_cast(await _ask_json(api_key, system, user, max_tokens=1500, temperature=0.8), count)
        except (ValueError, RuntimeError) as exc:
            last = exc
    raise RuntimeError(f"登場人物を考えられませんでした: {last}")


def unique_name(name: str, taken: set[str]) -> str:
    """登録済みのキャラと重ならない名前(重なれば 2, 3 … を足す)。"""
    if name not in taken:
        return name
    number = 2
    while f"{name}{number}" in taken:
        number += 1
    return f"{name}{number}"
