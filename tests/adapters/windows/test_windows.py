"""pixelcat.adapters.windows.windows: the window listing the cat walks on (mocked DLLs).

WinInfo itself is plain data and lives in pixelcat.domain.windows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from adapters.windows.fakes import by_hwnd, enum_windows, fill_text, out, raising
from pixelcat import domain
from pixelcat.adapters.windows import win32, windows
from pixelcat.adapters.windows.windows import (MIN_H, MIN_W, ctypes_rect, enumerate_windows, foreground_window,
                                      is_fullscreen_window, window_exists)
from pixelcat.domain import WinInfo

OWN_PID = 100


def test_reexports_the_domain_type():
    assert windows.WinInfo is domain.WinInfo


@dataclass
class W:
    """One fake top-level window."""
    hwnd: int
    title: str = "Document - Editor"
    cls: str = "Notepad"
    rect: tuple | None = (100, 100, 900, 700)
    pid: int = 200
    ex: int = 0
    style: int = 0
    visible: bool = True
    iconic: bool = False
    zoomed: bool = False
    cloaked: bool = False
    extra: dict = field(default_factory=dict)


def install(w32, wins: list[W]) -> None:  # type: ignore[no-untyped-def]
    t = {w.hwnd: w for w in wins}
    u = w32.user32
    enum_windows(u, [w.hwnd for w in wins])
    u.IsWindowVisible.result = lambda h: t[h].visible
    u.IsIconic.result = lambda h: t[h].iconic
    u.IsZoomed.result = lambda h: t[h].zoomed
    u.GetWindowLongPtrW.result = lambda h, i: t[h].ex if i == win32.GWL_EXSTYLE else t[h].style
    u.GetClassNameW.result = lambda h, buf, n: fill_text(buf, t[h].cls)
    u.GetWindowTextLengthW.result = lambda h: len(t[h].title)
    u.GetWindowTextW.result = lambda h, buf, n: fill_text(buf, t[h].title)

    def pid(h, p):  # type: ignore[no-untyped-def]
        out(p).value = t[h].pid
        return 1

    u.GetWindowThreadProcessId.result = pid

    def dwm(h, which, p, size):  # type: ignore[no-untyped-def]
        if which == win32.DWMWA_CLOAKED:
            out(p).value = int(t[h].cloaked)
            return 0
        if t[h].rect is None:
            return -1
        r = out(p)
        r.left, r.top, r.right, r.bottom = t[h].rect
        return 0

    w32.dwmapi.DwmGetWindowAttribute.result = dwm
    u.GetWindowRect.result = 0


def listed(w32, wins, include=frozenset()):  # type: ignore[no-untyped-def]
    install(w32, wins)
    return enumerate_windows(OWN_PID, include)


def test_a_normal_app_window_is_listed_with_its_frame(w32):
    (info,) = listed(w32, [W(1, zoomed=True, ex=win32.WS_EX_TOPMOST)])
    assert info == WinInfo(1, (100, 100, 900, 700), 0, "Document - Editor", "Notepad", True, True)
    assert (info.left, info.top, info.right, info.bottom) == (100, 100, 900, 700)


@pytest.mark.parametrize("win", [
    W(1, visible=False),
    W(1, iconic=True),
    W(1, ex=win32.WS_EX_TOOLWINDOW),
    W(1, ex=win32.WS_EX_TRANSPARENT | win32.WS_EX_LAYERED),
    W(1, ex=win32.WS_EX_NOACTIVATE),
    W(1, cloaked=True),
    W(1, cls="Shell_TrayWnd"),
    W(1, cls="Progman"),
    W(1, cls="Windows.UI.Core.CoreWindow"),
    W(1, title=""),
    W(1, rect=None),
    W(1, rect=(0, 0, MIN_W - 1, 500)),
    W(1, rect=(0, 0, 500, MIN_H - 1)),
    W(1, pid=OWN_PID),
    W(0),
], ids=["invisible", "minimised", "toolwindow", "click-through", "noactivate", "cloaked", "taskbar", "desktop",
        "corewindow", "untitled", "no-frame", "too-narrow", "too-short", "own-process", "null-hwnd"])
def test_windows_the_cat_must_not_use_are_skipped(w32, win):
    assert listed(w32, [win]) == []


@pytest.mark.parametrize("ex", [win32.WS_EX_TOOLWINDOW | win32.WS_EX_APPWINDOW,
                                win32.WS_EX_NOACTIVATE | win32.WS_EX_APPWINDOW,
                                win32.WS_EX_TRANSPARENT, win32.WS_EX_LAYERED])
def test_app_window_flag_and_single_overlay_bits_keep_a_window(w32, ex):
    assert [w.hwnd for w in listed(w32, [W(1, ex=ex)])] == [1]


def test_minimum_size_is_inclusive(w32):
    assert len(listed(w32, [W(1, rect=(0, 0, MIN_W, MIN_H))])) == 1


def test_z_order_counts_only_listed_windows(w32):
    wins = [W(1), W(2, visible=False), W(3, title=""), W(4), W(5, cloaked=True), W(6)]
    assert [(w.hwnd, w.z) for w in listed(w32, wins)] == [(1, 0), (4, 1), (6, 2)]


def test_own_windows_only_when_included_and_big_enough(w32):
    wins = [W(1, pid=OWN_PID, rect=(0, 0, 400, MIN_H // 2)),
            W(2, pid=OWN_PID),
            W(3, pid=OWN_PID, rect=(0, 0, 400, MIN_H // 2 - 1)),
            W(4, pid=OWN_PID, rect=None),
            W(5)]
    got = listed(w32, wins, include={1, 3, 4})
    assert [(w.hwnd, w.z) for w in got] == [(1, 0), (5, 1)]
    assert (got[0].title, got[0].cls, got[0].maximized, got[0].topmost) == ("Pixel Cat", "PixelCatOwn", False, True)


def test_a_failing_window_is_skipped_and_the_rest_still_listed(w32):
    install(w32, [W(1), W(2), W(3)])
    w32.user32.IsWindowVisible.result = lambda h: raising(OSError("gone"))() if h == 2 else True
    assert [w.hwnd for w in enumerate_windows(OWN_PID)] == [1, 3]


def test_enumwindows_failure_gives_an_empty_list(w32):
    w32.user32.EnumWindows.result = raising(OSError("desktop gone"))
    assert enumerate_windows(OWN_PID) == []


@pytest.mark.parametrize("value, expected", [(1234, 1234), (None, 0), (0, 0)])
def test_foreground_window(w32, value, expected):
    w32.user32.GetForegroundWindow.result = value
    assert foreground_window() == expected


def test_foreground_window_swallows_errors(w32):
    w32.user32.GetForegroundWindow.result = raising(OSError("x"))
    assert foreground_window() == 0


def fullscreen_case(w32, rect, cls="Game", zoomed=False, style=0):  # type: ignore[no-untyped-def]
    u = w32.user32
    u.GetClassNameW.result = lambda h, buf, n: fill_text(buf, cls)

    def wr(h, p):  # type: ignore[no-untyped-def]
        if rect is None:
            return 0
        r = out(p)
        r.left, r.top, r.right, r.bottom = rect
        return 1

    u.GetWindowRect.result = wr
    u.IsZoomed.result = int(zoomed)
    u.GetWindowLongPtrW.result = lambda h, i: style if i == win32.GWL_STYLE else 0


MON = (0, 0, 1920, 1080)


@pytest.mark.parametrize("rect, cls, zoomed, style, expected", [
    ((0, 0, 1920, 1080), "Game", False, 0, True),
    ((-8, -8, 1928, 1088), "Game", False, win32.WS_CAPTION, True),
    ((0, 0, 1920, 1040), "Game", False, 0, False),
    ((0, 0, 1920, 1080), "Game", True, win32.WS_CAPTION, False),
    ((0, 0, 1920, 1080), "Game", True, 0, True),
    ((0, 0, 1920, 1080), "Game", True, 0x00800000, True),
    ((0, 0, 1920, 1080), "WorkerW", False, 0, False),
    ((0, 0, 1920, 1080), "Progman", False, 0, False),
    (None, "Game", False, 0, False),
], ids=["exact", "overhanging-captioned", "smaller", "maximised-with-caption", "zoomed-no-caption",
        "zoomed-border-only", "desktop-worker", "desktop", "no-rect"])
def test_is_fullscreen_window(w32, rect, cls, zoomed, style, expected):
    fullscreen_case(w32, rect, cls, zoomed, style)
    assert is_fullscreen_window(5, MON) is expected


def test_is_fullscreen_window_null_and_errors(w32):
    assert is_fullscreen_window(0, MON) is False
    w32.user32.GetClassNameW.result = raising(OSError("gone"))
    assert is_fullscreen_window(5, MON) is False


def test_ctypes_rect_and_window_exists(w32):
    fullscreen_case(w32, (1, 2, 3, 4))
    assert ctypes_rect(9) == (1, 2, 3, 4)
    w32.user32.GetWindowRect.result = 0
    assert ctypes_rect(9) is None
    w32.user32.IsWindow.result = by_hwnd({9: 1})
    assert window_exists(9) is True and window_exists(8) is False
    w32.user32.IsWindow.result = raising(OSError("x"))
    assert window_exists(9) is False
