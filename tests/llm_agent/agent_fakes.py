"""Test doubles for the agent side of ``pixelcat.llm`` (imported by conftest and the tests).

Every port the tools, the desktop agent and the watches use is a :class:`~fakes.platform.FakePlatform`
answered by :class:`FakeDesktop`, in ONE place (:meth:`FakeDesktop.install`), so these tests never touch
a real desktop.  Tests change answers with ``desk.platform.<port>.answers(name, fn)``.
"""
from __future__ import annotations

import re
import subprocess
import threading
import webbrowser
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from fakes.platform import FakePlatform
from pixelcat import imaging
from pixelcat.domain import AppWindow, Element, KillPlan, Monitor, Snapshot
from pixelcat.llm import agent as agent_mod
from pixelcat.llm import tools as tools_mod
from pixelcat.llm.tools import AppHooks
from pixelcat.ports import KnownFolders

OWN_PID = 4242
SCREEN = (0, 0, 1920, 1080)
FOLDER_ALIASES = ("desktop", "documents", "downloads", "pictures", "music", "videos")
# what the fake InputPort.parse_combo knows: key name -> a key code (the Windows ones, so tests read familiar)
KEY_CODES = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
             "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "delete": 0x2E, "del": 0x2E, "end": 0x23,
             "home": 0x24, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27}
KEY_CODES.update({f"f{i}": 0x6F + i for i in range(1, 25)})


def parse_combo(combo: str) -> list[int]:
    """The fake InputPort.parse_combo: 'ctrl+s' -> [0x11, ord('S')]; ValueError for unknown keys."""
    keys: list[int] = []
    for p in [p.strip().lower() for p in re.split(r"\s*\+\s*", combo.strip()) if p.strip()]:
        if p in KEY_CODES:
            keys.append(KEY_CODES[p])
        elif len(p) == 1 and ("a" <= p <= "z" or "0" <= p <= "9"):
            keys.append(ord(p.upper()))
        else:
            raise ValueError(f"unknown key '{p}'")
    if not keys:
        raise ValueError("no keys given")
    return keys


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
       **kw: Any) -> Element:
    return Element(eid, kind, name, rect, **kw)


