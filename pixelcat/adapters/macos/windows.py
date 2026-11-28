"""WindowPort on macOS: other apps' windows from the Quartz window list (``CGWindowListCopyWindowInfo``), titles
and window actions through Accessibility; the cat's own windows through ``ownwin``.

* A window id is a CGWindowID (32 bit); the cat's own windows keep their Qt ids (NSView pointers, >= 2**32) and are
  answered by ``ownwin`` (Qt on the GUI thread).  Rects are Quartz points, like every other port on macOS.
* The window list needs no permission for bounds, owner pid, layer and on-screen state.  Titles need Screen
  Recording (``kCGWindowName``); without it they come from Accessibility if that is already granted, else they are
  empty.  Nothing here ever asks for a permission except the window actions the user asked for
  (``window_action``, ``close_windows``: :class:`~pixelcat.domain.PermissionNeeded`).  ``bring_to_front`` uses
  Accessibility when it may and otherwise just activates the app.
* The game loop asks about the window under the cat five times per frame; one window's row is cached for 15 ms,
  the on-screen list too.

Full screen (``is_fullscreen``: "the user is watching or playing full screen - hide the cat"): native full-screen
apps (the green button: an editor, a browser, a terminal in their own Space) cover the whole display too, and the
cat stays over those.  So a window counts only if it covers the monitor AND its app keeps the display awake (holds a
``PreventUserIdleDisplaySleep`` / ``NoDisplaySleepAssertion`` power assertion, as video players, browsers playing
video and games do).  The assertions are read without any permission (IOKit ``IOPMCopyAssertionsByProcess``, else
``pmset -g assertions``), only when a window covers its monitor, at most every 2 s.  Known misses: a paused video
drops its assertion, so the cat comes back over it; an app that keeps the display awake for something else
(a presentation, a video call, a caffeinate-like tool) hides the cat while it is full screen; an assertion taken by
a helper process that doesn't name the app (``AssertionOnBehalfOfPID``) isn't seen.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import _objc, display, ownwin, permissions
from ...domain import AppWindow, WindowId, WinInfo, monitor_at
from ...ports import Rect

log = logging.getLogger(__name__)

# CGWindowListOption
_ALL = 0
_ON_SCREEN = 1
_INCLUDING_WINDOW = 8
_NO_DESKTOP = 16

MIN_W, MIN_H = 140, 70          # smaller windows are no furniture for the cat (as on Windows)
MIN_APP = 40                    # smaller layer-0 windows are no app windows (helpers, drag images)
DOCK_LEVEL = 20                 # from the Dock up: system surfaces, not furniture
_IGNORED_OWNERS = {"Window Server", "Dock", "WindowManager", "Control Center", "Notification Center", "Spotlight",
                   "screencaptureui", "Screenshot"}
OWN_CLASS = "PixelCatOwn"

_TTL = 0.015                    # seconds one window list answers again
_ASSERT_TTL = 2.0
DISPLAY_ASSERTIONS = frozenset({"PreventUserIdleDisplaySleep", "NoDisplaySleepAssertion"})
_ON_BEHALF = ("AssertionOnBehalfOfPID", "AssertOnBehalfOfPID")

AX_CANNOT_COMPLETE = -25204     # kAXErrorCannotComplete: the app doesn't answer
_AX_TIMEOUT = 1.0
_POINT, _SIZE = 1, 2            # kAXValueCGPointType, kAXValueCGSizeType
_REGULAR = 0                    # NSApplicationActivationPolicyRegular
_IGNORING_OTHER_APPS = 2        # NSApplicationActivateIgnoringOtherApps


@dataclass(frozen=True)
class Row:
    """One window of the Quartz window list."""

    id: int
    pid: int
    owner: str
    name: str
    layer: int
    alpha: float
    onscreen: bool
    rect: Rect

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    def contains(self, x: float, y: float) -> bool:
        return self.rect[0] <= x < self.rect[2] and self.rect[1] <= y < self.rect[3]


def _row(d: Any) -> Row | None:
    try:
        b = d["kCGWindowBounds"]
        x, y, w, h = float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"])
        return Row(id=int(d["kCGWindowNumber"]), pid=int(d.get("kCGWindowOwnerPID", 0) or 0),
                   owner=str(d.get("kCGWindowOwnerName", "") or ""), name=str(d.get("kCGWindowName", "") or ""),
                   layer=int(d.get("kCGWindowLayer", 0) or 0), alpha=float(d.get("kCGWindowAlpha", 1.0) or 0.0),
                   onscreen=bool(d.get("kCGWindowIsOnscreen", False)),
                   rect=(round(x), round(y), round(x + w), round(y + h)))
    except Exception:  # noqa: BLE001 - a row without the basics
        return None


def _rows(option: int, relative: int = 0) -> list[Row]:
    out = []
    for d in _objc.Q.CGWindowListCopyWindowInfo(option, relative) or ():
        r = _row(d)
        if r is not None:
            out.append(r)
    return out


_lock = threading.Lock()
_screen_cache: tuple[float, list[Row]] | None = None
_one_cache: dict[int, tuple[float, Row | None]] = {}
_assert_cache: tuple[float, frozenset[int]] | None = None


def clear_caches() -> None:
    """Forget the cached window lists and assertions (tests)."""
    global _screen_cache, _assert_cache
    with _lock:
        _screen_cache = None
        _assert_cache = None
        _one_cache.clear()


def on_screen() -> list[Row]:
    """The on-screen windows (no desktop), front to back."""
    global _screen_cache
    now = time.monotonic()
    with _lock:
        if _screen_cache is not None and now - _screen_cache[0] < _TTL:
            return _screen_cache[1]
    rows = _rows(_ON_SCREEN | _NO_DESKTOP)
    with _lock:
        _screen_cache = (now, rows)
    return rows


def info(win: WindowId) -> Row | None:
    """One window's row (also off-screen ones: minimised, on another Space), or None if it's gone."""
    if win <= 0 or _objc.is_own(win):
        return None
    now = time.monotonic()
    with _lock:
        hit = _one_cache.get(win)
        if hit is not None and now - hit[0] < _TTL:
            return hit[1]
    row = next((r for r in _rows(_INCLUDING_WINDOW, int(win)) if r.id == win), None)
    with _lock:
        if len(_one_cache) > 64:
            _one_cache.clear()
        _one_cache[int(win)] = (now, row)
    return row


def _furniture(r: Row) -> bool:
    """A window the cat may treat as an app's window (normal or floating, not a system surface)."""
    return 0 <= r.layer < DOCK_LEVEL and r.alpha > 0.05 and r.owner not in _IGNORED_OWNERS


