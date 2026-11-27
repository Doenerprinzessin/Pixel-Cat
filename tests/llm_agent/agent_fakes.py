"""Test doubles for the agent side of ``pixelcat.llm`` (imported by conftest and the tests).

Every desktop call the tools and the desktop agent can reach is replaced by :class:`FakeDesktop`,
in ONE place (:meth:`FakeDesktop.install`), so these tests are safe on a real Windows desktop and
later PRs that move the desktop layer behind ports only need to update this file.
"""
from __future__ import annotations

import os
import subprocess
import threading
import webbrowser
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from pixelcat.desktop import capture, control, elevation, inputs, uia, volume, win32
from pixelcat.desktop.control import AppWindow
from pixelcat.llm import agent as agent_mod
from pixelcat.llm import tools as tools_mod
from pixelcat.llm.tools import AppHooks

OWN_PID = 4242
SCREEN = (0, 0, 1920, 1080)
FOLDER_ALIASES = ("desktop", "documents", "downloads", "pictures", "music", "videos")


class Clock:
    """Virtual time for agent.py: sleeps return at once and move the clock on."""

    def __init__(self) -> None:
        self.t = 1_000_000.0
        self.slept: list[float] = []

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += max(0.0, float(s))

    def time(self) -> float:
        return self.t

    monotonic = perf_counter = time


def el(eid: int, kind: str = "button", name: str = "", rect: tuple[int, int, int, int] = (110, 110, 150, 130),
       **kw: Any) -> uia.Element:
    return uia.Element(eid, kind, name, rect, **kw)


