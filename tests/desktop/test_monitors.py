"""pixelcat.desktop.monitors: Monitor geometry and enumeration (mocked DLLs)."""
from __future__ import annotations

import pytest

from desktop.fakes import out
from pixelcat.desktop import monitors, win32
from pixelcat.desktop.monitors import Monitor, enumerate_monitors, monitor_at, primary

LEFT = Monitor(2, (-1920, 0, 0, 1080), (-1920, 0, 0, 1040), 96, False, r"\\.\DISPLAY2")
MAIN = Monitor(1, (0, 0, 2560, 1440), (0, 0, 2560, 1392), 144, True, r"\\.\DISPLAY1")
ABOVE = Monitor(3, (0, -1080, 1920, 0), (0, -1080, 1920, -40), 120, False, r"\\.\DISPLAY3")


@pytest.mark.parametrize("x, y, inside", [
    (0, 0, True), (2559, 1439, True), (2560, 0, False), (0, 1440, False), (-1, 5, False), (1280.5, 720.5, True),
])
def test_contains_is_half_open(x, y, inside):
    assert MAIN.contains(x, y) is inside


@pytest.mark.parametrize("x, y, d2", [
    (100, 100, 0), (2559, 1439, 0), (2560, 0, 1), (-3, -4, 25), (2569, 1449, 200),
])
def test_distance2_to_the_nearest_pixel(x, y, d2):
    assert MAIN.distance2(x, y) == d2


def test_scale_factor():
    assert MAIN.scale_factor == 1.5 and LEFT.scale_factor == 1.0


@pytest.mark.parametrize("x, y, expected", [
    (10, 10, MAIN), (-10, 10, LEFT), (10, -10, ABOVE),
    (5000, 100, MAIN), (-5000, 500, LEFT), (100, -5000, ABOVE),
])
def test_monitor_at_containing_or_nearest(x, y, expected):
    assert monitor_at([LEFT, MAIN, ABOVE], x, y) is expected


def test_primary_or_first():
    assert primary([LEFT, MAIN, ABOVE]) is MAIN
    assert primary([ABOVE, LEFT]) is ABOVE


def fake_displays(w32, displays: dict[int, tuple], dpis: dict[int, int] | None = None):  # type: ignore[no-untyped-def]
    """displays: hmon -> (rect, work, flags, device) or None (GetMonitorInfoW fails)."""
    dpis = dpis or {}

    def enum(hdc, clip, proc, lparam):  # type: ignore[no-untyped-def]
        for h in displays:
            proc(h, None, None, lparam)
        return 1

    def info(hmon, p):  # type: ignore[no-untyped-def]
        d = displays[int(hmon)]
        if d is None:
            return 0
        mi = out(p)
        assert mi.cbSize == win32.ctypes.sizeof(win32.MONITORINFOEXW)
        (mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom) = d[0]
        (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom) = d[1]
        mi.dwFlags, mi.szDevice = d[2], d[3]
        return 1

    def dpi(hmon, kind, dx, dy):  # type: ignore[no-untyped-def]
        assert kind == win32.MDT_EFFECTIVE_DPI
        if int(hmon) not in dpis:
            return -1
        out(dx).value = out(dy).value = dpis[int(hmon)]
        return 0

    w32.user32.EnumDisplayMonitors.result = enum
    w32.user32.GetMonitorInfoW.result = info
    w32.shcore.GetDpiForMonitor.result = dpi


def test_enumerate_monitors_reads_rects_dpi_and_sorts_primary_first(w32):
    fake_displays(w32, {
        11: ((2560, 0, 4480, 1080), (2560, 0, 4480, 1040), 0, r"\\.\DISPLAY3"),
        12: ((-1920, 0, 0, 1080), (-1920, 0, 0, 1040), 0, r"\\.\DISPLAY2"),
        13: ((0, 0, 2560, 1440), (0, 0, 2560, 1392), 1, r"\\.\DISPLAY1"),
        14: None,
    }, dpis={11: 0, 13: 144})
    mons = enumerate_monitors()
    assert [m.handle for m in mons] == [13, 12, 11]
    assert mons[0] == Monitor(13, (0, 0, 2560, 1440), (0, 0, 2560, 1392), 144, True, r"\\.\DISPLAY1")
    assert [m.dpi for m in mons] == [144, 96, 96]       # failed query -> 96; a 0 answer -> 96
    assert [m.primary for m in mons] == [True, False, False]


def test_enumerate_monitors_without_shcore_uses_96_dpi(monkeypatch, w32):
    fake_displays(w32, {5: ((0, 0, 800, 600), (0, 0, 800, 560), 1, "D")}, dpis={5: 192})
    monkeypatch.setattr(win32, "shcore", None)
    assert [m.dpi for m in enumerate_monitors()] == [96]


def test_enumerate_monitors_falls_back_to_a_full_hd_screen(w32):
    def boom(*_a):  # type: ignore[no-untyped-def]
        raise OSError("no display")

    w32.user32.EnumDisplayMonitors.result = boom
    assert enumerate_monitors() == [Monitor(0, (0, 0, 1920, 1080), (0, 0, 1920, 1040), 96, True, "fallback")]


def test_a_failing_monitor_callback_is_swallowed(w32, caplog):
    fake_displays(w32, {7: ((0, 0, 10, 10), (0, 0, 10, 10), 1, "D")})

    def boom(*_a):  # type: ignore[no-untyped-def]
        raise RuntimeError("bad monitor")

    w32.user32.GetMonitorInfoW.result = boom
    with caplog.at_level("ERROR", logger=monitors.log.name):
        assert [m.device for m in enumerate_monitors()] == ["fallback"]
    assert "monitor callback failed" in caplog.text
