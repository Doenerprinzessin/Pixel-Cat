"""Real Windows: the desktop modules against the real Win32 / UI Automation / GDI / COM.

Run only on Windows (GitHub Actions windows-latest).  Everything here is read-only, or
touches only the test's own Qt window, its own temp files and its own uniquely named
kernel objects.  Nothing is sent to other apps.
"""
from __future__ import annotations

import base64
import ctypes
import os
import sys
import threading
import uuid
from ctypes import wintypes

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from pixelcat.desktop import capture, control, elevation, escwatch, hardware, inputs, ledges, system, uia, volume, win32
from pixelcat.desktop.monitors import enumerate_monitors
from pixelcat.desktop.windows import (ctypes_rect, enumerate_windows, foreground_window, is_fullscreen_window,
                                      window_exists)
from pixelcat.domain import monitor_at, primary

pytestmark = [pytest.mark.windows, pytest.mark.qt]

TITLE = f"PixelCat desktop test {os.getpid()}"


@pytest.fixture
def own_window(qtbot):  # type: ignore[no-untyped-def]
    """A normal top-level Qt window of the test's own, with a button, a field and two colours."""
    w = QWidget()
    w.setWindowTitle(TITLE)
    layout = QVBoxLayout(w)
    layout.addWidget(QPushButton("Press me"))
    edit = QLineEdit("hello uia")
    edit.setAccessibleName("Greeting")
    layout.addWidget(edit)
    colours = QHBoxLayout()
    for colour in ("#d01010", "#1030d0"):
        lab = QLabel()
        lab.setStyleSheet(f"background: {colour};")
        lab.setMinimumSize(150, 120)
        colours.addWidget(lab)
    layout.addLayout(colours)
    w.resize(420, 320)
    w.move(160, 160)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w, int(w.winId())


def in_worker(qtbot, fn, timeout_ms: int = 15000):  # type: ignore[no-untyped-def]
    """Run ``fn`` on a worker thread while the GUI thread keeps pumping (UIA asks our window)."""
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    qtbot.waitUntil(lambda: not t.is_alive(), timeout=timeout_ms)
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------------------------------------------------------------- monitors / input state
def test_monitors_are_real_and_sane():
    mons = enumerate_monitors()
    assert mons and mons[0].primary and sum(m.primary for m in mons) == 1
    for m in mons:
        l, t, r, b = m.rect
        wl, wt, wr, wb = m.work
        assert r > l and b > t and l <= wl < wr <= r and t <= wt < wb <= b
        assert 72 <= m.dpi <= 480 and m.device != "fallback"
    p = primary(mons)
    assert p.rect[:2] == (0, 0)
    assert monitor_at(mons, p.rect[0] + 5, p.rect[1] + 5) == p


def test_input_state_queries():
    x, y = win32.cursor_pos()
    assert isinstance(x, int) and isinstance(y, int)
    assert isinstance(win32.last_input_tick(), int)
    assert win32.idle_seconds() >= 0.0
    assert isinstance(win32.mouse_buttons_down(), bool) and isinstance(win32.left_button_down(), bool)
    assert win32.notification_state() in range(0, 8)
    assert win32.current_pid() == os.getpid()
    assert isinstance(foreground_window(), int)


# ---------------------------------------------------------------- our own window
def test_win32_reads_our_window(own_window):
    _w, hwnd = own_window
    assert win32.window_text(hwnd) == TITLE
    assert win32.class_name(hwnd)
    assert win32.window_pid(hwnd) == os.getpid()
    assert not win32.is_cloaked(hwnd)
    assert win32.get_style(hwnd) & win32.WS_CAPTION == win32.WS_CAPTION
    assert 72 <= win32.window_dpi(hwnd) <= 480
    assert not win32.has_mouse_capture(hwnd)
    l, t, r, b = win32.frame_bounds(hwnd)
    assert r - l >= 300 and b - t >= 200
    wl, wt, wr, wb = ctypes_rect(hwnd)
    assert wl <= l and wt <= t and wr >= r and wb >= b          # GetWindowRect adds the invisible borders
    assert window_exists(hwnd)
    assert not is_fullscreen_window(hwnd, primary(enumerate_monitors()).rect)


def test_window_listing_includes_our_window(own_window):
    _w, hwnd = own_window
    others = {w.hwnd: w for w in enumerate_windows(own_pid=-1)}
    assert hwnd in others and others[hwnd].title == TITLE and others[hwnd].rect == win32.frame_bounds(hwnd)
    assert hwnd not in {w.hwnd for w in enumerate_windows(os.getpid())}
    (mine,) = [w for w in enumerate_windows(os.getpid(), include={hwnd}) if w.hwnd == hwnd]
    assert mine.cls == "PixelCatOwn" and mine.topmost


