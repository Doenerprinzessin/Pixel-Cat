"""The Esc watcher on macOS: polls the Esc key's state (CGEventSourceKeyState) on a thread between start() and
stop(), and ignores the Esc presses the cat's own input makes.

Skeleton: never active.
"""
from __future__ import annotations

from typing import Callable


class MacEscapeWatcher:
    def __init__(self, on_escape: Callable[[], None]) -> None:
        self.on_escape = on_escape

    @property
    def active(self) -> bool:
        return False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass
