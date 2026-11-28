"""InputPort on macOS: the pointer and keyboard state from Quartz (no permission needed) and synthetic input
posted with CGEventPost (needs Accessibility: checked first, ``permissions.need``).

Skeleton: reads answer "nothing", synthetic input does nothing; the passthrough flag works.
"""
from __future__ import annotations

import threading
import time
from contextlib import AbstractContextManager, nullcontext
from typing import Callable

from . import escwatch, keys
from ...domain import WindowId
from ...ports import Keys


class MacInput:
    def __init__(self) -> None:
        self.passthrough_until = 0.0

    def cursor_pos(self) -> tuple[int, int]:
        return (0, 0)

    def release_went_elsewhere(self, win: WindowId) -> bool:
        return False

    def mouse_buttons_down(self) -> bool:
        return False

    def last_input_tick(self) -> int:
        return 0

    def idle_seconds(self) -> float:
        return 0.0

    def passthrough_active(self) -> bool:
        return time.monotonic() < self.passthrough_until

    def set_passthrough_until(self, until: float) -> None:
        self.passthrough_until = until

    def move_cursor(self, x: int, y: int) -> None:
        pass

    def click(self, x: int, y: int, button: str = "left", double: bool = False, count: int = 1, hold: float = 0.0,
              cancel: threading.Event | None = None) -> None:
        pass

    def scroll(self, x: int, y: int, clicks: int, horizontal: bool = False) -> None:
        pass

    def drag(self, points: list[tuple[int, int]], button: str = "left", seconds: float = 0.6,
             cancel: threading.Event | None = None) -> str:
        return ""

    def glide(self, x: int, y: int, seconds: float = 0.3, cancel: threading.Event | None = None) -> str:
        return ""

    def pinch(self, x: int, y: int, start: float, end: float, seconds: float = 0.5,
              cancel: threading.Event | None = None) -> str:
        return "failed"     # no public API posts magnify gestures: callers fall back to the wheel

    def holding(self, keys: list[str]) -> AbstractContextManager[None]:
        return nullcontext()

    def parse_combo(self, combo: str) -> Keys:
        return keys.parse_combo(combo)

    def press_combo(self, keys: Keys, hold: float = 0.0, cancel: threading.Event | None = None) -> None:
        pass

    def type_text(self, text: str, still_ok: Callable[[], bool], cancel: threading.Event | None = None,
                  chunk: int = 1, pause: float = 0.02) -> int:
        return 0

    def escape_watcher(self, on_escape: Callable[[], None]) -> escwatch.MacEscapeWatcher:
        return escwatch.MacEscapeWatcher(on_escape)
