"""Plain geometry helpers for the app (no Qt)."""
from __future__ import annotations


def rect_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    dx = max(a[0] - b[2], 0, b[0] - a[2])
    dy = max(a[1] - b[3], 0, b[1] - a[3])
    return (dx * dx + dy * dy) ** 0.5
