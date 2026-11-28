"""The words and shortcuts of the platform the cat lives on (no platform calls here).

The core builds its user-facing texts (the chat's system prompt, tool descriptions, menu items, error
messages) and its shortcut choices from a :class:`Profile`, so a platform adapter can name things the way
its users know them.  Every default is exactly what the cat says and does on Windows today
(:data:`WINDOWS`); an adapter for another platform makes its own ``Profile(...)``.
"""
from __future__ import annotations

from dataclasses import dataclass

MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")    # how normalize_combo sorts the held keys

# shortcuts that close things / lock the PC: always a real confirmation (no ALWAYS)
_CLOSING_KEYS = frozenset({"alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+q", "ctrl+f4", "shift+delete", "win+l"})
# shortcuts that open a command line or system menus: ask even in auto mode
_SYSTEM_KEYS = frozenset({"win+r", "win+x", "ctrl+shift+esc", "ctrl+alt+delete", "ctrl+alt+del", "win+ctrl+d"})


@dataclass(frozen=True)
class Profile:
    """Names, phrases and shortcuts of one platform.  Mappings are tuples of pairs (the profile is immutable)."""

    os: str = "Windows"                                  # "(Windows didn't take a touch pinch here, ...)"
    desktop: str = "Windows desktop"                     # "who lives on the user's Windows desktop"
    admin_prompt: str = "Windows shows its admin prompt (UAC)"   # "If a task needs them, or ..., stop"
    shell: str = "powershell"                            # the language tag of command code blocks
    prompt_note: str = ""                                # appended to the system prompt
    account: str = "your Windows account"                # "I keep it encrypted for your Windows account"
    other_account: str = "another Windows account or PC"  # "... or it's from another Windows account or PC."
    start_with: str = "Start with Windows"               # the menu item, "Meow! Start with Windows?"
    file_manager: str = "File Explorer"                  # "Open a folder in File Explorer."
    settings: str = "Windows Settings"                   # "Open a Windows Settings page."
    lock_keys: str = "Windows+L"                         # "Lock the PC (Windows+L)."
    notification: str = "Windows notification"          # "a new Windows notification", "Windows notifications"
    app_menu: str = "Start menu"                         # "any app installed in the Start menu", "'X' (Start menu)"
    task_manager: str = "Task Manager"                   # "Force-end an app like Task Manager.", /force
    text_editor: str = "Notepad"                         # "in Notepad or VS Code", "open Notepad and ..."
    new_chat_keys: str = "Ctrl+N"                        # "(Ctrl+N: new chat)", "Press ＋ (or Ctrl+N)"
    setup_hint: str = "Run PixelCat-Setup.exe again and tick “Set up the AI chat”, or install it from ollama.com."
    held_hint: str = "ctrl, shift, alt, win"             # keys that can be held while using the mouse
    primary: str = "ctrl"                                # the shortcut modifier: ctrl+v, ctrl+a, ctrl+n, ctrl+z
    doc_end: str = "ctrl+end"                            # to the end of a document
    zoom: str = "ctrl"                                   # held while turning the wheel to zoom
    # (primary, doc_end and zoom are key names as the model writes them: map them with key_aliases before
    # InputPort.holding, which only knows the canonical names)
    closing_keys: frozenset[str] = _CLOSING_KEYS        # compared after normalize_combo
    system_keys: frozenset[str] = _SYSTEM_KEYS          # (on Windows: as written)
    key_aliases: tuple[tuple[str, str], ...] = ()        # key names the user may write -> the canonical names
    apps: tuple[tuple[str, tuple[str, ...]], ...] | None = None    # the open_app table; None: the config's
    window_aliases: tuple[tuple[str, str], ...] | None = None      # app words -> window names; None: the built-in

    def app_commands(self) -> dict[str, list[str]] | None:
        """:attr:`apps` as the config's ``apps`` dict (name -> command), or None."""
        return None if self.apps is None else {name: list(cmd) for name, cmd in self.apps}

    def window_alias_map(self) -> dict[str, str] | None:
        """:attr:`window_aliases` as a dict, or None."""
        return None if self.window_aliases is None else dict(self.window_aliases)

    def key_alias_map(self) -> dict[str, str]:
        """:attr:`key_aliases` as a dict (empty on Windows)."""
        return dict(self.key_aliases)

    def normalize_combo(self, combo: str) -> str:
        """A combo as :attr:`closing_keys` / :attr:`system_keys` hold it: aliases mapped and the held keys in
        :data:`MODIFIER_ORDER` ("cmd+option+esc" -> "alt+win+esc").  Without aliases (Windows) it stays as it is."""
        if not self.key_aliases:
            return combo
        aliases = dict(self.key_aliases)
        parts = [aliases.get(k, k) for k in combo.split("+")]
        held, key = parts[:-1], parts[-1:]
        mods = [m for m in MODIFIER_ORDER if m in held]
        return "+".join(mods + [k for k in held if k not in MODIFIER_ORDER] + key)


WINDOWS = Profile()
