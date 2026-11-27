"""The names the chat tools use for mouse buttons, held keys, media keys and clipboard contents.

They are the AI tool vocabulary, so the tool catalog never depends on the platform; the desktop
adapter maps each name to its own codes (a test checks that it covers exactly these names).
"""
from __future__ import annotations

from typing import Literal

MOUSE_BUTTONS = ("left", "right", "middle", "back", "forward")

# keys that can be held with the mouse: what the user may type -> canonical name
MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "strg": "ctrl", "shift": "shift", "alt": "alt", "win": "win",
             "windows": "win", "meta": "win", "cmd": "win"}

MEDIA_ACTIONS = ("next", "previous", "stop", "play_pause")

ClipboardKind = Literal["files", "rtf", "html"]
