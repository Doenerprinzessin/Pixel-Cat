"""Monitors: the screen geometry the cat lives in (plain data, no platform calls)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    """One display. Rects are (left, top, right, bottom) in physical virtual-screen px."""

    handle: int
    rect: tuple[int, int, int, int]
    work: tuple[int, int, int, int]
    dpi: int
    primary: bool
    device: str

    @property
    def scale_factor(self) -> float:
        return self.dpi / 96.0

    def contains(self, x: float, y: float) -> bool:
        l, t, r, b = self.rect
        return l <= x < r and t <= y < b

    def distance2(self, x: float, y: float) -> float:
        l, t, r, b = self.rect
        dx = max(l - x, 0, x - (r - 1))
        dy = max(t - y, 0, y - (b - 1))
        return dx * dx + dy * dy


# What the platform answers when it can't list the monitors (the list is never empty).
FALLBACK_MONITOR = Monitor(0, (0, 0, 1920, 1080), (0, 0, 1920, 1040), 96, True, "fallback")


def monitor_at(monitors: list[Monitor], x: float, y: float) -> Monitor:
    """Monitor containing (x, y), or the nearest one."""
    for m in monitors:
        if m.contains(x, y):
            return m
    return min(monitors, key=lambda m: m.distance2(x, y))


def primary(monitors: list[Monitor]) -> Monitor:
    for m in monitors:
        if m.primary:
            return m
    return monitors[0]