class FakeDesktop:
    """A pretend desktop: windows, processes, focus, clipboard, UI Automation, input and launching.

    Everything that is "done" is recorded in ``calls`` as ``(name, *args)``."""

    def __init__(self) -> None:
        self.windows: list[AppWindow] = []
        self.alive: set[int] = set()
        self.rects: dict[int, tuple[int, int, int, int]] = {}
        self.titles: dict[int, str] = {}
        self.classes: dict[int, str] = {}
        self.pids: dict[int, int] = {}
        self.exes: dict[int, str] = {}          # pid -> exe
        self.popups: dict[int, int] = {}
        self.elevated: set[int] = set()         # pids running as admin
        self.hung: set[int] = set()
        self.foreground = 0
        self.bring_ok = True
        self.cursor = (5, 5)
        self.at: Any = None                     # window_at result (int or callable(x, y))
        self.uac = False
        self.shortcuts: dict[str, tuple[str, Any]] = {}
        self.admin_targets: set[str] = set()
        self.snapshots: dict[int, uia.Snapshot] = {}
        self.focused: uia.Element | None = None
        self.field_text: Any = None             # uia.focused_text result (value or callable)
        self.docs: dict[int, str] = {}
        self.clipboard: list[tuple[int, bytes]] | None = [(13, b"user clip")]
        self.clip_text: str | None = None
        self.clip_seq = 1
        self.clip_formats: set[Any] = set()
        self.copy_all_text: Any = None          # what ctrl+c copies (value or callable)
        self.volume = (40, False)
        self.typed_limit: int | None = None
        self.drag_result = ""
        self.pinch_result = ""
        self.glide_result = ""
        self.plan: Any = None
        self.private_text: str | None = None    # text the agent put on the clipboard for pasting
        self.color: Any = lambda: (255, 255, 255)   # colour of every captured picture
        self.calls: list[tuple] = []
        self.user32: Any = None
        self.clock = Clock()

    # ------------------------------------------------------------- setup helpers
    def add_window(self, hwnd: int, title: str, exe: str, *, minimized: bool = False, maximized: bool = False,
                   pid: int | None = None, rect: tuple[int, int, int, int] = (100, 100, 900, 700),
                   cls: str = "Win") -> AppWindow:
        pid = pid if pid is not None else hwnd + 10_000
        w = AppWindow(hwnd, title, exe, minimized, maximized, cls, pid)
        self.windows.append(w)
        self.alive.add(hwnd)
        self.rects[hwnd] = rect
        self.titles[hwnd] = title
        self.classes[hwnd] = cls
        self.pids[hwnd] = pid
        self.exes[pid] = exe
        return w

    def record(self, name: str, result: Any = None):  # type: ignore[no-untyped-def]
        def fn(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append((name, *args, *(sorted(kwargs.items()) if kwargs else ())))
            return result(*args, **kwargs) if callable(result) else result
        return fn

    def called(self, name: str) -> list[tuple]:
        return [c[1:] for c in self.calls if c[0] == name]

    def pid_of(self, hwnd: int) -> int:
        return self.pids.get(int(hwnd or 0), 0)

    # ------------------------------------------------------------- fakes
    def _same_app(self, a: int, b: int) -> bool:
        return bool(a and b) and (a == b or self.pid_of(a) == self.pid_of(b))

    def _bring(self, hwnd: int, timeout: float = 1.5) -> bool:
        self.calls.append(("bring_to_front", hwnd))
        if self.bring_ok:
            self.foreground = hwnd
        return self.bring_ok

    def _window_at(self, x: int, y: int) -> int:
        if callable(self.at):
            return self.at(x, y)
        if self.at is not None:
            return self.at
        return next((w.hwnd for w in self.windows if not w.minimized and _inside(self.rects[w.hwnd], x, y)), 0)

    def _focused_text(self, max_chars: int = 50000) -> str | None:
        return self.field_text() if callable(self.field_text) else self.field_text

    def _snapshot(self, hwnd: int, kinds: Any = None, limit: int = 150) -> uia.Snapshot:
        if hwnd not in self.snapshots:
            raise OSError("no UI Automation here")
        return self.snapshots[hwnd]

    def _type_text(self, text: str, still_ok, cancel=None, chunk: int = 1) -> int:  # type: ignore[no-untyped-def]
        self.calls.append(("type_text", text))
        n = len(text) if self.typed_limit is None else self.typed_limit
        if isinstance(self.field_text, str):
            self.field_text = self.field_text + text[:n]
        return n

    def _press_combo(self, keys: list[int], hold: float = 0.0, cancel=None) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(("press_combo", list(keys), hold))
        if keys == [0x11, ord("C")]:
            self.clip_seq += 1
            self.clip_text = self.copy_all_text() if callable(self.copy_all_text) else self.copy_all_text
        elif keys == [0x11, ord("V")] and isinstance(self.field_text, str) and self.private_text is not None:
            self.field_text += self.private_text

    def _set_private(self, text: str) -> bool:
        self.calls.append(("set_clipboard_private_text", text))
        self.private_text = text
        return True

    @contextmanager
    def _holding(self, keys: list[int]):  # type: ignore[no-untyped-def]
        self.calls.append(("hold", list(keys)))
        yield

    def _wait(self, seconds: float, cancel: threading.Event | None = None) -> bool:
        self.clock.sleep(seconds)
        return not (cancel is not None and cancel.is_set())

    def _image(self, rect, *_a):  # type: ignore[no-untyped-def]
        from PySide6.QtGui import QColor, QImage

        self.calls.append(("grab", tuple(rect)))
        img = QImage(max(1, rect[2] - rect[0]), max(1, rect[3] - rect[1]), QImage.Format.Format_RGB32)
        img.fill(QColor(*self.color()))
        return img

    def _plan(self, target: str) -> control.KillPlan:
        if self.plan is None:
            raise ValueError(f"no running app matches '{target}'")
        return self.plan

    def install(self, mp: pytest.MonkeyPatch, fake_dll: type, clock: Clock) -> None:
        self.clock = clock
        u = fake_dll("user32")
        u.IsWindow.result = lambda h: int(int(h or 0) in self.alive)
        u.IsIconic.result = lambda h: int(any(w.hwnd == h and w.minimized for w in self.windows))
        u.IsHungAppWindow.result = lambda h: int(h in self.hung)
        u.IsWindowVisible.result = lambda h: int(int(h or 0) in self.alive)
        u.GetForegroundWindow.result = lambda: self.foreground
        u.GetSystemMetrics.result = lambda i: {76: SCREEN[0], 77: SCREEN[1], 78: SCREEN[2] - SCREEN[0],
                                               79: SCREEN[3] - SCREEN[1]}.get(i, 0)
        u.EnumWindows.result = 1
        self.user32 = u
        mp.setattr(win32, "user32", u)
        mp.setattr(win32, "window_pid", self.pid_of)
        mp.setattr(win32, "window_text", lambda h: self.titles.get(h, ""))
        mp.setattr(win32, "class_name", lambda h: self.classes.get(h, ""))
        mp.setattr(win32, "frame_bounds", lambda h: self.rects.get(h))
        mp.setattr(win32, "cursor_pos", lambda: self.cursor)
        mp.setattr(win32, "get_exstyle", lambda h: 0)
        mp.setattr(win32, "set_exstyle", self.record("set_exstyle"))

        mp.setattr(control, "list_app_windows", lambda own_pid: [w for w in self.windows if w.hwnd in self.alive])
        mp.setattr(control, "process_exe", lambda pid: self.exes.get(pid, ""))
        mp.setattr(control, "find_shortcut", lambda name: self.shortcuts.get(name.strip().lower()))
        mp.setattr(control, "window_action", self.record("window_action"))
        mp.setattr(control, "close_windows", self.record("close_windows", lambda ws: len(ws)))
        mp.setattr(control, "launch", self.record("launch"))
        mp.setattr(control, "media_key", self.record("media_key"))
        mp.setattr(control, "lock_screen", self.record("lock_screen", True))
        mp.setattr(control, "system_info", self.record("system_info", "RAM: 8 GB | CPU: 3%"))
        mp.setattr(control, "plan_force_end", self.record("plan_force_end", lambda t, pid: self._plan(t)))
        mp.setattr(control, "force_end", self.record("force_end", lambda plan: ([1, 2], [])))
        mp.setattr(control, "alive", self.record("alive", lambda pids: []))
        mp.setattr(control, "force_end_elevated", self.record("force_end_elevated", lambda pids: (True, "")))
        mp.setattr(control, "run_as_admin", self.record("run_as_admin", lambda t: (True, "")))

        mp.setattr(elevation, "needs_admin", lambda t: str(t) in self.admin_targets)
        mp.setattr(elevation, "uac_prompt_open", lambda: self.uac)
        mp.setattr(elevation, "lnk_target", lambda p: ("", False))

        mp.setattr(volume, "get_volume", lambda: self.volume)
        mp.setattr(volume, "set_volume", self.record("set_volume", lambda p: p))
        mp.setattr(volume, "set_mute", self.record("set_mute"))

        mp.setattr(inputs, "active_popup", lambda h: self.popups.get(h, h))
        mp.setattr(inputs, "window_at", self._window_at)
        mp.setattr(inputs, "root_of", lambda h: h)
        mp.setattr(inputs, "same_app", self._same_app)
        mp.setattr(inputs, "is_foreground", lambda h: self._same_app(self.foreground, h))
        mp.setattr(inputs, "bring_to_front", self._bring)
        mp.setattr(inputs, "is_elevated", lambda pid: pid in self.elevated)
        mp.setattr(inputs, "self_elevated", lambda: False)
        mp.setattr(inputs, "click", self.record("click"))
        mp.setattr(inputs, "scroll", self.record("scroll"))
        mp.setattr(inputs, "move_cursor", self.record("move_cursor"))
        mp.setattr(inputs, "drag", self.record("drag", lambda *a, **k: self.drag_result))
        mp.setattr(inputs, "glide", self.record("glide", lambda *a, **k: self.glide_result))
        mp.setattr(inputs, "pinch", self.record("pinch", lambda *a, **k: self.pinch_result))
        mp.setattr(inputs, "holding", self._holding)
        mp.setattr(inputs, "press_combo", self._press_combo)
        mp.setattr(inputs, "type_text", self._type_text)
        mp.setattr(inputs, "_wait", self._wait)
        mp.setattr(inputs, "backup_clipboard", lambda: self.clipboard)
        mp.setattr(inputs, "write_clipboard", self.record("write_clipboard", True))
        mp.setattr(inputs, "set_clipboard_private_text", self._set_private)
        mp.setattr(inputs, "clear_clipboard", self.record("clear_clipboard"))
        mp.setattr(inputs, "clipboard_sequence", lambda: self.clip_seq)
        mp.setattr(inputs, "clipboard_has_format", lambda f: f in self.clip_formats)
        mp.setattr(inputs, "get_clipboard_text", lambda: self.clip_text)
        mp.setattr(inputs, "passthrough_until", 0.0)

        mp.setattr(uia, "snapshot", self._snapshot)
        mp.setattr(uia, "focused_element", lambda: self.focused)
        mp.setattr(uia, "focused_text", self._focused_text)
        mp.setattr(uia, "document_text", lambda h, n=8000: self.docs.get(h, "")[:n])

        mp.setattr(capture, "grab", self._image)
        mp.setattr(capture, "grab_without", self._image)
        mp.setattr(capture, "grab_window", lambda h, frame: self._image(frame))
        mp.setattr(capture, "excluding", lambda pid: nullcontext())
        mp.setattr(capture, "draw_marks", self.record("draw_marks"))

        mp.setattr(agent_mod, "time", clock)
        mp.setattr(agent_mod, "control_monitor", lambda x, y: SCREEN)

        mp.setattr(webbrowser, "open", self.record("browser"))
        mp.setattr(os, "startfile", self.record("startfile"), raising=False)
        mp.setattr(tools_mod, "subprocess", SimpleNamespace(Popen=self.record("popen"),
                                                            list2cmdline=subprocess.list2cmdline))


def _inside(r: tuple[int, int, int, int], x: int, y: int) -> bool:
    return r[0] <= x < r[2] and r[1] <= y < r[3]


class FakeFeed:
    """Stands in for notifications.Feed (the Windows notification database)."""

    def __init__(self) -> None:
        self.items: list = []
        self.badge: dict[str, str] = {}
        self.broken = False

    def last_id(self) -> int:
        if self.broken:
            raise OSError("no database")
        return max((n.id for n in self.items), default=0)

    def since(self, last_id: int) -> list:
        return [n for n in self.items if n.id > last_id]

    def badges(self) -> dict[str, str]:
        return dict(self.badge)


class Hooks:
    """The app's callbacks for the tools (cat, timers, screenshot, clipboard), recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.request = "the user's request"

    def hooks(self, watches: Any = None, task_request: bool = True) -> AppHooks:
        return AppHooks(
            cat_action=lambda act: self._rec("cat_action", act, f"The cat does {act}."),
            set_timer=lambda secs, label: self._rec("set_timer", (secs, label), f"Timer set: {label}."),
            screenshot=lambda: self._rec("screenshot", None, "Saved a screenshot."),
            set_clipboard=lambda text: self._rec("set_clipboard", text, "Copied."),
            own_pid=OWN_PID, watches=watches,
            task_request=(lambda: self.request) if task_request else None)

    def _rec(self, name: str, arg: Any, out: str) -> str:
        self.calls.append((name, arg))
        return out