def _work_areas() -> list[Rect]:
    mons = display.cached_monitors() or display.MacDisplay().monitors()
    return [m.work for m in mons]


# ------------------------------------------------------------------ full screen
def covers(rect: Rect, monitor: Rect) -> bool:
    return rect[0] <= monitor[0] and rect[1] <= monitor[1] and rect[2] >= monitor[2] and rect[3] >= monitor[3]


def watching_full_screen(rect: Rect, monitor: Rect, keeps_display_awake: Callable[[], bool]) -> bool:
    """The cat hides for this window: it covers its monitor and its app keeps the display awake (a video, a game).
    A native full-screen app (the green button) covers it too, but doesn't keep the display awake: the cat stays.
    ``keeps_display_awake`` is asked only for a covering window."""
    return covers(rect, monitor) and bool(keeps_display_awake())


_IOPM: list[Callable[..., Any]] = []         # IOPMCopyAssertionsByProcess, loaded once


def _iopm() -> Callable[..., Any]:
    if not _IOPM:
        bundle = _objc.FN.NSBundle.bundleWithPath_("/System/Library/Frameworks/IOKit.framework")
        found: dict[str, Any] = {}
        # (a Copy function: the dictionary is ours already, so pyobjc mustn't retain it again)
        _objc.objc.loadBundleFunctions(bundle, found, [("IOPMCopyAssertionsByProcess", b"io^@", "",
                                                        {"arguments": {0: {"already_cfretained": True}}})])
        _IOPM.append(found["IOPMCopyAssertionsByProcess"])
    return _IOPM[0]