class FakeDesktop:
    """A pretend desktop behind the ports: windows, processes, focus, clipboard, accessibility, input and launching.

    Everything that is "done" is recorded in ``calls`` as ``(name, *args)``; ``platform`` is the FakePlatform."""

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
        self.editors: dict[str, list[str]] = {"notepad": ["notepad.exe"]}   # editor_command answers (no VS Code)
        self.snapshots: dict[int, Snapshot] = {}
        self.focused: Element | None = None
        self.field_text: Any = None             # focused_text result (value or callable)
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
        self.platform: FakePlatform = FakePlatform()
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

    def _snapshot(self, hwnd: int, kinds: Any = None, limit: int = 150) -> Snapshot:
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

    def _plan(self, target: str) -> KillPlan:
        if self.plan is None:
            raise ValueError(f"no running app matches '{target}'")
        return self.plan

    def install(self, mp: pytest.MonkeyPatch, clock: Clock, folders: KnownFolders | None = None) -> None:
        self.clock = clock
        p = self.platform = FakePlatform(folders=folders) if folders is not None else FakePlatform()

        p.display.returns("virtual_screen", SCREEN)
        p.display.returns("monitors", [Monitor(1, SCREEN, SCREEN, 96, True, "fake")])

        win = p.windows
        win.answers("exists", lambda h: int(h or 0) in self.alive)
        win.answers("is_visible", lambda h: int(h or 0) in self.alive)
        win.answers("is_minimized", lambda h: any(w.hwnd == h and w.minimized for w in self.windows))
        win.answers("is_hung", lambda h: h in self.hung)
        win.answers("foreground", lambda: self.foreground)
        win.answers("pid", self.pid_of)
        win.answers("title", lambda h: self.titles.get(h, ""))
        win.answers("class_name", lambda h: self.classes.get(h, ""))
        win.answers("frame_bounds", lambda h: self.rects.get(h))
        win.answers("rect", lambda h: self.rects.get(h) or (0, 0, 0, 0))
        win.answers("app_windows", lambda own_pid: [w for w in self.windows if w.hwnd in self.alive])
        win.answers("window_action", self.record("window_action"))
        win.answers("close_windows", self.record("close_windows", lambda ws: len(ws)))
        win.answers("active_popup", lambda h: self.popups.get(h, h))
        win.answers("window_at", self._window_at)
        win.answers("same_app", self._same_app)
        win.answers("is_foreground", lambda h: self._same_app(self.foreground, h))
        win.answers("bring_to_front", self._bring)
        win.answers("own_click_through", self.record("own_click_through", []))
        win.answers("end_own_click_through", self.record("end_own_click_through"))

        proc = p.processes
        proc.answers("process_exe", lambda pid: self.exes.get(pid, ""))
        proc.answers("plan_force_end", self.record("plan_force_end", lambda t, pid: self._plan(t)))
        proc.answers("force_end", self.record("force_end", lambda plan: ([1, 2], [])))
        proc.answers("alive", self.record("alive", lambda pids: []))
        proc.answers("force_end_elevated", self.record("force_end_elevated", lambda pids: (True, "")))
        proc.answers("is_elevated", lambda pid: pid in self.elevated)
        proc.answers("self_elevated", lambda: False)
        proc.answers("uac_prompt_open", lambda: self.uac)

        apps = p.apps
        apps.answers("find_shortcut", lambda name: self.shortcuts.get(name.strip().lower()))
        apps.answers("needs_admin", lambda t: str(t) in self.admin_targets)
        apps.answers("editor_command", lambda app: list(self.editors[app]) if app in self.editors else None)
        apps.answers("run_as_admin", self.record("run_as_admin", lambda t: (True, "")))
        apps.answers("open", self.record("open"))
        apps.answers("open_elevated", self.record("open_elevated"))
        apps.answers("open_settings", self.record("open_settings"))

        system = p.system
        system.answers("media_key", self.record("media_key"))
        system.answers("lock_screen", self.record("lock_screen", True))
        system.answers("system_info", self.record("system_info", "RAM: 8 GB | CPU: 3%"))
        system.answers("get_volume", lambda: self.volume)
        system.answers("set_volume", self.record("set_volume", lambda pct: pct))
        system.answers("set_mute", self.record("set_mute"))

        inp = p.input
        inp.answers("cursor_pos", lambda: self.cursor)
        inp.answers("click", self.record("click"))
        inp.answers("scroll", self.record("scroll"))
        inp.answers("move_cursor", self.record("move_cursor"))
        inp.answers("drag", self.record("drag", lambda *a, **k: self.drag_result))
        inp.answers("glide", self.record("glide", lambda *a, **k: self.glide_result))
        inp.answers("pinch", self.record("pinch", lambda *a, **k: self.pinch_result))
        inp.answers("holding", self._holding)
        inp.answers("parse_combo", parse_combo)
        inp.answers("press_combo", self._press_combo)
        inp.answers("type_text", self._type_text)

        clip = p.clipboard
        clip.answers("backup", lambda: self.clipboard)
        clip.answers("write", self.record("write_clipboard", True))
        clip.answers("set_private_text", self._set_private)
        clip.answers("clear", self.record("clear_clipboard"))
        clip.answers("sequence", lambda: self.clip_seq)
        clip.answers("has", lambda kind: kind in self.clip_formats)
        clip.answers("text", lambda: self.clip_text)

        a11y = p.accessibility
        a11y.answers("snapshot", self._snapshot)
        a11y.answers("focused_element", lambda: self.focused)
        a11y.answers("focused_text", self._focused_text)
        a11y.answers("document_text", lambda h, n=8000: self.docs.get(h, "")[:n])

        cap = p.capture
        cap.answers("grab", self._image)
        cap.answers("grab_without", self._image)
        cap.answers("grab_window", lambda h, frame: self._image(frame))
        cap.answers("excluding", lambda pid: nullcontext())

        p.notifications.answers("open_feed", FakeFeed)

        mp.setattr(imaging, "draw_marks", self.record("draw_marks"))
        mp.setattr(agent_mod, "time", clock)
        mp.setattr(agent_mod, "_wait", self._wait)
        mp.setattr(webbrowser, "open", self.record("browser"))
        mp.setattr(tools_mod, "subprocess", SimpleNamespace(Popen=self.record("popen"),
                                                            list2cmdline=subprocess.list2cmdline))


def _inside(r: tuple[int, int, int, int], x: int, y: int) -> bool:
    return r[0] <= x < r[2] and r[1] <= y < r[3]


class FakeFeed:
    """A NotificationFeed (the system's notification list)."""

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
        self.folders: Any = None            # the KnownFolders port (set by the hooks fixture)
        self.platform: Any = None           # the FakePlatform for the ToolBox (set by the hooks fixture)

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
