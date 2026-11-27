"""pixelcat.adapters.windows.win32: the thin Win32 helpers (mocked DLLs)."""
from __future__ import annotations

import pytest

from ctypes import wintypes

from adapters.windows.fakes import FakeDLL, by_hwnd, fill_text, out
from pixelcat.adapters.windows import win32

T = win32.WS_EX_TRANSPARENT


def exstyle(w32, value: int) -> list:
    """Back GetWindowLongPtrW/SetWindowLongPtrW with one stored value; returns the writes."""
    state = {"ex": value}
    writes: list = []
    w32.user32.GetWindowLongPtrW.result = lambda h, idx: state["ex"] if idx == win32.GWL_EXSTYLE else 0

    def set_(h, idx, v):  # type: ignore[no-untyped-def]
        writes.append((h, idx, v))
        state["ex"] = v
        return 0

    w32.user32.SetWindowLongPtrW.result = set_
    return writes


@pytest.mark.parametrize("before, enabled, after", [
    (win32.WS_EX_LAYERED, True, win32.WS_EX_LAYERED | T),
    (win32.WS_EX_LAYERED | T, False, win32.WS_EX_LAYERED),
])
def test_set_click_through_toggles_only_the_transparent_bit(w32, before, enabled, after):
    writes = exstyle(w32, before)
    win32.set_click_through(42, enabled)
    assert writes == [(42, win32.GWL_EXSTYLE, after)]


@pytest.mark.parametrize("before, enabled", [(T, True), (0, False)])
def test_set_click_through_does_not_write_when_unchanged(w32, before, enabled):
    writes = exstyle(w32, before)
    win32.set_click_through(42, enabled)
    assert writes == []


def test_make_tool_noactivate_sets_tool_and_noactivate_clears_appwindow_and_refreshes_frame(w32):
    writes = exstyle(w32, win32.WS_EX_APPWINDOW | win32.WS_EX_LAYERED)
    win32.make_tool_noactivate(7)
    assert writes == [(7, win32.GWL_EXSTYLE, win32.WS_EX_LAYERED | win32.WS_EX_TOOLWINDOW | win32.WS_EX_NOACTIVATE)]
    (call,) = w32.user32.SetWindowPos.calls
    assert call[0] == 7 and call[-1] & win32.SWP_FRAMECHANGED and call[-1] & win32.SWP_NOACTIVATE


def test_make_tool_noactivate_is_a_no_op_when_already_set(w32):
    writes = exstyle(w32, win32.WS_EX_TOOLWINDOW | win32.WS_EX_NOACTIVATE)
    win32.make_tool_noactivate(7)
    assert writes == [] and w32.user32.SetWindowPos.calls == []


def test_move_window_topmost_and_not(w32):
    win32.move_window(5, 1.6, 2.4, 300, 200)
    win32.move_window(5, 10, 20, 30, 40, topmost=False)
    (h1, after1, *geo1, f1), (h2, after2, *geo2, f2) = w32.user32.SetWindowPos.calls
    assert after1 is win32.HWND_TOPMOST and geo1 == [1, 2, 300, 200] and not f1 & win32.SWP_NOZORDER
    assert after2 is None and geo2 == [10, 20, 30, 40] and f2 & win32.SWP_NOZORDER
    for flags in (f1, f2):
        assert flags & win32.SWP_NOACTIVATE and flags & win32.SWP_NOSENDCHANGING


def test_keep_topmost_does_not_move_or_activate(w32):
    win32.keep_topmost(9)
    (call,) = w32.user32.SetWindowPos.calls
    assert call[:2] == (9, win32.HWND_TOPMOST)
    assert call[-1] == win32.SWP_NOMOVE | win32.SWP_NOSIZE | win32.SWP_NOACTIVATE


def test_cursor_pos_reads_the_point_and_falls_back_to_origin(w32):
    def fill(p):  # type: ignore[no-untyped-def]
        out(p).x, out(p).y = -1200, 345
        return 1

    w32.user32.GetCursorPos.result = fill
    assert win32.cursor_pos() == (-1200, 345)
    w32.user32.GetCursorPos.result = 0
    assert win32.cursor_pos() == (0, 0)


@pytest.mark.parametrize("swapped, down_vk, expected", [
    (0, win32.VK_LBUTTON, True), (0, win32.VK_RBUTTON, False),
    (1, win32.VK_RBUTTON, True), (1, win32.VK_LBUTTON, False),
])
def test_left_button_down_honours_swapped_buttons(w32, swapped, down_vk, expected):
    w32.user32.GetSystemMetrics.result = lambda i: swapped if i == win32.SM_SWAPBUTTON else 0
    w32.user32.GetAsyncKeyState.result = lambda vk: -32768 if vk == down_vk else 0
    assert win32.left_button_down() is expected