def awake_pids_from(by_pid: Any) -> frozenset[int]:
    """The pids holding (or named by) an active display-sleep assertion, from ``IOPMCopyAssertionsByProcess``."""
    pids: set[int] = set()
    for pid, assertions in dict(by_pid or {}).items():
        for a in assertions or ():
            kind = a.get("AssertType") or a.get("AssertionTrueType")
            if kind not in DISPLAY_ASSERTIONS or not int(a.get("AssertLevel", 255) or 0):
                continue
            pids.add(int(pid))
            for key in _ON_BEHALF:
                if a.get(key):
                    pids.add(int(a.get(key)))
    return frozenset(pids)


_PMSET_PID = re.compile(r"^\s*pid (\d+)\(.*?\):\s*\[[^\]]*\]\s*\S+\s+(\w+)")
_PMSET_FOR = re.compile(r"Created for PID:\s*(\d+)")


def awake_pids_from_pmset(text: str) -> frozenset[int]:
    """The same from ``pmset -g assertions``' "Listed by owning process" lines."""
    pids: set[int] = set()
    current = False
    for line in text.splitlines():
        m = _PMSET_PID.match(line)
        if m:
            current = m.group(2) in DISPLAY_ASSERTIONS
            if current:
                pids.add(int(m.group(1)))
            continue
        f = _PMSET_FOR.search(line)
        if f and current:
            pids.add(int(f.group(1)))
    return frozenset(pids)


def display_awake_pids() -> frozenset[int]:
    """Pids that keep the display awake right now (cached for 2 s)."""
    global _assert_cache
    now = time.monotonic()
    with _lock:
        if _assert_cache is not None and now - _assert_cache[0] < _ASSERT_TTL:
            return _assert_cache[1]
    pids: frozenset[int] = frozenset()
    try:
        err, by_pid = _iopm()(None)
        if err:
            raise OSError(err, "IOPMCopyAssertionsByProcess failed")
        pids = awake_pids_from(by_pid)
    except Exception:  # noqa: BLE001 - IOKit not reachable: ask pmset
        log.debug("IOPMCopyAssertionsByProcess failed, using pmset", exc_info=True)
        try:
            out = _objc.run(["/usr/bin/pmset", "-g", "assertions"], timeout=2.0)
            pids = awake_pids_from_pmset(out.stdout or "")
        except Exception:  # noqa: BLE001 - unknown: nobody keeps it awake (the cat stays)
            log.debug("pmset -g assertions failed", exc_info=True)
    with _lock:
        _assert_cache = (now, pids)
    return pids


# ------------------------------------------------------------------ Accessibility (only when already trusted)
class _Hung(RuntimeError):
    pass


_get_window: Any = None     # _AXUIElementGetWindow, False if it can't be loaded


def _ax_get(elem: Any, attr: str) -> Any:
    err, value = _objc.AX.AXUIElementCopyAttributeValue(elem, attr, None)
    if err == AX_CANNOT_COMPLETE:
        raise _Hung(attr)
    return value if err == 0 else None


def _ax_app(pid: int, timeout: float = _AX_TIMEOUT) -> Any:
    app = _objc.AX.AXUIElementCreateApplication(int(pid))
    _objc.AX.AXUIElementSetMessagingTimeout(app, float(timeout))
    return app


def _ax_number(elem: Any) -> int:
    """The CGWindowID of an AX window (private ``_AXUIElementGetWindow``; 0 if it can't tell)."""
    global _get_window
    if _get_window is None:
        try:
            bundle = _objc.FN.NSBundle.bundleWithPath_(
                "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework")
            found: dict[str, Any] = {}
            _objc.objc.loadBundleFunctions(bundle, found, [("_AXUIElementGetWindow", b"i@o^I")])
            _get_window = found["_AXUIElementGetWindow"]
        except Exception:  # noqa: BLE001 - match by frame instead
            log.debug("_AXUIElementGetWindow not available", exc_info=True)
            _get_window = False
    if not _get_window:
        return 0
    try:
        err, number = _get_window(elem, None)
        return int(number) if err == 0 else 0
    except Exception:  # noqa: BLE001
        return 0


