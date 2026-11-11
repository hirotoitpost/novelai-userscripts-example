"""
安全の最低線(safety_floor.py)のテスト。最低線はコードにだけあり、注入した最低線をガードが使うこと、
コードより緩い最低線は注入できないこと、ユーザー定義(DB)で最低線を外せないことを確かめる。

実行方法:
  uv run pytest tests/test_safety_floor.py -v
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import content_guard, db, safety_floor  # noqa: E402
from python.safety_floor import CodeSafetyFloor, get_floor, is_adult_age, is_minor_age, use_floor  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    content_guard.reset_cache()
    yield
    content_guard.reset_cache()


def test_default_floor_is_code_floor() -> None:
    floor = get_floor()
    assert isinstance(floor, CodeSafetyFloor)
    assert floor.adult_age == 18
    for word in ("loli", "shota", "child", "underage", "school girl", "jk", "randoseru"):
        assert floor.never_costume(word), word
    assert not floor.never_costume("serafuku")


def test_age_helpers() -> None:
    assert is_adult_age(18) and not is_minor_age(18)
    assert is_minor_age(17) and not is_adult_age(17)
    assert not is_adult_age(None) and not is_minor_age(None)


def test_code_floor_is_frozen() -> None:
    with pytest.raises(AttributeError):
        get_floor().adult_age = 10  # type: ignore[misc]


def test_looser_floor_cannot_be_injected() -> None:
    with pytest.raises(ValueError):
        with use_floor(CodeSafetyFloor(adult_age=16)):
            pass
    without_loli = tuple(p for p in CodeSafetyFloor().never_costume_patterns if p != "loli(ta)?")
    with pytest.raises(ValueError):
        with use_floor(CodeSafetyFloor(never_costume_patterns=without_loli)):
            pass
    assert get_floor() is safety_floor._CODE_FLOOR


@dataclass(frozen=True)
class _StricterFloor:
    adult_age: int = 20
    never_costume_patterns: tuple[str, ...] = field(
        default_factory=lambda: (*CodeSafetyFloor().never_costume_patterns, "sailor")
    )

    def never_costume(self, tag: str) -> bool:
        return CodeSafetyFloor(never_costume_patterns=self.never_costume_patterns).never_costume(tag)


def test_stricter_floor_is_injected_into_guard() -> None:
    content_guard.set_value("safety.adult_costume_tags", ["school uniform", "sailor (collar|uniform)"])
    assert content_guard.is_costume("sailor uniform")
    assert content_guard.is_defined_adult({"age": 19, "is_minor": 0})
    with use_floor(_StricterFloor()):
        assert not content_guard.is_costume("sailor uniform")
        assert not content_guard.is_defined_adult({"age": 19, "is_minor": 0})
        assert "sailor" in content_guard.describe_one("safety.never_costume_tags")["fixed"]
    assert content_guard.is_costume("sailor uniform")


def test_user_definition_cannot_remove_floor() -> None:
    # ユーザー定義を空にし、衣装のタグに最低線の語を足しても、最低線の語は衣装にならない
    content_guard.set_value("safety.never_costume_tags", [])
    content_guard.set_value("safety.adult_costume_tags", ["loli", "school girl", "school uniform"])
    assert not content_guard.is_costume("loli")
    assert not content_guard.is_costume("school girl")
    assert content_guard.is_costume("school uniform")
    fixed = content_guard.describe_one("safety.never_costume_tags")["fixed"]
    assert "loli(ta)?" in fixed


def test_user_definition_adds_to_floor() -> None:
    content_guard.set_value("safety.adult_costume_tags", ["school uniform", "gym uniform"])
    content_guard.set_value("safety.never_costume_tags", ["gym uniform"])
    assert content_guard.is_costume("school uniform")
    assert not content_guard.is_costume("gym uniform")
