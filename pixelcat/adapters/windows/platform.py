"""The Windows adapter: every port implemented with the modules of this package.

Each method only forwards to the module function it wraps, looked up when it is called (so tests that
replace a module function reach the adapter too), and translates the platform-neutral names of the
ports (element kinds, clipboard kinds, modifier keys, settings pages, the user's state) to Windows values.
Only ``win32`` and ``system`` are imported up front: ``main`` sets the DPI awareness, runs the quiet
installer and lets a second copy exit before Qt (or anything heavy) is loaded.  The other modules are
imported on first use, as before the ports: the installer (with ``elevation``), ``hardware``, ``volume``
and the storage modules each on their own, and the desktop modules the app uses all together, in the
order the app always imported them (``control`` before ``inputs``: their ctypes declarations happen at
import time, and ``inputs.is_elevated`` relies on the ones ``control`` makes).
"""
from __future__ import annotations

import importlib
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable

from . import system, win32
from ...domain import (AppWindow, ClipboardKind, Element, ElementKinds, Hardware, KillPlan, Monitor, Snapshot,
                       UserState, WindowId, WinInfo)
from ...ports import ClipboardBackup, EscapeWatcher, Keys, NotificationFeed, Platform, Rect

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

# The desktop modules the app imported together (after Qt), in that order.
_DESKTOP = ("monitors", "windows", "ledges", "escwatch", "capture", "control", "inputs", "uia", "notifications")
_import_lock = threading.RLock()


def _load(name: str) -> ModuleType:
    """The adapter module ``name``; a desktop module brings all of ``_DESKTOP`` with it, in order."""
    with _import_lock:
        for each in _DESKTOP if name in _DESKTOP else (name,):
            importlib.import_module(f"{__package__}.{each}")
    return importlib.import_module(f"{__package__}.{name}")


class _Lazy:
    """Stands for an adapter module and imports it (see :func:`_load`) when one of its names is used."""

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attr: str) -> Any:
        return getattr(_load(self._name), attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        setattr(_load(self._name), attr, value)


(capture, control, dpapi, elevation, escwatch, hardware, inputs, installer, known_folders, ledges, monitors,
 notifications, registry, uia, volume, windows) = (
    _Lazy(name) for name in ("capture", "control", "dpapi", "elevation", "escwatch", "hardware", "inputs",
                             "installer", "known_folders", "ledges", "monitors", "notifications", "registry", "uia",
                             "volume", "windows"))

_KINDS = {"interactive": "INTERACTIVE", "readable": "READABLE"}       # uia's constants
_CLIPBOARD_FORMATS: dict[str, int | str] = {"files": 15, "rtf": "Rich Text Format", "html": "HTML Format"}  # CF_HDROP
_MODIFIER_VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}
_USER_STATES = {win32.QUNS_RUNNING_D3D_FULL_SCREEN: UserState.FULLSCREEN_APP,
                win32.QUNS_PRESENTATION_MODE: UserState.PRESENTATION}


class WindowsDisplay:
    def set_dpi_awareness(self) -> bool:
        return win32.set_dpi_awareness()

    def monitors(self) -> list[Monitor]:
        return monitors.enumerate_monitors()

    def virtual_screen(self) -> Rect:
        return win32.virtual_screen()

    def window_dpi(self, win: WindowId) -> int:
        return win32.window_dpi(win)

    def user_state(self) -> UserState:
        return _USER_STATES.get(win32.notification_state(), UserState.NORMAL)

    def session_locked(self) -> bool:
        return system.session_locked()


class WindowsWindows:
    def top_level(self, own_pid: int, include: set[int] | frozenset[int] = frozenset()) -> list[WinInfo]:
        return windows.enumerate_windows(own_pid, include)

    def app_windows(self, own_pid: int) -> list[AppWindow]:
        return control.list_app_windows(own_pid)

    def foreground(self) -> WindowId:
        return windows.foreground_window()

    def is_fullscreen(self, win: WindowId, monitor_rect: Rect) -> bool:
        return windows.is_fullscreen_window(win, monitor_rect)

    def exists(self, win: WindowId) -> bool:
        return win32.is_window(win)

    def is_visible(self, win: WindowId) -> bool:
        return win32.is_visible(win)

    def is_minimized(self, win: WindowId) -> bool:
        return win32.is_minimized(win)

    def is_hung(self, win: WindowId) -> bool:
        return win32.is_hung(win)

    def is_cloaked(self, win: WindowId) -> bool:
        return win32.is_cloaked(win)

    def frame_bounds(self, win: WindowId) -> Rect | None:
        return win32.frame_bounds(win)

    def rect(self, win: WindowId) -> Rect:
        return win32.window_rect(win)

    def pid(self, win: WindowId) -> int:
        return win32.window_pid(win)

    def title(self, win: WindowId) -> str:
        return win32.window_text(win)

    def class_name(self, win: WindowId) -> str:
        return win32.class_name(win)

    def window_at(self, x: int, y: int) -> WindowId:
        return inputs.window_at(x, y)

    def active_popup(self, win: WindowId) -> WindowId:
        return inputs.active_popup(win)

    def same_app(self, a: WindowId, b: WindowId) -> bool:
        return inputs.same_app(a, b)

    def is_foreground(self, win: WindowId) -> bool:
        return inputs.is_foreground(win)

    def bring_to_front(self, win: WindowId, timeout: float = 1.5) -> bool:
        return inputs.bring_to_front(win, timeout)

    def window_action(self, w: AppWindow, action: str) -> None:
        control.window_action(w, action)

    def close_windows(self, windows: list[AppWindow]) -> int:
        return control.close_windows(windows)

    def make_tool_window(self, win: WindowId) -> None:
        win32.make_tool_noactivate(win)

    def set_click_through(self, win: WindowId, enabled: bool) -> None:
        win32.set_click_through(win, enabled)

    def move(self, win: WindowId, x: int, y: int, w: int, h: int, topmost: bool = True) -> None:
        win32.move_window(win, x, y, w, h, topmost)

    def keep_topmost(self, win: WindowId) -> None:
        win32.keep_topmost(win)

    def place_below(self, win: WindowId, other: WindowId) -> None:
        win32.place_below(win, other)

    def own_click_through(self, own_pid: int, keep: WindowId) -> list[WindowId]:
        return win32.own_click_through(own_pid, keep)

    def end_own_click_through(self, changed: list[WindowId]) -> None:
        win32.end_own_click_through(changed)


