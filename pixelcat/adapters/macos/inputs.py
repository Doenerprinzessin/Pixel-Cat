"""InputPort on macOS: the pointer and keyboard state from Quartz (no permission needed) and synthetic input
posted with ``CGEventPost(kCGHIDEventTap)``.

* Reads: the pointer (``CGEventGetLocation``, top-left points like every coordinate of this adapter), the
  buttons (``NSEvent.pressedMouseButtons``) and the idle time (``CGEventSourceSecondsSinceLastEventType``).
* Posting needs Accessibility, and macOS drops posted events *silently* without it, so every synthetic action
  checks first and raises :class:`~pixelcat.domain.PermissionNeeded` (``permissions.need``: these actions are
  always the chat's helper doing what the user asked, never background code).  ``move_cursor`` (only used to
  put the pointer back) quietly does nothing without it.
* Every posted event carries :data:`escwatch.OWN_EVENT_MARK`, and the Esc presses the cat makes are announced to
  the Esc watcher (``escwatch.mark_own``), so they never stop the helper.
* Held keys (:meth:`MacInput.holding`) are posted as flagsChanged events and their flags are also set on every
  mouse event posted inside, so cmd-click / shift-drag work in every app.
* ``pinch``: no public API posts magnify gestures.  When the app under the point is the frontmost one it presses
  cmd + '+' / cmd + '-' (about one press per 90 px of finger travel, the core's zoom step) and returns "";
  otherwise "failed", and the core falls back to its zoom key + mouse wheel.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Callable, Iterator

from . import _objc, escwatch, keys, permissions
from ...domain import WindowId
from ...ports import Keys

HID_EVENT_TAP = 0                # kCGHIDEventTap
HID_SYSTEM_STATE = 1             # kCGEventSourceStateHIDSystemState
ANY_INPUT = 0xFFFFFFFF           # kCGAnyInputEventType
CLICK_STATE = 1                  # kCGMouseEventClickState
SCROLL_UNIT_LINE = 1             # kCGScrollEventUnitLine
MOUSE_MOVED = 5                  # kCGEventMouseMoved
FLAGS_CHANGED = 12               # kCGEventFlagsChanged
SYSTEM_DEFINED = 14              # NSEventTypeSystemDefined
OTHER_EVENT = "otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_"
CMD = keys.MODIFIERS["win"][0]

# button -> (down, up, dragged event type, CGMouseButton)
BUTTONS = {"left": (1, 2, 6, 0), "right": (3, 4, 7, 1), "middle": (25, 26, 27, 2),
           "back": (25, 26, 27, 3), "forward": (25, 26, 27, 4)}

UNICODE_MAX = 20                 # UTF-16 units one key event may carry
ZOOM_STEP_PX = 90.0              # the core's pinch: 90 px more finger distance per zoom step


def _wait(seconds: float, cancel: threading.Event | None) -> bool:
    """Sleep; False if cancelled meanwhile."""
    end = time.perf_counter() + seconds
    while True:
        if cancel is not None and cancel.is_set():
            return False
        left = end - time.perf_counter()
        if left <= 0:
            return True
        time.sleep(min(0.02, left))


def _route(points: list[tuple[int, int]], seconds: float) -> list[tuple[int, int, float]]:
    """Points every few pixels along the line through ``points``, each with the time to send it at."""
    lengths = [((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 for (x1, y1), (x2, y2) in zip(points, points[1:])]
    if not lengths:
        return [(points[0][0], points[0][1], 0.0)]
    total = sum(lengths)
    n = max(2, min(6000, int(max(seconds * 125, total / 6))))
    out: list[tuple[int, int, float]] = []
    seg, done = 0, 0.0
    for i in range(1, n + 1):
        t = i / n
        eased = t * t * (3 - 2 * t) if len(points) == 2 else t      # a hand speeds up and slows down
        want = eased * total
        while seg < len(lengths) - 1 and done + lengths[seg] < want:
            done += lengths[seg]
            seg += 1
        (x1, y1), (x2, y2) = points[seg], points[seg + 1]
        f = 0.0 if lengths[seg] == 0 else min(1.0, max(0.0, (want - done) / lengths[seg]))
        out.append((int(round(x1 + (x2 - x1) * f)), int(round(y1 + (y2 - y1) * f)), t * seconds))
    return out


class MacInput:
    def __init__(self) -> None:
        self.passthrough_until = 0.0
        self._tick = 0
        self._pos = (0, 0)
        self._flags = 0                  # the modifiers held by holding(): set on every mouse event
        self._src: Any = None
        self._src_lock = threading.Lock()

    # ---------------------------------------------------------------- state (no permission)
    def cursor_pos(self) -> tuple[int, int]:
        try:
            p = _objc.Q.CGEventGetLocation(_objc.Q.CGEventCreate(None))
            self._pos = (int(round(p.x)), int(round(p.y)))
        except Exception:  # noqa: BLE001 - keep the last known position
            pass
        return self._pos

    def release_went_elsewhere(self, win: WindowId) -> bool:
        # (no mouse capture on macOS: a press on the cat that is no longer held was let go somewhere else; the
        # overlay waits for two polls in a row before it ends the press)
        return not self.mouse_buttons_down()

    def mouse_buttons_down(self) -> bool:
        try:
            return int(_objc.AK.NSEvent.pressedMouseButtons()) != 0
        except Exception:  # noqa: BLE001
            return False

    def idle_seconds(self) -> float:
        try:
            return max(0.0, float(_objc.Q.CGEventSourceSecondsSinceLastEventType(HID_SYSTEM_STATE, ANY_INPUT)))
        except Exception:  # noqa: BLE001
            return 0.0

    def last_input_tick(self) -> int:
        """A ms tick of the last input.  Computed from the idle time, so it jitters by a few ms from poll to
        poll; it only moves when the input is more than 50 ms newer (jitter would look like typing)."""
        tick = int((time.monotonic() - self.idle_seconds()) * 1000)
        if tick > self._tick + 50:
            self._tick = tick
        return self._tick

    def passthrough_active(self) -> bool:
        return time.monotonic() < self.passthrough_until

    def set_passthrough_until(self, until: float) -> None:
        self.passthrough_until = until

    # ---------------------------------------------------------------- posting
    @staticmethod
    def _require() -> None:
        if permissions.post_events() or permissions.accessibility():
            return
        permissions.need(permissions.ACCESSIBILITY)

    def _source(self) -> Any:
        with self._src_lock:
            if self._src is None:
                src = _objc.Q.CGEventSourceCreate(HID_SYSTEM_STATE)
                if src is not None:
                    # the user's own mouse keeps working right after a posted event (and can interrupt a drag)
                    _objc.Q.CGEventSourceSetLocalEventsSuppressionInterval(src, 0.0)
                self._src = src
            return self._src

    @staticmethod
    def _post(ev: Any) -> None:
        if ev is None:
            return
        _objc.Q.CGEventSetIntegerValueField(ev, escwatch.EVENT_SOURCE_USER_DATA, escwatch.OWN_EVENT_MARK)
        _objc.Q.CGEventPost(HID_EVENT_TAP, ev)

    def _mouse(self, kind: int, x: float, y: float, button: int = 0, clicks: int = 0) -> None:
        Q = _objc.Q
        ev = Q.CGEventCreateMouseEvent(self._source(), kind, (float(x), float(y)), button)
        if ev is None:
            return
        if clicks:
            Q.CGEventSetIntegerValueField(ev, CLICK_STATE, clicks)
        if self._flags:
            Q.CGEventSetFlags(ev, self._flags)
        self._post(ev)

    def _key(self, code: int, down: bool, flags: int) -> None:
        Q = _objc.Q
        ev = Q.CGEventCreateKeyboardEvent(self._source(), code, down)
        if ev is None:
            return
        if code in keys.MODIFIER_FLAGS:
            Q.CGEventSetType(ev, FLAGS_CHANGED)
        Q.CGEventSetFlags(ev, flags)
        self._post(ev)

    def _media(self, code: int, down: bool) -> None:
        key = code - keys.MEDIA_BASE
        state = 0xA if down else 0xB
        make = getattr(_objc.AK.NSEvent, OTHER_EVENT)
        ev = make(SYSTEM_DEFINED, (0.0, 0.0), state << 8, 0.0, 0, None, 8, (key << 16) | (state << 8), -1)
        self._post(ev.CGEvent() if ev is not None else None)

    @staticmethod
    def _mask(codes: list[int]) -> int:
        """The flags of the modifiers among ``codes``."""
        mask = 0
        for code in codes:
            mask |= keys.MODIFIER_FLAGS.get(code, 0)
        return mask

    def _press(self, codes: list[int], flags: int) -> int:
        """Press keys in order; the flags after them."""
        for code in codes:
            if code >= keys.MEDIA_BASE:
                self._media(code, True)
            elif code in keys.MODIFIER_FLAGS:
                flags |= keys.MODIFIER_FLAGS[code]
                self._key(code, True, flags)
            else:
                self._key(code, True, flags)
        return flags

    def _release(self, codes: list[int], flags: int) -> int:
        """Let keys go in reverse order; the flags after them."""
        for code in reversed(codes):
            if code >= keys.MEDIA_BASE:
                self._media(code, False)
            elif code in keys.MODIFIER_FLAGS:
                flags &= ~keys.MODIFIER_FLAGS[code]
                self._key(code, False, flags)
            else:
                self._key(code, False, flags)
        return flags

    # ---------------------------------------------------------------- the mouse
    def move_cursor(self, x: int, y: int) -> None:
        if permissions.post_events() or permissions.accessibility():
            self._mouse(MOUSE_MOVED, x, y)

    def click(self, x: int, y: int, button: str = "left", double: bool = False, count: int = 1, hold: float = 0.0,
              cancel: threading.Event | None = None) -> None:
        self._require()
        down, up, _drag, btn = BUTTONS[button]
        count = 2 if double and count < 2 else max(1, count)
        self._mouse(MOUSE_MOVED, x, y)      # hover first (some apps highlight before they accept a click)
        time.sleep(0.04)
        for n in range(count):
            try:
                self._mouse(down, x, y, btn, n + 1)
                _wait(max(0.02, hold), cancel)
            finally:
                self._mouse(up, x, y, btn, n + 1)      # (a button is never left held down)
            time.sleep(0.02)
            if n < count - 1:
                time.sleep(0.05)

    def scroll(self, x: int, y: int, clicks: int, horizontal: bool = False) -> None:
        self._require()
        Q = _objc.Q
        self._mouse(MOUSE_MOVED, x, y)
        time.sleep(0.03)
        step = 1 if clicks > 0 else -1
        for _ in range(abs(clicks)):
            # (wheel 2 counts to the left: the port's positive horizontal clicks go right)
            ev = (Q.CGEventCreateScrollWheelEvent(self._source(), SCROLL_UNIT_LINE, 2, 0, -step) if horizontal else
                  Q.CGEventCreateScrollWheelEvent(self._source(), SCROLL_UNIT_LINE, 1, step))
            if ev is not None:
                Q.CGEventSetLocation(ev, (float(x), float(y)))
                if self._flags:
                    Q.CGEventSetFlags(ev, self._flags)
                self._post(ev)
            time.sleep(0.03)

    def _trace(self, points: list[tuple[int, int]], seconds: float, kind: int, button: int,
               cancel: threading.Event | None) -> str:
        """Move the pointer through ``points`` like a hand would.  "" when it got there, else why it stopped:
        "cancel" (Esc / Stop) or "user" (the user moved the mouse)."""
        start = time.perf_counter()
        last = self.cursor_pos()
        for x, y, at in _route(points, seconds):
            if cancel is not None and cancel.is_set():
                return "cancel"
            wait = start + at - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            cx, cy = self.cursor_pos()
            if max(abs(cx - last[0]), abs(cy - last[1])) > 60:
                return "user"
            self._mouse(kind, x, y, button)
            last = (x, y)
        time.sleep(0.02)
        return ""

    def drag(self, points: list[tuple[int, int]], button: str = "left", seconds: float = 0.6,
             cancel: threading.Event | None = None) -> str:
        self._require()
        down, up, dragged, btn = BUTTONS[button]
        x0, y0 = points[0]
        self._mouse(MOUSE_MOVED, x0, y0)
        time.sleep(0.06)
        why = "error"
        try:
            self._mouse(down, x0, y0, btn, 1)
            time.sleep(0.12)                  # apps start a drag only after a moment or a few pixels
            why = self._trace(points, seconds, dragged, btn, cancel)
            if not why:
                ex, ey = points[-1]           # a little wiggle over the drop target (some only react to movement)
                self._mouse(dragged, ex + 1, ey, btn)
                time.sleep(0.05)
                self._mouse(dragged, ex, ey, btn)
                time.sleep(0.12)
        finally:
            cx, cy = points[-1] if not why else self.cursor_pos()
            self._mouse(up, cx, cy, btn, 1)
            time.sleep(0.05)
        return why

    def glide(self, x: int, y: int, seconds: float = 0.3, cancel: threading.Event | None = None) -> str:
        self._require()
        return self._trace([self.cursor_pos(), (int(x), int(y))], seconds, MOUSE_MOVED, 0, cancel)

    def pinch(self, x: int, y: int, start: float, end: float, seconds: float = 0.5,
              cancel: threading.Event | None = None) -> str:
        self._require()
        if start == end or not self._frontmost_at(x, y):
            return "failed"
        zoom = keys.zoom_keys(end > start)
        if zoom is None:
            return "failed"
        presses = max(1, min(10, int(round(abs(end - start) / ZOOM_STEP_PX))))
        pause = max(0.05, seconds / presses)
        for n in range(presses):
            if cancel is not None and cancel.is_set():
                return "cancel"
            self.press_combo([CMD, *zoom])
            if n < presses - 1 and not _wait(pause, cancel):
                return "cancel"
        return ""

    @staticmethod
    def _frontmost_at(x: int, y: int) -> bool:
        """True if the topmost normal window at (x, y) (not the cat's) belongs to the frontmost app: the one
        that gets key presses."""
        try:
            front = _objc.AK.NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is None:
                return False
            infos = _objc.Q.CGWindowListCopyWindowInfo(1 | 16, 0) or []   # on screen, no desktop elements
            own = os.getpid()
            for info in infos:
                if int(info.get("kCGWindowLayer", 0)) != 0 or int(info.get("kCGWindowOwnerPID", 0)) == own:
                    continue
                b = info.get("kCGWindowBounds") or {}
                bx, by, bw, bh = (float(b.get(k, 0)) for k in ("X", "Y", "Width", "Height"))
                if bx <= x < bx + bw and by <= y < by + bh:
                    return int(info.get("kCGWindowOwnerPID", 0)) == int(front.processIdentifier())
        except Exception:  # noqa: BLE001 - can't tell: let the core use the wheel
            return False
        return False

    # ---------------------------------------------------------------- the keyboard
    def holding(self, keys: list[str]) -> AbstractContextManager[None]:
        return self._holding(keys)

    @contextmanager
    def _holding(self, names: list[str]) -> Iterator[None]:
        """Keep modifiers pressed (ctrl-click, shift-drag...); they are always let go again."""
        codes: list[int] = []
        for name in names:
            code = keys.MODIFIERS[keys.MODIFIER_NAMES[name.lower()]][0]
            if code not in codes:
                codes.append(code)
        if not codes:
            yield
            return
        self._require()
        before = self._flags
        try:
            self._flags = before | self._mask(codes)
            self._press(codes, before)
            time.sleep(0.04)
            yield
        finally:
            time.sleep(0.03)
            self._release(codes, before | self._mask(codes))
            self._flags = before

    def parse_combo(self, combo: str) -> Keys:
        return keys.parse_combo(combo)

    def press_combo(self, keys: Keys, hold: float = 0.0, cancel: threading.Event | None = None) -> None:
        self._require()
        codes = list(keys)
        if escwatch.KVK_ESCAPE in codes:
            escwatch.mark_own(max(0.03, hold) + 0.6)
        try:
            self._press(codes, self._flags)
            _wait(max(0.03, hold), cancel)
        finally:
            self._release(codes, self._flags | self._mask(codes))

    def type_text(self, text: str, still_ok: Callable[[], bool], cancel: threading.Event | None = None,
                  chunk: int = 1, pause: float = 0.02) -> int:
        """Type Unicode text (any layout: the characters ride on the key events); new lines as Return, tabs as
        Tab.  Checks ``still_ok`` before every chunk and stops when it returns False.  Returns characters typed."""
        self._require()
        Q = _objc.Q
        typed = 0
        i = 0
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        chunk = max(1, chunk)
        while i < len(text):
            if (cancel is not None and cancel.is_set()) or not still_ok():
                break
            part = text[i:i + chunk]
            if part[0] in "\n\t":
                part = part[0]
            else:
                cut = next((k for k, ch in enumerate(part) if ch in "\n\t"), len(part))
                part = part[:cut]
                while len(part.encode("utf-16-le")) // 2 > UNICODE_MAX:
                    part = part[:-1]
            if part in ("\n", "\t"):
                code = keys.RETURN if part == "\n" else keys.TAB
                self._key(code, True, 0)
                self._key(code, False, 0)
            else:
                units = len(part.encode("utf-16-le")) // 2
                for down in (True, False):
                    ev = Q.CGEventCreateKeyboardEvent(self._source(), 0, down)
                    if ev is None:
                        continue
                    Q.CGEventSetFlags(ev, 0)       # (no held modifier turns the text into shortcuts)
                    Q.CGEventKeyboardSetUnicodeString(ev, units, part)
                    self._post(ev)
            typed += len(part)
            i += len(part)
            time.sleep(pause + (0.03 if part == "\n" else 0.0))
        return typed

    def escape_watcher(self, on_escape: Callable[[], None]) -> escwatch.MacEscapeWatcher:
        return escwatch.MacEscapeWatcher(on_escape)
