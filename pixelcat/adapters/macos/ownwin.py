"""The cat's own windows on macOS (their ids are Qt ``winId()`` values: NSView pointers, see ``_objc.is_own``).

Every function here runs its AppKit / Qt work on the GUI thread (``_objc.on_main``, with a timeout and an explicit
answer for when it can't run) and resolves the id with ``QWidget.find`` first: an id whose widget is gone is
never dereferenced.  ``_numbers`` maps the windows' CGWindowIDs to their ids (kept up to date on the GUI thread),
so worker threads can tell the cat's windows in the system's window list apart without Qt.

The floating windows (:func:`make_tool_window`) sit at ``NSStatusWindowLevel`` (above the Dock and the menu bar,
below Qt's popup menus), join every Space (full-screen ones too), stay put in Mission Control, are left out of
Cmd+` and never hide when the cat's app isn't the active one.  The app itself becomes an Accessory app (no Dock
icon, no menu bar of its own) the first time: the tray icon is its way in, and only such apps may show windows
over other apps' full-screen Spaces.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

from . import _objc
from ...domain import WindowId
from ...ports import Rect

log = logging.getLogger(__name__)

T = TypeVar("T")

STATUS_LEVEL = 25           # NSStatusWindowLevel: above the Dock and the menu bar, below Qt's popup menus
NORMAL_LEVEL = 0            # NSNormalWindowLevel
# NSWindowCollectionBehavior: CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary
COLLECTION = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8)
ANIMATION_NONE = 2          # NSWindowAnimationBehaviorNone
WINDOW_BELOW = -1           # NSWindowBelow
ACCESSORY = 1               # NSApplicationActivationPolicyAccessory
TIMEOUT = 1.0               # seconds a worker thread waits for the GUI thread

_lock = threading.Lock()
_numbers: dict[int, int] = {}      # CGWindowID (NSWindow.windowNumber) -> the cat's window id
_through: set[int] = set()         # window numbers of the cat's windows that let clicks fall through
_accessory = False                 # the activation policy is set (once per run)


def remember(number: int, win: WindowId) -> None:
    """The cat's window ``win`` has the window number ``number``."""
    with _lock:
        _numbers[int(number)] = int(win)


def forget(win: WindowId) -> None:
    """The cat's window ``win`` is gone."""
    with _lock:
        for number in [n for n, w in _numbers.items() if w == win]:
            del _numbers[number]
            _through.discard(number)


def own_id(number: int) -> int | None:
    """The cat's window id for a window number, or None if it isn't one of the cat's windows."""
    with _lock:
        return _numbers.get(int(number))


def own_numbers() -> dict[int, int]:
    """A copy of the window number -> the cat's window id map."""
    with _lock:
        return dict(_numbers)


def click_through_numbers() -> frozenset[int]:
    """Window numbers of the cat's windows that let clicks fall through right now (any thread)."""
    with _lock:
        return frozenset(_through)


def reset() -> None:
    """Forget every window (tests)."""
    global _accessory
    with _lock:
        _numbers.clear()
        _through.clear()
        _accessory = False


# ------------------------------------------------------------------ resolving ids (GUI thread only)
def _cocoa() -> bool:
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.platformName() == "cocoa"


def _widget(win: WindowId) -> Any:
    """The live QWidget for one of the cat's window ids, or None (gone, or not a native Cocoa window)."""
    if not _objc.is_own(win):
        return None
    from PySide6.QtWidgets import QWidget

    return QWidget.find(int(win))


def _view_window(wid: int) -> Any:
    """The NSWindow holding a live Qt window's NSView (``wid`` must be checked first)."""
    return _objc.objc.objc_object(c_void_p=int(wid)).window()


def _top_level_widgets() -> list[Any]:
    from PySide6.QtWidgets import QApplication

    return list(QApplication.topLevelWidgets())


