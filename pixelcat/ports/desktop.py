"""What the cat needs from the desktop it lives on: screens, windows, mouse and keyboard, clipboard,
accessibility, screenshots, the PC itself, apps, processes and notifications.

Plain interfaces (``typing.Protocol``); an adapter in ``pixelcat.adapters`` implements them for one
platform and ``pixelcat.bootstrap`` picks it.  Coordinates are physical pixels on the virtual screen (or
Qt's logical points where :meth:`DisplayPort.qt_logical` says so), rects are (left, top, right, bottom).  Window handles are opaque :data:`~pixelcat.domain.WindowId`s.
"""
from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

from ..domain import (AppWindow, ClipboardKind, Element, ElementKinds, Hardware, KillPlan, Monitor, Notification,
                      Snapshot, UserState, WindowId, WinInfo)

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

Rect = tuple[int, int, int, int]   # left, top, right, bottom

# Opaque values: only the adapter that made them knows what is inside.
Keys = Any              # a parsed key combo: what InputPort.parse_combo returns, only for InputPort.press_combo
ClipboardBackup = Any   # what ClipboardPort.backup returns, only for ClipboardPort.write


class DisplayPort(Protocol):
    """The screens, and what the user is doing on them as a whole."""

    def set_dpi_awareness(self) -> bool:
        """Work in physical pixels on every monitor (call before any window exists)."""
        ...

    def monitors(self) -> list[Monitor]:
        """Every monitor with its rect, work area and DPI, the primary one first.  Never empty: when the
        platform can't tell, the list holds ``FALLBACK_MONITOR`` (the core relies on a primary monitor)."""
        ...

    def virtual_screen(self) -> Rect:
        """The rect that holds all monitors."""
        ...

    def window_dpi(self, win: WindowId) -> int:
        """DPI of the monitor a window is on; 0 if unknown."""
        ...

    def user_state(self) -> UserState:
        """A full-screen game or video, a presentation, or nothing special."""
        ...

    def session_locked(self) -> bool:
        """True while the PC is locked (desktop input can't work then)."""
        ...

    def qt_logical(self) -> bool:
        """True if this port's coordinates are Qt's logical coordinates (the window system scales for Retina
        screens); False if they are physical pixels (Qt's DPI scaling is off, Windows)."""
        ...


class WindowPort(Protocol):
    """Other apps' windows, and the cat's own floating windows."""

    def top_level(self, own_pid: int, include: set[int] | frozenset[int] = frozenset()) -> list[WinInfo]:
        """Visible top-level windows, top of the z-order first; the cat's own only if in ``include``."""
        ...

    def app_windows(self, own_pid: int) -> list[AppWindow]:
        """The windows a user would call apps (Alt+Tab-like, minimised ones too), not the cat's own."""
        ...

    def foreground(self) -> WindowId:
        """The active window; 0 if none."""
        ...

    def is_fullscreen(self, win: WindowId, monitor_rect: Rect) -> bool:
        """True when the window covers its whole monitor (games, videos, slideshows)."""
        ...

    def exists(self, win: WindowId) -> bool:
        """True while the window is still there."""
        ...

    def is_visible(self, win: WindowId) -> bool:
        """True if the window is shown."""
        ...

    def is_minimized(self, win: WindowId) -> bool:
        """True if the window is minimised."""
        ...

    def is_hung(self, win: WindowId) -> bool:
        """True if the window's app is not responding."""
        ...

    def is_cloaked(self, win: WindowId) -> bool:
        """True if the system hides a "visible" window (another virtual desktop, a suspended app)."""
        ...

    def frame_bounds(self, win: WindowId) -> Rect | None:
        """The window's visible frame (without invisible borders and shadow); None if unknown."""
        ...

    def rect(self, win: WindowId) -> Rect:
        """The visible frame, or the whole window rect if the frame is unknown."""
        ...

    def pid(self, win: WindowId) -> int:
        """The process the window belongs to."""
        ...

    def title(self, win: WindowId) -> str:
        """The window's title."""
        ...

    def class_name(self, win: WindowId) -> str:
        """The window's class name."""
        ...

    def window_at(self, x: int, y: int) -> WindowId:
        """The top-level window at a screen point; 0 if none."""
        ...

    def active_popup(self, win: WindowId) -> WindowId:
        """A modal dialog the window owns right now (e.g. 'Save as'), or the window itself."""
        ...

    def same_app(self, a: WindowId, b: WindowId) -> bool:
        """True if two top-level windows belong to the same app."""
        ...

    def is_foreground(self, win: WindowId) -> bool:
        """True if the active window belongs to the same app as ``win``."""
        ...

    def bring_to_front(self, win: WindowId, timeout: float = 1.5) -> bool:
        """Make the window the active one (restored if minimised); raises RuntimeError if its app hangs."""
        ...

    def window_action(self, w: AppWindow, action: str) -> None:
        """Minimise, maximise, restore or close an app window."""
        ...

    def close_windows(self, windows: list[AppWindow]) -> int:
        """Ask windows to close (like their X button, never kills); returns how many were asked."""
        ...

    def make_tool_window(self, win: WindowId) -> None:
        """One of the cat's windows: no taskbar button, no Alt+Tab, never takes the focus."""
        ...

    def set_click_through(self, win: WindowId, enabled: bool) -> None:
        """Let clicks fall through one of the cat's windows (or catch them again)."""
        ...

    def move(self, win: WindowId, x: int, y: int, w: int, h: int, topmost: bool = True) -> None:
        """Move and size one of the cat's windows (``topmost=False``: keep its place in the z-order)."""
        ...

    def keep_topmost(self, win: WindowId) -> None:
        """Put one of the cat's windows on top of everything again."""
        ...

    def place_below(self, win: WindowId, other: WindowId) -> None:
        """Put one of the cat's windows right under another window in the z-order."""
        ...

    def own_click_through(self, own_pid: int, keep: WindowId) -> list[WindowId]:
        """Make the cat's visible windows (except ``keep``) click-through; returns the ones that changed."""
        ...

    def end_own_click_through(self, changed: list[WindowId]) -> None:
        """Undo :meth:`own_click_through` for the windows it changed that are still there."""
        ...


