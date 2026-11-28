"""Key names -> macOS virtual key codes, for the synthetic keyboard input (``inputs``).

* Named keys (return, esc, arrows, f1-f20 ...) have fixed codes (Carbon's ``kVK_*``).
* Letters, digits and punctuation are looked up in the current keyboard layout: a char -> key code table built
  with ``CGEventKeyboardGetUnicodeString`` for the codes 0..127 (it only creates events, nothing is posted).  So
  cmd+a stays cmd+a on AZERTY or Swiss German keyboards (Swiss German: 'z' is code 16, 'y' code 6).  The table
  holds the characters the keys type *without* shift (the API ignores the modifier flags); a character only on
  the keypad ('+' on a US or Swiss keyboard) is pressed there.  Missing a-z / 0-9 (Cyrillic, Greek ... layouts)
  fall back to the US positions, like macOS does for command shortcuts; a few shifted US characters ('?', '!',
  ...) are shift + their US key when the layout has that key where the US one has it.
* :func:`parse_combo` returns the key codes in the order given; ``ValueError`` for unknown names (the core
  shows its text to the AI, as on Windows).  Media keys get codes from :data:`MEDIA_BASE` up (posted as
  system-defined events, not as key codes).
"""
from __future__ import annotations

import logging
import re
import threading
import time

from . import _objc

log = logging.getLogger(__name__)

# the held keys' names -> their canonical names (domain.MODIFIERS plus the Mac ones)
MODIFIER_NAMES = {"ctrl": "ctrl", "control": "ctrl", "strg": "ctrl", "shift": "shift", "alt": "alt", "option": "alt",
                  "opt": "alt", "win": "win", "windows": "win", "cmd": "win", "command": "win", "meta": "win",
                  "super": "win"}

# canonical modifier -> (key code, CGEventFlags mask)
MODIFIERS = {"ctrl": (59, 0x40000), "shift": (56, 0x20000), "alt": (58, 0x80000), "win": (55, 0x100000)}
MODIFIER_FLAGS = {code: mask for code, mask in MODIFIERS.values()}
SHIFT = MODIFIERS["shift"][0]
ESCAPE = 53
RETURN = 36
TAB = 48

MEDIA_BASE = 0x1000     # codes >= this are media keys: MEDIA_BASE + NX_KEYTYPE_*
NX_KEYTYPE = {"volumeup": 0, "volumedown": 1, "volumemute": 7}

KEYCODES: dict[str, int] = {       # key name -> virtual key code (layout-independent keys)
    "return": RETURN, "enter": RETURN, "esc": ESCAPE, "escape": ESCAPE, "tab": TAB, "space": 49, "spacebar": 49,
    "backspace": 51, "back": 51, "delete": 117, "del": 117, "entf": 117, "forwarddelete": 117, "fwddelete": 117,
    "insert": 114, "ins": 114, "help": 114,
    "home": 115, "pos1": 115, "end": 119, "pageup": 116, "pgup": 116, "pagedown": 121, "pgdn": 121,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "arrowup": 126, "arrowdown": 125, "arrowleft": 123, "arrowright": 124,
    "capslock": 57,
}
KEYCODES.update({f"f{i}": code for i, code in enumerate(
    (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113, 106, 64, 79, 80, 90), start=1)})
KEYCODES.update({name: MODIFIERS[canon][0] for name, canon in MODIFIER_NAMES.items()})
KEYCODES.update({name: MEDIA_BASE + t for name, t in NX_KEYTYPE.items()})

# names that stand for one character (looked up in the layout)
CHAR_NAMES = {"plus": "+", "minus": "-", "comma": ",", "period": ".", "dot": "."}

KEYPAD = frozenset({65, 67, 69, 71, 75, 76, 78, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92})

# the US ANSI layout (the fallback when the layout can't be read, and for a-z / 0-9 a layout lacks)
ANSI: dict[str, int] = dict(zip("asdfhgzxcv", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)))
ANSI.update(zip("bqweryt123465", (11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23)))
ANSI.update({"=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
             "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
             ".": 47, "`": 50})
