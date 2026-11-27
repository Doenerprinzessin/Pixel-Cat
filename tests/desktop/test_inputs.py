"""pixelcat.desktop.inputs: SendInput keyboard/mouse/touch, focus and clipboard (mocked DLLs).

Nothing here reaches the real user32: ``inputs.u32``/``k32``/``advapi32`` and the win32
handles are fake DLLs, and ``inputs.time`` is a virtual clock (no real waiting).
"""
from __future__ import annotations

import ctypes
import threading
from types import SimpleNamespace

import pytest

from desktop.fakes import FakeDLL, out, raising
from pixelcat.desktop import inputs

VK_CTRL, VK_SHIFT, VK_ALT, VK_WIN, VK_RETURN, VK_TAB = 0x11, 0x10, 0x12, 0x5B, 0x0D, 0x09


@pytest.fixture
def inp(monkeypatch, w32, clock):  # type: ignore[no-untyped-def]
    """inputs with fake DLLs; ``sent`` collects every SendInput batch as readable tuples."""
    adv = FakeDLL("advapi32")
    monkeypatch.setattr(inputs, "u32", w32.user32)
    monkeypatch.setattr(inputs, "k32", w32.kernel32)
    monkeypatch.setattr(inputs, "advapi32", adv)
    monkeypatch.setattr(inputs, "time", clock)
    monkeypatch.setattr(inputs, "_touch", None)
    sent: list[list[tuple]] = []

    def send(n, arr, size):  # type: ignore[no-untyped-def]
        assert size == ctypes.sizeof(inputs.INPUT)
        batch = []
        for i in range(n):
            e = arr[i]
            if e.type == inputs.INPUT_KEYBOARD:
                batch.append(("key", e.u.ki.wVk, e.u.ki.wScan, e.u.ki.dwFlags))
            else:
                mi = e.u.mi
                batch.append(("mouse", mi.dx, mi.dy, ctypes.c_int32(mi.mouseData).value, mi.dwFlags))
        sent.append(batch)
        return n

    w32.user32.SendInput.result = send
    w32.user32.MapVirtualKeyW.result = lambda vk, kind: 0x100 + vk     # only the low byte is the scan code
    return SimpleNamespace(u=w32.user32, k=w32.kernel32, adv=adv, sent=sent, clock=clock, w32=w32)


def keys_of(sent: list[list[tuple]]) -> list[tuple[int, bool]]:
    """(vk or unicode unit, released?) for every keyboard event, in order."""
    flat = [e for batch in sent for e in batch if e[0] == "key"]
    return [(e[1] or e[2], bool(e[3] & inputs.KEYEVENTF_KEYUP)) for e in flat]


# ---------------------------------------------------------------- key names
@pytest.mark.parametrize("combo, expected", [
    ("ctrl+shift+s", [VK_CTRL, VK_SHIFT, ord("S")]),
    ("Ctrl + C", [VK_CTRL, ord("C")]),
    ("strg+entf", [VK_CTRL, 0x2E]),
    ("alt+F4", [VK_ALT, 0x73]),
    ("win+d", [VK_WIN, ord("D")]),
    ("cmd+1", [VK_WIN, ord("1")]),
    ("f24", [0x87]),
    ("enter", [VK_RETURN]),
    ("ctrl+plus", [VK_CTRL, 0xBB]),
    ("+", [0xBB]),
    ("  pgdn  ", [0x22]),
    ("volumemute", [0xAD]),
])
def test_parse_combo(inp, combo, expected):
    assert inputs.parse_combo(combo) == expected


def test_parse_combo_ctrl_plus_plus_drops_the_plus(inp):
    assert inputs.parse_combo("ctrl++") == [VK_CTRL]       # (current behaviour - see report)


def test_parse_combo_punctuation_uses_the_keyboard_layout(inp):
    inp.u.VkKeyScanW.result = lambda ch: {"#": 0x1BF, "ä": -1}[ch]
    assert inputs.parse_combo("ctrl+#") == [VK_CTRL, 0xBF]
    with pytest.raises(ValueError, match="unknown key 'ä'"):
        inputs.parse_combo("ä")


@pytest.mark.parametrize("combo, message", [("ctrl+hyper", "unknown key 'hyper'"), ("", "no keys given"),
                                            ("   ", "no keys given")])
def test_parse_combo_rejects(inp, combo, message):
    with pytest.raises(ValueError, match=message):
        inputs.parse_combo(combo)