def _nswindow(win: WindowId) -> Any:
    """The NSWindow of one of the cat's live windows, or None.  Only after ``QWidget.find`` says it's alive."""
    if _widget(win) is None or not _cocoa():
        return None
    return _view_window(win)


def _number(win: WindowId) -> int | None:
    nswin = _nswindow(win)
    if nswin is None:
        return None
    number = int(nswin.windowNumber())
    if number <= 0:
        return None
    remember(number, win)
    return number


def _main(fn: Callable[[], T], default: T) -> T:
    """``fn`` on the GUI thread; ``default`` if it can't run there in time or fails."""
    def guarded() -> T:
        try:
            return fn()
        except Exception:  # noqa: BLE001 - a window that went away under us: nothing to do
            log.debug("own window call failed", exc_info=True)
            return default
    return _objc.on_main(guarded, TIMEOUT, default)


def refresh() -> dict[int, int]:
    """Read the window numbers of all the cat's native top-level windows again (GUI thread; a worker thread
    gets the map as it is).  Drops the windows that are gone."""
    def read() -> dict[int, int]:
        fresh: dict[int, int] = {}
        if not _cocoa():
            return own_numbers()
        for w in _top_level_widgets():
            wid = int(w.internalWinId() or 0)      # (never creates a native window)
            if not _objc.is_own(wid):
                continue
            nswin = _view_window(wid)
            if nswin is not None and int(nswin.windowNumber()) > 0:
                fresh[int(nswin.windowNumber())] = wid
        with _lock:
            _numbers.clear()
            _numbers.update(fresh)
            _through.intersection_update(fresh)
        return dict(fresh)
    return _main(read, own_numbers())


def numbers_for(wins: set[int] | frozenset[int]) -> dict[int, int]:
    """Window number -> id for those of ``wins`` that are the cat's live windows."""
    wanted = {int(w) for w in wins if _objc.is_own(w)}
    if not wanted:
        return {}
    return {n: w for n, w in refresh().items() if w in wanted}


def alive(win: WindowId) -> bool:
    """True while the cat's window is there."""
    return _main(lambda: _widget(win) is not None, False)


def visible(win: WindowId) -> bool:
    """True while the cat's window is shown."""
    def read() -> bool:
        w = _widget(win)
        return bool(w is not None and w.isVisible())
    return _main(read, False)


def minimized(win: WindowId) -> bool:
    def read() -> bool:
        w = _widget(win)
        return bool(w is not None and w.isMinimized())
    return _main(read, False)


def frame(win: WindowId) -> Rect | None:
    """The cat's window's frame in points (Qt's logical coordinates), or None if it's gone."""
    def read() -> Rect | None:
        w = _widget(win)
        if w is None:
            return None
        g = w.frameGeometry()
        return g.x(), g.y(), g.x() + g.width(), g.y() + g.height()
    return _main(read, None)


def title(win: WindowId) -> str:
    def read() -> str:
        w = _widget(win)
        return str(w.windowTitle()) if w is not None else ""
    return _main(read, "")


# ------------------------------------------------------------------ the window port's own-window calls
def _set_accessory() -> None:
    global _accessory
    if _accessory:
        return
    _accessory = True
    try:
        _objc.AK.NSApplication.sharedApplication().setActivationPolicy_(ACCESSORY)
    except Exception:  # noqa: BLE001 - keeps its Dock icon, still works
        log.debug("couldn't make the app an accessory app", exc_info=True)


def _float_on_top(nswin: Any) -> None:
    nswin.setLevel_(STATUS_LEVEL)
    nswin.setCollectionBehavior_(COLLECTION)
    nswin.setHidesOnDeactivate_(False)
    nswin.setCanHide_(False)
    nswin.setAnimationBehavior_(ANIMATION_NONE)


def make_tool_window(win: WindowId) -> None:
    """No Dock or Cmd+Tab entry, never takes the focus, on every Space, above other apps."""
    def apply() -> None:
        nswin = _nswindow(win)
        if nswin is None:
            return
        _set_accessory()
        _float_on_top(nswin)
        remember(int(nswin.windowNumber()), win)
    _main(apply, None)


