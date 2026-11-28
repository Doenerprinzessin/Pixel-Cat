"""pixelcat.adapters.macos.escwatch: the Esc key's state polled on a thread, the Accessibility monitor, own presses."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, escwatch


class Keyboard:
    """``CGEventSourceKeyState``: Esc is down while ``down`` is set; every read is counted."""

    def __init__(self) -> None:
        self.down = False
        self.reads = 0
        self.read = threading.Event()

    def CGEventSourceKeyState(self, state: int, key: int) -> bool:
        assert (state, key) == (1, 53)
        self.reads += 1
        self.read.set()
        return self.down

    def CGPreflightListenEventAccess(self) -> bool:
        return False

    def CGEventGetIntegerValueField(self, ev: Any, field: int) -> int:
        assert field == 42
        return ev


@pytest.fixture
def kb(monkeypatch: pytest.MonkeyPatch) -> Keyboard:
    k = Keyboard()
    monkeypatch.setattr(_objc, "Q", k)
    monkeypatch.setattr(_objc, "AX", FakeModule(AXIsProcessTrusted=lambda: False))
    monkeypatch.setattr(_objc, "on_main", lambda fn, timeout, default: fn())
    monkeypatch.setattr(escwatch, "DEBOUNCE", 0.0)
    escwatch.reset()
    return k


def polls(k: Keyboard, n: int = 3) -> None:
    """Wait until the watcher read the key ``n`` more times."""
    for _ in range(n):
        k.read.clear()
        assert k.read.wait(2.0)


def test_a_press_calls_back_once(kb: Keyboard) -> None:
    hits: list[int] = []
    w = escwatch.MacEscapeWatcher(lambda: hits.append(1), poll=0.002)
    assert not w.active
    w.start()
    w.start()                                     # (twice is once)
    assert w.active
    polls(kb)
    kb.down = True
    polls(kb)
    kb.down = False
    polls(kb)
    kb.down = True
    polls(kb)
    w.stop()
    assert not w.active
    assert hits == [1, 1]


def test_an_esc_held_at_the_start_isnt_a_press(kb: Keyboard) -> None:
    hits: list[int] = []
    kb.down = True
    w = escwatch.MacEscapeWatcher(lambda: hits.append(1), poll=0.002)
    w.start()
    polls(kb)
    w.stop()
    assert hits == []


def test_the_cats_own_esc_is_ignored(kb: Keyboard) -> None:
    hits: list[int] = []
    w = escwatch.MacEscapeWatcher(lambda: hits.append(1), poll=0.002)
    w.start()
    polls(kb)
    escwatch.mark_own(5.0)
    kb.down = True
    polls(kb)
    w.stop()
    assert hits == []


def test_stopped_it_reads_nothing(kb: Keyboard) -> None:
    w = escwatch.MacEscapeWatcher(lambda: None, poll=0.002)
    w.start()
    polls(kb)
    w.stop()
    before = kb.reads
    time.sleep(0.03)
    assert kb.reads == before


def test_a_failing_read_is_not_a_press(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> bool:
        raise RuntimeError
    monkeypatch.setattr(_objc, "Q", FakeModule(CGEventSourceKeyState=broken))
    assert escwatch.MacEscapeWatcher._esc_down() is False


def _event(code: int, repeat: bool = False, mark: int = 0) -> Any:
    return SimpleNamespace(keyCode=lambda: code, isARepeat=lambda: repeat, CGEvent=lambda: mark)


def test_with_accessibility_but_no_input_monitoring_a_monitor_watches_too(kb: Keyboard,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    added: list[Any] = []
    removed: list[Any] = []

    def add(mask: int, handler: Any) -> str:
        added.append((mask, handler))
        return "monitor"
    ns_event = SimpleNamespace(addGlobalMonitorForEventsMatchingMask_handler_=add, removeMonitor_=removed.append)
    monkeypatch.setattr(_objc, "AK", FakeModule(NSEvent=ns_event))
    monkeypatch.setattr(_objc, "AX", FakeModule(AXIsProcessTrusted=lambda: True))
    hits: list[int] = []
    w = escwatch.MacEscapeWatcher(lambda: hits.append(1), poll=10.0)
    w.start()
    (mask, handler), = added
    assert mask == 1 << 10
    handler(_event(12))                                   # not Esc
    handler(_event(53, repeat=True))                      # held down
    handler(_event(53, mark=escwatch.OWN_EVENT_MARK))     # posted by the cat
    assert hits == []
    handler(_event(53))
    assert hits == [1]
    w.stop()
    assert removed == ["monitor"]


def test_one_press_seen_twice_calls_back_once(kb: Keyboard, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(escwatch, "DEBOUNCE", 0.3)
    hits: list[int] = []
    w = escwatch.MacEscapeWatcher(lambda: hits.append(1))
    w._fire()
    w._fire()
    assert hits == [1]


def test_without_accessibility_there_is_no_monitor(kb: Keyboard) -> None:
    w = escwatch.MacEscapeWatcher(lambda: None, poll=10.0)    # (AK isn't faked: using it would fail the test)
    w.start()
    w.stop()


@pytest.mark.macos
def test_the_real_key_state_can_be_polled() -> None:
    hits: list[int] = []
    w = escwatch.MacEscapeWatcher(lambda: hits.append(1))
    if not escwatch.permissions.listen_events() and escwatch.permissions.accessibility():
        pytest.skip("would add a real NSEvent monitor")
    w.start()
    time.sleep(0.15)
    w.stop()
    assert not w.active
    assert isinstance(escwatch.MacEscapeWatcher._esc_down(), bool)