# ---------------------------------------------------------------- keyboard
def test_press_combo_presses_in_order_and_releases_in_reverse(inp):
    inputs.press_combo([VK_CTRL, VK_SHIFT, ord("S")], hold=0.2)
    assert keys_of(inp.sent) == [(VK_CTRL, False), (VK_SHIFT, False), (ord("S"), False),
                                 (ord("S"), True), (VK_SHIFT, True), (VK_CTRL, True)]
    assert len(inp.sent) == 2
    assert sum(inp.clock.sleeps) == pytest.approx(0.2, abs=0.02)


def test_press_combo_scan_codes_and_extended_flag(inp):
    inputs.press_combo([0x25, ord("A")])          # left arrow is an extended key
    (left_down, a_down), _ = inp.sent
    assert left_down == ("key", 0x25, 0x25, inputs.KEYEVENTF_EXTENDEDKEY)
    assert a_down == ("key", ord("A"), ord("A"), 0)


def test_press_combo_always_releases_even_if_pressing_fails(inp):
    calls = []

    def send(n, arr, size):  # type: ignore[no-untyped-def]
        calls.append([(arr[i].u.ki.wVk, arr[i].u.ki.dwFlags) for i in range(n)])
        if len(calls) == 1:
            raise OSError("blocked")
        return n

    inp.u.SendInput.result = send
    with pytest.raises(OSError):
        inputs.press_combo([VK_CTRL, ord("V")])
    assert calls[1] == [(ord("V"), inputs.KEYEVENTF_KEYUP), (VK_CTRL, inputs.KEYEVENTF_KEYUP)]


def test_press_combo_cancel_shortens_the_hold(inp):
    stop = threading.Event()
    stop.set()
    inputs.press_combo([VK_ALT], hold=5.0, cancel=stop)
    assert inp.clock.sleeps == [] and len(inp.sent) == 2


def test_release_modifiers(inp):
    inputs.release_modifiers()
    assert keys_of(inp.sent) == [(VK_CTRL, True), (VK_SHIFT, True), (VK_ALT, True), (VK_WIN, True)]


def test_type_text_unicode_newlines_tabs_and_emoji(inp):
    typed = inputs.type_text("a\r\nb\tä😀", lambda: True)
    assert typed == 6
    assert inp.sent[0] == [("key", 0, ord("a"), inputs.KEYEVENTF_UNICODE),
                           ("key", 0, ord("a"), inputs.KEYEVENTF_UNICODE | inputs.KEYEVENTF_KEYUP)]
    assert keys_of(inp.sent[1:2]) == [(VK_RETURN, False), (VK_RETURN, True)]
    assert keys_of(inp.sent[3:4]) == [(VK_TAB, False), (VK_TAB, True)]
    assert keys_of(inp.sent[4:5]) == [(ord("ä"), False), (ord("ä"), True)]
    # an emoji: both UTF-16 surrogates down, then both up, in a batch of its own
    assert keys_of(inp.sent[5:]) == [(0xD83D, False), (0xDE00, False), (0xD83D, True), (0xDE00, True)]
    assert len(inp.sent) == 6


def test_type_text_stops_when_the_window_is_no_longer_ok(inp):
    budget = iter([True, True, False])
    assert inputs.type_text("hello", lambda: next(budget)) == 2
    assert len(inp.sent) == 2


def test_type_text_cancel(inp):
    stop = threading.Event()
    inp.clock.on_sleep = lambda _s: stop.set()
    assert inputs.type_text("hello", lambda: True, cancel=stop) == 1


def test_type_text_emoji_inside_a_chunk_gets_its_own_batch(inp):
    assert inputs.type_text("ab😀c", lambda: True, chunk=4) == 4
    assert [len(b) for b in inp.sent] == [4, 4, 2]


def test_type_text_chunks(inp):
    assert inputs.type_text("abcde", lambda: True, chunk=2) == 5
    assert [len(b) for b in inp.sent] == [4, 4, 2]


# ---------------------------------------------------------------- mouse
SCREEN = {76: -1920, 77: 0, 78: 1920 + 2560, 79: 1440}    # virtual desktop: x, y, w, h


@pytest.fixture
def mouse(inp, monkeypatch):  # type: ignore[no-untyped-def]
    """Mouse input on a two-monitor desktop; the pointer follows the injected events."""
    inp.u.GetSystemMetrics.result = lambda i: SCREEN.get(i, 0)
    pos = {"xy": (0, 0)}
    real_mouse_at = inputs._mouse_at

    def follow(x, y, flag, data=0):  # type: ignore[no-untyped-def]
        pos["xy"] = (int(x), int(y))
        return real_mouse_at(x, y, flag, data)

    monkeypatch.setattr(inputs, "_mouse_at", follow)

    def get(p):  # type: ignore[no-untyped-def]
        out(p).x, out(p).y = pos["xy"]
        return 1

    inp.w32.user32.GetCursorPos.result = get
    inp.pos = pos
    return inp


