"""pixelcat.adapters.windows.monitors: monitor enumeration (mocked DLLs).

Monitor geometry, monitor_at and primary are plain data: tests/domain/test_monitors.py.
"""
from __future__ import annotations

from adapters.windows.fakes import out
from pixelcat import domain
from pixelcat.adapters.windows import monitors, win32
from pixelcat.adapters.windows.monitors import enumerate_monitors
from pixelcat.domain import Monitor


def test_reexports_the_domain_types():
    assert monitors.Monitor is domain.Monitor
    assert monitors.monitor_at is domain.monitor_at
    assert monitors.primary is domain.primary


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
