"""
ヘルプの記事(src/help/*.md)の点検: 先頭の項目、記事どうしのリンク、お知らせ(notices.py)が指す記事があるか。

実行方法:
  uv run pytest tests/test_help_articles.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HELP = _ROOT / "src" / "help"


def _articles() -> dict[str, str]:
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(_HELP.glob("*.md"))}


def _meta(text: str) -> dict[str, str]:
    match = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    assert match, "先頭に --- で囲んだ項目がありません"
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def test_every_article_has_title_feature_and_summary() -> None:
    articles = _articles()
    assert articles
    for slug, text in articles.items():
        meta = _meta(text)
        assert meta.get("title") and meta.get("feature") and meta.get("summary"), slug
        assert re.fullmatch(r"[a-z0-9-]+", slug), f"記事の名前は小文字・数字・- だけ: {slug}"


def test_links_between_articles_exist() -> None:
    articles = _articles()
    for slug, text in articles.items():
        for target in re.findall(r"\]\(/help/([^)#?\s]+)\)", text):
            assert target in articles, f"{slug} から無い記事 {target} へのリンク"


def test_notices_point_to_existing_articles() -> None:
    articles = _articles()
    source = (_ROOT / "src" / "python" / "notices.py").read_text(encoding="utf-8")
    slugs = set(re.findall(r'help="([^"]+)"', source))
    assert slugs
    missing = slugs - set(articles)
    assert not missing, f"お知らせが指す記事がありません: {missing}"
