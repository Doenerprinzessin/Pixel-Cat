"""The chat's eyes and hands on the desktop.

* Eyes: ``look`` takes a screenshot (sent to the chat's model) with numbered
  boxes on the clickable elements; ``read`` lists a window's controls and its
  document text through UI Automation (exact text, no OCR guessing); ``watch``
  waits inside a task until the screen changes (or a text appears / goes, or it
  stops moving) and then looks - for quizzes, games, things that appear.
* Hands: ``click`` (any button, double/triple, held keys, long press), ``drag``,
  ``hover``, ``zoom`` (ctrl + wheel or a touch pinch), ``type``, ``keys``,
  ``scroll`` via SendInput / touch injection, moving like a hand would.

Guard rails (enforced here, not by the model):
* Every action targets one app window, which is brought to the front first.
  Typing and key presses stop at once if another window comes to the front
  (e.g. because the user clicked somewhere else) or the user presses Esc.  A
  drag or hover stops (button let go) when the user moves the mouse.
* Clicks must land on the target app itself - never on another app that
  happens to cover the spot.  The cat and its chat let clicks pass through.
* Password fields are never typed into; apps running as administrator are left
  alone (Windows blocks that anyway); command lines and the Run box always need
  the user's OK, even in auto mode (decided in tools.py).

Everything runs on a worker thread so the cat keeps animating.
"""
from __future__ import annotations

import logging
import re as _re
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Iterator

from .. import imaging
from ..domain import AppWindow, Element, ElementKinds, PermissionNeeded, Snapshot, find_windows, monitor_at
from ..ports import Keys, Platform
from ..settings import Config

log = logging.getLogger(__name__)

# (process names in lower case: Windows exes, then macOS executables - those have no ".exe", so they never match
# on Windows)
CODE_EDITORS = {"code.exe", "code - insiders.exe", "cursor.exe", "devenv.exe", "notepad++.exe", "sublime_text.exe",
                "idea64.exe", "pycharm64.exe", "webstorm64.exe", "rider64.exe", "clion64.exe", "atom.exe",
                "code", "code - insiders", "cursor", "windsurf", "zed", "xcode", "sublime_text", "bbedit", "nova",
                "idea", "pycharm", "webstorm", "rider", "clion"}
PLAIN_TEXT_APPS = CODE_EDITORS | {"notepad.exe", "notepad2.exe", "notepad3.exe", "notepads.exe", "wordpad.exe",
                                  "textedit", "coteditor"}
RICH_HTML_APPS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "teams.exe",
                  "ms-teams.exe", "slack.exe", "discord.exe", "outlook.exe", "olk.exe", "winword.exe", "onenote.exe",
                  "powerpnt.exe", "excel.exe", "thunderbird.exe", "whatsapp.exe",
                  "google chrome", "safari", "firefox", "microsoft edge", "brave browser", "opera", "vivaldi", "arc",
                  "msteams", "microsoft teams", "slack", "discord", "microsoft outlook", "microsoft word",
                  "microsoft onenote", "microsoft powerpoint", "microsoft excel", "mail", "thunderbird", "whatsapp"}
COMMAND_LINES = {"windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "openconsole.exe",
                 "wsl.exe", "bash.exe", "mintty.exe", "regedit.exe", "powershell_ise.exe", "mmc.exe",
                 "terminal", "iterm2", "ghostty", "warp", "stable", "alacritty", "kitty", "wezterm-gui", "tabby",
                 "hyper", "script editor", "spotlight"}      # ("stable": Warp's executable)


class AgentError(Exception):
    """A desktop action could not be done; the message is shown to the user and the model."""


@dataclass
class ToolOutput:
    text: str                                   # what the model gets
    images: list[str] = field(default_factory=list)
    display: str | None = None                  # short line for the chat (default: text)


@dataclass
class Shot:
    hwnd: int                       # 0 = a whole monitor
    origin: tuple[int, int]         # screen position of the image's top-left pixel
    scale: float                    # image pixels per screen pixel
    size: tuple[int, int]


@dataclass
class Seen:
    """The latest picture the model got (without the pink marks) - watch_screen compares with it."""
    hwnd: int
    rect: tuple[int, int, int, int]  # the screen area it shows
    how: str                         # "window" (the window drew itself) or "screen"
    img: object                      # QImage
    t: float


@dataclass
class Spot:
    """Where a mouse action happens (screen pixels) and what is there."""
    x: int
    y: int
    hwnd: int                        # the window it belongs to (0: unknown - a whole-screen picture)
    what: str
    el: Element | None = None


# how much of a picture must change for watch_screen (share of its pixels)
SENSITIVITY = {"high": 0.0002, "normal": 0.001, "low": 0.03}
FP_W = 160                           # watch_screen compares pictures this wide (smoothed)


def _fp(img, crop: tuple[int, int, int, int] | None, size: tuple[int, int]) -> bytes:  # type: ignore[no-untyped-def]
    """A small copy of (part of) a picture (RGB), to compare."""
    from PySide6.QtCore import QRect
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QImage as _QImage

    if crop is not None:
        img = img.copy(QRect(crop[0], crop[1], max(1, crop[2] - crop[0]), max(1, crop[3] - crop[1])))
    w, h = size
    small = img.scaled(w, h, _Qt.AspectRatioMode.IgnoreAspectRatio,
                       _Qt.TransformationMode.SmoothTransformation).convertToFormat(_QImage.Format.Format_RGB888)
    bpl = small.bytesPerLine()
    raw = bytes(small.constBits())[: bpl * h]
    return b"".join(raw[i * bpl: i * bpl + w * 3] for i in range(h))


