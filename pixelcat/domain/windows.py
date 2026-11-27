"""Windows on the desktop as the cat sees them (plain data, no platform calls)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WinInfo:
    hwnd: int
    rect: tuple[int, int, int, int]   # exact visible frame (DWM extended frame bounds)
    z: int                             # 0 = top of the z-order
    title: str
    cls: str
    maximized: bool
    topmost: bool

    @property
    def left(self) -> int:
        return self.rect[0]

    @property
    def top(self) -> int:
        return self.rect[1]

    @property
    def right(self) -> int:
        return self.rect[2]

    @property
    def bottom(self) -> int:
        return self.rect[3]