def test_app_window_listing_and_processes(own_window):
    _w, hwnd = own_window
    apps = control.list_app_windows(own_pid=-1)
    (mine,) = [a for a in apps if a.hwnd == hwnd]
    assert mine.pid == os.getpid() and mine.title == TITLE and "python" in mine.exe.lower()
    assert [a.hwnd for a in control.find_windows(TITLE, apps)] == [hwnd]
    assert hwnd not in {a.hwnd for a in control.list_app_windows(own_pid=os.getpid())}
    assert control.process_exe(os.getpid()).lower().endswith(".exe")
    procs = {p.pid: p for p in control.list_processes()}
    assert os.getpid() in procs and procs[os.getpid()].exe.lower().endswith(".exe")
    assert control.alive([os.getpid()]) == [os.getpid()]


def test_click_through_and_styles_on_our_window(own_window, qtbot):
    _w, hwnd = own_window
    before = win32.get_exstyle(hwnd)
    try:
        win32.set_click_through(hwnd, True)
        assert win32.get_exstyle(hwnd) & win32.WS_EX_TRANSPARENT
        win32.set_click_through(hwnd, False)
        assert not win32.get_exstyle(hwnd) & win32.WS_EX_TRANSPARENT
        win32.move_window(hwnd, 180, 190, 500, 360, topmost=False)
        qtbot.wait(50)
        assert ctypes_rect(hwnd) == (180, 190, 680, 550)
    finally:
        win32.set_exstyle(hwnd, before)


def test_keep_topmost_keeps_a_stay_on_top_window_topmost(qtbot):
    """Like every window the app calls keep_topmost on (overlay, ball, game windows), this
    one is created stay-on-top.  A plain window can't be raised this way from the test:
    SetWindowPos only brings a visible window to the top for a process with
    SetForegroundWindow permission, which the CI test process doesn't have."""
    w = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    w.resize(120, 90)
    w.move(200, 200)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    hwnd = int(w.winId())
    assert win32.get_exstyle(hwnd) & win32.WS_EX_TOPMOST
    win32.set_click_through(hwnd, True)
    win32.keep_topmost(hwnd)
    ex = win32.get_exstyle(hwnd)
    assert ex & win32.WS_EX_TOPMOST and ex & win32.WS_EX_TRANSPARENT
    win32.set_click_through(hwnd, False)
    assert win32.get_exstyle(hwnd) & win32.WS_EX_TOPMOST


def test_focus_helpers_on_our_window(own_window):
    _w, hwnd = own_window
    assert inputs.root_of(hwnd) == hwnd
    assert inputs.same_app(hwnd, hwnd)
    assert inputs.active_popup(hwnd) == hwnd
    assert isinstance(inputs.is_foreground(hwnd), bool)
    assert isinstance(inputs.clipboard_sequence(), int)


# ---------------------------------------------------------------- UI Automation
def test_uia_reads_our_window(own_window, qtbot):
    _w, hwnd = own_window

    def look() -> uia.Snapshot:
        for _ in range(3):            # Qt switches accessibility on at the first question
            snap = uia.snapshot(hwnd)
            if any(e.name == "Press me" for e in snap.elements):
                return snap
        return snap

    snap = in_worker(qtbot, look)
    buttons = [e for e in snap.elements if e.kind == "button" and e.name == "Press me"]
    assert buttons, [e.describe() for e in snap.elements]
    l, t, r, b = snap.window_rect
    cx, cy = buttons[0].center
    assert l <= cx < r and t <= cy < b
    assert [e.id for e in snap.elements] == list(range(1, len(snap.elements) + 1))


def test_uia_focus_queries_do_not_fail(qtbot):
    el = in_worker(qtbot, uia.focused_element)
    assert el is None or isinstance(el, uia.Element)
    text = in_worker(qtbot, uia.focused_text)
    assert text is None or isinstance(text, str)


# ---------------------------------------------------------------- capture
def test_capture_of_our_window_is_a_png(own_window):
    _w, hwnd = own_window
    frame = win32.frame_bounds(hwnd)
    img = capture.grab_window(hwnd, frame)
    assert img is not None
    assert (img.width(), img.height()) == (frame[2] - frame[0], frame[3] - frame[1])
    raw = base64.b64decode(capture.to_base64_png(img))
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    shot = capture.grab(frame)
    assert (shot.width(), shot.height()) == (img.width(), img.height())
    assert hwnd in capture.own_windows(os.getpid())


def test_grab_without_our_windows(own_window):
    _w, hwnd = own_window
    frame = win32.frame_bounds(hwnd)
    img = capture.grab_without(frame, os.getpid())
    assert (img.width(), img.height()) == (frame[2] - frame[0], frame[3] - frame[1])