def mouse_events(sent):  # type: ignore[no-untyped-def]
    return [e for batch in sent for e in batch if e[0] == "mouse"]


def button_bits(flags: int) -> int:
    """The button/wheel part of a mouse event's flags (without move/absolute/virtual desk)."""
    return flags & ~(inputs.MOUSEEVENTF_MOVE | inputs.MOUSEEVENTF_ABSOLUTE | inputs.MOUSEEVENTF_VIRTUALDESK)


@pytest.mark.parametrize("x, y, expected", [(-1920, 0, (0, 0)), (2559, 1439, (65535, 65535)), (320, 720, (32775, 32790))])
def test_absolute_maps_screen_pixels_to_virtual_desk_units(mouse, x, y, expected):
    assert inputs._absolute(x, y) == expected


def test_click_moves_first_then_presses_and_releases_at_the_spot(mouse):
    inputs.click(100, 200)
    assert mouse.u.SetCursorPos.calls == [(100, 200)]
    down, up = mouse_events(mouse.sent)
    absolute = inputs.MOUSEEVENTF_MOVE | inputs.MOUSEEVENTF_ABSOLUTE | inputs.MOUSEEVENTF_VIRTUALDESK
    assert down[1:3] == up[1:3] == inputs._absolute(100, 200)
    assert down[4] == inputs.MOUSEEVENTF_LEFTDOWN | absolute
    assert up[4] == inputs.MOUSEEVENTF_LEFTUP | absolute


@pytest.mark.parametrize("kwargs, clicks", [({}, 1), ({"double": True}, 2), ({"count": 3}, 3),
                                            ({"double": True, "count": 3}, 3), ({"count": 0}, 1)])
def test_click_count(mouse, kwargs, clicks):
    inputs.click(5, 5, **kwargs)
    assert len(mouse_events(mouse.sent)) == 2 * clicks


@pytest.mark.parametrize("button, swapped, down, data", [
    ("left", 0, inputs.MOUSEEVENTF_LEFTDOWN, 0),
    ("right", 0, inputs.MOUSEEVENTF_RIGHTDOWN, 0),
    ("left", 1, inputs.MOUSEEVENTF_RIGHTDOWN, 0),
    ("right", 1, inputs.MOUSEEVENTF_LEFTDOWN, 0),
    ("middle", 1, inputs.MOUSEEVENTF_MIDDLEDOWN, 0),
    ("back", 0, inputs.MOUSEEVENTF_XDOWN, inputs.XBUTTON1),
    ("forward", 0, inputs.MOUSEEVENTF_XDOWN, inputs.XBUTTON2),
])
def test_click_buttons_and_left_handed_mouse(mouse, button, swapped, down, data):
    mouse.u.GetSystemMetrics.result = lambda i: swapped if i == inputs.SM_SWAPBUTTON else SCREEN.get(i, 0)
    inputs.click(1, 1, button=button)
    first = mouse_events(mouse.sent)[0]
    assert button_bits(first[4]) == down and first[3] == data


def test_click_unknown_button_raises_before_anything_is_sent(mouse):
    with pytest.raises(KeyError):
        inputs.click(1, 1, button="thumb")
    assert mouse.sent == [] and mouse.u.SetCursorPos.calls == []


