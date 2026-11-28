"""CapturePort on macOS: screenshots with ScreenCaptureKit (CGWindowListCreateImage as a fallback), scaled to
points so an image is as big as its rect.  Needs Screen Recording: background snapshots return None without
it, the chat's screenshots ask for it once (``permissions.need``).

Skeleton: empty pictures.
"""
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING

from ...domain import WindowId
from ...ports import Rect

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


def _no_image() -> QImage:
    from PySide6.QtGui import QImage

    return QImage()


class MacCapture:
    def grab(self, rect: Rect) -> QImage:
        return _no_image()

    def grab_without(self, rect: Rect, pid: int) -> QImage:
        return _no_image()

    def grab_window(self, win: WindowId, frame: Rect) -> QImage | None:
        return None

    def excluding(self, pid: int) -> AbstractContextManager[None]:
        return nullcontext()

    def snapshot(self, rect: Rect) -> QImage | None:
        return None
