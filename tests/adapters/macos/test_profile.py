"""pixelcat.adapters.macos.profile: the Mac's words and shortcuts."""
from __future__ import annotations

import itertools

import pytest

from pixelcat.adapters.macos import escwatch, inputs, keys
from pixelcat.adapters.macos.profile import MAC


def test_the_mac_profile():
    assert MAC.os == "macOS" and MAC.primary == "cmd" and MAC.shell == "zsh" and MAC.prompt_note
    for combo in ("cmd+q", "cmd+w", "ctrl+cmd+q", "win+l", "alt+f4"):
        assert MAC.normalize_combo(combo) in MAC.closing_keys, combo
    for combo in ("cmd+option+esc", "cmd+space", "win+r", "win+ctrl+d"):
        assert MAC.normalize_combo(combo) in MAC.system_keys, combo
    assert MAC.normalize_combo("cmd+s") not in MAC.closing_keys | MAC.system_keys
    apps = MAC.app_commands()
    assert apps is not None and apps["notepad"] == ["/usr/bin/open", "-a", "TextEdit"]
    assert MAC.window_alias_map()["task manager"] == "activity monitor"
    assert {MAC.key_alias_map()[k] for k in ("cmd", "command", "meta", "option", "opt")} == {"win", "alt"}
    hash(MAC)


def test_every_key_name_the_mac_accepts_has_one_canonical_name():
    from pixelcat.adapters.macos import keys

    aliases = MAC.key_alias_map()
    by_code: dict[int, set[str]] = {}
    for name, code in keys.KEYCODES.items():
        canon = aliases.get(name, name)
        assert canon not in aliases, name
        by_code.setdefault(code, set()).add(canon)
    assert all(len(names) == 1 for names in by_code.values()), by_code
    assert {aliases.get(m, m) for m in keys.MODIFIER_NAMES} == {"ctrl", "shift", "alt", "win"}


def test_any_spelling_of_a_closing_or_system_shortcut_asks_first():
    for combo in ("super+q", "strg+cmd+q", "control+command+q", "cmd+backspace", "cmd+back", "command+del",
                  "cmd+shift+backspace", "cmd+option+shift+backspace", "cmd+option+backspace", "cmd+fwddelete"):
        assert MAC.normalize_combo(combo) in MAC.closing_keys, combo
    for combo in ("cmd+option+escape", "cmd+spacebar", "super+space", "ctrl+alt+entf", "windows+r"):
        assert MAC.normalize_combo(combo) in MAC.system_keys, combo


BYPASSES = ["cmd++q", "cmd++space", "+cmd+q", "cmd+q+", "cmd+option++esc", "ctrl+cmd++q", "cmd + + q", " cmd+q ",
            "CMD++Q", "Cmd+Option++Esc", "+CMD+SPACE+", "cmd+q+w", "cmd+a+q", "cmd+shift++w"]
CLOSING = {"cmd++q", "+cmd+q", "cmd+q+", "ctrl+cmd++q", "cmd + + q", " cmd+q ", "CMD++Q", "cmd+q+w", "cmd+a+q",
           "cmd+shift++w"}


@pytest.mark.parametrize("combo", BYPASSES)
def test_empty_segments_and_extra_keys_dont_hide_a_closing_or_system_shortcut(combo: str) -> None:
    names = MAC.combo_names(combo)
    assert bool(names & MAC.closing_keys) == (combo in CLOSING), names
    assert bool(names & MAC.system_keys) == (combo not in CLOSING), names


@pytest.mark.parametrize(("combo", "normalized"), [
    ("cmd++q", "win+q"), ("+cmd+q", "win+q"), ("cmd+q+", "win+q"), ("cmd + + q", "win+q"), (" CMD+Q ", "win+q"),
    ("cmd+option++esc", "alt+win+esc"), ("ctrl+cmd++q", "ctrl+win+q"), ("cmd++space", "win+space"),
    ("cmd++", "win+plus"), ("cmd + +", "win+plus"), ("+", "plus"),
])
def test_normalize_combo_reads_a_combo_as_the_keyboard_input_does(combo: str, normalized: str) -> None:
    assert MAC.normalize_combo(combo) == normalized


def test_combos_that_press_the_same_keys_are_checked_alike(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "layout_table", lambda: dict(keys.ANSI))
    combos = BYPASSES + ["cmd+q", "cmd+space", "cmd+option+esc", "ctrl+cmd+q", "command+q", "super+q", "q+cmd",
                         "cmd++", "cmd+plus", "cmd+s", "cmd+shift+w", "shift+cmd+w", "option+cmd+escape", "+"]
    for a, b in itertools.combinations(combos, 2):
        if keys.parse_combo(a) == keys.parse_combo(b):
            na, nb = MAC.combo_names(a), MAC.combo_names(b)
            assert (bool(na & MAC.closing_keys), bool(na & MAC.system_keys)) == \
                (bool(nb & MAC.closing_keys), bool(nb & MAC.system_keys)), (a, b)
            assert MAC.normalize_combo(a) == MAC.normalize_combo(b), (a, b)
    assert not MAC.combo_names("q+cmd") & (MAC.closing_keys | MAC.system_keys)    # q is down before cmd


