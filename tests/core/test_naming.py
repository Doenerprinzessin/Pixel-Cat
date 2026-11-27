"""naming: the cat's own name."""
from __future__ import annotations

import pytest

from pixelcat import naming


@pytest.fixture(autouse=True)
def no_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(naming, "_name", "")


@pytest.mark.parametrize("text, name", [
    ("Mochi", "Mochi"), ("  Mochi  ", "Mochi"), ("Sir   Fluff\tthe\n3rd", "Sir Fluff the 3rd"),
    ("Kätzchen", "Kätzchen"), ("ねこ", "ねこ"), ("Mr. Whiskers-Paw", "Mr. Whiskers-Paw"),
    ("Tom & Jerry!", "Tom & Jerry!"), ("o'malley_~", "o'malley_~"), ("A" * 20, "A" * 20),
    ("Cafe\u0301", "Café"),
])
def test_check_accepts(text: str, name: str) -> None:
    assert naming.check(text) == (name, "")


@pytest.mark.parametrize("text", ["", "   ", None, "\n\t"])
def test_check_empty_means_back_to_default(text) -> None:  # type: ignore[no-untyped-def]
    assert naming.check(text) == ("", "")


@pytest.mark.parametrize("text, problem", [
    ("Mochi🐾", "Letters, numbers"), ("<script>", "Letters, numbers"), ("a/b", "Letters, numbers"),
    ("A" * 21, "At most 20"), ("---", "at least one letter"), ("! ! !", "at least one letter"),
])
def test_check_refuses(text: str, problem: str) -> None:
    name, msg = naming.check(text)
    assert name == "" and problem in msg


def test_check_accepts_non_strings() -> None:
    assert naming.check(42) == ("42", "")


def test_without_a_name_everything_is_the_default() -> None:
    assert (naming.custom(), naming.title(), naming.label(), naming.talk_to(), naming.find(), naming.ai()) == \
        ("", "Pixel Cat", "cat", "the cat", "Find my cat", "Pixel")


def test_with_a_name_everything_uses_it() -> None:
    assert naming.set_name("  Mochi ") == "Mochi"
    assert (naming.custom(), naming.title(), naming.label(), naming.talk_to(), naming.find(), naming.ai()) == \
        ("Mochi", "Mochi", "Mochi", "Mochi", "Find Mochi", "Mochi")


def test_an_invalid_name_counts_as_no_name() -> None:
    naming.set_name("Mochi")
    assert naming.set_name("Mochi🐾") == ""
    assert naming.title() == "Pixel Cat"