class WindowsInput:
    def cursor_pos(self) -> tuple[int, int]:
        return win32.cursor_pos()

    def release_went_elsewhere(self, win: WindowId) -> bool:
        return win32.release_went_elsewhere(win)

    def mouse_buttons_down(self) -> bool:
        return win32.mouse_buttons_down()

    def last_input_tick(self) -> int:
        return win32.last_input_tick()

    def idle_seconds(self) -> float:
        return win32.idle_seconds()

    def passthrough_active(self) -> bool:
        return inputs.passthrough_active()

    def set_passthrough_until(self, until: float) -> None:
        inputs.passthrough_until = until

    def move_cursor(self, x: int, y: int) -> None:
        inputs.move_cursor(x, y)

    def click(self, x: int, y: int, button: str = "left", double: bool = False, count: int = 1, hold: float = 0.0,
              cancel: threading.Event | None = None) -> None:
        inputs.click(x, y, button, double, count, hold, cancel)

    def scroll(self, x: int, y: int, clicks: int, horizontal: bool = False) -> None:
        inputs.scroll(x, y, clicks, horizontal)

    def drag(self, points: list[tuple[int, int]], button: str = "left", seconds: float = 0.6,
             cancel: threading.Event | None = None) -> str:
        return inputs.drag(points, button, seconds, cancel)

    def glide(self, x: int, y: int, seconds: float = 0.3, cancel: threading.Event | None = None) -> str:
        return inputs.glide(x, y, seconds, cancel)

    def pinch(self, x: int, y: int, start: float, end: float, seconds: float = 0.5,
              cancel: threading.Event | None = None) -> str:
        return inputs.pinch(x, y, start, end, seconds, cancel)

    def holding(self, keys: list[str]) -> AbstractContextManager[None]:
        return inputs.holding([_MODIFIER_VK[k] for k in keys])

    def parse_combo(self, combo: str) -> Keys:
        return inputs.parse_combo(combo)

    def press_combo(self, keys: Keys, hold: float = 0.0, cancel: threading.Event | None = None) -> None:
        inputs.press_combo(keys, hold, cancel)

    def type_text(self, text: str, still_ok: Callable[[], bool], cancel: threading.Event | None = None,
                  chunk: int = 1, pause: float = 0.02) -> int:
        return inputs.type_text(text, still_ok, cancel, chunk, pause)

    def escape_watcher(self, on_escape: Callable[[], None]) -> EscapeWatcher:
        return escwatch.EscWatcher(on_escape)


class WindowsClipboard:
    def backup(self) -> ClipboardBackup | None:
        return inputs.backup_clipboard()

    def write(self, backup: ClipboardBackup, private: bool = True) -> bool:
        return inputs.write_clipboard(backup, private)

    def set_private_text(self, text: str) -> bool:
        return inputs.set_clipboard_private_text(text)

    def clear(self) -> None:
        inputs.clear_clipboard()

    def sequence(self) -> int:
        return inputs.clipboard_sequence()

    def has(self, kind: ClipboardKind) -> bool:
        return inputs.clipboard_has_format(_CLIPBOARD_FORMATS[kind])

    def text(self) -> str | None:
        return inputs.get_clipboard_text()


class WindowsAccessibility:
    def snapshot(self, win: WindowId, kinds: ElementKinds = "interactive", limit: int = 150) -> Snapshot:
        return uia.snapshot(win, getattr(uia, _KINDS[kinds]), limit)

    def focused_element(self) -> Element | None:
        return uia.focused_element()

    def focused_text(self, max_chars: int = 50000) -> str | None:
        return uia.focused_text(max_chars)

    def document_text(self, win: WindowId, max_chars: int = 8000) -> str:
        return uia.document_text(win, max_chars)