@pytest.mark.parametrize("capture, expected", [(42, True), (43, False), (0, False), (None, False), (OSError, False)])
def test_has_mouse_capture(w32, capture, expected):
    if capture is OSError:
        def boom():  # type: ignore[no-untyped-def]
            raise OSError("no")
        w32.user32.GetCapture.result = boom
    else:
        w32.user32.GetCapture.result = capture
    assert win32.has_mouse_capture(42) is expected


@pytest.mark.parametrize("button_down, capture, expected", [
    (True, 0, False), (False, 42, False), (False, 0, True), (False, 99, True),
])
def test_release_went_elsewhere(w32, button_down, capture, expected):
    w32.user32.GetAsyncKeyState.result = lambda vk: 0x8000 if button_down and vk == win32.VK_LBUTTON else 0
    w32.user32.GetCapture.result = capture
    assert win32.release_went_elsewhere(42) is expected


@pytest.mark.parametrize("held, expected", [
    ((), False), ((win32.VK_LBUTTON,), True), ((win32.VK_RBUTTON,), True), ((win32.VK_MBUTTON,), True), ((0x05,), False),
])
def test_mouse_buttons_down(w32, held, expected):
    w32.user32.GetAsyncKeyState.result = lambda vk: 0x8000 if vk in held else 1
    assert win32.mouse_buttons_down() is expected


def test_last_input_tick_and_idle_seconds_with_tick_wraparound(w32):
    def fill(info):  # type: ignore[no-untyped-def]
        assert out(info).cbSize == win32.ctypes.sizeof(win32.LASTINPUTINFO)
        out(info).dwTime = 0xFFFFFC18          # 1 s before the 49.7-day wrap
        return 1

    w32.user32.GetLastInputInfo.result = fill
    w32.kernel32.GetTickCount.result = 1000    # 1 s after it
    assert win32.last_input_tick() == 0xFFFFFC18
    assert win32.idle_seconds() == pytest.approx(2.0)
    w32.user32.GetLastInputInfo.result = 0
    assert win32.last_input_tick() == 0


def test_idle_seconds_is_never_negative(w32):
    def fill(info):  # type: ignore[no-untyped-def]
        out(info).dwTime = 5000
        return 1

    w32.user32.GetLastInputInfo.result = fill
    w32.kernel32.GetTickCount.result = 5000
    assert win32.idle_seconds() == 0.0


def test_window_text(w32):
    w32.user32.GetWindowTextLengthW.result = by_hwnd({1: 11})
    w32.user32.GetWindowTextW.result = lambda h, buf, n: fill_text(buf, "Hello wörld"[: n - 1])
    assert win32.window_text(1) == "Hello wörld"
    assert win32.window_text(2) == ""
    assert len(w32.user32.GetWindowTextW.calls) == 1


def test_class_name_and_window_pid(w32):
    w32.user32.GetClassNameW.result = lambda h, buf, n: fill_text(buf, "Chrome_WidgetWin_1")

    def pid(h, p):  # type: ignore[no-untyped-def]
        out(p).value = 4321
        return 99

    w32.user32.GetWindowThreadProcessId.result = pid
    assert win32.class_name(1) == "Chrome_WidgetWin_1"
    assert win32.window_pid(1) == 4321


@pytest.mark.parametrize("hr, value, expected", [(0, 1, True), (0, 2, True), (0, 0, False), (-1, 1, False)])
def test_is_cloaked(w32, hr, value, expected):
    def attr(h, which, p, size):  # type: ignore[no-untyped-def]
        assert which == win32.DWMWA_CLOAKED
        out(p).value = value
        return hr

    w32.dwmapi.DwmGetWindowAttribute.result = attr
    assert win32.is_cloaked(3) is expected


def _rect(r, box):  # type: ignore[no-untyped-def]
    r.left, r.top, r.right, r.bottom = box


def test_frame_bounds_prefers_dwm_then_window_rect_then_none(w32):
    def dwm(h, which, p, size):  # type: ignore[no-untyped-def]
        assert which == win32.DWMWA_EXTENDED_FRAME_BOUNDS
        _rect(out(p), (10, 20, 110, 220))
        return 0

    w32.dwmapi.DwmGetWindowAttribute.result = dwm
    assert win32.frame_bounds(1) == (10, 20, 110, 220)
    assert w32.user32.GetWindowRect.calls == []

    w32.dwmapi.DwmGetWindowAttribute.result = -2147467259
    w32.user32.GetWindowRect.result = lambda h, p: (_rect(out(p), (3, 4, 50, 60)), 1)[1]
    assert win32.frame_bounds(1) == (3, 4, 50, 60)

    w32.user32.GetWindowRect.result = 0
    assert win32.frame_bounds(1) is None


