"""pixelcat.adapters.windows.escwatch: the emergency-stop Esc hook (mocked user32/kernel32)."""
from __future__ import annotations

import ctypes

import pytest

from adapters.windows.fakes import FakeDLL
from pixelcat.adapters.windows import escwatch
from pixelcat.adapters.windows.escwatch import KBDLLHOOKSTRUCT, EscWatcher


@pytest.fixture
def hooks(monkeypatch):  # type: ignore[no-untyped-def]
    u, k = FakeDLL("user32"), FakeDLL("kernel32")
    u.SetWindowsHookExW.result = 0xBEEF
    u.CallNextHookEx.result = 77
    k.GetModuleHandleW.result = 0x400000
    monkeypatch.setattr(escwatch, "_u32", u)
    monkeypatch.setattr(escwatch, "_k32", k)
    return u


def test_start_installs_one_low_level_hook_and_stop_removes_it(hooks):
    w = EscWatcher(lambda: None)
    assert not w.active
    w.start()
    w.start()
    assert w.active
    (call,) = hooks.SetWindowsHookExW.calls
    assert call[0] == escwatch.WH_KEYBOARD_LL and call[1] is w._proc and call[2:] == (0x400000, 0)
    w.stop()
    w.stop()
    assert not w.active and hooks.UnhookWindowsHookEx.calls == [(0xBEEF,)]


def test_start_failure_is_logged_and_not_active(hooks, monkeypatch, caplog):
    hooks.SetWindowsHookExW.result = None
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1428)
    w = EscWatcher(lambda: None)
    with caplog.at_level("WARNING", logger=escwatch.log.name):
        w.start()
    assert not w.active and "could not watch for Esc (error 1428)" in caplog.text
    w.stop()
    assert hooks.UnhookWindowsHookEx.calls == []


def key(vk: int, flags: int = 0) -> KBDLLHOOKSTRUCT:
    return KBDLLHOOKSTRUCT(vk, 1, flags, 0, 0)


@pytest.mark.qt
@pytest.mark.parametrize("code, msg, vk, flags, fires", [
    (0, escwatch.WM_KEYDOWN, escwatch.VK_ESCAPE, 0, True),
    (0, escwatch.WM_SYSKEYDOWN, escwatch.VK_ESCAPE, 0, True),
    (0, escwatch.WM_KEYDOWN, escwatch.VK_ESCAPE, escwatch.LLKHF_INJECTED, False),
    (0, 0x0101, escwatch.VK_ESCAPE, 0, False),                    # key up
    (0, escwatch.WM_KEYDOWN, 0x41, 0, False),
    (3, escwatch.WM_KEYDOWN, escwatch.VK_ESCAPE, 0, False),        # not HC_ACTION
])
def test_callback_reports_physical_esc_presses_only(hooks, qtbot, code, msg, vk, flags, fires):
    fired = []
    w = EscWatcher(lambda: fired.append(1))
    kb = key(vk, flags)
    assert w._callback(code, msg, ctypes.addressof(kb)) == 77
    assert fired == []                                   # never inside the hook: queued for the GUI thread
    if fires:
        qtbot.waitUntil(lambda: fired == [1], timeout=2000)
    else:
        qtbot.wait(20)
        assert fired == []
    assert hooks.CallNextHookEx.calls == [(None, code, msg, ctypes.addressof(kb))]


def test_callback_never_raises(hooks):
    w = EscWatcher(lambda: None)
    assert w._callback(0, escwatch.WM_KEYDOWN, 0) == 77     # a null pointer: swallowed