def _ax_rect(elem: Any) -> Rect | None:
    pos, size = _ax_get(elem, "AXPosition"), _ax_get(elem, "AXSize")
    if pos is None or size is None:
        return None
    ok1, p = _objc.AX.AXValueGetValue(pos, _POINT, None)
    ok2, s = _objc.AX.AXValueGetValue(size, _SIZE, None)
    if not (ok1 and ok2):
        return None
    x, y = float(p.x), float(p.y)
    return round(x), round(y), round(x + float(s.width)), round(y + float(s.height))


def _ax_windows(pid: int) -> list[tuple[int, Any]]:
    """The app's AX windows with their CGWindowIDs (matched by frame if the id can't be read; 0 if neither)."""
    elems = list(_ax_get(_ax_app(pid), "AXWindows") or ())
    rows: list[Row] | None = None
    out = []
    for e in elems:
        number = _ax_number(e)
        if not number:
            if rows is None:
                rows = [r for r in _rows(_ALL | _NO_DESKTOP) if r.pid == pid and r.layer == 0]
            rect = _ax_rect(e)
            number = next((r.id for r in rows if r.rect == rect), 0) if rect else 0
        out.append((number, e))
    return out


def _ax_window(pid: int, win: WindowId) -> Any:
    return next((e for n, e in _ax_windows(pid) if n == win), None)


def _ax_minimized(win: WindowId, pid: int) -> bool:
    if not permissions.accessibility():
        return False
    try:
        e = _ax_window(pid, win)
        return bool(e is not None and _ax_get(e, "AXMinimized"))
    except Exception:  # noqa: BLE001
        log.debug("AXMinimized of %s unknown", win, exc_info=True)
        return False


def _ax_title(win: WindowId, pid: int) -> str:
    if not permissions.accessibility():
        return ""
    try:
        e = _ax_window(pid, win)
        return str(_ax_get(e, "AXTitle") or "") if e is not None else ""
    except Exception:  # noqa: BLE001
        log.debug("AXTitle of %s unknown", win, exc_info=True)
        return ""


def _ax_do(err: int, what: str) -> None:
    if err == AX_CANNOT_COMPLETE:
        raise _Hung(what)


def _frontmost_pid() -> int:
    app = _objc.AK.NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(app.processIdentifier()) if app is not None else 0


def _running_app(pid: int) -> Any:
    return _objc.AK.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))


def _exe(app: Any) -> str:
    try:
        url = app.executableURL()
        name = str(url.lastPathComponent()) if url is not None else ""
    except Exception:  # noqa: BLE001
        name = ""
    return name or str(app.localizedName() or "")