def set_click_through(win: WindowId, enabled: bool) -> None:
    """Let clicks fall through the window (or catch them again)."""
    def apply() -> None:
        nswin = _nswindow(win)
        if nswin is None:
            return
        nswin.setIgnoresMouseEvents_(bool(enabled))
        number = int(nswin.windowNumber())
        remember(number, win)
        with _lock:
            (_through.add if enabled else _through.discard)(number)
    _main(apply, None)


def _order_front(w: Any, nswin: Any) -> None:
    """Back to the cat's level and in front of it, if shown (ordering a hidden window in would show it behind
    Qt's back)."""
    nswin.setLevel_(STATUS_LEVEL)
    if w.isVisible() and nswin.isVisible():
        nswin.orderFrontRegardless()


def move(win: WindowId, x: int, y: int, w: int, h: int, topmost: bool = True) -> None:
    """Move and size the window through Qt (it knows its geometry and flips the y axis); ``topmost``: in front of
    everything at the cat's level again."""
    def apply() -> None:
        widget = _widget(win)
        if widget is None:
            return
        widget.setGeometry(int(x), int(y), int(w), int(h))
        if topmost:
            nswin = _nswindow(win)
            if nswin is not None:
                _order_front(widget, nswin)
    _main(apply, None)


def keep_topmost(win: WindowId) -> None:
    """Back on top of everything."""
    def apply() -> None:
        widget = _widget(win)
        nswin = _nswindow(win)
        if widget is not None and nswin is not None:
            _order_front(widget, nswin)
    _main(apply, None)


def _level_of(number: int) -> int:
    """Another window's level (its CG layer), 0 if unknown."""
    try:
        rows = _objc.Q.CGWindowListCopyWindowInfo(8, number)        # kCGWindowListOptionIncludingWindow
        for row in rows or ():
            return int(row.get("kCGWindowLayer", 0))
    except Exception:  # noqa: BLE001
        log.debug("level of window %s unknown", number, exc_info=True)
    return NORMAL_LEVEL


def place_below(win: WindowId, other: WindowId) -> None:
    """Right under another window (another app's, or one of the cat's): the cat's window drops to that window's
    level and is ordered just below it (``keep_topmost`` brings it back up)."""
    def apply() -> None:
        nswin = _nswindow(win)
        if nswin is None:
            return
        number = _number(other) if _objc.is_own(other) else int(other)
        if not number:
            return
        level = int(_nswindow(other).level()) if _objc.is_own(other) else _level_of(number)
        nswin.setLevel_(level)
        nswin.orderWindow_relativeTo_(WINDOW_BELOW, number)
    _main(apply, None)


def own_click_through(own_pid: int, keep: WindowId) -> list[WindowId]:
    """Make the cat's visible windows except ``keep`` click-through; the ones that changed."""
    def apply() -> list[WindowId]:
        if not _cocoa():
            return []
        changed: list[WindowId] = []
        for w in _top_level_widgets():
            wid = int(w.internalWinId() or 0)
            if wid == int(keep or 0) or not _objc.is_own(wid) or not w.isVisible():
                continue
            nswin = _view_window(wid)
            if nswin is None or not nswin.isVisible() or nswin.ignoresMouseEvents():
                continue
            nswin.setIgnoresMouseEvents_(True)
            with _lock:
                _through.add(int(nswin.windowNumber()))
            changed.append(wid)
        return changed
    return _main(apply, [])


def end_own_click_through(changed: list[WindowId]) -> None:
    """Undo :func:`own_click_through` for the windows still there."""
    def apply() -> None:
        for win in changed:
            nswin = _nswindow(win)
            if nswin is None:
                continue
            nswin.setIgnoresMouseEvents_(False)
            with _lock:
                _through.discard(int(nswin.windowNumber()))
    if changed:
        _main(apply, None)
