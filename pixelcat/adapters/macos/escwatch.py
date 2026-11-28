"""The Esc watcher on macOS: notices the user pressing Esc anywhere while the chat's helper drives the desktop.

* A thread polls the Esc key's state (``CGEventSourceKeyState(HIDSystemState, kVK_Escape)``, ~30 Hz) between
  :meth:`MacEscapeWatcher.start` and :meth:`~MacEscapeWatcher.stop` and calls back when it goes down.  Reading
  the key state never prompts (checked on macOS 26 without Input Monitoring: it answers, no dialog appears).
* If Input Monitoring isn't granted the key state may read "up" for keys of other apps, so then - and only if
  Accessibility is trusted, which the helper needs anyway to drive anything - a global ``NSEvent`` key-down
  monitor watches too (added on the GUI thread; it never prompts either).
* Esc presses the cat's own input makes are ignored: ``inputs`` calls :func:`mark_own` before it presses Esc
  (and marks its events, :data:`OWN_EVENT_MARK`).
* ``on_escape`` runs on the GUI thread (like the Windows hook's callback).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from . import _objc, permissions

log = logging.getLogger(__name__)

KVK_ESCAPE = 53
HID_SYSTEM_STATE = 1             # kCGEventSourceStateHIDSystemState
EVENT_SOURCE_USER_DATA = 42      # kCGEventSourceUserData
OWN_EVENT_MARK = 0x50434154      # 'PCAT': the user-data field of every event the cat posts
KEY_DOWN_MASK = 1 << 10          # NSEventMaskKeyDown
POLL = 1 / 30
DEBOUNCE = 0.3                   # one press seen by both the poll and the monitor calls back once

_own_until = 0.0
_own_lock = threading.Lock()


def mark_own(seconds: float) -> None:
    """The cat itself presses Esc during the next ``seconds``: don't take it for the user's."""
    global _own_until
    with _own_lock:
        _own_until = max(_own_until, time.monotonic() + seconds)


def own_press() -> bool:
    return time.monotonic() < _own_until


def reset() -> None:
    """Forget the own presses (tests)."""
    global _own_until
    with _own_lock:
        _own_until = 0.0


class MacEscapeWatcher:
    def __init__(self, on_escape: Callable[[], None], poll: float = POLL) -> None:
        self.on_escape = on_escape
        self.poll = poll
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._monitor: Any = None
        self._last_fire = 0.0
        self._fire_lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop = threading.Event()
        th = threading.Thread(target=self._run, args=(self._stop,), name="pixelcat-esc-watch", daemon=True)
        self._thread = th
        th.start()
        if not permissions.listen_events() and permissions.accessibility():
            self._monitor = _objc.on_main(self._add_monitor, 1.0, None)

    def stop(self) -> None:
        th, self._thread = self._thread, None
        self._stop.set()
        monitor, self._monitor = self._monitor, None
        if monitor is not None:
            _objc.on_main(lambda: self._remove_monitor(monitor), 1.0, None)
        if th is not None and th is not threading.current_thread():
            th.join(timeout=0.5)

    # ---------------------------------------------------------------- the poll
    @staticmethod
    def _esc_down() -> bool:
        try:
            return bool(_objc.Q.CGEventSourceKeyState(HID_SYSTEM_STATE, KVK_ESCAPE))
        except Exception:  # noqa: BLE001 - can't tell: not pressed
            return False

    def _run(self, stop: threading.Event) -> None:
        was_down = self._esc_down()          # one held when the watch starts isn't a new press
        while not stop.wait(self.poll):
            down = self._esc_down()
            if down and not was_down and not own_press():
                self._fire()
            was_down = down

    # ---------------------------------------------------------------- the monitor (Accessibility)
    def _add_monitor(self) -> Any:
        try:
            return _objc.AK.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(KEY_DOWN_MASK, self._on_key)
        except Exception:  # noqa: BLE001 - the poll still watches
            log.debug("couldn't add the Esc monitor", exc_info=True)
            return None

    @staticmethod
    def _remove_monitor(monitor: Any) -> None:
        try:
            _objc.AK.NSEvent.removeMonitor_(monitor)
        except Exception:  # noqa: BLE001
            log.debug("couldn't remove the Esc monitor", exc_info=True)

    def _on_key(self, event: Any) -> None:
        try:
            if event.keyCode() != KVK_ESCAPE or event.isARepeat() or own_press():
                return
            cg = event.CGEvent()
            if cg is not None and _objc.Q.CGEventGetIntegerValueField(cg, EVENT_SOURCE_USER_DATA) == OWN_EVENT_MARK:
                return
            self._fire()
        except Exception:  # noqa: BLE001 - never break the user's keyboard
            log.debug("Esc monitor failed", exc_info=True)

    # ---------------------------------------------------------------- calling back
    def _fire(self) -> None:
        with self._fire_lock:
            now = time.monotonic()
            if now - self._last_fire < DEBOUNCE:
                return
            self._last_fire = now
        _objc.on_main(self._call, 2.0, None)

    def _call(self) -> None:
        try:
            self.on_escape()
        except Exception:  # noqa: BLE001
            log.exception("the Esc callback failed")