def test_notification_state(w32):
    def ok(p):  # type: ignore[no-untyped-def]
        out(p).value = win32.QUNS_PRESENTATION_MODE
        return 0

    w32.shell32.SHQueryUserNotificationState.result = ok
    assert win32.notification_state() == win32.QUNS_PRESENTATION_MODE

    def failed(p):  # type: ignore[no-untyped-def]
        out(p).value = 3
        return -1

    w32.shell32.SHQueryUserNotificationState.result = failed
    assert win32.notification_state() == 0

    def boom(p):  # type: ignore[no-untyped-def]
        raise OSError("no shell")

    w32.shell32.SHQueryUserNotificationState.result = boom
    assert win32.notification_state() == 0


def test_message_box_is_topmost_information_box(w32):
    win32.message_box("Hi there")
    (call,) = w32.user32.MessageBoxW.calls
    assert call == (None, "Hi there", "Pixel Cat", win32.MB_ICONINFORMATION | win32.MB_TOPMOST | win32.MB_SETFOREGROUND)


def test_current_pid_get_style_and_window_dpi(w32):
    w32.kernel32.GetCurrentProcessId.result = 777
    w32.user32.GetWindowLongPtrW.result = lambda h, idx: win32.WS_CAPTION if idx == win32.GWL_STYLE else 0
    w32.user32.GetDpiForWindow.result = by_hwnd({1: 144}, None)
    assert win32.current_pid() == 777
    assert win32.get_style(1) == win32.WS_CAPTION
    assert win32.window_dpi(1) == 144
    assert win32.window_dpi(2) == 0


def test_set_dpi_awareness_off_windows_is_false(monkeypatch, w32):
    monkeypatch.setattr(win32, "IS_WINDOWS", False)
    assert win32.set_dpi_awareness() is False
    assert w32.user32.SetProcessDpiAwarenessContext.calls == []


@pytest.mark.parametrize("ctx, shcore_hr, expected, shcore_called", [
    (1, None, True, False),
    (0, 0, True, True),
    (0, 1, False, True),
    (OSError, 0, True, True),
])
def test_set_dpi_awareness_prefers_v2_context_then_shcore(monkeypatch, w32, ctx, shcore_hr, expected, shcore_called):
    monkeypatch.setattr(win32, "IS_WINDOWS", True)
    if ctx is OSError:
        def boom(_c):  # type: ignore[no-untyped-def]
            raise OSError("old Windows")
        w32.user32.SetProcessDpiAwarenessContext.result = boom
    else:
        w32.user32.SetProcessDpiAwarenessContext.result = ctx
    w32.shcore.SetProcessDpiAwareness.result = shcore_hr
    assert win32.set_dpi_awareness() is expected
    assert bool(w32.shcore.SetProcessDpiAwareness.calls) is shcore_called
    if shcore_called:
        assert w32.shcore.SetProcessDpiAwareness.calls == [(2,)]


def test_set_dpi_awareness_without_shcore(monkeypatch, w32):
    monkeypatch.setattr(win32, "IS_WINDOWS", True)
    monkeypatch.setattr(win32, "shcore", None)
    w32.user32.SetProcessDpiAwarenessContext.result = 0
    assert win32.set_dpi_awareness() is False


class User32Without64BitLongPtr(FakeDLL):
    """32-bit user32 exports only Get/SetWindowLongW."""

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        if "LongPtr" in name:
            raise AttributeError(name)
        return super().__getattr__(name)


def test_window_long_falls_back_to_32_bit_functions(monkeypatch):
    u = User32Without64BitLongPtr("user32")
    monkeypatch.setattr(win32, "user32", u)
    u.GetWindowLongW.result = lambda h, idx: {win32.GWL_EXSTYLE: win32.WS_EX_LAYERED, win32.GWL_STYLE: 7}[idx]
    assert win32.get_exstyle(3) == win32.WS_EX_LAYERED and win32.get_style(3) == 7
    win32.set_click_through(3, True)
    assert u.SetWindowLongW.calls == [(3, win32.GWL_EXSTYLE, win32.WS_EX_LAYERED | T)]


def test_declare_gives_handle_functions_pointer_sized_types(w32):
    win32._declare()
    u = w32.user32
    for fn in (u.GetForegroundWindow, u.GetWindow, u.MonitorFromWindow):
        assert fn.restype in (wintypes.HWND, wintypes.HMONITOR)
    assert u.EnumWindows.argtypes[0] is win32.WNDENUMPROC
    assert u.GetWindowLongPtrW.restype is win32.ctypes.c_ssize_t
    assert w32.kernel32.CreateMutexW.restype is wintypes.HANDLE
