"""
安全の最低線(SafetyFloor)。成人として扱う年齢の下限と、成人の衣装の例外にしない語(子どもの体・年齢・身分を表す語)。

値はコード(このファイル)にだけ置き、DB・画面・API からは変えられない。使う側は get_floor() で注入された最低線を読む
(定数を直接読まない)。開発管理者のページで編集できるのはユーザー定義の分(content_guard の safety.never_costume_tags
など)で、実際に効く値は「最低線 + ユーザー定義」になる。ユーザー定義で最低線を外したり、年齢を下げたりはできない。

差し替え(use_floor)はテスト用。差し替えた最低線も、コードの最低線より緩いもの(年齢が低い・語が欠けている)は受け付けない。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol


class SafetyFloor(Protocol):
    """安全の最低線。"""

    @property
    def adult_age(self) -> int:
        """この年齢から成人として扱う。"""
        ...

    @property
    def never_costume_patterns(self) -> tuple[str, ...]:
        """成人の衣装の例外にしない語(正規表現。単語として照合する)。"""
        ...

    def never_costume(self, tag: str) -> bool:
        """成人の衣装の例外にしない語に当たるか。"""
        ...


_CODE_ADULT_AGE = 18
# 衣装の例外にしない語。成人の年齢を入れても、組み合わせで成人を表しても、これに当たるタグは未成年の手がかりのまま
# (絵が子どもの体になるため)
_CODE_NEVER_COSTUME_PATTERNS: tuple[str, ...] = (
    "loli(ta)?", "shota", "child(ren)?", "kids?", "toddler", "bab(y|ies)", "infant", "little (girl|boy)s?",
    "young (girl|boy)s?", "underage", "minor", "teen(age|ager)?s?", "petite child", "elementary", "kindergarten",
    "randoseru", "aged down", "jk", "joshi ?kousei", "school ?(girl|boy)s?", "students?", "(high|middle) school",
)


@dataclass(frozen=True)
class CodeSafetyFloor:
    """コードに置いた最低線(本番で注入するのはこれだけ)。"""

    adult_age: int = _CODE_ADULT_AGE
    never_costume_patterns: tuple[str, ...] = _CODE_NEVER_COSTUME_PATTERNS
    _matcher: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        pattern = re.compile(r"\b(" + "|".join(self.never_costume_patterns) + r")\b", re.IGNORECASE)
        object.__setattr__(self, "_matcher", pattern)

    def never_costume(self, tag: str) -> bool:
        return bool(self._matcher.search(tag or ""))


_CODE_FLOOR = CodeSafetyFloor()
_floor: SafetyFloor = _CODE_FLOOR


def get_floor() -> SafetyFloor:
    """今注入されている最低線。"""
    return _floor


def _check_not_looser(floor: SafetyFloor) -> None:
    if floor.adult_age < _CODE_FLOOR.adult_age:
        raise ValueError(f"成人の年齢は {_CODE_FLOOR.adult_age} より下げられません")
    missing = set(_CODE_FLOOR.never_costume_patterns) - set(floor.never_costume_patterns)
    if missing:
        raise ValueError(f"最低線の語は外せません: {', '.join(sorted(missing))}")


@contextmanager
def use_floor(floor: SafetyFloor) -> Iterator[SafetyFloor]:
    """テストで最低線を差し替える(コードの最低線より厳しいものだけ)。抜けると元に戻す。"""
    global _floor
    _check_not_looser(floor)
    previous = _floor
    _floor = floor
    try:
        yield floor
    finally:
        _floor = previous


def is_adult_age(age: int | float | None) -> bool:
    """年齢が入っていて、成人の年齢以上か。"""
    return age is not None and int(age) >= get_floor().adult_age


def is_minor_age(age: int | float | None) -> bool:
    """年齢が入っていて、成人の年齢未満か。"""
    return age is not None and int(age) < get_floor().adult_age
