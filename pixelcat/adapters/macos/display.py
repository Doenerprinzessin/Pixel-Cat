"""DisplayPort on macOS: the screens in Quartz points (Qt's logical coordinates), DPI 96 for every screen.

Skeleton: one fallback monitor until the real one (CGDisplayBounds, NSScreen.visibleFrame) is filled in.
"""
from __future__ import annotations

from ...domain import FALLBACK_MONITOR, Monitor, UserState, WindowId
from ...ports import Rect

DPI = 96        # the art is sized in points; Retina draws each point as 2x2 pixels


class MacDisplay:
    def set_dpi_awareness(self) -> bool:
        return True         # Cocoa always scales for the screen

    def monitors(self) -> list[Monitor]:
        return [FALLBACK_MONITOR]

    def virtual_screen(self) -> Rect:
        return FALLBACK_MONITOR.rect

    def window_dpi(self, win: WindowId) -> int:
        return DPI

    def user_state(self) -> UserState:
        return UserState.NORMAL

    def session_locked(self) -> bool:
        return False

    def qt_logical(self) -> bool:
        return True         # Quartz points are Qt's logical coordinates
