"""Windows on the desktop as the cat sees them (plain data, no platform calls)."""
from __future__ import annotations

import difflib
import enum
from dataclasses import dataclass

WindowId = int   # an opaque window handle: only the desktop adapter knows what it means


class UserState(enum.Enum):
    """What the user is doing on the whole screen, as far as the cat should care."""

    NORMAL = "normal"
    FULLSCREEN_APP = "fullscreen_app"   # a full-screen game or video (Direct3D exclusive full screen)
    PRESENTATION = "presentation"       # presentation mode: notifications are off


@dataclass(frozen=True)
class WinInfo:
    hwnd: int
    rect: tuple[int, int, int, int]   # exact visible frame (DWM extended frame bounds)
    z: int                             # 0 = top of the z-order
    title: str
    cls: str
    maximized: bool
    topmost: bool

    @property
    def left(self) -> int:
        return self.rect[0]

    @property
    def top(self) -> int:
        return self.rect[1]

    @property
    def right(self) -> int:
        return self.rect[2]

    @property
    def bottom(self) -> int:
        return self.rect[3]


@dataclass(frozen=True)
class AppWindow:
    hwnd: int
    title: str
    exe: str          # e.g. "chrome.exe"
    minimized: bool
    maximized: bool
    cls: str = ""
    pid: int = 0

    @property
    def app(self) -> str:
        return self.exe[:-4] if self.exe.lower().endswith(".exe") else self.exe

    def label(self) -> str:
        state = " (minimized)" if self.minimized else (" (maximized)" if self.maximized else "")
        title = self.title if len(self.title) <= 60 else self.title[:57] + "..."
        return f"{self.app}: {title}{state}"


_ALIASES = {
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook", "teams": "ms-teams",
    "edge": "msedge", "microsoft edge": "msedge", "chrome": "chrome", "google chrome": "chrome", "firefox": "firefox",
    "vs code": "code", "vscode": "code", "visual studio code": "code", "file explorer": "explorer",
    "explorer": "explorer", "notepad": "notepad", "spotify": "spotify", "discord": "discord", "slack": "slack",
    "terminal": "windowsterminal", "task manager": "taskmgr", "paint": "mspaint",
}


def find_windows(query: str, windows: list[AppWindow], aliases: dict[str, str] | None = None) -> list[AppWindow]:
    """Match by app name (exe), alias, or window title - best match group first.  ``aliases``: what users call
    an app -> its app name (None: the Windows names, :data:`_ALIASES`)."""
    q = query.strip().lower()
    q = q[:-4] if q.endswith(".exe") else q
    if not q:
        return []
    target = (_ALIASES if aliases is None else aliases).get(q, q)
    by_exe = [w for w in windows if w.app.lower() == target or (target == "ms-teams" and w.app.lower() in ("teams", "ms-teams"))]
    if by_exe:
        return by_exe
    by_exe_part = [w for w in windows if target in w.app.lower()]
    if by_exe_part:
        return by_exe_part
    by_title = [w for w in windows if q in w.title.lower()]
    if by_title:
        return by_title
    names = {w.title.lower(): w for w in windows}
    close = difflib.get_close_matches(q, list(names), n=3, cutoff=0.75)
    return [names[c] for c in close]
