"""pixelcat.adapters.macos.profile: the Mac's words and shortcuts."""
from __future__ import annotations

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