class MacWindows:
    # -------------------------------------------------------------- listing
    def top_level(self, own_pid: int, include: set[int] | frozenset[int] = frozenset()) -> list[WinInfo]:
        try:
            rows = on_screen()
            own = ownwin.numbers_for(include) if include else {}
            works = set(_work_areas())
        except Exception:  # noqa: BLE001 - the world then has only the screen edges
            log.debug("window list failed", exc_info=True)
            return []
        out: list[WinInfo] = []
        for r in rows:
            if r.pid == own_pid:
                wid = own.get(r.id)
                if wid is not None and r.width >= MIN_W and r.height >= MIN_H // 2:
                    out.append(WinInfo(hwnd=wid, rect=r.rect, z=len(out), title="Pixel Cat", cls=OWN_CLASS,
                                       maximized=False, topmost=True))
                continue
            if not _furniture(r) or r.width < MIN_W or r.height < MIN_H:
                continue
            out.append(WinInfo(hwnd=r.id, rect=r.rect, z=len(out), title=r.name, cls="",
                               maximized=r.rect in works, topmost=r.layer > 0))
        return out

    def app_windows(self, own_pid: int) -> list[AppWindow]:
        apps: dict[int, str] = {}
        for app in _objc.AK.NSWorkspace.sharedWorkspace().runningApplications() or ():
            try:
                if int(app.activationPolicy()) == _REGULAR:
                    apps[int(app.processIdentifier())] = _exe(app)
            except Exception:  # noqa: BLE001
                continue
        rows = [r for r in _rows(_ALL | _NO_DESKTOP)
                if r.layer == 0 and r.pid in apps and r.pid != own_pid and r.alpha > 0.05
                and r.width >= MIN_APP and r.height >= MIN_APP]
        ax: dict[int, tuple[str, bool]] = {}
        if permissions.accessibility():
            for pid in {r.pid for r in rows}:
                try:
                    for number, e in _ax_windows(pid):
                        if number:
                            ax[number] = (str(_ax_get(e, "AXTitle") or ""), bool(_ax_get(e, "AXMinimized")))
                except Exception:  # noqa: BLE001 - a hung app: its on-screen windows still count
                    log.debug("AX windows of %s unknown", pid, exc_info=True)
        works = set(_work_areas())
        out: list[AppWindow] = []
        seen: set[int] = set()
        for r in rows:
            if r.id in seen or not (r.onscreen or r.id in ax):
                continue
            seen.add(r.id)
            ax_title, mini = ax.get(r.id, ("", False))
            out.append(AppWindow(hwnd=r.id, title=r.name or ax_title, exe=apps[r.pid],
                                 minimized=mini and not r.onscreen, maximized=r.rect in works, cls="", pid=r.pid))
        return out

    def foreground(self) -> WindowId:
        try:
            pid = _frontmost_pid()
            if not pid:
                return 0
            rows = on_screen()
            if pid == os.getpid():
                numbers = ownwin.own_numbers()
                mine = [r for r in rows if r.pid == pid]
                hit = next((r for r in mine if r.id in numbers), None) or next(
                    (r for r in mine if 0 <= r.layer < DOCK_LEVEL), None)
                return (numbers.get(hit.id) or hit.id) if hit is not None else 0
            hit = next((r for r in rows if r.pid == pid and r.layer == 0 and r.alpha > 0.05
                        and r.width >= MIN_APP and r.height >= MIN_APP), None) or next(
                (r for r in rows if r.pid == pid and _furniture(r)), None)
            return hit.id if hit is not None else 0
        except Exception:  # noqa: BLE001
            log.debug("foreground unknown", exc_info=True)
            return 0

    def is_fullscreen(self, win: WindowId, monitor_rect: Rect) -> bool:
        try:
            r = info(win)
            if r is None or r.owner in _IGNORED_OWNERS:
                return False
            return watching_full_screen(r.rect, monitor_rect, lambda: r.pid in display_awake_pids())
        except Exception:  # noqa: BLE001
            log.debug("full screen check failed", exc_info=True)
            return False

    # -------------------------------------------------------------- one window
    def exists(self, win: WindowId) -> bool:
        if _objc.is_own(win):
            return ownwin.alive(win)
        try:
            return info(win) is not None
        except Exception:  # noqa: BLE001
            return False

    def is_visible(self, win: WindowId) -> bool:
        if _objc.is_own(win):
            return ownwin.visible(win)
        try:
            r = info(win)
        except Exception:  # noqa: BLE001
            return False
        return bool(r is not None and r.onscreen)

    def is_minimized(self, win: WindowId) -> bool:
        if _objc.is_own(win):
            return ownwin.minimized(win)
        try:
            r = info(win)
        except Exception:  # noqa: BLE001
            return False
        if r is None or r.onscreen:          # (an on-screen window isn't minimised: no AX round trip)
            return False
        return _ax_minimized(win, r.pid)

    def is_hung(self, win: WindowId) -> bool:
        return False

    def is_cloaked(self, win: WindowId) -> bool:
        if _objc.is_own(win):
            return False
        try:
            r = info(win)
        except Exception:  # noqa: BLE001
            return False
        return bool(r is not None and not r.onscreen and not self.is_minimized(win))

    def frame_bounds(self, win: WindowId) -> Rect | None:
        if _objc.is_own(win):
            return ownwin.frame(win)
        try:
            r = info(win)
        except Exception:  # noqa: BLE001
            return None
        return r.rect if r is not None else None

    def rect(self, win: WindowId) -> Rect:
        return self.frame_bounds(win) or (0, 0, 0, 0)

    def pid(self, win: WindowId) -> int:
        if _objc.is_own(win):
            return os.getpid() if ownwin.alive(win) else 0
        try:
            r = info(win)
        except Exception:  # noqa: BLE001
            return 0
        return r.pid if r is not None else 0

    def title(self, win: WindowId) -> str:
        if _objc.is_own(win):
            return ownwin.title(win)
        try:
            r = info(win)
        except Exception:  # noqa: BLE001
            return ""
        if r is None:
            return ""
        return r.name or _ax_title(win, r.pid)

    def class_name(self, win: WindowId) -> str:
        return ""

    def window_at(self, x: int, y: int) -> WindowId:
        try:
            rows = on_screen()
        except Exception:  # noqa: BLE001
            return 0
        me = os.getpid()
        through = ownwin.click_through_numbers()
        numbers = ownwin.own_numbers()
        for r in rows:
            if r.alpha <= 0.05 or not r.contains(x, y):
                continue
            if r.pid == me:
                if r.id in through:              # (clicks fall through the cat: the window below gets them)
                    continue
                return numbers.get(r.id, r.id)
            return r.id
        return 0

    def active_popup(self, win: WindowId) -> WindowId:
        if _objc.is_own(win) or not permissions.accessibility():
            return win
        try:
            r = info(win)
            if r is None:
                return win
            windows = _ax_windows(r.pid)
            mine = next((e for n, e in windows if n == win), None)
            if mine is not None:
                for child in _ax_get(mine, "AXChildren") or ():
                    if _ax_get(child, "AXRole") == "AXSheet":
                        number = _ax_number(child) or self._number_by_frame(r.pid, _ax_rect(child))
                        if number and number != win:
                            return number
            for number, e in windows:
                if number and number != win and _ax_get(e, "AXSubrole") in ("AXDialog", "AXSystemDialog"):
                    found = info(number)
                    if found is not None and found.onscreen:
                        return number
        except Exception:  # noqa: BLE001 - (a hung app: no popup known)
            log.debug("active popup of %s unknown", win, exc_info=True)
        return win

    @staticmethod
    def _number_by_frame(pid: int, rect: Rect | None) -> int:
        if rect is None:
            return 0
        return next((r.id for r in on_screen() if r.pid == pid and r.rect == rect), 0)

    def same_app(self, a: WindowId, b: WindowId) -> bool:
        if not a or not b:
            return False
        if a == b:
            return True
        pa, pb = self.pid(a), self.pid(b)
        return bool(pa) and pa == pb

    def is_foreground(self, win: WindowId) -> bool:
        try:
            front = _frontmost_pid()
        except Exception:  # noqa: BLE001
            return False
        return bool(front) and front == self.pid(win)

    # -------------------------------------------------------------- doing things with other apps' windows
    def bring_to_front(self, win: WindowId, timeout: float = 1.5) -> bool:
        if _objc.is_own(win):
            return False
        r = info(win)
        if r is None:
            return False
        trusted = permissions.accessibility()
        if trusted:
            try:
                app = _ax_app(r.pid, min(timeout, _AX_TIMEOUT))
                e = _ax_window(r.pid, win)
                if e is not None:
                    if _ax_get(e, "AXMinimized"):
                        _ax_do(_objc.AX.AXUIElementSetAttributeValue(e, "AXMinimized", False), "AXMinimized")
                    _ax_do(_objc.AX.AXUIElementPerformAction(e, "AXRaise"), "AXRaise")
                _ax_do(_objc.AX.AXUIElementSetAttributeValue(app, "AXFrontmost", True), "AXFrontmost")
            except _Hung:
                raise RuntimeError("that app is not responding right now") from None
            except Exception:  # noqa: BLE001 - activating the app below still helps
                log.debug("AX raise of %s failed", win, exc_info=True)
        running = _running_app(r.pid)
        if running is not None and _frontmost_pid() != r.pid:
            if running.isHidden():
                running.unhide()
            running.activateWithOptions_(_IGNORING_OTHER_APPS)
        end = time.monotonic() + max(0.0, timeout)
        while True:
            if _frontmost_pid() == r.pid:
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.05)

    def _ax_target(self, w: AppWindow) -> Any:
        pid = w.pid or self.pid(w.hwnd)
        e = _ax_window(pid, w.hwnd) if pid else None
        if e is None:
            raise RuntimeError(f"I can't reach that {w.app} window any more")
        return e

    def window_action(self, w: AppWindow, action: str) -> None:
        if action not in ("minimize", "maximize", "restore", "focus"):
            raise ValueError(action)
        permissions.need(permissions.ACCESSIBILITY)
        try:
            e = self._ax_target(w)
            set_ = _objc.AX.AXUIElementSetAttributeValue
            if action == "minimize":
                _ax_do(set_(e, "AXMinimized", True), action)
            elif action == "maximize":
                if _ax_get(e, "AXMinimized"):
                    _ax_do(set_(e, "AXMinimized", False), action)
                rect = _ax_rect(e) or self.rect(w.hwnd)
                mons = display.cached_monitors() or display.MacDisplay().monitors()
                l, t, r, b = monitor_at(mons, (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2).work
                _ax_do(set_(e, "AXPosition", _objc.AX.AXValueCreate(_POINT, _objc.Q.CGPoint(l, t))), action)
                _ax_do(set_(e, "AXSize", _objc.AX.AXValueCreate(_SIZE, _objc.Q.CGSize(r - l, b - t))), action)
            elif action == "restore":
                _ax_do(set_(e, "AXMinimized", False), action)
                _ax_do(_objc.AX.AXUIElementPerformAction(e, "AXRaise"), action)
            else:
                if _ax_get(e, "AXMinimized"):
                    _ax_do(set_(e, "AXMinimized", False), action)
                _ax_do(_objc.AX.AXUIElementPerformAction(e, "AXRaise"), action)
                _ax_do(set_(_ax_app(w.pid or self.pid(w.hwnd)), "AXFrontmost", True), action)
        except _Hung:
            raise RuntimeError(f"{w.app} is not responding right now") from None

    def close_windows(self, windows: list[AppWindow]) -> int:
        if not windows:
            return 0
        permissions.need(permissions.ACCESSIBILITY)
        n = 0
        for w in windows:
            try:
                button = _ax_get(self._ax_target(w), "AXCloseButton")
                if button is not None and _objc.AX.AXUIElementPerformAction(button, "AXPress") == 0:
                    n += 1
            except Exception:  # noqa: BLE001 - (gone, or its app hangs): the others still close
                log.debug("closing %s failed", w.hwnd, exc_info=True)
        return n

    # -------------------------------------------------------------- the cat's own windows
    def make_tool_window(self, win: WindowId) -> None:
        ownwin.make_tool_window(win)

    def set_click_through(self, win: WindowId, enabled: bool) -> None:
        ownwin.set_click_through(win, enabled)

    def move(self, win: WindowId, x: int, y: int, w: int, h: int, topmost: bool = True) -> None:
        ownwin.move(win, x, y, w, h, topmost)

    def keep_topmost(self, win: WindowId) -> None:
        ownwin.keep_topmost(win)

    def place_below(self, win: WindowId, other: WindowId) -> None:
        ownwin.place_below(win, other)

    def own_click_through(self, own_pid: int, keep: WindowId) -> list[WindowId]:
        return ownwin.own_click_through(own_pid, keep)

    def end_own_click_through(self, changed: list[WindowId]) -> None:
        ownwin.end_own_click_through(changed)