def _diff(a: bytes, b: bytes) -> float:
    """Share of the pixels that clearly changed in any colour (1.0 if they can't be compared).
    Colour, not grey: a red and a blue answer box can be equally bright."""
    if not a or len(a) != len(b):
        return 1.0
    changed = 0
    for i in range(0, len(a) - 2, 3):
        if abs(a[i] - b[i]) > 28 or abs(a[i + 1] - b[i + 1]) > 28 or abs(a[i + 2] - b[i + 2]) > 28:
            changed += 1
    return changed / (len(a) // 3)


def _clip(r: tuple[int, int, int, int], to: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (max(r[0], to[0]), max(r[1], to[1]), min(r[2], to[2]), min(r[3], to[3]))


def _wait(seconds: float, cancel: threading.Event | None) -> bool:
    """Sleep; False if cancelled meanwhile."""
    end = time.perf_counter() + seconds
    while True:
        if cancel is not None and cancel.is_set():
            return False
        left = end - time.perf_counter()
        if left <= 0:
            return True
        time.sleep(min(0.02, left))


class DesktopAgent:
    def __init__(self, cfg: Config, own_pid: int, *, platform: Platform) -> None:
        self.cfg = cfg
        self.own_pid = own_pid
        self._display = platform.display
        self._win = platform.windows
        self._input = platform.input
        self._clipboard = platform.clipboard
        self._a11y = platform.accessibility
        self._cap = platform.capture
        self._procs = platform.processes
        self._profile = platform.profile     # the platform's shortcuts (ctrl+v / cmd+v ...) and app names
        self.cancel = threading.Event()
        self.overlay_hwnd = 0
        self.last_hwnd = 0
        self.snap: Snapshot | None = None
        self.shot: Shot | None = None
        self._seen: Seen | None = None
        self._quick = False              # inside capture.excluding(): grab the screen directly
        self._no_pictures = False        # the last fingerprint failed: the system doesn't allow screen pictures yet
        self._uia_busy: dict[int, threading.Thread] = {}

    # ================================================================ helpers
    def _keys_of(self, combo: str) -> Keys:
        """A combo the profile names ("ctrl+v", on a Mac "cmd+v"), with its key names as the ports know them."""
        aliases = self._profile.key_alias_map()     # (empty on Windows: the combo as it is)
        return self._input.parse_combo("+".join(aliases.get(k, k) for k in combo.split("+")) if aliases else combo)

    def _shortcut(self, key: str) -> Keys:
        """The platform's shortcut with ``key``: ctrl+``key`` (Windows), cmd+``key`` (Mac)."""
        return self._keys_of(f"{self._profile.primary}+{key}")

    def _uia_snapshot(self, hwnd: int, kinds: ElementKinds = "interactive", limit: int = 80,
                      timeout: float = 4.0, strict: bool = False) -> Snapshot | None:
        """UI Automation snapshot, given up after ``timeout`` s (some apps build huge trees).  ``strict`` (reading
        on the user's request): the system's "not allowed yet" (:class:`PermissionNeeded`, macOS) is raised."""
        busy = self._uia_busy.get(hwnd)
        if busy is not None and busy.is_alive():
            return None   # the last request for this window is still running
        box: list[object] = []

        def work() -> None:
            try:
                box.append(self._a11y.snapshot(hwnd, kinds, limit))
            except Exception as exc:  # noqa: BLE001
                box.append(exc)

        th = threading.Thread(target=work, daemon=True)
        self._uia_busy[hwnd] = th
        th.start()
        th.join(timeout)
        if th.is_alive():
            log.info("UI Automation too slow for %s - continuing without an element list", self.describe_hwnd(hwnd))
            return None
        res = box[0] if box else None
        if strict and isinstance(res, PermissionNeeded):
            raise res
        return res if isinstance(res, Snapshot) else None

    def _window_image(self, hwnd: int):  # type: ignore[no-untyped-def]
        """The window's pixels (drawn by the window itself if possible)."""
        img, rect, _how = self._capture(hwnd)
        return img, rect

    def _capture(self, hwnd: int, screen: tuple[int, int, int, int] | None = None):  # type: ignore[no-untyped-def]
        """(picture, screen rect, how) of a window - drawn by the window itself if it can - or of a
        screen area (``hwnd`` 0).  The cat's own windows are never in it."""
        if hwnd:
            frame = self._win.rect(hwnd)
            rect = _clip(frame, self._display.virtual_screen())
            img = self._cap.grab_window(hwnd, frame) if rect == frame else None
            if img is not None:
                return img, rect, "window"
            if rect[2] - rect[0] <= 4 or rect[3] - rect[1] <= 4:
                return None, rect, ""
        else:
            rect = screen or self.control_monitor(*self._input.cursor_pos())
        img = self._cap.grab(rect) if self._quick else self._cap.grab_without(rect, self.own_pid)
        return img, rect, "screen"

    def _remember(self, hwnd: int, rect: tuple[int, int, int, int], img, how: str) -> None:  # type: ignore[no-untyped-def]
        self._seen = Seen(hwnd, rect, how, img.copy(), time.monotonic())

    def _fingerprint(self, hwnd: int) -> bytes | None:
        """Small grayscale picture of the window, to notice whether anything changed (None: no picture).  Nobody
        asked for this picture, so it never asks the user for a permission: a capture with ``quiet()`` (macOS)
        raises PermissionNeeded at once instead (noted in ``_no_pictures``); Windows' capture has no ``quiet``."""
        quiet = getattr(self._cap, "quiet", None)
        try:
            with quiet() if quiet is not None else nullcontext():
                img, _r = self._window_image(hwnd)
        except PermissionNeeded:
            self._no_pictures = True
            return None
        except Exception:  # noqa: BLE001
            return None
        if img is None:
            return None
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QImage as _QImage

        small = img.scaled(400, 300, _Qt.AspectRatioMode.IgnoreAspectRatio,
                           _Qt.TransformationMode.FastTransformation).convertToFormat(_QImage.Format.Format_Grayscale8)
        ptr = small.constBits()
        return bytes(ptr)[: small.bytesPerLine() * small.height()]

    @staticmethod
    def _changed(a: bytes | None, b: bytes | None) -> bool | None:
        """True if two fingerprints differ by more than a blinking caret would."""
        if a is None or b is None or len(a) != len(b):
            return None
        diff = sum(1 for x, y in zip(a, b) if abs(x - y) > 30)
        return diff > 40

    def _shot_output(self, hwnd: int, text: str, display: str) -> ToolOutput:
        """A tool result with a fresh screenshot of the window, so the model can check it."""
        img, rect, how = self._capture(hwnd)
        if img is None:
            return ToolOutput(text, display=display)
        img, scale = imaging.fit(img, self.cfg.vision_max_px)
        self.shot = Shot(hwnd, (rect[0], rect[1]), scale, (img.width(), img.height()))
        self._remember(hwnd, rect, img, how)
        return ToolOutput(text, [imaging.to_base64_png(img)], display=display)

    def _after_output(self, sp: Spot, text: str, display: str, popups: bool = False) -> ToolOutput:
        """After a drag / zoom / hover: the result with a picture of where it happened.  ``popups``:
        a picture of the screen around the window, so tooltips and hover menus are in it."""
        hwnd = sp.hwnd if sp.hwnd and self._win.exists(sp.hwnd) else 0
        if hwnd and not popups:
            return self._shot_output(self._win.active_popup(hwnd), text, display)
        mon = self.control_monitor(sp.x, sp.y)
        if hwnd:
            r = self._win.rect(hwnd)
            rect = _clip((r[0] - 150, r[1] - 150, r[2] + 150, r[3] + 150), mon)
        else:
            rect = mon
        img = self._cap.grab_without(rect, self.own_pid)
        img, scale = imaging.fit(img, self.cfg.vision_max_px)
        self.shot = Shot(hwnd, (rect[0], rect[1]), scale, (img.width(), img.height()))
        self._remember(hwnd, rect, img, "screen")
        return ToolOutput(text, [imaging.to_base64_png(img)], display=display)

    def wait_until_drawn(self, hwnd: int, max_s: float = 15.0) -> float:
        """Wait until a starting app's window stops changing (splash screens, loading)."""
        t0 = time.time()
        self._no_pictures = False
        last = self._fingerprint(hwnd)
        if last is None and self._no_pictures:     # nothing to compare (macOS without Screen Recording): a short wait
            _wait(min(2.0, max_s), self.cancel)
            return time.time() - t0
        calm = 0
        while time.time() - t0 < max_s and not self.cancel.is_set():
            time.sleep(0.5)
            now = self._fingerprint(hwnd)
            changed = self._changed(last, now)
            calm = calm + 1 if changed is False else 0
            last = now
            if calm >= 3 and time.time() - t0 >= 1.5:
                break
        return time.time() - t0

    # ================================================================ windows
    def control_monitor(self, x: int, y: int) -> tuple[int, int, int, int]:
        """The rect of the monitor at (or nearest to) a screen point."""
        return tuple(monitor_at(self._display.monitors(), x, y).rect)  # type: ignore[return-value]

    def windows(self) -> list[AppWindow]:
        return self._win.app_windows(self.own_pid)

    def describe_hwnd(self, hwnd: int) -> str:
        exe = self._procs.process_exe(self._win.pid(hwnd)) or "app"
        app = exe[:-4] if exe.lower().endswith(".exe") else exe
        title = self._win.title(hwnd) or "(untitled)"
        return f"{app}: '{title if len(title) <= 70 else title[:67] + '...'}'"

    def resolve(self, target: str | None) -> AppWindow:
        """The window an action is meant for: the named one, else the one the cat
        worked with last, else the app the user was using before opening the chat."""
        wins = self.windows()
        if target and str(target).strip():
            hits = find_windows(str(target), wins, self._profile.window_alias_map())
            if not hits:
                raise AgentError(f"no open window matches '{target}' (open windows: "
                                 + ", ".join(w.label() for w in wins[:8]) + ")")
            for w in hits:
                if w.hwnd == self.last_hwnd:
                    return w
            return next((w for w in hits if not w.minimized), hits[0])
        for w in wins:
            if w.hwnd == self.last_hwnd:
                return w
        for w in wins:
            if not w.minimized:
                return w
        raise AgentError("no app window is open")

    def exe_of(self, hwnd: int) -> str:
        return self._procs.process_exe(self._win.pid(hwnd)).lower()

    def is_command_line(self, hwnd: int) -> bool:
        """Terminals, consoles, registry editor - and the Win+R 'Run' box."""
        hwnd = self._win.active_popup(hwnd)
        exe = self.exe_of(hwnd)
        if exe in COMMAND_LINES:
            return True
        return exe == "explorer.exe" and self._win.class_name(hwnd) == "#32770"

    def _focus(self, hwnd: int) -> int:
        """Bring the window (or its open dialog) to the front; returns the window that has focus."""
        if not self._win.exists(hwnd):
            raise AgentError("that window is gone (it was closed)")
        target = self._win.active_popup(hwnd)
        if not self._win.bring_to_front(target):
            if not self._win.is_foreground(target):
                raise AgentError(f"I couldn't bring {self.describe_hwnd(target)} to the front - "
                                 "please click on it once, then ask again")
        self.last_hwnd = hwnd
        time.sleep(0.12)
        return target

    def _check_input_allowed(self, hwnd: int) -> None:
        pid = self._win.pid(hwnd)
        if self._procs.is_elevated(pid) and not self._procs.self_elevated():
            raise AgentError(f"{self.describe_hwnd(hwnd)} runs as administrator - Windows doesn't let me "
                             "type or click in it (and I won't ask for admin rights for that)")

    def after(self, settle: float = 0.2) -> str:
        """Short description of what's in front now (helps the model notice dialogs)."""
        time.sleep(settle)
        if self._procs.uac_prompt_open():
            return (" Windows is now showing its admin-rights prompt (UAC). Only the user can answer it - "
                    "stop here and tell the user; never try to click it.")
        fg = self._win.foreground()
        if not fg or self._win.pid(fg) == self.own_pid:
            return ""
        text = f" Now in front: {self.describe_hwnd(fg)}."
        el = self._a11y.focused_element()
        if el is not None and el.kind in ("edit", "document", "combobox"):
            text += f" Keyboard focus: {el.kind}{(' ' + repr(el.name[:40])) if el.name else ''}."
        return text

    # ================================================================ eyes
    def look(self, hwnd: int | None, screen: tuple[int, int, int, int] | None = None) -> ToolOutput:
        """``screen``: the screen area to show when there is no window (default: the monitor of
        the frontmost app)."""
        # Looking is read-only: the window is NOT brought to the front (that would be an action).
        snap: Snapshot | None = None
        img = None
        if hwnd:
            if not self._win.exists(hwnd):
                raise AgentError("that window is gone (it was closed)")
            if self._win.is_minimized(hwnd):
                raise AgentError(f"{self.describe_hwnd(hwnd)} is minimized - restore it first "
                                 "(window_action restore), then look again")
            focus = self._win.active_popup(hwnd)
            if self._win.is_hung(focus):
                raise AgentError(f"{self.describe_hwnd(focus)} is not responding right now")
            frame = self._win.rect(focus)
            rect = _clip(frame, self._display.virtual_screen())
            label = self.describe_hwnd(focus)
            snap = self._uia_snapshot(focus, limit=self.cfg.vision_max_elements)
            shot_hwnd = focus
            self.last_hwnd = hwnd
            if rect == frame:   # fully on screen: draw the window itself, even if something covers it
                img = self._cap.grab_window(focus, frame)
        else:
            wins = [w for w in self.windows() if not w.minimized]
            top = wins[0] if wins else None
            cx, cy = self._input.cursor_pos()
            if top is not None:
                r = self._win.rect(top.hwnd)
                cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
            mon = self.control_monitor(cx, cy)
            rect = screen or mon
            label = "the whole screen"
            if top is not None:
                snap = self._uia_snapshot(top.hwnd, limit=self.cfg.vision_max_elements)
            shot_hwnd = 0
        if rect[2] - rect[0] < 4 or rect[3] - rect[1] < 4:
            raise AgentError("that window is not visible on any screen")
        how = "window" if img is not None else "screen"
        if img is None:
            img = self._cap.grab(rect) if self._quick else self._cap.grab_without(rect, self.own_pid)
        img, scale = imaging.fit(img, self.cfg.vision_max_px)
        self._remember(shot_hwnd, rect, img, how)
        ox, oy = rect[0], rect[1]
        lines: list[str] = []
        marks: list[tuple[int, int, int, int, int]] = []
        if snap is not None:
            for e in snap.elements:
                l, t, r, b = e.rect
                if r <= rect[0] or l >= rect[2] or b <= rect[1] or t >= rect[3]:
                    continue
                marks.append((e.id, int((l - ox) * scale), int((t - oy) * scale), int((r - ox) * scale),
                              int((b - oy) * scale)))
                lines.append(e.describe((ox, oy), scale))
        if snap is None and hwnd:
            lines = []
        if self.cfg.vision_marks and marks:
            imaging.draw_marks(img, marks)
        self.snap = snap
        self.shot = Shot(shot_hwnd, (ox, oy), scale, (img.width(), img.height()))
        text = f"Screenshot of {label}, {img.width()}x{img.height()} px."
        if lines:
            text += (" Pink numbered boxes mark the clickable elements (use click with element=ID):\n"
                     + "\n".join(lines))
        else:
            text += " (No element list for this window - use x/y pixel positions in this image to click.)"
        if not hwnd:
            text += "\nOpen windows: " + " | ".join(w.label() for w in self.windows()[:12])
        return ToolOutput(text, [imaging.to_base64_png(img)])

    def read(self, hwnd: int) -> str:
        """Read-only: never changes the window (a minimized window is read as it is)."""
        if not self._win.exists(hwnd):
            raise AgentError("that window is gone (it was closed)")
        target = self._win.active_popup(hwnd)
        if self._win.is_hung(target):
            raise AgentError(f"{self.describe_hwnd(target)} is not responding right now")
        snap = self._uia_snapshot(target, "readable", limit=self.cfg.read_max_elements, timeout=6.0, strict=True)
        if snap is None:
            raise AgentError(f"{self.describe_hwnd(target)} doesn't let me read its contents (or is too slow) - "
                             "use look_at_screen instead")
        self.snap = snap
        self.last_hwnd = hwnd
        doc = self._a11y.document_text(target, self.cfg.read_max_chars)
        if not doc:
            boxes = [e for e in snap.elements if e.kind in ("document", "edit") and e.value and not e.password]
            if boxes:
                doc = max(boxes, key=lambda e: len(e.value)).value[: self.cfg.read_max_chars]
        lines = [e.describe() for e in snap.elements]
        lines = [ln.rsplit(" at ", 1)[0] for ln in lines]
        out = f"Window {self.describe_hwnd(target)}" + (" (minimized)." if self._win.is_minimized(target) else ".")
        if snap.focused is not None:
            out += f" Keyboard focus is on [{snap.focused.id}]."
        out += "\nElements (use click with element=ID):\n" + ("\n".join(lines) if lines else "(none found)")
        if doc:
            cut = len(doc) >= self.cfg.read_max_chars
            out += f"\n\nText content{' (beginning)' if cut else ''}:\n\"\"\"\n{doc}\n\"\"\""
        return out

    # ================================================================ hands
    def element(self, eid: int) -> tuple[Element, int]:
        """Element ``eid`` from the latest look/read, re-located in case the window moved."""
        if self.snap is None:
            raise AgentError("I need to look at the window first (look_at_screen or read_window)")
        old = next((e for e in self.snap.elements if e.id == eid), None)
        if old is None:
            raise AgentError(f"there is no element [{eid}] in my last look - look again")
        hwnd = self.snap.hwnd
        if not self._win.exists(hwnd):
            raise AgentError("that window is gone (it was closed)")
        return old, hwnd

    def _relocate(self, old: Element, hwnd: int) -> Element:
        snap = self._uia_snapshot(hwnd, "readable", limit=400, timeout=3.0)
        if snap is None:
            return old
        fresh = snap.elements
        same = [e for e in fresh if e.kind == old.kind and e.name == old.name]
        if not same:
            return old
        ox, oy = old.center
        return min(same, key=lambda e: abs(e.center[0] - ox) + abs(e.center[1] - oy))

    @contextmanager
    def _passthrough(self, seconds: float = 3.0) -> Iterator[None]:
        """Let clicks fall through the cat and its chat while we use the mouse (``seconds``: at most)."""
        self._input.set_passthrough_until(time.monotonic() + seconds)
        changed = self._win.own_click_through(self.own_pid, self.overlay_hwnd)
        time.sleep(0.09)  # the cat's 25 Hz hover check switches it to click-through
        try:
            yield
        finally:
            self._win.end_own_click_through(changed)
            self._input.set_passthrough_until(0.0)

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """x/y in the latest screenshot -> screen pixels."""
        if self.shot is None:
            raise AgentError("give an element id, or x and y from a screenshot (look_at_screen first)")
        sw, sh = self.shot.size
        if not (0 <= x <= sw and 0 <= y <= sh):
            raise AgentError(f"x/y must be inside the last screenshot ({sw}x{sh})")
        px, py = int(self.shot.origin[0] + x / self.shot.scale), int(self.shot.origin[1] + y / self.shot.scale)
        vs = self._display.virtual_screen()
        if not (vs[0] <= px < vs[2] and vs[1] <= py < vs[3]):
            raise AgentError("that spot is off-screen")
        return px, py

    def spot(self, ref: tuple, focus: bool = True) -> Spot:
        """Where a mouse action goes: ("el", id) an element from the latest look, ("xy", x, y) a spot
        in the latest screenshot, ("mid", hwnd) the middle of a window.  ``focus``: bring its window
        to the front first (the start of an action; not the end of a drag)."""
        kind = ref[0]
        if kind == "el":
            old, hwnd = self.element(int(ref[1]))
            if focus:
                self._check_input_allowed(hwnd)
                self._focus(hwnd)
            el = self._relocate(old, hwnd)
            return Spot(el.center[0], el.center[1], hwnd,
                        f"[{ref[1]}] {el.kind}{(' ' + repr(el.name[:50])) if el.name else ''}", el)
        if kind == "xy":
            px, py = self.to_screen(float(ref[1]), float(ref[2]))
            hwnd = self.shot.hwnd if self.shot is not None else 0
            if hwnd and not self._win.exists(hwnd):
                raise AgentError("that window is gone (it was closed)")
            if hwnd and focus:
                self._check_input_allowed(hwnd)
                self._focus(hwnd)
            return Spot(px, py, hwnd, f"the spot {int(ref[1])},{int(ref[2])}")
        hwnd = int(ref[1])
        if focus:
            self._check_input_allowed(hwnd)
            self._focus(hwnd)
        r = self._win.rect(self._win.active_popup(hwnd))
        return Spot((r[0] + r[2]) // 2, (r[1] + r[3]) // 2, hwnd, "the middle of the window")

    def _clear_spot(self, sp: Spot, verb: str) -> int:
        """Inside _passthrough: the window at the spot - our own chat or another app on top of
        the target means no action there.  Returns that window."""
        at = self._win.window_at(sp.x, sp.y)
        vs = self._display.virtual_screen()
        if not (vs[0] <= sp.x < vs[2] and vs[1] <= sp.y < vs[3]):
            raise AgentError("that spot is off-screen")
        if at and self._win.pid(at) == self.own_pid:
            raise AgentError("my own chat window covers that spot - move the chat aside and ask again")
        if sp.hwnd and at and not self._win.same_app(at, sp.hwnd):
            raise AgentError(f"{self.describe_hwnd(at)} covers that spot, so I didn't {verb}")
        if not sp.hwnd and at:
            self._check_input_allowed(at)
        return at

    def click(self, eid: int | None, x: float | None, y: float | None, hwnd: int | None,
              button: str = "left", double: bool = False, quiet: bool = False, count: int = 1,
              hold: float = 0.0, keys: list[str] | None = None) -> str:
        if eid is None and (self.shot is None or x is None or y is None):
            raise AgentError("give an element id, or x and y from a screenshot (look_at_screen first)")
        sp = self.spot(("el", eid) if eid is not None else ("xy", x, y))
        hwnd, px, py, el, what = sp.hwnd or None, sp.x, sp.y, sp.el, sp.what
        count = 2 if double and count < 2 else max(1, count)
        with self._passthrough(2.0 + (hold + 0.1) * count):
            at = self._clear_spot(sp, "click")
            old_pos = self._input.cursor_pos()
            with self._input.holding(keys or []):
                self._input.click(px, py, button, count=count, hold=hold, cancel=self.cancel)
            time.sleep(0.05)
        if self._input.cursor_pos() == (px, py) and button == "left":
            self._input.move_cursor(*old_pos)  # put the pointer back where the user left it
        verb = {"left": "Clicked", "right": "Right-clicked", "middle": "Middle-clicked",
                "back": "Clicked the mouse's back button on", "forward": "Clicked the mouse's forward button on"}[button]
        if count == 2:
            verb = "Double-clicked" if button == "left" else verb + " twice:"
        elif count >= 3:
            verb = "Triple-clicked" if button == "left" else verb + f" {count} times:"
        if hold >= 0.3:
            verb = f"Pressed and held ({hold:g} s)" if button == "left" else verb + f" (held {hold:g} s)"
        if keys:
            verb += " (holding " + "+".join(keys) + ")"
        state = ""
        if eid is not None and el is not None and el.checked is not None and hwnd:
            time.sleep(0.25)
            now = self._relocate(el, hwnd)
            if now.checked is not None:
                state = f" It is now {'checked' if now.checked else 'unchecked'}."
        if quiet:   # clicking into a field before typing: wait until the field really has the focus
            end = time.time() + 0.4
            while time.time() < end:
                time.sleep(0.05)
                f = self._a11y.focused_element()
                if f is not None and f.kind in ("edit", "document", "combobox"):
                    break
            return ""
        return f"{verb} {what} in {self.describe_hwnd(hwnd or at)}.{state}" + self.after()

    def type(self, text: str, hwnd: int, eid: int | None = None, method: str = "auto") -> str | ToolOutput:
        if eid is not None:
            self.click(eid, None, None, None, quiet=True)
            hwnd = self.snap.hwnd if self.snap else hwnd
        self._check_input_allowed(hwnd)
        target = self._focus(hwnd)
        el = self._a11y.focused_element()
        if el is not None and el.password:
            raise AgentError("the keyboard focus is on a password field - I never type passwords")
        multiline = "\n" in text
        if method == "auto":
            # Anything but a few characters is pasted: exact and fast in every app, and new lines
            # arrive as text - typing them as Enter could send a chat message or submit a form.
            method = "type" if len(text) <= 12 and not multiline else "paste"
        label = self.describe_hwnd(target)
        before_txt = self._a11y.focused_text()
        before_img = self._fingerprint(target)
        saved = None
        if method == "paste":
            saved = self._clipboard.backup()
            if saved is None:
                if multiline:
                    raise AgentError("the clipboard is busy right now, and typing several lines key by key could "
                                     "press Enter in the wrong place - try again in a moment")
                method = "type"   # clipboard busy or huge: type instead (slower, still exact)
        if method == "paste":
            if not self._clipboard.set_private_text(text):
                self._clipboard.write(saved or [], private=False)
                raise AgentError("the clipboard is busy - try again")
            try:
                if not self._win.is_foreground(target):
                    raise AgentError(f"{label} is no longer in front, so I stopped")
                self._input.press_combo(self._shortcut("v"))
                self._wait_for_text(text, before_txt)   # restore the clipboard only once the app has it
            finally:
                if saved:
                    self._clipboard.write(saved)
                else:
                    self._clipboard.clear()
            how = "Pasted"
        else:
            typed = self._input.type_text(text, lambda: self._win.is_foreground(target), self.cancel,
                                     chunk=max(1, self.cfg.typing_chunk))
            if typed < len(text):
                why = "you pressed Esc / Stop" if self.cancel.is_set() else f"{label} was no longer in front"
                return f"Stopped typing after {typed} of {len(text)} characters because {why}."
            how = "Typed"
        return self._confirm_written(text, target, label, how, before_txt, before_img)

    def _confirm_written(self, text: str, target: str | int, label: str, how: str, before_txt: str | None,
                         before_img: bytes | None) -> str | ToolOutput:
        """Never report success blindly: read the text back, or compare the window before/after."""
        hwnd = int(target)
        check = self._verify(text, before_txt)
        if check == "unknown":       # the app may still be busy: one more look
            time.sleep(0.3)
            check = self._verify(text, before_txt)
        if check in ("exact", "tail"):
            # show the model how the field ends now, so it notices e.g. missing line breaks
            now = (self._a11y.focused_text() or "").replace("\r\n", "\n").replace("\r", "\n")
            last = now.rstrip("\n").split("\n")[-3:] if now.strip() else []
            shown = ("\nThe last lines of the field are now (one per line):\n" + "\n".join(
                f"  | {ln[-100:]}" for ln in last) + "\n") if last else ""
            return (f"{how} {len(text)} characters into {label}. Checked: the text arrived"
                    + (" exactly." if check == "exact" else " (its end is visible).") + shown + self.after(0.05))
        focus = self._a11y.focused_element()
        where = f"{focus.kind} {focus.name[:50]!r}" if focus is not None else "unknown"
        if check == "differs":
            raise AgentError(f"the text in {label} does not match what I sent (keyboard focus: {where}). "
                             "Look at the window and fix it, or tell the user")
        changed = self._changed(before_img, self._fingerprint(hwnd))
        if changed is False:
            raise AgentError(f"nothing changed in {label} - the keyboard focus is not in a text field "
                             f"(focus: {where}). Open or click the right text area first (in editors press "
                             f"{self._profile.primary}+n for a new file), then try again")
        msg = (f"{how} {len(text)} characters into {label}, but this app does not let me read text back. "
               "Here is a screenshot of the window now - CHECK where the text landed (e.g. in the editor, not in a "
               "chat panel or search box) before you tell the user it worked." + self.after(0.2))
        return self._shot_output(hwnd, msg, f"{how} {len(text)} characters into {label} (checking with a screenshot)")

    def _wait_for_text(self, text: str, before: str | None, max_s: float = 0.8) -> None:
        """After pasting: return as soon as the text shows up in the field (then the app has read
        the clipboard).  Apps that hide their text get the full ``max_s``."""
        if before is None:
            time.sleep(max_s)
            return
        norm = lambda t: t.replace("\r\n", "\n").replace("\r", "\n")  # noqa: E731
        want = norm(text).strip()
        end = time.time() + max_s
        while time.time() < end:
            time.sleep(0.05)
            now = self._a11y.focused_text()
            if now is not None and want in norm(now):
                time.sleep(0.05)
                return
        return

    def _verify(self, text: str, before: str | None) -> str:
        """Read the focused field back (UI Automation): 'exact', 'tail' (end of the text visible,
        e.g. editors that only expose the lines around the cursor), 'unchanged', 'differs', 'unknown'."""
        time.sleep(0.05)
        after = self._a11y.focused_text()
        if after is None:
            return "unknown"
        norm = lambda t: t.replace("\r\n", "\n").replace("\r", "\n")  # noqa: E731
        if norm(text).strip() in norm(after):
            return "exact"
        if before is not None and norm(after) == norm(before):
            return "unchanged"
        lines = [ln.strip() for ln in norm(text).split("\n") if len(ln.strip()) >= 4]
        if lines and lines[-1] in norm(after) and (len(lines) < 2 or lines[-2] in norm(after)):
            return "tail"
        return "differs"

    # ---------------------------------------------------------------- editing existing text
    def _copy_all(self, target: int) -> str | None:
        """Select everything in the focused editor and copy it (the exact text, even from editors
        that hide it from UI Automation, like VS Code).  Leaves everything selected."""
        seq = self._clipboard.sequence()
        self._input.press_combo(self._shortcut("a"))
        time.sleep(0.08)
        self._input.press_combo(self._shortcut("c"))
        end = time.time() + 1.5
        while time.time() < end:
            time.sleep(0.05)
            if self._clipboard.sequence() != seq:
                time.sleep(0.05)
                if self._clipboard.has("files"):   # files, not text: this is not an editor
                    return None
                return self._clipboard.text()
        return None

    @staticmethod
    def find_span(doc: str, old: str, replace_all: bool = False) -> list[tuple[int, int]]:
        """Where ``old`` occurs in ``doc`` (both with \\n line ends).  Exact match first; else
        tolerate different amounts of spaces/tabs/line breaks.  Raises AgentError if it is missing
        or (without replace_all) not unique."""
        spans = [(m.start(), m.end()) for m in _re.finditer(_re.escape(old), doc)]
        if not spans:   # same words, different amount of spaces/line breaks
            words = [w for w in _re.split(r"\s+", old.strip()) if w]
            if words:
                pattern = r"\s+".join(_re.escape(w) for w in words)
                spans = [(m.start(), m.end()) for m in _re.finditer(pattern, doc)]
        if not spans:   # spaces missing or added (e.g. "(b,a)" read off a screenshot as "(b, a)")
            bare = _re.sub(r"\s+", "", old)
            if len(bare) >= 6:
                pos = [i for i, ch in enumerate(doc) if not ch.isspace()]
                flat = "".join(doc[i] for i in pos)
                found = [m.start() for m in _re.finditer(_re.escape(bare), flat)]
                spans = [(pos[f], pos[f + len(bare) - 1] + 1) for f in found]
        if not spans:
            preview = doc if len(doc) <= 1500 else doc[:1500] + "\n..."
            raise AgentError(f"I can't find {old[:80]!r} in the document. Its current text is:\n{preview}\n"
                             "Use an exact piece of that text as 'old'")
        if len(spans) > 1 and not replace_all:
            raise AgentError(f"{old[:80]!r} appears {len(spans)} times - include more of the surrounding text "
                             "so it is unique (or set replace_all)")
        return spans

    def replace(self, hwnd: int, old: str, new: str, replace_all: bool = False) -> str:
        """Change existing text in an editor: copy its whole text, replace ``old`` with ``new``,
        paste it back over the selection and copy again to check every character."""
        self._check_input_allowed(hwnd)
        target = self._focus(hwnd)
        label = self.describe_hwnd(target)
        el = self._a11y.focused_element()
        if el is not None and el.password:
            raise AgentError("the keyboard focus is on a password field - I never touch passwords")
        if el is None or el.kind not in ("edit", "document"):
            where = f"{el.kind} {el.name[:40]!r}" if el is not None else "nothing"
            raise AgentError(f"the keyboard focus in {label} is not in a text editor (it's on {where}) - "
                             "click into the text first")
        saved = self._clipboard.backup()
        if saved is None:
            raise AgentError("the clipboard is busy right now - try again in a moment")
        exe = self.exe_of(target)
        selected = False
        try:
            raw = self._copy_all(target)
            selected = True
            if raw is None:
                raise AgentError(f"I couldn't copy the text of {label} - is the keyboard focus in the editor?")
            # Plain editors (VS Code, Notepad, ...) also copy an HTML version; real formatting shows up as
            # Rich Text Format (Word, WordPad, Outlook) or as HTML in browsers/office/chat apps.
            rich = exe not in PLAIN_TEXT_APPS and (self._clipboard.has("rtf") or (
                self._clipboard.has("html") and exe in RICH_HTML_APPS))
            if rich:
                raise AgentError(f"{label} holds formatted text - replace_text only works in plain-text editors "
                                 "(Notepad, VS Code...), it would lose the formatting here")
            crlf = "\r\n" in raw
            doc = raw.replace("\r\n", "\n").replace("\r", "\n")
            old_n = old.replace("\r\n", "\n").replace("\r", "\n")
            new_n = new.replace("\r\n", "\n").replace("\r", "\n")
            spans = self.find_span(doc, old_n, replace_all)
            out, pos = [], 0
            for a, b in spans:
                out += [doc[pos:a], new_n]
                pos = b
            result = "".join(out) + doc[pos:]
            paste = result.replace("\n", "\r\n") if crlf else result
            if not self._win.is_foreground(target):
                raise AgentError(f"{label} is no longer in front, so I stopped")
            if paste:                                          # everything is still selected
                self._clipboard.set_private_text(paste)
                self._input.press_combo(self._shortcut("v"))
            else:                                              # nothing left: pasting "" would do nothing
                self._input.press_combo(self._input.parse_combo("delete"))
            time.sleep(0.4)
            check = self._copy_all(target)
            if check is None and not result.strip():
                check = ""                                     # an empty editor has nothing to copy
            ok = check is not None and check.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") == result.rstrip("\n")
        finally:
            if selected and self._win.is_foreground(target):
                self._input.press_combo(self._keys_of(self._profile.doc_end))   # never leave the whole text selected
            self._clipboard.write(saved)
        n = len(spans)
        what = f"{n} place{'s' if n != 1 else ''}"
        if not ok:
            raise AgentError(f"I replaced the text in {label}, but reading it back it doesn't match exactly "
                             "(the editor may have reformatted it) - look at it and fix it if needed; "
                             f"{self._profile.primary}+z undoes the change")
        return (f"Replaced {old[:60]!r} with {new[:60]!r} in {label} ({what}). Checked: the whole text is exactly "
                f"as intended. ({self._profile.primary}+z would undo it.)" + self.after(0.05))

    def keys(self, combos: list[tuple[str, Keys]], hwnd: int, hold: float = 0.0) -> str:
        self._check_input_allowed(hwnd)
        target = self._focus(hwnd)
        done: list[str] = []
        for name, vks in combos:
            if self.cancel.is_set():
                break
            if not self._win.is_foreground(target):
                # Only the same app may have come to the front (e.g. its "Save as" dialog opened by a key).
                break
            self._input.press_combo(vks, hold, self.cancel)
            done.append(name)
            time.sleep(0.12)
        if len(done) < len(combos):
            why = "you pressed Esc / Stop" if self.cancel.is_set() else "another app came to the front"
            return f"Pressed {', '.join(done) or 'nothing'}; stopped before {combos[len(done)][0]} because {why}."
        held = f" (held {hold:g} s{' each' if len(done) > 1 else ''})" if hold >= 0.1 else ""
        return f"Pressed {', '.join(done)}{held} in {self.describe_hwnd(target)}." + self.after()

    def scroll(self, direction: str, amount: int, hwnd: int, eid: int | None = None,
               xy: tuple[float, float] | None = None, keys: list[str] | None = None) -> str:
        """Over an element, a spot (x, y in the latest screenshot) or the middle of the window;
        ``keys`` are held meanwhile (ctrl: zoom, shift: sideways)."""
        ref = ("el", eid) if eid is not None else ("xy", *xy) if xy is not None else ("mid", hwnd)
        sp = self.spot(ref)
        clicks = max(1, min(50, int(amount)))
        sign = 1 if direction in ("up", "left") else -1
        with self._passthrough(3.0 + clicks * 0.05):
            at = self._clear_spot(sp, "scroll")
            old_pos = self._input.cursor_pos()
            with self._input.holding(keys or []):
                self._input.scroll(sp.x, sp.y, sign * clicks, horizontal=direction in ("left", "right"))
        self._input.move_cursor(*old_pos)
        where = "" if ref[0] == "mid" else f" over {sp.what}"
        held = " (holding " + "+".join(keys) + ")" if keys else ""
        return (f"Scrolled {direction} {clicks}x{where}{held} in {self.describe_hwnd(sp.hwnd or at)}."
                + self.after(0.25))

    def _label(self, *hwnds: int) -> str:
        h = next((h for h in hwnds if h and self._win.exists(h)), 0)
        return self.describe_hwnd(h) if h else "the screen"

    def drag(self, src: tuple, dst: tuple | None, path: list[tuple[float, float]], button: str = "left",
             keys: list[str] | None = None, seconds: float = 0.6) -> ToolOutput:
        """Press at ``src``, move through ``path`` (x/y in the latest screenshot) to ``dst``, let go."""
        a = self.spot(src)
        pts = [(a.x, a.y)] + [self.to_screen(x, y) for x, y in path]
        b = self.spot(dst, focus=False) if dst is not None else None
        if b is not None:
            pts.append((b.x, b.y))
        if len(pts) < 2:
            raise AgentError("say where to drag to (to_element or to_x/to_y, or a path)")
        with self._passthrough(seconds + 3.0):
            self._clear_spot(a, "drag")
            end_at = self._win.window_at(*pts[-1])
            if end_at and self._win.pid(end_at) == self.own_pid:
                raise AgentError("my own chat window covers the spot to drop on - move the chat aside and ask again")
            if end_at and not (a.hwnd and self._win.same_app(end_at, a.hwnd)):
                self._check_input_allowed(end_at)
            old_pos = self._input.cursor_pos()
            with self._input.holding(keys or []):
                why = self._input.drag(pts, button, seconds, self.cancel)
        if not why:
            self._input.move_cursor(*old_pos)
        label = self._label(a.hwnd, end_at)
        if why == "cancel":
            text = "Stopped the drag because you pressed Esc / Stop - I let go of the button where the pointer was."
        elif why == "user":
            text = "Stopped the drag because you moved the mouse - I let go of the button and left it to you."
        else:
            to = b.what if b is not None else f"the spot {path[-1][0]:.0f},{path[-1][1]:.0f}"
            via = f" along {len(path)} point{'s' if len(path) != 1 else ''}" if path else ""
            held = " holding " + "+".join(keys) if keys else ""
            btn = "" if button == "left" else f" with the {button} button"
            onto = (f" onto {self.describe_hwnd(end_at)}"
                    if end_at and a.hwnd and not self._win.same_app(end_at, a.hwnd) else "")
            text = f"Dragged from {a.what} to {to}{via}{btn}{held} in {label}{onto}."
        return self._after_output(a, text + self.after(0.25), f"🖱️ {text}")

    def zoom(self, ref: tuple, direction: str, steps: int, how: str = "wheel") -> ToolOutput:
        """Zoom at a spot: ctrl + mouse wheel, or a two-finger touch pinch (falls back to the wheel)."""
        sp = self.spot(ref)
        used, note, why = how, "", ""
        with self._passthrough(4.0 + steps * 0.15):
            self._clear_spot(sp, "zoom")
            old_pos = self._input.cursor_pos()
            if how == "pinch":
                r = self._win.rect(sp.hwnd) if sp.hwnd else self.control_monitor(sp.x, sp.y)
                room = max(60, 2 * (min(sp.x - r[0], r[2] - sp.x) - 15))
                near, far = min(100.0, room * 0.3), min(float(room), 100.0 + 90 * steps)
                start, end = (near, far) if direction == "in" else (far, near)
                why = self._input.pinch(sp.x, sp.y, start, end, 0.3 + 0.08 * steps, self.cancel)
                if why == "failed":
                    used, why = "wheel", ""
                    note = (f" ({self._profile.os} didn't take a touch pinch here, so I used {self._profile.zoom} + "
                            "mouse wheel instead.)")
            if used == "wheel" and not why:
                zoom = self._profile.zoom
                with self._input.holding([self._profile.key_alias_map().get(zoom, zoom)]):
                    self._input.scroll(sp.x, sp.y, steps if direction == "in" else -steps)
            self._input.move_cursor(*old_pos)
        if why == "cancel":
            text = "Stopped zooming because you pressed Esc / Stop."
        else:
            way = "two-finger pinch" if used == "pinch" else f"{self._profile.zoom} + mouse wheel"
            where = "" if ref[0] == "mid" else f" at {sp.what}"
            text = (f"Zoomed {direction} ({steps} step{'s' if steps != 1 else ''}, {way}){where} in "
                    f"{self._label(sp.hwnd)}.{note}")
        return self._after_output(sp, text + self.after(0.3), f"🔍 {text}")

    def hover(self, ref: tuple, seconds: float = 1.0) -> ToolOutput:
        """Move the pointer onto something and leave it there (tooltips, hover menus)."""
        sp = self.spot(ref)
        with self._passthrough(seconds + 4.0):
            self._clear_spot(sp, "move the mouse there")
            why = self._input.glide(sp.x, sp.y, 0.3, self.cancel)
            if not why:
                _wait(seconds, self.cancel)
            if why == "user":
                text = "Stopped because you moved the mouse."
            elif why or self.cancel.is_set():
                text = "Stopped because you pressed Esc / Stop."
            else:
                text = f"The mouse pointer is on {sp.what} in {self._label(sp.hwnd)} and stays there."
            return self._after_output(sp, text, f"🖱️ {text}", popups=True)

    # ================================================================ watching the screen
    def watch(self, hwnd: int, screen: tuple[int, int, int, int] | None, until: str = "change", text: str = "",
              area: tuple[int, int, int, int] | None = None, seconds: float = 120.0,
              sensitivity: str = "normal") -> ToolOutput:
        """Wait inside the task until something happens on a window (or a screen area), then show it
        with a fresh look - so the model can react at once.  ``area``: screen rect to watch only."""
        if hwnd:
            if not self._win.exists(hwnd):
                raise AgentError("that window is gone (it was closed)")
            if self._win.is_minimized(hwnd):
                raise AgentError(f"{self.describe_hwnd(hwnd)} is minimized - restore it first "
                                 "(window_action restore), then watch it")
        label = self.describe_hwnd(hwnd) if hwnd else "the screen"
        t0 = time.monotonic()
        end = t0 + seconds
        if until in ("text", "gone"):
            happened = self._watch_text(hwnd, label, text, until == "gone", t0, end)
        else:
            with self._cap.excluding(self.own_pid):
                self._quick = True
                try:
                    happened = self._watch_pixels(hwnd, screen, until, area,
                                                  SENSITIVITY.get(sensitivity, SENSITIVITY["normal"]), t0, end)
                finally:
                    self._quick = False
        if self.cancel.is_set():
            return ToolOutput("Stopped watching because you pressed Esc / Stop.", display=f"👀 Stopped watching {label}")
        if happened is None:
            what = {"change": "Nothing changed", "still": "It kept changing", "text": f"{text!r} didn't appear",
                    "gone": f"{text!r} was still there"}[until]
            msg = (f"{what} on {label} within {seconds:g} s. If it is still going (a game, a quiz, a lobby), "
                   "call watch_screen again to keep waiting.")
            return ToolOutput(msg, display=f"👀 {what} on {label} ({seconds:g} s)")
        out = self.look(hwnd or None, screen=screen)
        out.text = f"{happened} {out.text}"
        out.display = f"👀 {happened[:-1] if happened.endswith('.') else happened} - {label}"
        return out

    def _watch_pixels(self, hwnd: int, screen: tuple[int, int, int, int] | None, until: str,
                      area: tuple[int, int, int, int] | None, thr: float, t0: float, end: float) -> str | None:
        def grab():  # type: ignore[no-untyped-def]
            img, r, how = self._capture(hwnd, screen)
            if img is None:
                raise AgentError("that window is not visible on any screen")
            return img, r, how

        img, rect, how = grab()
        part = _clip(area or rect, rect)
        if part[2] - part[0] < 2 or part[3] - part[1] < 2:
            raise AgentError("that area is not on the window")
        off = (part[0] - rect[0], part[1] - rect[1], part[2] - part[0], part[3] - part[1])   # area in the window
        w = min(FP_W, off[2])
        size = (w, max(1, round(w * off[3] / off[2])))

        def fp(im, r) -> bytes:  # type: ignore[no-untyped-def]
            s = im.width() / max(1, r[2] - r[0])
            return _fp(im, (round((part[0] - r[0]) * s), round((part[1] - r[1]) * s),
                            round((part[2] - r[0]) * s), round((part[3] - r[1]) * s)), size)

        def moved(r: tuple[int, int, int, int]) -> None:      # the window moved: the area moves along
            nonlocal rect, part
            rect = r
            part = _clip((r[0] + off[0], r[1] + off[1], r[0] + off[0] + off[2], r[1] + off[1] + off[3]), r)

        cur = fp(img, rect)
        seen = self._seen
        base = cur
        if (seen is not None and seen.hwnd == hwnd and seen.how == how and seen.rect == rect
                and time.monotonic() - seen.t < 120):
            base = fp(seen.img, seen.rect)                    # what the model saw last
        pop0 = self._win.active_popup(hwnd) if hwnd else 0

        def frame() -> bytes | None:
            im, r, _how = grab()
            if r != rect:
                moved(r)
                return None
            return fp(im, rect)

        if until == "still":
            prev, calm = cur, time.monotonic()
            while not self.cancel.is_set():
                now = time.monotonic()
                if now - calm >= 1.0:
                    return f"The screen stopped changing after {now - t0:.1f} s."
                if now >= end:
                    return None
                time.sleep(0.2)
                c = frame()
                if c is None or _diff(prev, c) > thr:
                    calm = time.monotonic()
                prev = c if c is not None else fp(grab()[0], rect)
            return None
        if _diff(base, cur) > thr:
            happened = "It had already changed since your latest screenshot."
        else:
            happened = ""
            while not happened:
                if self.cancel.is_set() or time.monotonic() >= end:
                    return None
                time.sleep(0.25)
                if hwnd and self._win.active_popup(hwnd) != pop0:
                    happened = f"A dialog window opened or closed after {time.monotonic() - t0:.1f} s."
                    break
                c = frame()
                if c is None:
                    base = cur = fp(grab()[0], rect)
                    continue
                cur = c
                if _diff(base, cur) > thr:
                    happened = f"The screen changed after {time.monotonic() - t0:.1f} s."
        prev, calm, stop = cur, None, time.monotonic() + 2.5   # let animations finish (up to 2.5 s)
        while time.monotonic() < stop and not self.cancel.is_set():
            time.sleep(0.15)
            c = frame()
            if c is None:
                break
            if _diff(prev, c) <= thr:
                calm = calm or time.monotonic()
                if time.monotonic() - calm >= 0.35:
                    break
            else:
                calm = None
            prev = c
        return happened

    def _watch_text(self, hwnd: int, label: str, text: str, gone: bool, t0: float, end: float) -> str | None:
        from .watch import _norm, _window_text

        if not hwnd:
            raise AgentError("until='text' or 'gone' needs a window to read - give target")
        need = _norm(text)
        if not need:
            raise AgentError("say which text to watch for")
        first = True
        while not self.cancel.is_set():
            got = _window_text(self._a11y, self._win.active_popup(hwnd), timeout=6.0, strict=True)
            if got is None:
                if first:
                    raise AgentError(f"I can't read the text of {label} - use until='change' instead")
            elif (need in _norm(got)) != gone:
                when = "now" if first else f"after {time.monotonic() - t0:.1f} s"
                return f"{text!r} is {'gone' if gone else 'there'} ({when})."
            first = False
            if time.monotonic() >= end:
                return None
            _wait(0.6, self.cancel)
        return None

    def wait(self, seconds: float) -> str:
        end = time.time() + seconds
        while time.time() < end and not self.cancel.is_set():
            time.sleep(0.1)
        return f"Waited {seconds:.1f} s." + self.after(0.0)

    def _raise_new(self, hwnd: int) -> None:
        """Bring a just-started app's window to the front - if the system lets the cat (opening it worked anyway)."""
        try:
            self._win.bring_to_front(hwnd, timeout=0.8)
        except PermissionNeeded:
            log.info("couldn't bring the new window to the front: not allowed yet")

    def wait_for_window(self, before: set[int], name: str, timeout: float = 15.0) -> str:
        """After launching an app: find its new window, wait until it has finished loading
        (keys sent to a half-started app get lost) and remember it as the target."""
        end = time.time() + timeout
        key = name.lower().split()[0] if name else ""
        while time.time() < end and not self.cancel.is_set():
            time.sleep(0.25)
            new = [w for w in self.windows() if w.hwnd not in before and not w.minimized]
            if new:
                best = next((w for w in new if key and (key in w.app.lower() or key in w.title.lower())), new[0])
                self.last_hwnd = best.hwnd
                self._raise_new(best.hwnd)
                waited = self.wait_until_drawn(best.hwnd, max_s=max(3.0, end - time.time()))
                self._raise_new(best.hwnd)
                label = self.describe_hwnd(best.hwnd) if self._win.exists(best.hwnd) else best.label()
                return f" Its window is ready and in front ({waited:.0f} s to load): {label}."
        fg = self._win.foreground()
        if fg and self._win.pid(fg) != self.own_pid and fg not in before:
            self.last_hwnd = fg
            return f" In front now: {self.describe_hwnd(fg)}."
        return " (I don't see a new window yet - it may still be starting, or it reused an open window.)"
