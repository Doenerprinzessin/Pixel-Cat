"""Key names -> macOS virtual key codes, for the synthetic keyboard input (``inputs``).

Letters, digits and punctuation are looked up in the current keyboard layout (a table built with
``CGEventKeyboardGetUnicodeString``), so cmd+a stays cmd+a on AZERTY or Swiss German keyboards.

Skeleton: :func:`parse_combo` checks nothing yet and returns the key names.
"""
from __future__ import annotations

import re

# the held keys' names -> their canonical names (domain.MODIFIERS plus the Mac ones)
MODIFIER_NAMES = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "option": "alt", "opt": "alt",
                  "win": "win", "cmd": "win", "command": "win", "meta": "win"}

KEYCODES: dict[str, int] = {}       # key name -> virtual key code (layout-independent keys)


def split_combo(combo: str) -> list[str]:
    """'ctrl+shift+s' -> ['ctrl', 'shift', 's'] (lower case; a lone '+' is 'plus'); ValueError if empty."""
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", combo.strip()) if p.strip()]
    if not parts and "+" in combo:
        parts = ["plus"]
    if not parts:
        raise ValueError("no keys given")
    return parts


def parse_combo(combo: str) -> list[str]:
    """The keys of a combo, for ``MacInput.press_combo``; ValueError for unknown keys."""
    return split_combo(combo)
