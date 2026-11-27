"""Platform-free domain types shared by the core (engine, games) and the platform adapters.

Nothing in this package may import ctypes, Qt or ``pixelcat.adapters`` (tests/test_boundaries.py).
"""
from __future__ import annotations

from .accessibility import Element, ElementKinds, Snapshot
from .apps import SETTINGS_PAGES
from .folders import KNOWN_FOLDERS
from .hardware import Gpu, Hardware
from .input import MEDIA_ACTIONS, MODIFIERS, MOUSE_BUTTONS, ClipboardKind
from .monitors import Monitor, monitor_at, primary
from .notifications import Notification, app_matches, badge_count, friendly
from .processes import KillPlan, Proc
from .secrets import Unreadable
from .windows import AppWindow, UserState, WindowId, WinInfo, find_windows

__all__ = [
    "AppWindow", "ClipboardKind", "Element", "ElementKinds", "Gpu", "Hardware", "KNOWN_FOLDERS", "KillPlan",
    "MEDIA_ACTIONS", "MODIFIERS", "MOUSE_BUTTONS", "Monitor", "Notification", "Proc", "SETTINGS_PAGES", "Snapshot",
    "Unreadable", "UserState", "WinInfo", "WindowId", "app_matches", "badge_count", "find_windows", "friendly",
    "monitor_at", "primary",
]
