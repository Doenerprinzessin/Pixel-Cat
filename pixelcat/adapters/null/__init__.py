"""The null adapter: every port, doing nothing, for platforms the cat doesn't support yet.

Queries answer "nothing there" (no monitors, no windows, 0, False, None, ""), the cat's own window
actions do nothing, and actions on the desktop that can't happen give their "failed" answer (or raise
the error their callers already handle: OSError when starting things, ValueError for ending processes).
It imports only the standard library, ``pixelcat.domain`` and ``pixelcat.ports`` (Qt only when a
screenshot is asked for), so it loads anywhere.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ...domain import (FALLBACK_MONITOR, AppWindow, ClipboardKind, Element, ElementKinds, Hardware, KillPlan, Monitor,
                       Notification, Snapshot, Unreadable, UserState, WindowId, WinInfo)
from ...ports import ClipboardBackup, Keys, Platform, Rect

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

UNSUPPORTED = "not supported on this platform"


def _no_image() -> QImage:
    from PySide6.QtGui import QImage

    return QImage()


class NullDisplay:
    def set_dpi_awareness(self) -> bool:
        return False

    def monitors(self) -> list[Monitor]:
        return [FALLBACK_MONITOR]

    def virtual_screen(self) -> Rect:
        return FALLBACK_MONITOR.rect

    def window_dpi(self, win: WindowId) -> int:
        return 0

    def user_state(self) -> UserState:
        return UserState.NORMAL

    def session_locked(self) -> bool:
        return False


class NullWindows:
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
        return win      # no dialog: the window itself, as the port says

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
        pass

    def set_click_through(self, win: WindowId, enabled: bool) -> None:
        pass

    def move(self, win: WindowId, x: int, y: int, w: int, h: int, topmost: bool = True) -> None:
        pass

    def keep_topmost(self, win: WindowId) -> None:
        pass

    def place_below(self, win: WindowId, other: WindowId) -> None:
        pass

    def own_click_through(self, own_pid: int, keep: WindowId) -> list[WindowId]:
        return []

    def end_own_click_through(self, changed: list[WindowId]) -> None:
        pass


class NullEscapeWatcher:
    def __init__(self, on_escape: Callable[[], None]) -> None:
        self.on_escape = on_escape

    @property
    def active(self) -> bool:
        return False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class NullInput:
    """No synthetic input; the passthrough flag works (it is the cat's own state)."""

    def __init__(self) -> None:
        self.passthrough_until = 0.0

    def cursor_pos(self) -> tuple[int, int]:
        return (0, 0)

    def release_went_elsewhere(self, win: WindowId) -> bool:
        return False

    def mouse_buttons_down(self) -> bool:
        return False

    def last_input_tick(self) -> int:
        return 0

    def idle_seconds(self) -> float:
        return 0.0

    def passthrough_active(self) -> bool:
        return time.monotonic() < self.passthrough_until

    def set_passthrough_until(self, until: float) -> None:
        self.passthrough_until = until

    def move_cursor(self, x: int, y: int) -> None:
        pass

    def click(self, x: int, y: int, button: str = "left", double: bool = False, count: int = 1, hold: float = 0.0,
              cancel: threading.Event | None = None) -> None:
        pass

    def scroll(self, x: int, y: int, clicks: int, horizontal: bool = False) -> None:
        pass

    def drag(self, points: list[tuple[int, int]], button: str = "left", seconds: float = 0.6,
             cancel: threading.Event | None = None) -> str:
        return ""

    def glide(self, x: int, y: int, seconds: float = 0.3, cancel: threading.Event | None = None) -> str:
        return ""

    def pinch(self, x: int, y: int, start: float, end: float, seconds: float = 0.5,
              cancel: threading.Event | None = None) -> str:
        return "failed"     # callers fall back to the mouse wheel

    def holding(self, keys: list[str]) -> AbstractContextManager[None]:
        return nullcontext()

    def parse_combo(self, combo: str) -> Keys:
        """The key names in lower case, unchecked (there are no key codes here); ValueError if there are none."""
        parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", combo.strip()) if p.strip()]
        if not parts and "+" in combo:
            parts = ["plus"]
        if not parts:
            raise ValueError("no keys given")
        return parts

    def press_combo(self, keys: Keys, hold: float = 0.0, cancel: threading.Event | None = None) -> None:
        pass

    def type_text(self, text: str, still_ok: Callable[[], bool], cancel: threading.Event | None = None,
                  chunk: int = 1, pause: float = 0.02) -> int:
        return 0

    def escape_watcher(self, on_escape: Callable[[], None]) -> NullEscapeWatcher:
        return NullEscapeWatcher(on_escape)


class NullClipboard:
    def backup(self) -> ClipboardBackup | None:
        return None

    def write(self, backup: ClipboardBackup, private: bool = True) -> bool:
        return False

    def set_private_text(self, text: str) -> bool:
        return False

    def clear(self) -> None:
        pass

    def sequence(self) -> int:
        return 0

    def has(self, kind: ClipboardKind) -> bool:
        return False

    def text(self) -> str | None:
        return None


class NullAccessibility:
    def snapshot(self, win: WindowId, kinds: ElementKinds = "interactive", limit: int = 150) -> Snapshot:
        return Snapshot(hwnd=win, window_rect=(0, 0, 0, 0))

    def focused_element(self) -> Element | None:
        return None

    def focused_text(self, max_chars: int = 50000) -> str | None:
        return None

    def document_text(self, win: WindowId, max_chars: int = 8000) -> str:
        return ""


class NullCapture:
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


class NullSystem:
    @property
    def instance(self) -> str:
        return ""

    def acquire_single_instance(self) -> bool:
        return True     # no guard against a second cat

    def create_quit_event(self) -> int:
        return 0

    def quit_requested(self, handle: int) -> bool:
        return False

    def message_box(self, text: str, title: str = "Pixel Cat") -> None:
        log.warning("%s: %s", title, text)

    def system_info(self) -> str:
        return "System information is not available on this platform."

    def lock_screen(self) -> bool:
        return False

    def media_key(self, action: str) -> None:
        pass

    def get_volume(self) -> tuple[int, bool]:
        return (0, False)

    def set_volume(self, percent: int) -> int:
        return 0

    def set_mute(self, muted: bool) -> None:
        pass

    def hardware(self, fresh: bool = False) -> Hardware:
        return Hardware()


class NullApps:
    def prefetch(self) -> None:
        pass

    def find_shortcut(self, name: str) -> tuple[str, Path | str] | None:
        return None

    def open(self, target: str | Path) -> None:
        raise OSError(UNSUPPORTED)

    def open_elevated(self, file: str, args: list[str]) -> None:
        raise OSError(UNSUPPORTED)

    def open_settings(self, page: str) -> None:
        raise OSError(UNSUPPORTED)

    def needs_admin(self, target: object) -> bool:
        return False

    def lnk_target(self, path: str | Path) -> tuple[str, bool]:
        return ("", False)

    def run_as_admin(self, target: str | Path) -> tuple[bool, str]:
        return (False, UNSUPPORTED)


class NullProcesses:
    def process_exe(self, pid: int) -> str:
        return ""

    def plan_force_end(self, target: str, own_pid: int) -> KillPlan:
        raise ValueError("ending processes is not supported on this platform")

    def force_end(self, plan: KillPlan) -> tuple[list[int], list[int]]:
        return ([], [])

    def force_end_elevated(self, pids: list[int]) -> tuple[bool, str]:
        return (False, UNSUPPORTED)

    def alive(self, pids: list[int]) -> list[int]:
        return []

    def is_elevated(self, pid: int) -> bool:
        return False

    def self_elevated(self) -> bool:
        return False

    def uac_prompt_open(self) -> bool:
        return False


class NullFeed:
    def available(self) -> bool:
        return False

    def last_id(self) -> int:
        return 0

    def since(self, last_id: int) -> list[Notification]:
        return []

    def badges(self) -> dict[str, str]:
        return {}


class NullNotifications:
    def open_feed(self) -> NullFeed:
        return NullFeed()

    def remember_own(self, *texts: str) -> None:
        pass


class NullSecretStore:
    def protect(self, data: bytes) -> bytes:
        raise OSError(UNSUPPORTED)

    def unprotect(self, blob: bytes) -> bytes:
        raise Unreadable(f"protected data can't be opened: {UNSUPPORTED}")


class NullKnownFolders:
    def known_folder(self, name: str) -> Path | None:
        return None


class NullAutostart:
    def enabled(self) -> bool:
        return False

    def set(self, enabled: bool) -> bool:
        return False


class NullSaveMarks:
    def is_marked(self, folder_id: str) -> bool:
        return False

    def mark(self, folder_id: str) -> None:
        pass

    def forget(self, folder_id: str) -> None:
        pass


class NullInstaller:
    def is_setup_name(self, path: str | Path) -> bool:
        return False

    def run_quiet(self, args: Any) -> int:
        return 1

    def run_window(self, mode: str) -> int:
        return 1


def build() -> Platform:
    return Platform(
        name="null", display=NullDisplay(), windows=NullWindows(), input=NullInput(), clipboard=NullClipboard(),
        accessibility=NullAccessibility(), capture=NullCapture(), system=NullSystem(), apps=NullApps(),
        processes=NullProcesses(), notifications=NullNotifications(), secrets=NullSecretStore(),
        folders=NullKnownFolders(), autostart=NullAutostart(), save_marks=NullSaveMarks(), installer=NullInstaller(),
    )