def test_ledges_grab_and_scan_our_window(own_window):
    _w, hwnd = own_window
    frame = win32.frame_bounds(hwnd)
    img = ledges.grab(frame)
    assert img is not None and (img.width(), img.height()) == (frame[2] - frame[0], frame[3] - frame[1])
    finder = ledges.LedgeFinder()
    wins = [w for w in enumerate_windows(own_pid=-1) if w.hwnd == hwnd]
    finder.scan(wins, frame, [], 48, 1)
    finder.scan(wins, frame, [], 48, 1)
    assert finder.searches == 2 and isinstance(finder.current(wins), dict)


# ---------------------------------------------------------------- system
@pytest.fixture
def instance_names(monkeypatch):  # type: ignore[no-untyped-def]
    tag = uuid.uuid4().hex
    monkeypatch.setattr(system, "MUTEX_NAME", f"Local\\PixelCatTests_Mutex_{tag}")
    monkeypatch.setattr(system, "QUIT_EVENT_NAME", f"Local\\PixelCatTests_Quit_{tag}")
    monkeypatch.setattr(system, "_mutex_handle", None)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    handles: list[int] = []
    yield handles
    for h in handles:
        k.CloseHandle(h)


def test_single_instance_and_quit_request_with_our_own_names(instance_names):
    assert not system.is_running()
    assert system.acquire_single_instance() is True
    instance_names.append(system._mutex_handle)
    assert system.is_running()
    assert system.acquire_single_instance() is False         # a second cat
    instance_names.append(system._mutex_handle)
    event = system.create_quit_event()
    assert event
    instance_names.append(event)
    assert system.quit_requested(event) is False
    assert system.close_running(timeout=1.0) == "closed"       # our mutex is free: "the cat" is gone
    assert system.quit_requested(event) is True


def test_system_read_only_queries():
    assert isinstance(system.autostart_enabled(), bool)
    assert system.session_locked() in (True, False)
    tracker = system.ActivityTracker()
    tracker.poll(False)
    assert isinstance(tracker.busy(1.0), bool) and system.ActivityTracker.idle_seconds() >= 0.0


def test_hardware_detect(monkeypatch):
    monkeypatch.setattr(hardware, "_cached", None)
    hw = hardware.detect(fresh=True)
    assert hw.ram_gb > 0.5 and hw.cores >= 1 and hw.disk_free_gb >= 0
    assert all(isinstance(g, hardware.Gpu) and g.name for g in hw.gpus)
    assert "GB memory" in hw.summary()


def test_system_info_and_cpu():
    info = control.system_info()
    assert "RAM:" in info and "CPU:" in info and "Uptime:" in info
    assert 0.0 <= control.cpu_percent(0.05) <= 100.0


# ---------------------------------------------------------------- elevation / shortcuts
def test_elevation_read_only_checks():
    assert elevation.elevation_type() in (1, 2, 3)
    assert inputs.is_elevated(os.getpid()) == inputs.self_elevated()
    assert elevation.manifest_level(sys.executable) in (None, "", "asInvoker")
    assert elevation.exe_needs_admin(sys.executable) is False
    assert elevation.uac_prompt_open() in (True, False)


def test_shortcut_round_trip(real_profile, tmp_path):
    lnk = tmp_path / "Pixel Cat test.lnk"
    assert elevation.make_lnk(lnk, sys.executable, args="-V", workdir=tmp_path, description="test shortcut")
    target, runas = elevation.lnk_target(lnk)
    assert os.path.normcase(target) == os.path.normcase(sys.executable) and runas is False
    assert elevation.needs_admin(str(lnk)) is False


# ---------------------------------------------------------------- read-only odds and ends
def test_volume_is_readable_or_cleanly_unavailable():
    try:
        percent, muted = volume.get_volume()
    except OSError:
        pytest.skip("no audio device on this machine")
    assert 0 <= percent <= 100 and isinstance(muted, bool)


def test_esc_watcher_hook_installs_and_goes_away():
    w = escwatch.EscWatcher(lambda: None)
    w.start()
    try:
        assert w.active
    finally:
        w.stop()
    assert not w.active


@pytest.mark.skipif(sys.maxsize <= 2**32, reason="layouts below are the 64-bit ones")
@pytest.mark.parametrize("struct, size", [
    (win32.RECT, 16), (win32.MONITORINFOEXW, 104), (win32.LASTINPUTINFO, 8),
    (inputs.INPUT, 40), (inputs.POINTER_TOUCH_INFO, 144), (escwatch.KBDLLHOOKSTRUCT, 24),
    (control.PROCESSENTRY32W, 568), (control.SHELLEXECUTEINFOW, 112), (control.SYSTEM_POWER_STATUS, 12),
    (control.MEMORYSTATUSEX, 64), (capture.BITMAPINFOHEADER, 40), (ledges._BITMAPINFOHEADER, 40),
    (uia.VARIANT, 24), (uia.GUID, 16), (volume.GUID, 16),
])
def test_struct_layouts_match_the_windows_sdk(struct, size):
    assert ctypes.sizeof(struct) == size
