"""pixelcat.adapters.macos.keys: key names and the current keyboard layout -> virtual key codes."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, keys


@pytest.fixture(autouse=True)
def fresh_table() -> Iterator[None]:
    keys.reset()
    yield
    keys.reset()


def layout(monkeypatch: pytest.MonkeyPatch, chars: dict[int, str], dead: frozenset[int] = frozenset()) -> FakeModule:
    """A fake Quartz whose layout types ``chars[code]``; a dead key spoils the next lookup from the same source."""
    pending: dict[Any, str] = {}

    def create(src: Any, kc: int, down: bool) -> tuple:
        return (src, kc)

    def get(ev: tuple, n: int, a: Any, b: Any) -> tuple[int, str]:
        src, kc = ev
        if kc in dead:
            pending[src] = "^"
            return (0, "")
        s = pending.pop(src, "") + chars.get(kc, "")
        return (len(s), s)
    sources = iter(range(10_000))
    q = FakeModule(CGEventSourceCreate=lambda state: next(sources), CGEventCreateKeyboardEvent=create,
                   CGEventKeyboardGetUnicodeString=get)
    monkeypatch.setattr(_objc, "Q", q)
    return q


AZERTY = {0: "q", 1: "s", 2: "d", 12: "a", 13: "z", 6: "w", 46: ",", 18: "&", 27: ")", 24: "-", 69: "+", 78: "-"}


def test_letters_follow_the_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    layout(monkeypatch, AZERTY)
    assert keys.parse_combo("cmd+a") == [55, 12]              # not cmd+q
    assert keys.parse_combo("Ctrl+Shift+Z") == [59, 56, 13]
    assert keys.parse_combo("minus") == [24]                   # the main key, not the keypad's


def test_a_dead_key_doesnt_spoil_the_next_key(monkeypatch: pytest.MonkeyPatch) -> None:
    q = layout(monkeypatch, {31: "x"}, dead=frozenset({30}))
    assert keys.parse_combo("x") == [31]                       # (not '^x', and so not the US 'x' key 7)
    assert len(q.called("CGEventSourceCreate")) == 128         # a fresh source per key
    assert all(args == (-1,) for args in q.called("CGEventSourceCreate"))


def test_the_table_is_cached_for_a_while(monkeypatch: pytest.MonkeyPatch) -> None:
    q = layout(monkeypatch, AZERTY)
    keys.parse_combo("a")
    keys.parse_combo("s")
    assert len(q.called("CGEventSourceCreate")) == 128


def test_latin_letters_missing_from_the_layout_use_the_us_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    layout(monkeypatch, {0: "ф", 8: "с", 9: "м"})              # Russian
    assert keys.parse_combo("cmd+c") == [55, 8]
    assert keys.parse_combo("ф") == [0]


def test_a_layout_that_cant_be_read_is_us(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> Any:
        raise RuntimeError("no Quartz")
    monkeypatch.setattr(_objc, "Q", FakeModule(CGEventSourceCreate=broken))
    assert keys.parse_combo("cmd+z") == [55, 6]
    assert keys.parse_combo("/") == [44]


def test_keypad_characters_are_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    layout(monkeypatch, {24: "=", 27: "-", 69: "+", 78: "-", 75: "/"})
    assert keys.parse_combo("ctrl+plus") == [59, 69]
    assert keys.parse_combo("-") == [27]
    assert keys.zoom_keys(True) == [24] and keys.zoom_keys(False) == [27]
    layout(monkeypatch, {30: "+", 44: "-", 69: "+"})           # German: '+' has its own key
    keys.reset()
    assert keys.parse_combo("plus") == [30]
    assert keys.zoom_keys(True) == [30]


def test_shifted_us_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    layout(monkeypatch, {44: "/", 18: "1"})
    assert keys.parse_combo("?") == [56, 44]
    assert keys.parse_combo("shift+?") == [56, 44]             # (shift held once)
    layout(monkeypatch, {44: "-", 18: "1"})                    # '/' isn't where the US has it
    keys.reset()
    with pytest.raises(ValueError, match="unknown key '\\?'"):
        keys.parse_combo("?")


@pytest.mark.parametrize("combo, codes", [
    ("ctrl++", [59, 69]), ("ctrl + +", [59, 69]), ("+", [69]), ("cmd+plus", [55, 69]),
    ("command+option+esc", [55, 58, 53]), ("opt+left", [58, 123]), ("meta+return", [55, 36]),
    ("enter", [36]), ("delete", [117]), ("backspace", [51]), ("forwarddelete", [117]), ("tab", [48]),
    ("space", [49]), ("pageup", [116]), ("pgdn", [121]), ("home", [115]), ("end", [119]),
    ("f1", [122]), ("f12", [111]), ("f20", [90]), ("up", [126]), ("arrowdown", [125]),
    ("win+shift+3", [55, 56, 20]), ("volumemute", [keys.MEDIA_BASE + 7]),
])
def test_named_keys(monkeypatch: pytest.MonkeyPatch, combo: str, codes: list[int]) -> None:
    monkeypatch.setattr(keys, "layout_table", lambda: dict(keys.ANSI, **{"+": 69}))
    assert keys.parse_combo(combo) == codes


@pytest.mark.parametrize("combo, msg", [("ctrl+banana", "unknown key 'banana'"), ("f21", "unknown key 'f21'"),
                                        ("printscreen", "unknown key 'printscreen'"), ("", "no keys given"),
                                        ("  ", "no keys given")])
def test_unknown_keys_are_value_errors(monkeypatch: pytest.MonkeyPatch, combo: str, msg: str) -> None:
    monkeypatch.setattr(keys, "layout_table", lambda: dict(keys.ANSI))
    with pytest.raises(ValueError, match=msg):
        keys.parse_combo(combo)


@pytest.mark.macos
def test_the_real_layout_table() -> None:
    table = keys.layout_table()
    assert len(table) >= 30
    for ch in "abcdefghijklmnopqrstuvwxyz0123456789":
        assert keys.parse_combo(f"cmd+{ch}")[0] == 55
    assert keys.parse_combo("cmd+v") == [55, table.get("v", 9)]