class WindowsCapture:
    def grab(self, rect: Rect) -> QImage:
        return capture.grab(rect)

    def grab_without(self, rect: Rect, pid: int) -> QImage:
        return capture.grab_without(rect, pid)

    def grab_window(self, win: WindowId, frame: Rect) -> QImage | None:
        return capture.grab_window(win, frame)

    def excluding(self, pid: int) -> AbstractContextManager[None]:
        return capture.excluding(pid)

    def snapshot(self, rect: Rect) -> QImage | None:
        return ledges.grab(rect)


class WindowsSystem:
    @property
    def instance(self) -> str:
        return system.INSTANCE

    def acquire_single_instance(self) -> bool:
        return system.acquire_single_instance()

    def create_quit_event(self) -> int:
        return system.create_quit_event()

    def quit_requested(self, handle: int) -> bool:
        return system.quit_requested(handle)

    def message_box(self, text: str, title: str = "Pixel Cat") -> None:
        win32.message_box(text, title)

    def system_info(self) -> str:
        return control.system_info()

    def lock_screen(self) -> bool:
        return control.lock_screen()

    def media_key(self, action: str) -> None:
        control.media_key(action)

    def get_volume(self) -> tuple[int, bool]:
        return volume.get_volume()

    def set_volume(self, percent: int) -> int:
        return volume.set_volume(percent)

    def set_mute(self, muted: bool) -> None:
        volume.set_mute(muted)

    def hardware(self, fresh: bool = False) -> Hardware:
        return hardware.detect(fresh)


class WindowsApps:
    def prefetch(self) -> None:
        control.prefetch_store_apps()

    def find_shortcut(self, name: str) -> tuple[str, Path | str] | None:
        return control.find_shortcut(name)

    def open(self, target: str | Path) -> None:
        control.launch(target)

    def open_elevated(self, file: str, args: list[str]) -> None:
        control.launch_elevated(file, args)

    def open_settings(self, page: str) -> None:
        control.launch(control.SETTINGS_PAGES[page])

    def needs_admin(self, target: object) -> bool:
        return elevation.needs_admin(target)

    def lnk_target(self, path: str | Path) -> tuple[str, bool]:
        return elevation.lnk_target(path)

    def run_as_admin(self, target: str | Path) -> tuple[bool, str]:
        return control.run_as_admin(target)


class WindowsProcesses:
    def process_exe(self, pid: int) -> str:
        return control.process_exe(pid)

    def plan_force_end(self, target: str, own_pid: int) -> KillPlan:
        return control.plan_force_end(target, own_pid)

    def force_end(self, plan: KillPlan) -> tuple[list[int], list[int]]:
        return control.force_end(plan)

    def force_end_elevated(self, pids: list[int]) -> tuple[bool, str]:
        return control.force_end_elevated(pids)

    def alive(self, pids: list[int]) -> list[int]:
        return control.alive(pids)

    def is_elevated(self, pid: int) -> bool:
        return inputs.is_elevated(pid)

    def self_elevated(self) -> bool:
        return inputs.self_elevated()

    def uac_prompt_open(self) -> bool:
        return elevation.uac_prompt_open()


class WindowsNotifications:
    def open_feed(self) -> NotificationFeed:
        return notifications.Feed()

    def remember_own(self, *texts: str) -> None:
        notifications.remember_own(*texts)


# ------------------------------------------------------------------ storage (the code moves here in a later step)
class WindowsSecretStore:
    def protect(self, data: bytes) -> bytes:
        return dpapi.protect(data)

    def unprotect(self, blob: bytes) -> bytes:
        return dpapi.unprotect(blob)


class WindowsKnownFolders:
    def known_folder(self, name: str) -> Path | None:
        return known_folders.shell_folder(name)


class WindowsAutostart:
    def enabled(self) -> bool:
        return system.autostart_enabled()

    def set(self, enabled: bool) -> bool:
        return system.set_autostart(enabled)


class WindowsSaveMarks:
    """HKCU values named after the folder (raises OSError like winreg)."""

    def is_marked(self, folder_id: str) -> bool:
        return registry.is_marked(folder_id)

    def mark(self, folder_id: str) -> None:
        registry.mark(folder_id)

    def forget(self, folder_id: str) -> None:
        registry.forget(folder_id)


class WindowsInstaller:
    def is_setup_name(self, path: str | Path) -> bool:
        return installer.is_setup_name(path)

    def run_quiet(self, args: Any) -> int:
        return installer.run_quiet(args)

    def run_window(self, mode: str) -> int:
        return installer.run_window(mode)


def build() -> Platform:
    return Platform(
        name="windows", display=WindowsDisplay(), windows=WindowsWindows(), input=WindowsInput(),
        clipboard=WindowsClipboard(), accessibility=WindowsAccessibility(), capture=WindowsCapture(),
        system=WindowsSystem(), apps=WindowsApps(), processes=WindowsProcesses(), notifications=WindowsNotifications(),
        secrets=WindowsSecretStore(), folders=WindowsKnownFolders(), autostart=WindowsAutostart(),
        save_marks=WindowsSaveMarks(), installer=WindowsInstaller(),
    )
