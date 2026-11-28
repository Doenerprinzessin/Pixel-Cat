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
