"""What the user is doing right now (typing, clicking, moving the mouse), from the input port."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ports import InputPort


class ActivityTracker:
    """Infers whether the user is busy (typing/clicking) from cheap polling.

    Keyboard activity is detected as "last input time changed while the cursor
    did not move and no mouse button is down".  No hooks, no key logging.
    """

    def __init__(self, input: InputPort) -> None:
        self.input = input
        self._last_tick = input.last_input_tick()
        self._last_cursor = input.cursor_pos()
        self.last_typing = 0.0
        self.last_click = 0.0
        self.last_mouse_move = 0.0
        self.cursor = self._last_cursor
        self.cursor_speed = 0.0
        self._last_t = time.monotonic()

    def poll(self, cursor_over_cat: bool) -> None:
        now = time.monotonic()
        dt = max(1e-3, now - self._last_t)
        self._last_t = now
        try:
            tick = self.input.last_input_tick()
            cur = self.input.cursor_pos()
            buttons = self.input.mouse_buttons_down()
        except Exception:  # noqa: BLE001
            return
        moved = cur != self._last_cursor
        if moved:
            self.last_mouse_move = now
            dx, dy = cur[0] - self._last_cursor[0], cur[1] - self._last_cursor[1]
            self.cursor_speed = (dx * dx + dy * dy) ** 0.5 / dt
        else:
            self.cursor_speed *= 0.5
        if buttons and not cursor_over_cat:
            self.last_click = now
        if tick != self._last_tick and not moved and not buttons:
            self.last_typing = now
        self._last_tick = tick
        self._last_cursor = cur
        self.cursor = cur

    def busy(self, grace: float) -> bool:
        now = time.monotonic()
        return now - self.last_typing < grace or now - self.last_click < grace

    def typing(self, grace: float) -> bool:
        return time.monotonic() - self.last_typing < grace

    def idle_seconds(self) -> float:
        try:
            return self.input.idle_seconds()
        except Exception:  # noqa: BLE001
            return 0.0
