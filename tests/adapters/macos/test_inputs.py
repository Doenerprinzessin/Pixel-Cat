"""pixelcat.adapters.macos.inputs: the pointer/keyboard reads and the synthetic input it posts (to a recorder)."""
from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, escwatch, inputs, keys, permissions
from pixelcat.domain import PermissionNeeded

CMD, SHIFT, CTRL = 55, 56, 59
F_CMD, F_SHIFT, F_CTRL = 0x100000, 0x20000, 0x40000


class Clock:
    """A virtual clock for ``inputs.time``: sleeping only moves it on."""

    def __init__(self) -> None:
        self.now = 100.0

    def perf_counter(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += max(s, 0.001)


class FakeCG:
    """Quartz with an event recorder: every posted event lands in ``posted`` as a dict."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.posted: list[dict[str, Any]] = []
        self.pos = (10.0, 20.0)
        self.idle = 5.0
        self.windows: list[dict[str, Any]] = []
        self.on_post = None

    # permissions
    def CGPreflightPostEventAccess(self) -> bool:
        return self.allowed

    # reads
    def CGEventCreate(self, src: Any) -> dict:
        return {"loc": self.pos}

    def CGEventGetLocation(self, ev: dict) -> Any:
        return SimpleNamespace(x=ev["loc"][0], y=ev["loc"][1])

    def CGEventSourceSecondsSinceLastEventType(self, state: int, kind: int) -> float:
        assert (state, kind) == (1, 0xFFFFFFFF)
        return self.idle

    def CGWindowListCopyWindowInfo(self, option: int, rel: int) -> list:
        return self.windows

    # events
    def CGEventSourceCreate(self, state: int) -> str:
        return "src"

    def CGEventSourceSetLocalEventsSuppressionInterval(self, src: Any, s: float) -> None:
        pass

    def CGEventCreateMouseEvent(self, src: Any, kind: int, at: tuple, button: int) -> dict:
        return {"type": kind, "at": at, "button": button, "flags": None, "clicks": 0}

    def CGEventCreateKeyboardEvent(self, src: Any, code: int, down: bool) -> dict:
        return {"type": 10 if down else 11, "code": code, "flags": None}

    def CGEventCreateScrollWheelEvent(self, src: Any, unit: int, count: int, *wheels: int) -> dict:
        return {"type": 22, "unit": unit, "wheels": wheels, "flags": None}

    def CGEventSetType(self, ev: dict, kind: int) -> None:
        ev["type"] = kind

    def CGEventSetFlags(self, ev: dict, flags: int) -> None:
        ev["flags"] = flags

    def CGEventSetLocation(self, ev: dict, at: tuple) -> None:
        ev["at"] = at

    def CGEventKeyboardSetUnicodeString(self, ev: dict, n: int, s: str) -> None:
        ev["text"], ev["units"] = s, n

    def CGEventSetIntegerValueField(self, ev: dict, field: int, value: int) -> None:
        ev["clicks" if field == 1 else f"field{field}"] = value

    def CGEventPost(self, tap: int, ev: dict) -> None:
        assert tap == 0
        if self.on_post is not None:
            self.on_post(ev)
        self.posted.append(ev)
        if ev["type"] in (5, 6, 7, 27):
            self.pos = ev["at"]

    # what was posted
    def types(self) -> list[int]:
        return [e["type"] for e in self.posted]

    def keys(self) -> list[tuple[int, int, int | None]]:
        return [(e["type"], e["code"], e["flags"]) for e in self.posted if "code" in e]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(inputs, "time", c)
    return c


@pytest.fixture
def cg(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> FakeCG:
    q = FakeCG()
    monkeypatch.setattr(_objc, "Q", q)
    monkeypatch.setattr(_objc, "AX", FakeModule(AXIsProcessTrusted=lambda: q.allowed, kAXTrustedCheckOptionPrompt="p",
                                                AXIsProcessTrustedWithOptions=lambda o: False))
    monkeypatch.setattr(keys, "layout_table", lambda: dict(keys.ANSI, **{"+": 69}))
    escwatch.reset()
    return q


def test_the_reads_need_no_permission(cg: FakeCG, monkeypatch: pytest.MonkeyPatch) -> None:
    cg.allowed = False
    monkeypatch.setattr(_objc, "AK", FakeModule(NSEvent=SimpleNamespace(pressedMouseButtons=lambda: 2)))
    m = inputs.MacInput()
    assert m.cursor_pos() == (10, 20)
    assert m.mouse_buttons_down() and not m.release_went_elsewhere(1)
    assert m.idle_seconds() == 5.0
    assert cg.posted == []


def test_the_last_input_tick_is_held_steady(cg: FakeCG, clock: Clock) -> None:
    m = inputs.MacInput()
    first = m.last_input_tick()
    assert first == int((clock.now - 5.0) * 1000)
    clock.now += 1.0
    cg.idle = 6.03                       # the same input, measured with a little jitter
    assert m.last_input_tick() == first
    cg.idle = 0.0                        # new input
    assert m.last_input_tick() == int(clock.now * 1000)


@pytest.mark.parametrize("act", [
    lambda m: m.click(1, 2),
    lambda m: m.scroll(1, 2, 3),
    lambda m: m.drag([(1, 2), (30, 40)]),
    lambda m: m.glide(5, 5),
    lambda m: m.pinch(1, 2, 100, 200),
    lambda m: m.press_combo([CMD, 9]),
    lambda m: m.type_text("hi", lambda: True),
    lambda m: m.holding(["cmd"]).__enter__(),
])
def test_without_accessibility_nothing_is_posted(cg: FakeCG, act: Any) -> None:
    cg.allowed = False
    with pytest.raises(PermissionNeeded) as err:
        act(inputs.MacInput())
    assert err.value.permission == permissions.ACCESSIBILITY
    assert cg.posted == []


def test_move_cursor_without_accessibility_is_quiet(cg: FakeCG) -> None:
    cg.allowed = False
    inputs.MacInput().move_cursor(5, 6)
    assert cg.posted == []
    cg.allowed = True
    inputs.MacInput().move_cursor(5, 6)
    assert [(e["type"], e["at"]) for e in cg.posted] == [(5, (5.0, 6.0))]


def test_a_double_click_counts_its_clicks_and_marks_its_events(cg: FakeCG) -> None:
    inputs.MacInput().click(30, 40, double=True)
    assert [(e["type"], e["clicks"]) for e in cg.posted] == [(5, 0), (1, 1), (2, 1), (1, 2), (2, 2)]
    assert {e["at"] for e in cg.posted} == {(30.0, 40.0)}
    assert all(e["field42"] == escwatch.OWN_EVENT_MARK for e in cg.posted)


@pytest.mark.parametrize("button, down, up, number", [("right", 3, 4, 1), ("middle", 25, 26, 2),
                                                     ("back", 25, 26, 3), ("forward", 25, 26, 4)])
def test_other_buttons(cg: FakeCG, button: str, down: int, up: int, number: int) -> None:
    inputs.MacInput().click(1, 1, button, count=3)
    clicks = [(e["type"], e["button"], e["clicks"]) for e in cg.posted[1:]]
    assert clicks == [(down, number, 1), (up, number, 1), (down, number, 2), (up, number, 2),
                      (down, number, 3), (up, number, 3)]


def test_a_cancelled_long_press_still_lets_go(cg: FakeCG) -> None:
    cancel = threading.Event()
    cancel.set()
    inputs.MacInput().click(1, 1, hold=5.0, cancel=cancel)
    assert cg.types() == [5, 1, 2]


def test_held_keys_are_flags_changed_and_ride_on_the_mouse_events(cg: FakeCG) -> None:
    m = inputs.MacInput()
    with m.holding(["cmd", "shift"]):
        m.click(5, 5)
    assert cg.keys() == [(12, CMD, F_CMD), (12, SHIFT, F_CMD | F_SHIFT), (12, SHIFT, F_CMD), (12, CMD, 0)]
    assert {e["flags"] for e in cg.posted if "button" in e} == {F_CMD | F_SHIFT}
    cg.posted.clear()
    m.click(5, 5)
    assert {e["flags"] for e in cg.posted} == {None}       # let go: the next click is plain


def test_held_keys_are_let_go_when_the_action_fails(cg: FakeCG) -> None:
    m = inputs.MacInput()
    with pytest.raises(RuntimeError), m.holding(["ctrl"]):
        raise RuntimeError
    assert cg.keys() == [(12, CTRL, F_CTRL), (12, CTRL, 0)]


def test_press_combo_presses_in_order_and_lets_go_in_reverse(cg: FakeCG) -> None:
    m = inputs.MacInput()
    m.press_combo(m.parse_combo("cmd+shift+s"))
    assert cg.keys() == [(12, CMD, F_CMD), (12, SHIFT, F_CMD | F_SHIFT), (10, 1, F_CMD | F_SHIFT),
                         (11, 1, F_CMD | F_SHIFT), (12, SHIFT, F_CMD), (12, CMD, 0)]


def test_press_combo_always_lets_go(cg: FakeCG) -> None:
    def fail_on_s(ev: dict) -> None:
        if ev.get("code") == 1 and ev["type"] == 10:
            raise RuntimeError("posting failed")
    cg.on_post = fail_on_s
    with pytest.raises(RuntimeError):
        inputs.MacInput().press_combo([CMD, 1])
    assert cg.keys() == [(12, CMD, F_CMD), (11, 1, F_CMD), (12, CMD, 0)]


def test_the_cats_own_esc_is_not_the_users(cg: FakeCG) -> None:
    assert not escwatch.own_press()
    inputs.MacInput().press_combo([keys.ESCAPE])
    assert escwatch.own_press()
    assert [k[:2] for k in cg.keys()] == [(10, 53), (11, 53)]


def test_media_keys_are_system_defined_events(cg: FakeCG, monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[tuple] = []

    def other(*args: Any) -> Any:
        made.append(args)
        return SimpleNamespace(CGEvent=lambda: {"type": 14, "data1": args[7]})
    name = "otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_"
    monkeypatch.setattr(_objc, "AK", FakeModule(NSEvent=SimpleNamespace(**{name: other})))
    m = inputs.MacInput()
    m.press_combo(m.parse_combo("volumeup"))
    assert [(e["type"], e["data1"]) for e in cg.posted] == [(14, 0x0A00), (14, 0x0B00)]
    assert [a[6] for a in made] == [8, 8]


def test_type_text_rides_the_text_on_key_events(cg: FakeCG) -> None:
    n = inputs.MacInput().type_text("a😀\n\tb", lambda: True, chunk=20)
    assert n == 5
    assert [(e["type"], e.get("text"), e.get("units"), e.get("code")) for e in cg.posted] == [
        (10, "a😀", 3, 0), (11, "a😀", 3, 0), (10, None, None, 36), (11, None, None, 36),
        (10, None, None, 48), (11, None, None, 48), (10, "b", 1, 0), (11, "b", 1, 0)]
    assert {e["flags"] for e in cg.posted} == {0}


def test_type_text_stops_when_the_target_goes_or_on_esc(cg: FakeCG) -> None:
    calls = []

    def still_ok() -> bool:
        calls.append(1)
        return len(calls) <= 2
    assert inputs.MacInput().type_text("hello", still_ok) == 2
    cancel = threading.Event()
    cancel.set()
    assert inputs.MacInput().type_text("hello", lambda: True, cancel) == 0


def test_type_text_chunks_fit_one_event(cg: FakeCG) -> None:
    inputs.MacInput().type_text("x" * 50, lambda: True, chunk=100)
    assert [e["units"] for e in cg.posted if e["type"] == 10] == [20, 20, 10]


def test_scroll_directions(cg: FakeCG) -> None:
    m = inputs.MacInput()
    m.scroll(7, 8, -2)
    m.scroll(7, 8, 1, horizontal=True)
    wheels = [(e["wheels"], e["at"]) for e in cg.posted if e["type"] == 22]
    assert wheels == [((-1,), (7.0, 8.0)), ((-1,), (7.0, 8.0)), ((0, -1), (7.0, 8.0))]


def test_drag_presses_moves_and_lets_go(cg: FakeCG) -> None:
    assert inputs.MacInput().drag([(0, 0), (60, 0)], seconds=0.1) == ""
    t = cg.types()
    assert t[:2] == [5, 1] and t[-1] == 2 and set(t[2:-1]) == {6}
    assert cg.posted[-1]["at"] == (60.0, 0.0)


def test_drag_stops_when_the_user_takes_the_mouse(cg: FakeCG) -> None:
    real_post = cg.CGEventPost
    dragged = []

    def post(tap: int, ev: dict) -> None:
        real_post(tap, ev)
        if ev["type"] == 6:
            dragged.append(ev)
            if len(dragged) == 3:
                cg.pos = (500.0, 500.0)       # the user grabs the mouse
    cg.CGEventPost = post  # type: ignore[method-assign]
    assert inputs.MacInput().drag([(0, 0), (300, 0)], seconds=0.2) == "user"
    assert cg.posted[-1]["type"] == 2 and cg.posted[-1]["at"] == (500.0, 500.0)    # let go where it is


def test_glide_can_be_cancelled(cg: FakeCG) -> None:
    cancel = threading.Event()
    cancel.set()
    assert inputs.MacInput().glide(100, 100, cancel=cancel) == "cancel"
    assert cg.posted == []


def _front(monkeypatch: pytest.MonkeyPatch, pid: int) -> None:
    app = SimpleNamespace(processIdentifier=lambda: pid)
    ws = SimpleNamespace(frontmostApplication=lambda: app)
    monkeypatch.setattr(_objc, "AK", FakeModule(NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: ws)))


def _window(pid: int, x: int = 0, y: int = 0, w: int = 800, h: int = 600, layer: int = 0) -> dict:
    return {"kCGWindowLayer": layer, "kCGWindowOwnerPID": pid,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


def test_pinch_presses_cmd_plus_minus_in_the_frontmost_app(cg: FakeCG, monkeypatch: pytest.MonkeyPatch) -> None:
    _front(monkeypatch, 77)
    cg.windows = [_window(os.getpid()), _window(77)]        # (the cat's own windows don't count)
    m = inputs.MacInput()
    assert m.pinch(100, 100, 100, 370) == ""
    assert [k[:2] for k in cg.keys() if k[0] == 10] == [(10, 24)] * 3     # '=' as on a US keyboard
    cg.posted.clear()
    assert m.pinch(100, 100, 190, 100) == ""
    assert [k[:2] for k in cg.keys() if k[0] == 10] == [(10, 27)]
    assert {k[2] for k in cg.keys() if k[1] != CMD} == {F_CMD}


def test_pinch_fails_over_a_window_in_the_background(cg: FakeCG, monkeypatch: pytest.MonkeyPatch) -> None:
    _front(monkeypatch, 77)
    cg.windows = [_window(55, w=200), _window(77)]
    m = inputs.MacInput()
    assert m.pinch(100, 100, 100, 300) == "failed"
    assert m.pinch(5000, 5000, 100, 300) == "failed"         # no window there
    assert cg.posted == []


@pytest.mark.macos
def test_the_real_reads() -> None:
    m = inputs.MacInput()
    x, y = m.cursor_pos()
    assert isinstance(x, int) and isinstance(y, int)
    assert isinstance(m.mouse_buttons_down(), bool)
    assert m.idle_seconds() >= 0.0
    assert m.last_input_tick() == m.last_input_tick()


@pytest.mark.macos_live
def test_a_real_click_and_typing_reach_an_own_window(qtbot: Any) -> None:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QLineEdit

    if QGuiApplication.platformName() != "cocoa":
        pytest.skip("needs the real (cocoa) Qt platform, not offscreen")
    if not (permissions.post_events() or permissions.accessibility()):
        pytest.skip("needs Accessibility for the terminal / python running the tests")
    edit = QLineEdit()
    qtbot.addWidget(edit)
    edit.resize(320, 40)
    edit.show()
    edit.raise_()
    edit.activateWindow()
    qtbot.waitExposed(edit)
    m = inputs.MacInput()
    old = m.cursor_pos()
    try:
        c = edit.mapToGlobal(edit.rect().center())
        m.click(c.x(), c.y())
        qtbot.wait(300)
        assert m.type_text("Hi Ü😀", lambda: True) == 5
        qtbot.waitUntil(lambda: edit.text() == "Hi Ü😀", timeout=3000)
        m.press_combo(m.parse_combo("cmd+a"))
        m.press_combo(m.parse_combo("backspace"))
        qtbot.waitUntil(lambda: edit.text() == "", timeout=3000)
    finally:
        m.move_cursor(*old)