class EscapeWatcher(Protocol):
    """Calls back when the user presses Esc anywhere (stops the chat's helper)."""

    @property
    def active(self) -> bool:
        """True while it is watching."""
        ...

    def start(self) -> None:
        """Start watching."""
        ...

    def stop(self) -> None:
        """Stop watching."""
        ...


class InputPort(Protocol):
    """The pointer and the keyboard: their state, and synthetic input for the chat's helper."""

    def cursor_pos(self) -> tuple[int, int]:
        """Where the mouse pointer is."""
        ...

    def release_went_elsewhere(self, win: WindowId) -> bool:
        """True if a mouse press on the window was let go without the window hearing of it."""
        ...

    def mouse_buttons_down(self) -> bool:
        """True while any mouse button is held."""
        ...

    def last_input_tick(self) -> int:
        """When the user last pressed a key or moved the mouse (a ms tick)."""
        ...

    def idle_seconds(self) -> float:
        """Seconds since the user last pressed a key or moved the mouse."""
        ...

    def passthrough_active(self) -> bool:
        """True while the chat's helper drives the mouse (the cat lets clicks through then)."""
        ...

    def set_passthrough_until(self, until: float) -> None:
        """The helper drives the mouse until ``time.monotonic()`` reaches ``until`` (0 = not any more)."""
        ...

    def move_cursor(self, x: int, y: int) -> None:
        """Put the mouse pointer somewhere."""
        ...

    def click(self, x: int, y: int, button: str = "left", double: bool = False, count: int = 1, hold: float = 0.0,
              cancel: threading.Event | None = None) -> None:
        """``count`` clicks in a row (2 = double, 3 = triple); ``hold`` keeps the button down (a long press)."""
        ...

    def scroll(self, x: int, y: int, clicks: int, horizontal: bool = False) -> None:
        """Turn the mouse wheel over a point (negative = down / left)."""
        ...

    def drag(self, points: list[tuple[int, int]], button: str = "left", seconds: float = 0.6,
             cancel: threading.Event | None = None) -> str:
        """Press at the first point, move along the others, let go at the last; "" or why it stopped."""
        ...

    def glide(self, x: int, y: int, seconds: float = 0.3, cancel: threading.Event | None = None) -> str:
        """Move the pointer to a point like a hand would (hover); "" or why it stopped."""
        ...

    def pinch(self, x: int, y: int, start: float, end: float, seconds: float = 0.5,
              cancel: threading.Event | None = None) -> str:
        """Two fingers around a point, from ``start`` px apart to ``end`` px apart; "" or why it stopped."""
        ...

    def holding(self, keys: list[str]) -> AbstractContextManager[None]:
        """Keep modifier keys ("ctrl", "shift", "alt", "win") pressed while inside; always let go again."""
        ...

    def parse_combo(self, combo: str) -> Keys:
        """'ctrl+shift+s' -> keys for :meth:`press_combo`; raises ValueError for unknown keys."""
        ...

    def press_combo(self, keys: Keys, hold: float = 0.0, cancel: threading.Event | None = None) -> None:
        """Press keys in order and release them in reverse (after ``hold`` seconds); always released."""
        ...

    def type_text(self, text: str, still_ok: Callable[[], bool], cancel: threading.Event | None = None,
                  chunk: int = 1, pause: float = 0.02) -> int:
        """Type text, checking ``still_ok`` before every chunk; returns the characters typed."""
        ...

    def escape_watcher(self, on_escape: Callable[[], None]) -> EscapeWatcher:
        """A watcher that calls ``on_escape`` when the user presses Esc (not started yet)."""
        ...


