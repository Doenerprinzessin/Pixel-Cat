"""DisplayPort on macOS: the screens in Quartz points (Qt's logical coordinates), DPI 96 for every screen.

* A monitor's ``rect`` is its ``CGDisplayBounds`` (global points, origin at the main display's top-left, y down);
  its ``work`` area is ``NSScreen.visibleFrame`` (no menu bar, no Dock) flipped to the same coordinates.
* DPI is 96 everywhere, so the cat's art is sized in points; a Retina screen draws each point as 2x2 pixels.
* ``user_state`` is FULLSCREEN_APP while a game has captured a display (``CGDisplayIsCaptured``) or a window at the
  shielding level covers one; presentation mode can't be read on macOS.
* ``session_locked`` reads ``CGSSessionScreenIsLocked`` from ``CGSessionCopyCurrentDictionary`` (no permission).

NSScreen is AppKit: :meth:`MacDisplay.monitors` reads it on the GUI thread (other threads get the last answer).
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from . import _objc
from ...domain import FALLBACK_MONITOR, Monitor, UserState, WindowId
from ...ports import Rect

log = logging.getLogger(__name__)

DPI = 96        # the art is sized in points; Retina draws each point as 2x2 pixels
SHIELDING_LEVEL = 2147483628        # CGShieldingWindowLevel(): what a display capture puts above everything
_MAX_DISPLAYS = 32
_ON_SCREEN = 1 | 16                 # kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements

_lock = threading.Lock()
_last: list[Monitor] = []           # the latest monitors read on the GUI thread


def cached_monitors() -> list[Monitor]:
    """The monitors as last read (any thread); empty before the first read."""
    with _lock:
        return list(_last)


def _bounds(r: Any) -> Rect:
    x, y, w, h = float(r.origin.x), float(r.origin.y), float(r.size.width), float(r.size.height)
    return round(x), round(y), round(x + w), round(y + h)


def display_ids() -> list[int]:
    """The active displays (not the ones mirroring another), the main one first."""
    err, ids, count = _objc.Q.CGGetActiveDisplayList(_MAX_DISPLAYS, None, None)
    if err:
        return []
    out = [int(d) for d in list(ids or ())[:int(count)] if not _objc.Q.CGDisplayMirrorsDisplay(d)]
    main = int(_objc.Q.CGMainDisplayID())
    return sorted(out, key=lambda d: d != main)


def _read_monitors() -> list[Monitor]:
    screens = list(_objc.AK.NSScreen.screens() or ())
    by_id: dict[int, Any] = {}
    for s in screens:
        try:
            by_id[int(s.deviceDescription()["NSScreenNumber"])] = s
        except Exception:  # noqa: BLE001 - a screen without a number: it gets no work area of its own
            log.debug("NSScreen without a display number", exc_info=True)
    ids = display_ids()
    out: list[Monitor] = []
    if screens:         # Cocoa's y axis goes up from the bottom of the first screen (the menu bar's)
        main_h = float(screens[0].frame().size.height)
    else:
        main_h = float(_objc.Q.CGDisplayBounds(ids[0]).size.height) if ids else 0.0
    for d in ids:
        rect = _bounds(_objc.Q.CGDisplayBounds(d))
        work, device = rect, f"Display {d}"
        s = by_id.get(d)
        if s is not None:
            vf = s.visibleFrame()
            x, y, w, h = float(vf.origin.x), float(vf.origin.y), float(vf.size.width), float(vf.size.height)
            top = main_h - (y + h)
            work = (round(x), round(top), round(x + w), round(top + h))
            device = str(s.localizedName() or device)
        out.append(Monitor(handle=d, rect=rect, work=work, dpi=DPI, primary=bool(_objc.Q.CGDisplayIsMain(d)),
                           device=device))
    return sorted(out, key=lambda m: not m.primary)


def shielded(windows: list[Any], displays: list[Rect]) -> bool:
    """True if a window at the shielding level (or above) covers one of the displays."""
    for w in windows:
        try:
            if int(w.get("kCGWindowLayer", 0)) < SHIELDING_LEVEL:
                continue
            b = w["kCGWindowBounds"]
            x, y = float(b["X"]), float(b["Y"])
            r = (x, y, x + float(b["Width"]), y + float(b["Height"]))
        except Exception:  # noqa: BLE001 - a row without bounds
            continue
        if any(r[0] <= d[0] and r[1] <= d[1] and r[2] >= d[2] and r[3] >= d[3] for d in displays):
            return True
    return False


def screen_state(captured: bool, shield: bool) -> UserState:
    """A captured display or a shielding window: a full-screen game (or video) owns the screen."""
    return UserState.FULLSCREEN_APP if captured or shield else UserState.NORMAL


class MacDisplay:
    def set_dpi_awareness(self) -> bool:
        return True         # Cocoa always scales for the screen

    def monitors(self) -> list[Monitor]:
        if not _objc.is_main_thread():
            return _objc.on_main(self.monitors, 1.0, cached_monitors() or [FALLBACK_MONITOR])
        try:
            mons = _read_monitors()
        except Exception:  # noqa: BLE001 - pyobjc missing or the API failed: the core needs a monitor anyway
            log.debug("reading the monitors failed", exc_info=True)
            mons = []
        if mons:
            with _lock:
                _last[:] = mons
        return mons or [FALLBACK_MONITOR]

    def virtual_screen(self) -> Rect:
        try:
            rects = [_bounds(_objc.Q.CGDisplayBounds(d)) for d in display_ids()]
        except Exception:  # noqa: BLE001
            log.debug("reading the displays failed", exc_info=True)
            rects = []
        if not rects:
            return FALLBACK_MONITOR.rect
        return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects),
                max(r[3] for r in rects))

    def window_dpi(self, win: WindowId) -> int:
        return DPI

    def user_state(self) -> UserState:
        try:
            ids = display_ids()
            captured = any(bool(_objc.Q.CGDisplayIsCaptured(d)) for d in ids)
            shield = False
            if not captured:
                rows = list(_objc.Q.CGWindowListCopyWindowInfo(_ON_SCREEN, 0) or ())
                shield = shielded(rows, [_bounds(_objc.Q.CGDisplayBounds(d)) for d in ids])
            return screen_state(captured, shield)
        except Exception:  # noqa: BLE001
            log.debug("user state unknown", exc_info=True)
            return UserState.NORMAL

    def session_locked(self) -> bool:
        try:
            info = _objc.Q.CGSessionCopyCurrentDictionary()
            return bool(info and info.get("CGSSessionScreenIsLocked", False))
        except Exception:  # noqa: BLE001
            log.debug("session state unknown", exc_info=True)
            return False

    def qt_logical(self) -> bool:
        return True         # Quartz points are Qt's logical coordinates
