"""pixelcat.adapters.macos.profile: the Mac's words and shortcuts."""
from __future__ import annotations

import itertools

import pytest

from pixelcat.adapters.macos import keys
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
