"""pixelcat.desktop.system: single instance, quit request, autostart, activity, lock screen (mocked)."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop.fakes import FakeDLL, raising
from pixelcat.desktop import system, win32


@pytest.fixture
def on_windows(monkeypatch, w32):  # type: ignore[no-untyped-def]
    """Take the Windows code paths, with fake kernel32s everywhere."""
    monkeypatch.setattr(win32, "IS_WINDOWS", True)
    k = FakeDLL("kernel32")
    monkeypatch.setattr(system, "_k32", lambda: k)
    last_error = {"code": 0}
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error["code"])
    monkeypatch.setattr(system, "_mutex_handle", None)
    return SimpleNamespace(k=k, w32=w32, last_error=last_error)


@pytest.fixture
def off_windows(monkeypatch, w32):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(win32, "IS_WINDOWS", False)
    return w32


def test_off_windows_everything_is_a_harmless_default(off_windows):
    assert system.acquire_single_instance() is True
    assert system.create_quit_event() == 0
    assert system.is_running() is False
    assert system.close_running() == "not running"
    assert system.session_locked() is False
    assert off_windows.kernel32.CreateMutexW.calls == []


@pytest.mark.parametrize("handle, error, expected", [(0x10, 0, True), (0x10, win32.ERROR_ALREADY_EXISTS, False),
                                                     (0, 5, True)])
def test_acquire_single_instance(on_windows, handle, error, expected):
    def create(attrs, owner, name):  # type: ignore[no-untyped-def]
        on_windows.last_error["code"] = error
        return handle

    on_windows.w32.kernel32.CreateMutexW.result = create
    assert system.acquire_single_instance() is expected
    assert on_windows.w32.kernel32.CreateMutexW.calls == [(None, False, system.MUTEX_NAME)]
    assert system._mutex_handle == handle


def test_quit_event(on_windows):
    on_windows.k.CreateEventW.result = 0x44
    assert system.create_quit_event() == 0x44
    assert on_windows.k.CreateEventW.calls == [(None, True, False, system.QUIT_EVENT_NAME)]
    on_windows.k.CreateEventW.result = raising(OSError("x"))
    assert system.create_quit_event() == 0


@pytest.mark.parametrize("handle, wait, expected", [(0x44, 0, True), (0x44, 0x102, False), (0, 0, False),
                                                    (0x44, OSError, False)])
def test_quit_requested(on_windows, handle, wait, expected):
    on_windows.k.WaitForSingleObject.result = raising(OSError("x")) if wait is OSError else wait
    assert system.quit_requested(handle) is expected


def test_is_running(on_windows):
    on_windows.k.OpenMutexW.result = 0x55
    assert system.is_running() is True
    assert on_windows.k.OpenMutexW.calls == [(0x00100000, False, system.MUTEX_NAME)]
    assert on_windows.k.CloseHandle.calls == [(0x55,)]
    on_windows.k.OpenMutexW.result = 0
    assert system.is_running() is False


@pytest.mark.parametrize("event, wait, expected, released", [
    (0x66, 0, "closed", True), (0x66, 0x80, "closed", True), (0, 0x102, "still running", False),
])
def test_close_running(on_windows, event, wait, expected, released):
    k = on_windows.k
    k.OpenMutexW.result = 0x55
    k.OpenEventW.result = event
    k.WaitForSingleObject.result = wait
    assert system.close_running(timeout=2.5) == expected
    assert k.SetEvent.calls == ([(event,)] if event else [])
    assert k.WaitForSingleObject.calls == [(0x55, 2500)]
    assert (k.ReleaseMutex.calls == [(0x55,)]) is released
    assert k.CloseHandle.calls[-1] == (0x55,)
    if event:
        assert (event,) in k.CloseHandle.calls
    assert k.OpenEventW.calls == [(0x0002, False, system.QUIT_EVENT_NAME)]


def test_close_running_when_not_running(on_windows):
    on_windows.k.OpenMutexW.result = 0
    assert system.close_running() == "not running"


# ---------------------------------------------------------------- autostart
def test_launch_command_from_source(monkeypatch, tmp_path):
    exe = tmp_path / "python.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delattr(sys, "frozen", raising=False)
    main = Path(system.__file__).resolve().parent.parent.parent / "main.py"
    assert system.launch_command() == f'"{exe}" "{main}"'
    (tmp_path / "pythonw.exe").write_text("")
    assert system.launch_command() == f'"{tmp_path / "pythonw.exe"}" "{main}"'


def test_launch_command_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "PixelCat.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert system.launch_command() == f'"{exe.resolve()}"'


RUN = "HKCU\\" + system.RUN_KEY


@pytest.mark.parametrize("values, expected", [({system.RUN_VALUE: '"C:\\cat.exe"'}, True),
                                              ({system.RUN_VALUE: ""}, False), ({}, False), (None, False)])
def test_autostart_enabled(fake_winreg, values, expected):
    if values is not None:
        fake_winreg.add(RUN, **values)
    assert system.autostart_enabled() is expected


def test_autostart_enabled_read_error(fake_winreg):
    fake_winreg.add(RUN, **{system.RUN_VALUE: PermissionError(5, "Access is denied")})
    assert system.autostart_enabled() is False


def test_set_autostart_writes_and_removes_the_run_value(fake_winreg, monkeypatch):
    fake_winreg.add(RUN, Other="keep me")
    monkeypatch.setattr(system, "launch_command", lambda: '"C:\\PixelCat\\PixelCat.exe"')
    assert system.set_autostart(True) is True
    assert fake_winreg.keys[RUN.lower()] == {"Other": "keep me", system.RUN_VALUE: '"C:\\PixelCat\\PixelCat.exe"'}
    assert fake_winreg.writes[0][3] == fake_winreg.REG_SZ
    assert system.autostart_enabled() is True
    assert system.set_autostart(False) is True
    assert system.set_autostart(False) is True
    assert fake_winreg.keys[RUN.lower()] == {"Other": "keep me"}


def test_set_autostart_without_the_run_key(fake_winreg):
    assert system.set_autostart(True) is False


# ---------------------------------------------------------------- activity
@pytest.fixture
def activity(monkeypatch, clock, w32):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(win32, "IS_WINDOWS", True)
    st = SimpleNamespace(tick=100, cursor=(0, 0), buttons=False, clock=clock)
    monkeypatch.setattr(win32, "last_input_tick", lambda: st.tick)
    monkeypatch.setattr(win32, "cursor_pos", lambda: st.cursor)
    monkeypatch.setattr(win32, "mouse_buttons_down", lambda: st.buttons)
    monkeypatch.setattr(system, "time", clock)
    st.tracker = system.ActivityTracker()
    return st


def test_typing_is_input_without_mouse_movement(activity):
    t = activity.tracker
    activity.clock.now += 1
    t.poll(False)
    assert not t.typing(2.0) and t.last_typing == 0.0
    activity.tick = 150
    activity.clock.now += 1
    t.poll(False)
    assert t.typing(2.0) and t.busy(2.0) and t.last_typing == activity.clock.now
    activity.clock.now += 3
    assert not t.typing(2.0) and not t.busy(2.0)


def test_mouse_movement_is_not_typing_and_sets_speed(activity):
    t = activity.tracker
    activity.tick, activity.cursor = 150, (30, 40)
    activity.clock.now += 0.5
    t.poll(False)
    assert not t.typing(5.0) and t.cursor == (30, 40)
    assert t.cursor_speed == pytest.approx(100.0) and t.last_mouse_move == activity.clock.now
    activity.clock.now += 0.5
    t.poll(False)
    assert t.cursor_speed == pytest.approx(50.0)


@pytest.mark.parametrize("over_cat, busy", [(False, True), (True, False)])
def test_clicks_count_unless_on_the_cat(activity, over_cat, busy):
    activity.buttons, activity.tick = True, 101
    activity.clock.now += 0.1
    activity.tracker.poll(over_cat)
    assert activity.tracker.busy(1.0) is busy
    assert not activity.tracker.typing(1.0)


def test_poll_ignores_read_errors(activity, monkeypatch):
    monkeypatch.setattr(win32, "cursor_pos", raising(OSError("desktop switched")))
    activity.tracker.poll(False)
    assert activity.tracker.cursor == (0, 0)


def test_idle_seconds(monkeypatch):
    monkeypatch.setattr(win32, "idle_seconds", lambda: 12.5)
    assert system.ActivityTracker.idle_seconds() == 12.5
    monkeypatch.setattr(win32, "idle_seconds", raising(OSError("x")))
    assert system.ActivityTracker.idle_seconds() == 0.0


# ---------------------------------------------------------------- lock screen
@pytest.fixture
def wts(monkeypatch, on_windows):  # type: ignore[no-untyped-def]
    dll = FakeDLL("wtsapi32")
    st = SimpleNamespace(dll=dll, info=None)

    def query(server, session, cls, buf, size):  # type: ignore[no-untyped-def]
        assert (server, session, cls) == (None, 0xFFFFFFFF, 25)
        if st.info is None:
            return 0
        st.mem = ctypes.create_string_buffer(st.info, 64)
        buf._obj.value = ctypes.addressof(st.mem)
        return 1

    dll.WTSQuerySessionInformationW.result = query
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, *a, **k: dll if name == "wtsapi32" else FakeDLL(name))
    return st


def wtsinfo(level: int, state: int) -> bytes:
    return level.to_bytes(4, "little") + b"\x00" * 4 + (1).to_bytes(4, "little") + b"\x00" * 4 + \
        state.to_bytes(4, "little", signed=True)


@pytest.mark.parametrize("info, locked", [(wtsinfo(1, 0), True), (wtsinfo(1, 1), False), (wtsinfo(2, 0), False),
                                          (None, False)])
def test_session_locked(wts, info, locked):
    wts.info = info
    assert system.session_locked() is locked
    assert len(wts.dll.WTSFreeMemory.calls) == (1 if info else 0)


def test_session_locked_without_wtsapi(monkeypatch, on_windows):
    monkeypatch.setattr(ctypes, "WinDLL", raising(OSError("no wtsapi32")))
    assert system.session_locked() is False