class ClipboardPort(Protocol):
    """The clipboard (from any thread)."""

    def backup(self) -> ClipboardBackup | None:
        """Everything on the clipboard, to put back later; None if it can't be read."""
        ...

    def write(self, backup: ClipboardBackup, private: bool = True) -> bool:
        """Put a :meth:`backup` back (``private``: kept out of clipboard history and cloud sync)."""
        ...

    def set_private_text(self, text: str) -> bool:
        """Put text on the clipboard, kept out of clipboard history and cloud sync."""
        ...

    def clear(self) -> None:
        """Empty the clipboard."""
        ...

    def sequence(self) -> int:
        """A number that changes every time anything is copied."""
        ...

    def has(self, kind: ClipboardKind) -> bool:
        """True if the clipboard holds files, rich text or HTML."""
        ...

    def text(self) -> str | None:
        """The clipboard's text, or None if it holds no text."""
        ...


class AccessibilityPort(Protocol):
    """Another app's controls and text, read exactly (no OCR)."""

    def snapshot(self, win: WindowId, kinds: ElementKinds = "interactive", limit: int = 150) -> Snapshot:
        """The window's controls of one kind (at most ``limit``)."""
        ...

    def focused_element(self) -> Element | None:
        """The control that has the keyboard focus."""
        ...

    def focused_text(self, max_chars: int = 50000) -> str | None:
        """The text of the focused control (an editor's document); None if it has none."""
        ...

    def document_text(self, win: WindowId, max_chars: int = 8000) -> str:
        """The readable text of a window."""
        ...


class CapturePort(Protocol):
    """Screenshots (kept in memory only)."""

    def grab(self, rect: Rect) -> QImage:
        """The screen in ``rect`` (may span monitors)."""
        ...

    def grab_without(self, rect: Rect, pid: int) -> QImage:
        """Like :meth:`grab`, with process ``pid``'s windows (the cat) left out."""
        ...

    def grab_window(self, win: WindowId, frame: Rect) -> QImage | None:
        """One window's own pixels, even where others cover it (``frame``: its visible bounds); None if it can't."""
        ...

    def excluding(self, pid: int) -> AbstractContextManager[None]:
        """While inside, process ``pid``'s windows don't show up in :meth:`grab`."""
        ...

    def snapshot(self, rect: Rect) -> QImage | None:
        """The screen in ``rect`` as an RGB32 image for the ledge finder; None if it can't."""
        ...


