"""The cat's own name (Settings -> Name...).

The chat, the menu title, the tray and the notifications show it, and the AI calls itself by
it.  Without a name everything stays as it was ("Pixel Cat", "the cat"; the AI is "Pixel").
The app itself keeps its name: installer, data folders, window titles, sign-in pages.
"""
from __future__ import annotations

import re
import unicodedata

APP = "Pixel Cat"
AI_DEFAULT = "Pixel"
MAX_LEN = 20
PUNCT = " -'.&!~_"                       # besides letters and digits (any language)

_name = ""


def _squash(text: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip()


def check(text: object) -> tuple[str, str]:
    """A typed name -> (name, problem).  No problem and an empty name: back to the default."""
    raw = _squash(text)
    if not raw:
        return "", ""
    if any(not (ch.isalnum() or ch in PUNCT) for ch in raw):
        return "", "Letters, numbers, spaces and - ' . & ! ~ _ only, please."
    if len(raw) > MAX_LEN:
        return "", f"At most {MAX_LEN} characters, please."
    if not any(ch.isalnum() for ch in raw):
        return "", "A name needs at least one letter or number."
    return raw, ""


def set_name(name: object) -> str:
    """Use this name from now on (one that doesn't pass ``check`` counts as no name)."""
    global _name
    _name = check(name)[0]
    return _name


def custom() -> str:
    """The name the user gave the cat ("" = none)."""
    return _name


def title() -> str:
    """The menu title, the tray and the notifications: the name, or "Pixel Cat"."""
    return _name or APP


def label() -> str:
    """Above the cat's messages in the chat."""
    return _name or "cat"


def talk_to() -> str:
    """ "Talk to ..." in the chat's input."""
    return _name or "the cat"


def find() -> str:
    """The tray's "Find my cat"."""
    return f"Find {_name}" if _name else "Find my cat"


def ai() -> str:
    """What the AI calls itself."""
    return _name or AI_DEFAULT