def test_click_releases_the_button_even_if_waiting_fails(mouse, monkeypatch):
    monkeypatch.setattr(inputs, "_wait", raising(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        inputs.click(1, 1, count=2)
    assert [button_bits(e[4]) for e in mouse_events(mouse.sent)] == [inputs.MOUSEEVENTF_LEFTDOWN, inputs.MOUSEEVENTF_LEFTUP]


def test_click_hold_keeps_the_button_down(mouse):
    inputs.click(1, 1, hold=1.5)
    assert max(mouse.clock.sleeps) <= 0.05 and sum(mouse.clock.sleeps) >= 1.5


@pytest.mark.parametrize("clicks, horizontal, flag, data, n", [
    (3, False, inputs.MOUSEEVENTF_WHEEL, 120, 3),
    (-2, False, inputs.MOUSEEVENTF_WHEEL, -120, 2),
    (-1, True, inputs.MOUSEEVENTF_HWHEEL, -120, 1),
    (0, False, None, None, 0),
])
def test_scroll(mouse, clicks, horizontal, flag, data, n):
    inputs.scroll(10, 20, clicks, horizontal)
    events = mouse_events(mouse.sent)
    assert len(events) == n and mouse.u.SetCursorPos.calls == [(10, 20)]
    assert all(button_bits(e[4]) == flag and e[3] == data for e in events)


def test_move_cursor(mouse):
    inputs.move_cursor(12.7, -3.2)
    assert mouse.u.SetCursorPos.calls == [(12, -3)]


def test_holding_releases_keys_in_reverse_even_on_error(inp):
    with pytest.raises(ZeroDivisionError):
        with inputs.holding([VK_CTRL, VK_SHIFT]):
            assert keys_of(inp.sent) == [(VK_CTRL, False), (VK_SHIFT, False)]
            1 / 0
    assert keys_of(inp.sent)[2:] == [(VK_SHIFT, True), (VK_CTRL, True)]


def test_holding_nothing_sends_nothing(inp):
    with inputs.holding([]):
        pass
    assert inp.sent == []


# ---------------------------------------------------------------- routes, trace, drag, glide
def test_route_single_point():
    assert inputs._route([(5, 6)], 1.0) == [(5, 6, 0.0)]


def test_route_two_points_eases_and_ends_exactly():
    route = inputs._route([(0, 0), (600, 0)], 1.0)
    assert len(route) == 125                       # max(seconds * 125, length / 6)
    assert route[-1] == (600, 0, 1.0)
    xs = [x for x, _y, _t in route]
    assert xs == sorted(xs)
    assert xs[1] - xs[0] < xs[62] - xs[61]         # slow start, fast middle
    assert [t for *_p, t in route] == sorted(t for *_p, t in route)


def test_route_through_several_points_follows_the_segments():
    route = inputs._route([(0, 0), (100, 0), (100, 100)], 0.01)
    assert len(route) == 33 and route[-1][:2] == (100, 100)
    assert all((y == 0 and 0 <= x <= 100) or (x == 100 and 0 <= y <= 100) for x, y, _t in route)
    steps = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(route, route[1:])]
    assert max(steps) - min(steps) <= 1                  # linear: even steps (no easing)


def test_route_caps_the_number_of_points():
    assert len(inputs._route([(0, 0), (10**6, 0)], 1.0)) == 6000


def test_trace_moves_along_and_reports_done(mouse):
    assert inputs.trace([(0, 0), (60, 0)], 0.1) == ""
    events = mouse_events(mouse.sent)
    assert events and events[-1][1:3] == inputs._absolute(60, 0)


def test_trace_stops_when_the_user_moves_the_mouse(mouse):
    mouse.clock.on_sleep = lambda _s: mouse.pos.update(xy=(500, 500))
    assert inputs.trace([(0, 0), (40, 0)], 0.2) == "user"


def test_trace_cancel(mouse):
    stop = threading.Event()
    stop.set()
    assert inputs.trace([(0, 0), (40, 0)], 0.2, stop) == "cancel"
    assert mouse.sent == []


def test_glide_starts_at_the_cursor(mouse):
    mouse.pos["xy"] = (10, 10)
    assert inputs.glide(30, 10, seconds=0.05) == ""
    assert mouse_events(mouse.sent)[-1][1:3] == inputs._absolute(30, 10)
    assert all(button_bits(e[4]) == 0 for e in mouse_events(mouse.sent))


def test_drag_presses_at_the_start_and_lets_go_at_the_end(mouse):
    assert inputs.drag([(0, 0), (30, 0)], seconds=0.05) == ""
    presses = [(button_bits(e[4]), e[1:3]) for e in mouse_events(mouse.sent) if button_bits(e[4])]
    assert presses == [(inputs.MOUSEEVENTF_LEFTDOWN, inputs._absolute(0, 0)),
                       (inputs.MOUSEEVENTF_LEFTUP, inputs._absolute(30, 0))]
    assert mouse.u.SetCursorPos.calls == [(0, 0)]


def test_drag_interrupted_by_the_user_lets_go_where_the_pointer_is(mouse):
    mouse.clock.on_sleep = lambda _s: mouse.pos.update(xy=(400, 300)) if len(mouse.clock.sleeps) > 3 else None
    assert inputs.drag([(0, 0), (100, 0)], seconds=0.5) == "user"
    last = mouse_events(mouse.sent)[-1]
    assert button_bits(last[4]) == inputs.MOUSEEVENTF_LEFTUP and last[1:3] == inputs._absolute(400, 300)


def test_drag_lets_go_even_if_tracing_fails(mouse, monkeypatch):
    def broken(*_a):  # type: ignore[no-untyped-def]
        mouse.pos["xy"] = (7, 8)
        raise OSError("x")

    monkeypatch.setattr(inputs, "trace", broken)
    with pytest.raises(OSError):
        inputs.drag([(0, 0), (100, 0)])
    last = mouse_events(mouse.sent)[-1]
    assert button_bits(last[4]) == inputs.MOUSEEVENTF_LEFTUP and last[1:3] == inputs._absolute(7, 8)


# ---------------------------------------------------------------- touch
@pytest.fixture
def touch_log(inp):  # type: ignore[no-untyped-def]
    frames: list[tuple[int, list[tuple[int, int, int]]]] = []

    def inject(n, arr):  # type: ignore[no-untyped-def]
        flags = arr[0].pointerInfo.pointerFlags
        frames.append((flags, [(arr[i].pointerInfo.pointerId, arr[i].pointerInfo.ptPixelLocation.x,
                                arr[i].pointerInfo.ptPixelLocation.y) for i in range(n)]))
        assert all(arr[i].pointerInfo.pointerType == inputs.PT_TOUCH for i in range(n))
        return 1

    inp.u.InitializeTouchInjection.result = 1
    inp.u.InjectTouchInput.result = inject
    return frames


def test_touch_available_is_cached(inp):
    inp.u.InitializeTouchInjection.result = 1
    assert inputs.touch_available() is True
    inp.u.InitializeTouchInjection.result = 0
    assert inputs.touch_available() is True
    assert inp.u.InitializeTouchInjection.calls == [(10, inputs.TOUCH_FEEDBACK_DEFAULT)]


def test_touch_unavailable(inp):
    inp.u.InitializeTouchInjection.result = raising(OSError("no touch"))
    assert inputs.touch_available() is False
    assert inputs.touch([[(1, 1)]], 0.1) == "failed"


def test_touch_down_update_up(touch_log):
    contact = inputs.POINTER_FLAG_INRANGE | inputs.POINTER_FLAG_INCONTACT
    assert inputs.touch([[(0, 0), (10, 0)], [(1, 1), (11, 1)], [(2, 2), (12, 2)]], 0.1) == ""
    assert [f for f, _ in touch_log] == [inputs.POINTER_FLAG_DOWN | contact, inputs.POINTER_FLAG_UPDATE | contact,
                                         inputs.POINTER_FLAG_UPDATE | contact, inputs.POINTER_FLAG_UP]
    assert touch_log[-1][1] == [(0, 2, 2), (1, 12, 2)]


def test_touch_no_frames_or_refused(touch_log, inp):
    assert inputs.touch([], 0.1) == "failed"
    inp.u.InjectTouchInput.result = 0
    assert inputs.touch([[(0, 0)]], 0.1) == "failed"


def test_touch_cancel_lifts_where_the_fingers_are(touch_log):
    stop = threading.Event()
    stop.set()
    assert inputs.touch([[(0, 0)], [(5, 5)]], 0.1, stop) == "cancel"
    assert [f for f, _ in touch_log][-1] == inputs.POINTER_FLAG_UP and touch_log[-1][1] == [(0, 0, 0)]


def test_touch_update_refused_still_lifts(touch_log, inp):
    results = iter([1, 0, 1])
    inp.u.InjectTouchInput.result = lambda n, arr: next(results)
    assert inputs.touch([[(0, 0)], [(5, 5)], [(9, 9)]], 0.1) == "failed"
    assert len(inp.u.InjectTouchInput.calls) == 3


def test_pinch_spreads_two_fingers_symmetrically(touch_log):
    assert inputs.pinch(500, 400, 100, 300, seconds=0.1) == ""
    first, last = touch_log[0][1], touch_log[-2][1]
    assert first == [(0, 450, 400), (1, 550, 400)]
    assert last == [(0, 350, 400), (1, 650, 400)]
    assert len(touch_log) == 8 + 1 + 1          # max(8, seconds*60) steps + the first frame + lift


# ---------------------------------------------------------------- windows / focus
@pytest.fixture
def tree(inp):  # type: ignore[no-untyped-def]
    roots = {11: 1, 12: 1, 21: 2, 1: 1, 2: 2, 3: 3}
    owners = {1: 1, 2: 2, 3: 1}
    pids = {1: 100, 2: 200, 3: 300}

    def ancestor(h, kind):  # type: ignore[no-untyped-def]
        return (roots if kind == inputs.GA_ROOT else owners).get(h, 0)

    def pid(h, p):  # type: ignore[no-untyped-def]
        if p is not None:
            out(p).value = pids.get(h, 0)
        return 5

    inp.u.GetAncestor.result = ancestor
    inp.u.GetWindowThreadProcessId.result = pid
    return inp


def test_root_of_and_window_at(tree):
    assert inputs.root_of(12) == 1 and inputs.root_of(0) == 0
    tree.u.WindowFromPoint.result = lambda pt: 21 if (pt.x, pt.y) == (5, 6) else 0
    assert inputs.window_at(5, 6) == 2
    assert inputs.window_at(0, 0) == 0


@pytest.mark.parametrize("a, b, expected", [(1, 1, True), (11, 12, True), (1, 2, False), (1, 3, True),
                                            (0, 1, False), (2, 0, False)])
def test_same_app(tree, a, b, expected):
    assert inputs.same_app(a, b) is expected


def test_same_app_same_process(tree):
    def one_pid(h, p):  # type: ignore[no-untyped-def]
        out(p).value = 9
        return 1

    tree.u.GetWindowThreadProcessId.result = one_pid
    assert inputs.same_app(1, 2) is True


def test_is_foreground(tree):
    tree.u.GetForegroundWindow.result = 12
    assert inputs.is_foreground(1) is True and inputs.is_foreground(2) is False
    tree.u.GetForegroundWindow.result = None
    assert inputs.is_foreground(1) is False


def test_active_popup(inp):
    inp.u.GetWindow.result = lambda h, kind: {1: 5, 2: 2, 3: 6}.get(h, 0)
    inp.u.IsWindowVisible.result = lambda h: h == 5
    assert [inputs.active_popup(h) for h in (1, 2, 3, 4)] == [5, 2, 3, 4]
    assert inp.u.GetWindow.calls[0] == (1, inputs.GW_ENABLEDPOPUP)


def test_bring_to_front_refuses_a_hung_app(inp):
    inp.u.IsHungAppWindow.result = 1
    with pytest.raises(RuntimeError, match="not responding"):
        inputs.bring_to_front(5)


def test_bring_to_front_already_in_front_restores_minimised(inp):
    inp.u.IsIconic.result = 1
    inp.u.GetForegroundWindow.result = 5
    assert inputs.bring_to_front(5) is True
    assert inp.u.ShowWindowAsync.calls == [(5, inputs.SW_RESTORE)] and inp.u.SetForegroundWindow.calls == []


def test_bring_to_front_borrows_the_foreground_input_queue(tree):
    fg = {"h": 2}
    tree.u.GetForegroundWindow.result = lambda: fg["h"]
    tree.k.GetCurrentThreadId.result = 77
    tree.u.AttachThreadInput.result = 1
    tree.u.SetForegroundWindow.result = lambda h: fg.update(h=h) if tree.u.AttachThreadInput.calls else 0
    assert inputs.bring_to_front(1) is True
    assert tree.u.AttachThreadInput.calls == [(77, 5, True), (77, 5, False)]
    assert tree.u.BringWindowToTop.calls == [(1,)]


def test_bring_to_front_gives_up_after_the_timeout(inp):
    inp.u.GetForegroundWindow.result = 2
    inp.u.GetWindowThreadProcessId.result = 0
    assert inputs.bring_to_front(1, timeout=0.5) is False
    assert inp.u.AttachThreadInput.calls == []


def token(inp, ok_open=1, ok_info=1, value=1):  # type: ignore[no-untyped-def]
    def open_token(proc, access, tok):  # type: ignore[no-untyped-def]
        assert access == inputs.TOKEN_QUERY
        out(tok).value = 0xABC
        return ok_open

    def info(tok, cls, buf, size, ret):  # type: ignore[no-untyped-def]
        assert cls == inputs.TokenElevation
        out(buf).value = value
        return ok_info

    inp.adv.OpenProcessToken.result = open_token
    inp.adv.GetTokenInformation.result = info


@pytest.mark.parametrize("ok_open, ok_info, value, expected", [(1, 1, 1, True), (1, 1, 0, False), (0, 1, 1, False),
                                                               (1, 0, 1, False)])
def test_is_elevated(inp, ok_open, ok_info, value, expected):
    inp.k.OpenProcess.result = 0x55
    token(inp, ok_open, ok_info, value)
    assert inputs.is_elevated(1234) is expected
    assert inp.k.OpenProcess.calls == [(0x1000, False, 1234)]
    assert (0x55,) in inp.k.CloseHandle.calls


def test_is_elevated_unknown_process(inp):
    inp.k.OpenProcess.result = 0
    assert inputs.is_elevated(1) is False


@pytest.mark.parametrize("ok_open, value, expected", [(1, 1, True), (1, 0, False), (0, 1, False)])
def test_self_elevated(inp, ok_open, value, expected):
    token(inp, ok_open, 1, value)
    assert inputs.self_elevated() is expected
    assert len(inp.k.CloseHandle.calls) == (1 if ok_open else 0)


# ---------------------------------------------------------------- clipboard
class Memory:
    """GlobalAlloc/GlobalLock/GlobalSize backed by real ctypes buffers."""

    def __init__(self, k) -> None:  # type: ignore[no-untyped-def]
        self.blocks: dict[int, ctypes.Array] = {}
        k.GlobalAlloc.result = self.alloc
        k.GlobalLock.result = lambda h: ctypes.addressof(self.blocks[h]) if h in self.blocks else 0
        k.GlobalSize.result = lambda h: len(self.blocks[h]) if h in self.blocks else 0
        k.GlobalUnlock.result = 1

    def alloc(self, flags, size):  # type: ignore[no-untyped-def]
        h = 0x1000 + len(self.blocks)
        self.blocks[h] = ctypes.create_string_buffer(size)
        return h

    def put(self, data: bytes) -> int:
        h = self.alloc(0, len(data))
        ctypes.memmove(self.blocks[h], data, len(data))
        return h


@pytest.fixture
def clip(inp):  # type: ignore[no-untyped-def]
    mem = Memory(inp.k)
    inp.mem = mem
    inp.u.OpenClipboard.result = 1
    inp.u.CloseClipboard.result = 1
    names = {}
    inp.u.RegisterClipboardFormatW.result = lambda name: names.setdefault(name, 0xC000 + len(names))
    inp.formats = names
    return inp


def text_handle(clip, text: str) -> int:  # type: ignore[no-untyped-def]
    return clip.mem.put(text.encode("utf-16-le" if ctypes.sizeof(ctypes.c_wchar) == 2 else "utf-32-le")
                        + b"\x00" * 4)


def test_get_clipboard_text(clip):
    h = text_handle(clip, "grüezi 😀")
    clip.u.IsClipboardFormatAvailable.result = lambda f: f == inputs.CF_UNICODETEXT
    clip.u.GetClipboardData.result = lambda f: h
    assert inputs.get_clipboard_text() == "grüezi 😀"
    assert len(clip.u.CloseClipboard.calls) == 1


@pytest.mark.parametrize("available, handle", [(0, 1), (1, 0)])
def test_get_clipboard_text_none(clip, available, handle):
    clip.u.IsClipboardFormatAvailable.result = available
    clip.u.GetClipboardData.result = handle
    assert inputs.get_clipboard_text() is None
    assert len(clip.u.CloseClipboard.calls) == 1


def test_clipboard_busy_gives_up_after_retries(clip):
    clip.u.OpenClipboard.result = 0
    assert inputs.get_clipboard_text() is None
    assert inputs.clipboard_has_only_text() is False
    assert inputs.set_clipboard_text("x") is False
    assert inputs.write_clipboard([(1, b"x")]) is False
    assert inputs.backup_clipboard() is None
    inputs.clear_clipboard()
    assert len(clip.u.OpenClipboard.calls) == 6 * 20
    assert clip.u.CloseClipboard.calls == [] and clip.u.EmptyClipboard.calls == []


@pytest.mark.parametrize("count, has_text, expected", [(0, 0, True), (2, 1, True), (1, 0, False)])
def test_clipboard_has_only_text(clip, count, has_text, expected):
    clip.u.CountClipboardFormats.result = count
    clip.u.IsClipboardFormatAvailable.result = has_text
    assert inputs.clipboard_has_only_text() is expected


def test_backup_clipboard_copies_memory_formats_only(clip):
    h_text = clip.mem.put(b"t\x00\x00\x00")
    h_html = clip.mem.put(b"<b>x</b>")
    formats = [13, 2, 0x0300, 0xC010, 0xC011, 0xC012, 0]
    handles = {13: h_text, 2: 99, 0x0300: 98, 0xC010: h_html, 0xC011: 0, 0xC012: 0x7777}
    clip.u.EnumClipboardFormats.result = lambda prev: formats[formats.index(prev) + 1] if prev else formats[0]
    clip.u.GetClipboardData.result = lambda f: handles[f]
    clip.u.GetClipboardOwner.result = None
    assert inputs.backup_clipboard() == [(13, b"t\x00\x00\x00"), (0xC010, b"<b>x</b>")]
    assert {c[0] for c in clip.u.GetClipboardData.calls} == {13, 0xC010, 0xC011, 0xC012}


def test_backup_clipboard_refuses_huge_data(clip, monkeypatch):
    monkeypatch.setattr(inputs, "_BACKUP_LIMIT", 4)
    h = clip.mem.put(b"12345")
    clip.u.EnumClipboardFormats.result = lambda prev: 0 if prev else 13
    clip.u.GetClipboardData.result = h
    clip.u.GetClipboardOwner.result = None
    assert inputs.backup_clipboard() is None
    assert len(clip.u.CloseClipboard.calls) == 1


@pytest.mark.parametrize("answers, expected_none", [(0, True), (1, False)])
def test_backup_clipboard_asks_a_busy_owner_first(clip, answers, expected_none):
    clip.u.GetClipboardOwner.result = 0x4242
    clip.u.SendMessageTimeoutW.result = answers
    clip.u.EnumClipboardFormats.result = 0
    assert (inputs.backup_clipboard() is None) is expected_none
    (call,) = clip.u.SendMessageTimeoutW.calls
    assert call[:6] == (0x4242, 0, 0, 0, 0x0002, 500)


def test_write_clipboard_private_adds_history_markers(clip):
    stored = {}

    def set_data(fmt, h):  # type: ignore[no-untyped-def]
        stored[fmt] = bytes(clip.mem.blocks[h])
        return h

    clip.u.SetClipboardData.result = set_data
    assert inputs.write_clipboard([(13, b"a\x00\x00\x00"), (0xC123, b"")]) is True
    assert len(clip.u.EmptyClipboard.calls) == 1
    assert stored[13] == b"a\x00\x00\x00" and stored[0xC123] == b"\x00"
    markers = {clip.formats[n] for n in ("ExcludeClipboardContentFromMonitorProcessing",
                                         "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")}
    assert markers <= set(stored) and all(stored[m] == b"\x00" * 4 for m in markers)


def test_write_clipboard_not_private_and_failures(clip):
    clip.u.SetClipboardData.result = lambda fmt, h: 0 if fmt == 2 else h
    allocs = iter([0, 0x50, 0x51])
    real_alloc = clip.mem.alloc
    clip.k.GlobalAlloc.result = lambda f, n: real_alloc(f, n) if next(allocs) else 0
    assert inputs.write_clipboard([(1, b"x"), (2, b"y"), (3, b"z")], private=False) is False
    assert clip.formats == {}
    assert len(clip.k.GlobalFree.calls) == 1
    assert [c[0] for c in clip.u.SetClipboardData.calls] == [2, 3]


def test_set_clipboard_text_and_private_text(clip):
    stored = {}
    clip.u.SetClipboardData.result = lambda fmt, h: stored.setdefault(fmt, bytes(clip.mem.blocks[h])) and h
    assert inputs.set_clipboard_text("hé") is True
    assert stored[inputs.CF_UNICODETEXT] == "hé".encode("utf-16-le") + b"\x00\x00"
    stored.clear()
    assert inputs.set_clipboard_private_text("ok") is True
    assert stored[inputs.CF_UNICODETEXT] == "ok".encode("utf-16-le") + b"\x00\x00" and len(stored) == 4


def test_set_clipboard_text_failures(clip):
    clip.k.GlobalAlloc.result = 0
    assert inputs.set_clipboard_text("x") is False
    clip.k.GlobalAlloc.result = clip.mem.alloc
    clip.u.SetClipboardData.result = 0
    assert inputs.set_clipboard_text("x") is False
    assert len(clip.k.GlobalFree.calls) == 1
    assert len(clip.u.CloseClipboard.calls) == 2


def test_clipboard_sequence_format_and_clear(clip):
    clip.u.GetClipboardSequenceNumber.result = 31337
    assert inputs.clipboard_sequence() == 31337
    clip.u.IsClipboardFormatAvailable.result = lambda f: f in (13, 0xC000)
    assert inputs.clipboard_has_format(13) is True
    assert inputs.clipboard_has_format("HTML Format") is True
    assert inputs.clipboard_has_format("Rich Text Format") is False
    inputs.clear_clipboard()
    assert len(clip.u.EmptyClipboard.calls) == 1 and len(clip.u.CloseClipboard.calls) == 1


def test_passthrough_active(inp, monkeypatch):
    monkeypatch.setattr(inputs, "passthrough_until", inp.clock.now + 1)
    assert inputs.passthrough_active() is True
    inp.clock.now += 2
    assert inputs.passthrough_active() is False


def test_wait_returns_false_when_cancelled_meanwhile(inp):
    stop = threading.Event()
    inp.clock.on_sleep = lambda _s: stop.set() if len(inp.clock.sleeps) == 3 else None
    assert inputs._wait(1.0, stop) is False
    assert inputs._wait(0.05, None) is True