# ---------------------------------------------------------------- the safety check vs what MacInput really posts
_DANGER = MAC.closing_keys | MAC.system_keys
_MODS = ["cmd", "command", "option", "opt", "alt", "ctrl", "control", "strg", "shift", "win", "meta", "super"]
_KEYS = ["q", "w", "l", "r", "x", "d", "a", "1", "/", "=", "esc", "escape", "delete", "del", "backspace", "back",
         "space", "f4", "tab", "plus", "minus", "comma"] + sorted(set(keys.US_SHIFTED) - {"+"})
_LAYOUTS = {
    "us fallback": dict(keys.ANSI),                                    # every shifted character is shift + a key
    "us": dict(keys.ANSI, **{"+": 69, "*": 67}),                        # '+' / '*' on the keypad
    "azerty-like": dict(keys.ANSI, q=0, a=12, **{"!": 30, "?": 27, "+": 33, "&": 18, "(": 23, "@": 50, "_": 42}),
}


class _Recorder(inputs.MacInput):
    """A MacInput whose key events are recorded instead of posted: (code, down, flags)."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[int, bool, int]] = []

    @staticmethod
    def _require() -> None:
        pass

    def _key(self, code: int, down: bool, flags: int) -> None:
        self.events.append((code, down, flags))

    def _media(self, code: int, down: bool) -> None:
        self.events.append((code, down, 0))


def _pressed_chords(combo: str, table: dict[str, int]) -> set[str]:
    """The shortcuts MacInput.press_combo really presses for ``combo``: at every key-down of a key that isn't held,
    the held modifiers (from the event's flags) + that key's name."""
    rec = _Recorder()
    rec.press_combo(keys.parse_combo(combo))
    names = {code: keys._FIRST[code] for code in keys.KEYCODES.values()}
    for ch, code in table.items():
        names.setdefault(code, ch)
    chords = set()
    for code, down, flags in rec.events:
        if down and code not in keys.MODIFIER_FLAGS and code < keys.MEDIA_BASE:
            held = [m for m in ("ctrl", "alt", "shift", "win") if flags & keys.MODIFIERS[m][1]]
            chords.add("+".join(held + [names[code]]))
    return chords


def _combos() -> list[str]:
    import random

    rnd = random.Random(20261128)
    out = ["cmd+option+!+q", "cmd+option+plus+q", "!+delete", "plus+delete", "ctrl+!+esc", "cmd+option+q",
           "option+cmd+?+q", "cmd+?+q", "ctrl+cmd+(+q", "cmd+option+@+esc", "~+cmd+delete", "cmd+_+w", "cmd++",
           "cmd+option+:+backspace", "+", "cmd+option++q"]
    for _ in range(4000):
        parts = [rnd.choice(_MODS + _KEYS) for _ in range(rnd.randint(1, 5))]
        parts = [p.upper() if rnd.random() < 0.2 else p for p in parts]
        text = "".join(p + rnd.choice(["+", "+", "+", "++", " + "]) for p in parts[:-1]) + parts[-1]
        out.append(rnd.choice(["", "+", " "]) + text + rnd.choice(["", "", "+", " "]))
    return out


@pytest.mark.parametrize("layout", list(_LAYOUTS))
def test_every_closing_or_system_shortcut_a_combo_really_presses_is_seen_by_the_check(
        layout: str, monkeypatch: pytest.MonkeyPatch) -> None:
    table = _LAYOUTS[layout]
    monkeypatch.setattr(keys, "layout_table", lambda: table)
    monkeypatch.setattr(inputs, "_wait", lambda seconds, cancel: True)
    monkeypatch.setattr(escwatch, "mark_own", lambda seconds: None)
    dangerous = 0
    for combo in _combos():
        try:
            chords = _pressed_chords(combo, table) & _DANGER
        except ValueError:
            continue                                     # (not pressed: press_keys refuses it)
        names = MAC.combo_names(combo.lower().replace("control", "ctrl").replace("windows", "win"))  # as press_keys
        assert chords <= names, (combo, chords, names)
        dangerous += bool(chords)
    assert dangerous > 300


@pytest.mark.parametrize("combo", ["cmd+option+!+q", "cmd+option+plus+q", "cmd+option+q", "!+delete",
                                   "plus+delete", "cmd+?+q", "cmd+option+shift+esc"])
def test_shifted_characters_and_cmd_option_q_ask_for_a_real_confirmation(combo: str) -> None:
    assert MAC.combo_names(combo) & MAC.closing_keys, combo


def test_ctrl_shifted_char_esc_asks_even_in_auto_mode() -> None:
    assert "ctrl+shift+esc" in MAC.combo_names("ctrl+!+esc") & MAC.system_keys