# shifted US characters -> the unshifted character on the same key
US_SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
              "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
              "~": "`"}

_TTL = 5.0      # seconds a layout table is used before it is read again (the user may switch layouts)
_lock = threading.Lock()
_cache: tuple[float, dict[str, int]] | None = None


def _read_layout() -> dict[str, int]:
    """char -> key code in the current layout (main keys first, the keypad for what they don't have)."""
    Q = _objc.Q
    found: dict[int, str] = {}
    for kc in range(128):
        # a fresh source per key: a source remembers a dead key (Swiss '^' / '¨') and would mix it into the next
        src = Q.CGEventSourceCreate(-1)   # kCGEventSourceStatePrivate
        ev = Q.CGEventCreateKeyboardEvent(src, kc, True)
        if ev is None:
            continue
        n, s = Q.CGEventKeyboardGetUnicodeString(ev, 4, None, None)
        s = str(s or "")[:max(0, int(n or 0))]
        if len(s) == 1 and s.isprintable() and not s.isspace():
            found[kc] = s
    table: dict[str, int] = {}
    for keypad in (False, True):
        for kc, ch in found.items():
            if (kc in KEYPAD) == keypad:
                table.setdefault(ch.lower(), kc)
    return table


def layout_table() -> dict[str, int]:
    """The current layout's char -> key code table (cached a few seconds); the US one if it can't be read."""
    global _cache
    with _lock:
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < _TTL:
            return _cache[1]
        try:
            table = _read_layout()
        except Exception:  # noqa: BLE001 - no pyobjc or the API failed: US positions
            log.debug("couldn't read the keyboard layout", exc_info=True)
            table = {}
        if not table:
            table = dict(ANSI)
        _cache = (now, table)
        return table


def reset() -> None:
    """Forget the cached layout table (tests)."""
    global _cache
    with _lock:
        _cache = None


def char_keys(ch: str) -> list[int] | None:
    """The key codes that type ``ch`` (one key, or shift + a key); None if the layout has no key for it."""
    table = layout_table()
    if ch in table:
        return [table[ch]]
    if ch.isascii() and ch.isalnum():
        return [ANSI[ch]]
    partner = US_SHIFTED.get(ch)
    if partner is not None and table.get(partner) == ANSI[partner]:
        return [SHIFT, ANSI[partner]]
    return None


def split_combo(combo: str) -> list[str]:
    """'ctrl+shift+s' -> ['ctrl', 'shift', 's'] (lower case; '+' itself is 'plus': 'ctrl++', 'ctrl + +', '+');
    ValueError if empty."""
    text = re.sub(r"\s*\+\s*", "+", combo.strip().lower())
    if text == "+":
        return ["plus"]
    if text.endswith("++"):
        text = text[:-2] + "+plus"
    parts = [p.strip() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("no keys given")
    return parts


def parse_combo(combo: str) -> list[int]:
    """'cmd+shift+s' -> [55, 56, 1] (the key codes in order, for ``MacInput.press_combo``); ValueError for
    unknown keys."""
    codes: list[int] = []
    for p in split_combo(combo):
        if p in KEYCODES:
            got: list[int] | None = [KEYCODES[p]]
        elif p in CHAR_NAMES:
            got = char_keys(CHAR_NAMES[p])
        elif len(p) == 1:
            got = char_keys(p)
        else:
            got = None
        if got is None:
            raise ValueError(f"unknown key '{p}'")
        for code in got:
            if code not in codes or code not in MODIFIER_FLAGS:    # a modifier is held once
                codes.append(code)
    return codes


def zoom_keys(zoom_in: bool) -> list[int] | None:
    """The key that zooms with cmd: '+' (or '=' as on a US keyboard, else the keypad '+'), '-'."""
    table = layout_table()
    if not zoom_in:
        return [table["-"]] if "-" in table else None
    for ch in ("+", "="):
        if ch in table and table[ch] not in KEYPAD:
            return [table[ch]]
    return [table["+"]] if "+" in table else None
