"""Platform-free domain types shared by the core (engine, games) and the desktop layer.

Nothing in this package may import ctypes, Qt or ``pixelcat.desktop`` (tests/test_boundaries.py).
"""
from __future__ import annotations

from .monitors import Monitor, monitor_at, primary
from .windows import WinInfo

__all__ = ["Monitor", "WinInfo", "monitor_at", "primary"]
