"""
本文に書ける台本の書き方(話し手と効果音を、地の文から推定させずに書く)。

    みお：「結構、降ってるね」      ← みおのセリフ(吹き出しのしっぽは、みおへ向ける)
    ゆら：（ガタッ、ドキドキ）      ← 効果音・オノマトペ・描き文字の候補(このシーンの描き文字になる)

行頭の「名前：」(全角・半角のコロン)の直後が「」ならセリフ、（）なら効果音の候補。（）の中は 、 , で区切る。
名前の付かない1行まるごとの（…）は、これまでどおり心の声。
"""

from __future__ import annotations

import re

# 名前: 空白・括弧・コロン・句読点を含まない 1〜20 字
_NAME = r"[^\s「」『』（）()：:。、！？!?]{1,20}"
# 行頭の「名前：」で、直後にセリフか効果音の括弧が続くもの
_PREFIX_RE = re.compile(rf"^[ 　]*({_NAME})[ 　]*[：:][ 　]*(?=[「『（(])", re.MULTILINE)
# 1行まるごとの「名前：（…）」
_SFX_LINE_RE = re.compile(rf"^[ 　]*({_NAME})[ 　]*[：:][ 　]*[（(]([^）)\n]+)[）)][ 　]*$", re.MULTILINE)
_SFX_SPLIT_RE = re.compile(r"[、,，／/]")


def speaker_before(text: str, start: int) -> str | None:
    """text[start] の括弧(セリフ)の前が、その行の頭からの「名前：」だけなら、その名前。"""
    line_start = text.rfind("\n", 0, start) + 1
    match = _PREFIX_RE.match(text, line_start)
    return match.group(1) if match and match.end() == start else None


def script_sfx(text: str) -> list[str]:
    """「名前：（…）」の行の効果音の候補(書いた順、重なりは1つに)。"""
    words = [w.strip() for m in _SFX_LINE_RE.finditer(text) for w in _SFX_SPLIT_RE.split(m.group(2))]
    return list(dict.fromkeys(w for w in words if w))


def script_names(text: str) -> list[str]:
    """台本の書き方で話し手として書かれた名前(出てきた順)。"""
    return list(dict.fromkeys(m.group(1) for m in _PREFIX_RE.finditer(text)))