class SystemPort(Protocol):
    """This PC, and this running copy of the cat."""

    @property
    def instance(self) -> str:
        """Which copy of the cat this is ("" normally; test setups run more than one)."""
        ...

    def acquire_single_instance(self) -> bool:
        """False if another cat already runs."""
        ...

    def create_quit_event(self) -> int:
        """A handle Setup can use to ask the running cat to close; 0 if there is none."""
        ...

    def quit_requested(self, handle: int) -> bool:
        """True once Setup asked the cat to close."""
        ...

    def message_box(self, text: str, title: str = "Pixel Cat") -> None:
        """Show a message the user has to click away."""
        ...

    def system_info(self) -> str:
        """Battery, memory, CPU and uptime as text."""
        ...

    def lock_screen(self) -> bool:
        """Lock the PC."""
        ...

    def media_key(self, action: str) -> None:
        """Press a media key (one of the domain's MEDIA_ACTIONS)."""
        ...

    def get_volume(self) -> tuple[int, bool]:
        """The speaker volume in percent, and whether it is muted."""
        ...

    def set_volume(self, percent: int) -> int:
        """Set the speaker volume; returns the new volume."""
        ...

    def set_mute(self, muted: bool) -> None:
        """Mute or unmute the speakers."""
        ...

    def hardware(self, fresh: bool = False) -> Hardware:
        """What this PC can do for the local AI (looked at once unless ``fresh``)."""
        ...


class AppsPort(Protocol):
    """Finding and starting apps, folders and settings pages."""

    def prefetch(self) -> None:
        """Warm the list of installed apps in the background."""
        ...

    def find_shortcut(self, name: str) -> tuple[str, Path | str] | None:
        """The installed app that best matches a name: (its name, what to open)."""
        ...

    def open(self, target: str | Path) -> None:
        """Open an app, file, folder or URI the way double-clicking it would; raises OSError if it can't."""
        ...

    def open_elevated(self, file: str, args: list[str]) -> None:
        """Start a program that asks for admin rights itself (Windows shows UAC); raises OSError if it can't."""
        ...

    def open_settings(self, page: str) -> None:
        """Open a system settings page (one of the domain's SETTINGS_PAGES); raises OSError if it can't."""
        ...

    def needs_admin(self, target: object) -> bool:
        """True if starting an app target (command list, shortcut, program, URI) asks for admin rights."""
        ...

    def editor_command(self, app: str) -> list[str] | None:
        """The command that opens a file in an editor ("vscode" or "notepad", the plain text editor); the file's
        path goes last.  None if that editor isn't installed."""
        ...

    def find_ollama(self) -> Path | None:
        """Ollama on this PC (its app, or the command-line program); None if it isn't installed."""
        ...

    def start_ollama(self, exe: Path) -> None:
        """Start Ollama (what :meth:`find_ollama` found) in the background; gives up quietly."""
        ...

    def run_as_admin(self, target: str | Path) -> tuple[bool, str]:
        """Start an app with admin rights (the user confirms); (started, why not)."""
        ...


class ProcessPort(Protocol):
    """Running programs."""

    def process_exe(self, pid: int) -> str:
        """The program file name of a process (e.g. "chrome.exe"); "" if unknown."""
        ...

    def plan_force_end(self, target: str, own_pid: int) -> KillPlan:
        """Which processes ending ``target`` would stop; raises ValueError if none match or it must not."""
        ...

    def force_end(self, plan: KillPlan) -> tuple[list[int], list[int]]:
        """End the planned processes; (ended, denied) pids."""
        ...

    def force_end_elevated(self, pids: list[int]) -> tuple[bool, str]:
        """End processes that need admin rights (the user confirms); (done, why not)."""
        ...

    def alive(self, pids: list[int]) -> list[int]:
        """The pids that still run."""
        ...

    def is_elevated(self, pid: int) -> bool:
        """True if a process runs with admin rights (the cat's input can't reach it)."""
        ...

    def self_elevated(self) -> bool:
        """True if the cat itself runs with admin rights."""
        ...

    def uac_prompt_open(self) -> bool:
        """True while the system asks the user for admin rights."""
        ...


class NotificationFeed(Protocol):
    """Notifications other apps showed, as the system keeps them."""

    def available(self) -> bool:
        """True if the notifications can be read."""
        ...

    def last_id(self) -> int:
        """The newest notification's id."""
        ...

    def since(self, last_id: int) -> list[Notification]:
        """Pop-up notifications that arrived after ``last_id``, oldest first."""
        ...

    def badges(self) -> dict[str, str]:
        """Unread counters on app icons: app id -> value."""
        ...


class NotificationPort(Protocol):
    """Other apps' notifications."""

    def open_feed(self) -> NotificationFeed:
        """A reader for the notifications."""
        ...

    def remember_own(self, *texts: str) -> None:
        """The cat is about to show these texts itself: a feed never reports them."""
        ...
