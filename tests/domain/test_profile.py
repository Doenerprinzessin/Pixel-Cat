"""domain.profile: the default Profile says exactly what the cat says on Windows today."""
from __future__ import annotations

import dataclasses

import pytest

from pixelcat.domain import WINDOWS, Profile


def test_windows_is_the_default_profile():
    assert WINDOWS == Profile()
    with pytest.raises(dataclasses.FrozenInstanceError):
        WINDOWS.os = "x"  # type: ignore[misc]
    hash(WINDOWS)       # immutable all the way down


def test_the_windows_texts_are_the_old_literals():
    p = WINDOWS
    assert f" ({p.os} didn't take a touch pinch here, so I used {p.zoom} + mouse wheel instead.)" == \
        " (Windows didn't take a touch pinch here, so I used ctrl + mouse wheel instead.)"
    assert f"lives on the user's {p.desktop}." == "lives on the user's Windows desktop."
    assert f"If a task needs them, or {p.admin_prompt}, stop" == \
        "If a task needs them, or Windows shows its admin prompt (UAC), stop"
    assert f"```{p.shell}\nollama serve\n```" == "```powershell\nollama serve\n```"
    assert p.prompt_note == ""
    assert f"I keep it encrypted for {p.account} and" == "I keep it encrypted for your Windows account and"
    assert f"or it's from {p.other_account}." == "or it's from another Windows account or PC."
    assert (p.start_with, f"Meow! {p.start_with}?") == ("Start with Windows", "Meow! Start with Windows?")
    assert f"Open a folder in {p.file_manager}." == "Open a folder in File Explorer."
    assert f"Open a {p.settings} page." == "Open a Windows Settings page."
    assert f"Lock the PC ({p.lock_keys})." == "Lock the PC (Windows+L)."
    assert f"a new {p.notification} (chat" == "a new Windows notification (chat"
    assert f"I can't read {p.notification}s on this PC" == "I can't read Windows notifications on this PC"
    assert f" or any app installed in the {p.app_menu}" == " or any app installed in the Start menu"
    assert f"'Spotify' ({p.app_menu})" == "'Spotify' (Start menu)"
    assert f"Force-end an app like {p.task_manager}." == "Force-end an app like Task Manager."
    assert f"Show a text file to the user in {p.text_editor} or VS Code" == \
        "Show a text file to the user in Notepad or VS Code"
    assert f"(Notepad, VS Code, ...)" == f"({p.text_editor}, VS Code, ...)"
    assert f"Talk to Pixel… ({p.new_chat_keys}: new chat)" == "Talk to Pixel… (Ctrl+N: new chat)"
    assert f"Press ＋ (or {p.new_chat_keys}) for a fresh" == "Press ＋ (or Ctrl+N) for a fresh"
    assert f"Ollama (the AI engine) isn't installed, so I can't chat yet. {p.setup_hint}" == (
        "Ollama (the AI engine) isn't installed, so I can't chat yet. Run PixelCat-Setup.exe again and "
        "tick “Set up the AI chat”, or install it from ollama.com.")
    assert f"keys held down meanwhile: {p.held_hint}, or" == "keys held down meanwhile: ctrl, shift, alt, win, or"
    assert [f"{p.primary}+{k}" for k in "vacnz"] == ["ctrl+v", "ctrl+a", "ctrl+c", "ctrl+n", "ctrl+z"]
    assert (p.doc_end, p.zoom) == ("ctrl+end", "ctrl")


def test_the_windows_shortcut_sets_and_tables_are_the_old_ones():
    from pixelcat.llm import tools

    assert WINDOWS.closing_keys == tools.CLOSING_KEYS and WINDOWS.system_keys == tools.SYSTEM_KEYS
    assert WINDOWS.key_aliases == () and WINDOWS.key_alias_map() == {}
    assert WINDOWS.apps is None and WINDOWS.app_commands() is None
    assert WINDOWS.window_aliases is None and WINDOWS.window_alias_map() is None


@pytest.mark.parametrize("combo", ["win+ctrl+d", "ctrl+alt+delete", "cmd+q", "Alt+F4", "ctrl++w", "+alt+f4", " ctrl+q ",
                                   "ctrl+w+q", "+", ""])
def test_windows_keeps_combos_as_written(combo: str) -> None:
    assert WINDOWS.normalize_combo(combo) == combo
    assert WINDOWS.combo_names(combo) == {combo}


@pytest.mark.parametrize(("combo", "normalized"), [
    ("cmd+q", "win+q"), ("command+option+esc", "alt+win+esc"), ("win+ctrl+d", "ctrl+win+d"),
    ("shift+cmd+w", "shift+win+w"), ("opt+shift+cmd+q", "alt+shift+win+q"), ("f5", "f5"), ("ctrl+fn+x", "ctrl+fn+x"),
])
def test_aliases_map_and_sort_the_held_keys(combo: str, normalized: str) -> None:
    p = Profile(key_aliases=(("cmd", "win"), ("command", "win"), ("option", "alt"), ("opt", "alt")))
    assert p.normalize_combo(combo) == normalized
