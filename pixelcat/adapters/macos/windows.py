"""WindowPort on macOS: other apps' windows from the Quartz window list (CGWindowListCopyWindowInfo), titles
and window actions through Accessibility; the cat's own windows through ``ownwin``.

Skeleton: no other windows yet (the world then has only the screen edges).
"""
from __future__ import annotations

from . import ownwin
from ...domain import AppWindow, WindowId, WinInfo
from ...ports import Rect


class MacWindows:
    def top_level(self, own_pid: int, include: set[int] | frozenset[int] = frozenset()) -> list[WinInfo]:
        return []

    def app_windows(self, own_pid: int) -> list[AppWindow]:
        return []

    def foreground(self) -> WindowId:
        return 0

    def is_fullscreen(self, win: WindowId, monitor_rect: Rect) -> bool:
        return False

    def exists(self, win: WindowId) -> bool:
        return False

    def is_visible(self, win: WindowId) -> bool:
        return False

    def is_minimized(self, win: WindowId) -> bool:
        return False

    def is_hung(self, win: WindowId) -> bool:
        return False

    def is_cloaked(self, win: WindowId) -> bool:
        return False

    def frame_bounds(self, win: WindowId) -> Rect | None:
        return None

    def rect(self, win: WindowId) -> Rect:
        return (0, 0, 0, 0)

    def pid(self, win: WindowId) -> int:
        return 0

    def title(self, win: WindowId) -> str:
        return ""

    def class_name(self, win: WindowId) -> str:
        return ""

    def window_at(self, x: int, y: int) -> WindowId:
        return 0

    def active_popup(self, win: WindowId) -> WindowId:
        return win

    def same_app(self, a: WindowId, b: WindowId) -> bool:
        return False

    def is_foreground(self, win: WindowId) -> bool:
        return False

    def bring_to_front(self, win: WindowId, timeout: float = 1.5) -> bool:
        return False

    def window_action(self, w: AppWindow, action: str) -> None:
        pass

    def close_windows(self, windows: list[AppWindow]) -> int:
        return 0

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
