"""pixelcat.engine.activity: typing, clicking and mouse moves, from the input port (faked)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from fakes.platform import FakeInput
from pixelcat.engine import activity as activity_mod
from pixelcat.engine.activity import ActivityTracker


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _raise(exc: BaseException):  # type: ignore[no-untyped-def]
    def fn(*_a):  # type: ignore[no-untyped-def]
        raise exc
    return fn


@pytest.fixture
def activity(monkeypatch):  # type: ignore[no-untyped-def]
    st = SimpleNamespace(tick=100, cursor=(0, 0), buttons=False, clock=Clock(), input=FakeInput())
    st.input.answers("last_input_tick", lambda: st.tick)
    st.input.answers("cursor_pos", lambda: st.cursor)
    st.input.answers("mouse_buttons_down", lambda: st.buttons)
    monkeypatch.setattr(activity_mod, "time", st.clock)
    st.tracker = ActivityTracker(st.input)
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


def test_poll_ignores_read_errors(activity):
    activity.input.answers("cursor_pos", _raise(OSError("desktop switched")))
    activity.tracker.poll(False)
    assert activity.tracker.cursor == (0, 0)


def test_idle_seconds(activity):
    activity.input.returns("idle_seconds", 12.5)
    assert activity.tracker.idle_seconds() == 12.5
    activity.input.answers("idle_seconds", _raise(OSError("x")))
    assert activity.tracker.idle_seconds() == 0.0
