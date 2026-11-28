"""The cat's own windows on macOS (their ids are Qt ``winId()`` values: NSView pointers, see ``_objc.is_own``).

Every function here runs its AppKit / Qt work on the GUI thread (``_objc.on_main``) and resolves the id with
``QWidget.find`` first: an id whose widget is gone is never dereferenced.  ``_numbers`` maps the windows'
CGWindowIDs to their ids (kept up to date on the GUI thread), so worker threads can tell the cat's windows in
the system's window list apart without Qt.

Skeleton: the window actions do nothing yet.
"""
from __future__ import annotations

import threading

from ...domain import WindowId

STATUS_LEVEL = 25           # NSStatusWindowLevel: above the Dock and the menu bar, below Qt's popup menus

_lock = threading.Lock()
_numbers: dict[int, int] = {}      # CGWindowID (NSWindow.windowNumber) -> the cat's window id


def remember(number: int, win: WindowId) -> None:
    """The cat's window ``win`` has the window number ``number``."""
    with _lock:
        _numbers[int(number)] = int(win)


def forget(win: WindowId) -> None:
    """The cat's window ``win`` is gone."""
    with _lock:
        for number in [n for n, w in _numbers.items() if w == win]:
            del _numbers[number]


def own_id(number: int) -> int | None:
    """The cat's window id for a window number, or None if it isn't one of the cat's windows."""
    with _lock:
        return _numbers.get(int(number))


def own_numbers() -> dict[int, int]:
    """A copy of the window number -> the cat's window id map."""
    with _lock:
        return dict(_numbers)


def make_tool_window(win: WindowId) -> None:
    """No Dock or Cmd+Tab entry, never takes the focus, on every Space, above other apps."""


def set_click_through(win: WindowId, enabled: bool) -> None:
    """Let clicks fall through the window (or catch them again)."""


def move(win: WindowId, x: int, y: int, w: int, h: int, topmost: bool = True) -> None:
    """Move and size the window (through Qt, which knows its geometry)."""


def keep_topmost(win: WindowId) -> None:
    """Back on top of everything."""


def place_below(win: WindowId, other: WindowId) -> None:
    """Right under another window."""


def own_click_through(own_pid: int, keep: WindowId) -> list[WindowId]:
    """Make the cat's visible windows except ``keep`` click-through; the ones that changed."""
    return []


def end_own_click_through(changed: list[WindowId]) -> None:
    """Undo :func:`own_click_through` for the windows still there."""
