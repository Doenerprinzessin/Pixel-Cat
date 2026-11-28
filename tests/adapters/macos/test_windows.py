"""pixelcat.adapters.macos.windows: other apps' windows from the Quartz window list, the full-screen rule (covers
the monitor AND keeps the display awake), Accessibility only when already granted, the cat's own ids."""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, display, ownwin, permissions, windows
from pixelcat.domain import AppWindow, Monitor, PermissionNeeded

OWN = (1 << 32) + 0x5500        # a Qt winId: an NSView pointer
ME = os.getpid()
WORK = (0, 33, 1728, 1117)
SCREEN = (0, 0, 1728, 1117)


def cg(number: int, pid: int, owner: str, rect: tuple[int, int, int, int], layer: int = 0, onscreen: bool = True,
       name: str | None = None, alpha: float = 1.0) -> dict[str, Any]:
    l, t, r, b = rect
    return {"kCGWindowNumber": number, "kCGWindowOwnerPID": pid, "kCGWindowOwnerName": owner, "kCGWindowName": name,
            "kCGWindowLayer": layer, "kCGWindowAlpha": alpha, "kCGWindowIsOnscreen": onscreen,
            "kCGWindowBounds": {"X": l, "Y": t, "Width": r - l, "Height": b - t}}


class Desktop:
    """A fake Quartz window list (front to back) and NSWorkspace."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.front = 0
        self.apps: dict[int, SimpleNamespace] = {}
        self.list_calls = 0

    def copy(self, option: int, rel: int) -> list[dict[str, Any]]:
        self.list_calls += 1
        if option & 8:
            return [r for r in self.rows if r["kCGWindowNumber"] == rel]
        return [r for r in self.rows if r["kCGWindowIsOnscreen"] or not option & 1]

    def app(self, pid: int, exe: str, regular: bool = True) -> SimpleNamespace:
        a = SimpleNamespace(processIdentifier=lambda: pid, activationPolicy=lambda: 0 if regular else 1,
                            executableURL=lambda: SimpleNamespace(lastPathComponent=lambda: exe),
                            localizedName=lambda: exe, isHidden=lambda: False, activated=[])
        a.activateWithOptions_ = lambda opts: (a.activated.append(opts), setattr(self, "front", pid))
        self.apps[pid] = a
        return a


@pytest.fixture
def desk(monkeypatch):  # type: ignore[no-untyped-def]
    d = Desktop()
    monkeypatch.setattr(_objc, "Q", FakeModule(CGWindowListCopyWindowInfo=d.copy))
    workspace = SimpleNamespace(frontmostApplication=lambda: d.apps.get(d.front) or SimpleNamespace(
        processIdentifier=lambda: d.front), runningApplications=lambda: list(d.apps.values()))
    monkeypatch.setattr(_objc, "AK", FakeModule(
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
        NSRunningApplication=SimpleNamespace(runningApplicationWithProcessIdentifier_=lambda pid: d.apps.get(pid))))
    monkeypatch.setattr(display, "_last", [Monitor(1, SCREEN, WORK, 96, True, "Built-in")])
    windows.clear_caches()
    ownwin.reset()
    yield d
    windows.clear_caches()
    ownwin.reset()


@pytest.fixture
def no_wait(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(windows.time, "sleep", lambda s: None)


# ------------------------------------------------------------------ the full-screen rule (USER DECISION)
def _never() -> bool:
    raise AssertionError("asked about the assertions for a window that doesn't cover the monitor")


def test_a_native_full_screen_app_without_an_assertion_keeps_the_cat():
    assert windows.watching_full_screen(SCREEN, SCREEN, lambda: False) is False


def test_a_full_screen_window_keeping_the_display_awake_hides_the_cat():
    assert windows.watching_full_screen(SCREEN, SCREEN, lambda: True) is True
    assert windows.watching_full_screen((-10, -10, 1738, 1127), SCREEN, lambda: True) is True


def test_a_window_that_doesnt_cover_the_monitor_never_asks_about_assertions():
    assert windows.watching_full_screen(WORK, SCREEN, _never) is False
    assert windows.watching_full_screen((0, 0, 1728, 1116), SCREEN, _never) is False


def test_is_fullscreen_reads_the_assertions_only_for_a_covering_window(desk, monkeypatch):  # type: ignore[no-untyped-def]
    desk.rows = [cg(10, 500, "Safari", SCREEN), cg(11, 501, "Code", WORK), cg(12, 502, "IINA", SCREEN)]
    asked: list[int] = []

    def awake() -> frozenset[int]:
        asked.append(1)
        return frozenset({502})
    monkeypatch.setattr(windows, "display_awake_pids", awake)
    w = windows.MacWindows()
    assert w.is_fullscreen(11, SCREEN) is False and asked == []
    assert w.is_fullscreen(10, SCREEN) is False         # Safari in its own full-screen Space: the cat stays
    assert w.is_fullscreen(12, SCREEN) is True          # a video player playing: the cat hides
    assert w.is_fullscreen(99, SCREEN) is False and w.is_fullscreen(OWN, SCREEN) is False


def test_the_display_assertions_by_process():
    by_pid = {502: [{"AssertType": "PreventUserIdleDisplaySleep", "AssertLevel": 255}],
              503: [{"AssertType": "PreventUserIdleSystemSleep", "AssertLevel": 255}],
              504: [{"AssertType": "NoDisplaySleepAssertion", "AssertLevel": 0}],
              77: [{"AssertType": "NoDisplaySleepAssertion", "AssertLevel": 255, "AssertionOnBehalfOfPID": 600}]}
    assert windows.awake_pids_from(by_pid) == {502, 77, 600}


def test_the_display_assertions_from_pmset():
    text = """Listed by owning process:
   pid 427(powerd): [0x00015a0b0001876d] 04:42:32 PreventUserIdleSystemSleep named: "Powerd"
   pid 50687(caffeinate): [0x00015a0b00098ec8] 00:00:01 PreventUserIdleDisplaySleep named: "caffeinate"
   pid 900(com.apple.WebKit.GPU): [0x0000000100098ec9] 00:01:10 PreventUserIdleDisplaySleep named: "video"
\tCreated for PID: 812.
   pid 901(helper): [0x0000000100098eca] 00:01:10 BackgroundTask named: "x"
\tCreated for PID: 813.
"""
    assert windows.awake_pids_from_pmset(text) == {50687, 900, 812}


def test_the_assertions_are_cached_and_pmset_answers_when_iokit_cant(monkeypatch, fake_run):  # type: ignore[no-untyped-def]
    windows.clear_caches()
    calls: list[int] = []

    def iopm() -> Any:
        calls.append(1)
        raise OSError("no IOKit")
    monkeypatch.setattr(windows, "_iopm", iopm)
    fake_run.answer(["/usr/bin/pmset", "-g", "assertions"],
                    "   pid 7(mpv): [0x1] 00:00:01 PreventUserIdleDisplaySleep named: \"mpv\"\n")
    assert windows.display_awake_pids() == {7}
    assert windows.display_awake_pids() == {7}
    assert len(calls) == 1 and len(fake_run.calls) == 1
    windows.clear_caches()
    monkeypatch.setattr(windows, "_iopm", lambda: lambda out: (0, {8: [{"AssertType": "NoDisplaySleepAssertion"}]}))
    assert windows.display_awake_pids() == {8}


def test_the_iokit_function_is_loaded_once_and_its_copy_not_retained_again(monkeypatch):  # type: ignore[no-untyped-def]
    loads: list[Any] = []

    def load(bundle, found, infos):  # type: ignore[no-untyped-def]
        loads.append((bundle, infos))
        found["IOPMCopyAssertionsByProcess"] = lambda out: (0, {})
    monkeypatch.setattr(windows, "_IOPM", [])
    monkeypatch.setattr(_objc, "FN", FakeModule(NSBundle=FakeModule(bundleWithPath_=lambda path: path)))
    monkeypatch.setattr(_objc, "objc", FakeModule(loadBundleFunctions=load))
    assert windows._iopm() is windows._iopm()
    assert loads == [("/System/Library/Frameworks/IOKit.framework",
                      [("IOPMCopyAssertionsByProcess", b"io^@", "", {"arguments": {0: {"already_cfretained": True}}})])]


# ------------------------------------------------------------------ listing
def test_top_level_lists_furniture_front_to_back(desk, monkeypatch):  # type: ignore[no-untyped-def]
    chat = (1 << 32) + 0x7700
    desk.rows = [cg(1, 844, "Control Center", (1400, 0, 1440, 33), layer=25),
                 cg(2, 488, "Window Server", (0, 0, 1728, 33), layer=24, name="Menubar"),
                 cg(3, ME, "Python", (500, 300, 800, 500), layer=3),           # the chat (included)
                 cg(4, ME, "Python", (100, 100, 400, 400), layer=25),          # the cat (not included)
                 cg(5, 600, "Notes", (200, 200, 700, 600), layer=3, name="Shopping"),
                 cg(6, 601, "Code", WORK),
                 cg(7, 602, "Finder", (10, 10, 100, 50)),                      # too small
                 cg(8, 603, "Ghost", (0, 0, 900, 900), alpha=0.0)]
    monkeypatch.setattr(ownwin, "numbers_for", lambda include: {3: chat} if chat in include else {})
    got = windows.MacWindows().top_level(ME, {chat})
    assert [(w.hwnd, w.rect, w.z, w.title, w.cls, w.maximized, w.topmost) for w in got] == [
        (chat, (500, 300, 800, 500), 0, "Pixel Cat", "PixelCatOwn", False, True),
        (5, (200, 200, 700, 600), 1, "Shopping", "", False, True),
        (6, WORK, 2, "", "", True, False),
    ]
    assert [w.hwnd for w in windows.MacWindows().top_level(ME)] == [5, 6]


def test_top_level_is_empty_when_the_list_fails():  # the conftest's stubs fail on use
    assert windows.MacWindows().top_level(ME) == []


def test_app_windows_are_regular_apps_on_screen_without_accessibility(desk):  # type: ignore[no-untyped-def]
    desk.app(601, "Code")
    desk.app(602, "Google Chrome")
    desk.app(844, "Control Center", regular=False)
    desk.app(ME, "Python")
    desk.rows = [cg(1, 844, "Control Center", (1400, 0, 1440, 33)),
                 cg(2, 601, "Code", WORK, name="main.py"),
                 cg(3, 602, "Google Chrome", (100, 100, 900, 700)),
                 cg(4, 602, "Google Chrome", (100, 100, 900, 700), onscreen=False),   # minimised or elsewhere
                 cg(5, ME, "Python", (100, 100, 900, 700))]
    got = windows.MacWindows().app_windows(ME)
    assert got == [AppWindow(2, "main.py", "Code", False, True, "", 601),
                   AppWindow(3, "", "Google Chrome", False, False, "", 602)]


def test_the_foreground_window_is_the_front_apps_first_window(desk):  # type: ignore[no-untyped-def]
    desk.rows = [cg(1, 844, "Control Center", (1400, 0, 1440, 33), layer=25),
                 cg(2, 602, "Google Chrome", (0, 0, 20, 20)),
                 cg(3, 602, "Google Chrome", (100, 100, 900, 700)),
                 cg(4, ME, "Python", (1400, 0, 1430, 33), layer=25),       # the tray icon
                 cg(5, ME, "Python", (500, 300, 800, 500), layer=3)]
    w = windows.MacWindows()
    assert w.foreground() == 0
    desk.front = 602
    assert w.foreground() == 3
    assert w.is_foreground(3) and not w.is_foreground(5)
    desk.front = ME
    assert w.foreground() == 5                       # (not mapped yet: the window number, the cat's pid)
    ownwin.remember(5, OWN)
    windows.clear_caches()
    assert w.foreground() == OWN


def test_window_at_skips_the_cats_click_through_windows(desk):  # type: ignore[no-untyped-def]
    desk.rows = [cg(4, ME, "Python", (100, 100, 400, 400), layer=25),
                 cg(3, ME, "Python", (300, 300, 800, 500), layer=3),
                 cg(6, 601, "Code", WORK)]
    ownwin.remember(4, OWN)
    ownwin.remember(3, OWN + 16)
    with ownwin._lock:
        ownwin._through.add(4)
    w = windows.MacWindows()
    assert w.window_at(150, 150) == 6
    assert w.window_at(350, 350) == OWN + 16
    assert w.window_at(900, 900) == 6
    assert w.window_at(5000, 5000) == 0


# ------------------------------------------------------------------ one window (the game loop's questions)
def test_the_ride_questions_share_one_window_query(desk, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(windows, "_TTL", 60.0)      # (a slow machine mustn't let the 15 ms run out)
    desk.rows = [cg(6, 601, "Code", (100, 100, 900, 700), name="main.py")]
    w = windows.MacWindows()
    assert (w.exists(6) and w.is_visible(6) and not w.is_minimized(6) and not w.is_cloaked(6)
            and w.frame_bounds(6) == (100, 100, 900, 700))
    assert desk.list_calls == 1
    assert (w.rect(6), w.pid(6), w.title(6), w.class_name(6)) == ((100, 100, 900, 700), 601, "main.py", "")


def test_a_window_off_screen_without_accessibility_is_on_another_space(desk):  # type: ignore[no-untyped-def]
    desk.rows = [cg(6, 601, "Code", (100, 100, 900, 700), onscreen=False)]
    w = windows.MacWindows()      # (AXIsProcessTrusted is a stub that fails: not trusted, no AX call)
    assert w.exists(6) and not w.is_visible(6) and not w.is_minimized(6) and w.is_cloaked(6)
    assert w.title(6) == ""
    assert not w.exists(7) and w.frame_bounds(7) is None and w.rect(7) == (0, 0, 0, 0) and w.pid(7) == 0


def test_the_cats_own_ids_go_to_ownwin_never_to_quartz(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(ownwin, "alive", lambda win: win == OWN)
    monkeypatch.setattr(ownwin, "visible", lambda win: True)
    monkeypatch.setattr(ownwin, "minimized", lambda win: False)
    monkeypatch.setattr(ownwin, "frame", lambda win: (1, 2, 3, 4) if win == OWN else None)
    w = windows.MacWindows()
    assert w.exists(OWN) and w.is_visible(OWN) and not w.is_cloaked(OWN) and w.frame_bounds(OWN) == (1, 2, 3, 4)
    assert w.pid(OWN) == ME and w.pid(OWN + 8) == 0
    assert not w.exists(OWN + 8) and w.frame_bounds(OWN + 8) is None
    assert w.same_app(OWN, OWN) and w.active_popup(OWN) == OWN and w.bring_to_front(OWN) is False


# ------------------------------------------------------------------ Accessibility
class AXWin:
    def __init__(self, number: int, **attrs: Any) -> None:
        self.number = number
        self.attrs = {"AXMinimized": False, "AXTitle": f"window {number}", **attrs}
        self.actions: list[str] = []


@pytest.fixture
def ax(desk, monkeypatch):  # type: ignore[no-untyped-def]
    """Accessibility granted; the apps' AX windows by pid (``ax.windows[pid]``), ``ax.hung`` pids don't answer."""
    state = SimpleNamespace(windows={}, hung=set(), apps={})

    def app(pid: int) -> SimpleNamespace:
        return state.apps.setdefault(pid, SimpleNamespace(pid=pid, attrs={}))

    def copy(elem: Any, attr: str, _: Any) -> tuple[int, Any]:
        pid = getattr(elem, "pid", None)
        if pid in state.hung:
            return windows.AX_CANNOT_COMPLETE, None
        if attr == "AXWindows":
            return 0, state.windows.get(pid, [])
        value = elem.attrs.get(attr)
        return (0, value) if value is not None else (-25212, None)

    def set_(elem: Any, attr: str, value: Any) -> int:
        if getattr(elem, "pid", None) in state.hung:
            return windows.AX_CANNOT_COMPLETE
        elem.attrs[attr] = value
        return 0

    def act(elem: Any, action: str) -> int:
        elem.actions.append(action)
        return 0
    monkeypatch.setattr(_objc, "AX", FakeModule(
        AXIsProcessTrusted=lambda: True, AXUIElementCreateApplication=app,
        AXUIElementSetMessagingTimeout=lambda e, t: 0, AXUIElementCopyAttributeValue=copy,
        AXUIElementSetAttributeValue=set_, AXUIElementPerformAction=act,
        AXValueCreate=lambda kind, v: (kind, v),
        AXValueGetValue=lambda v, kind, out: (True, SimpleNamespace(x=v[0], y=v[1], width=v[0], height=v[1]))))
    monkeypatch.setattr(_objc, "Q", FakeModule(CGWindowListCopyWindowInfo=desk.copy,
                                               CGPoint=lambda x, y: (x, y), CGSize=lambda w, h: (w, h)))
    monkeypatch.setattr(windows, "_get_window", lambda elem, out: (0, elem.number))
    return state


def test_minimised_and_titles_come_from_accessibility_when_granted(desk, ax):  # type: ignore[no-untyped-def]
    desk.rows = [cg(6, 601, "Code", (100, 100, 900, 700), onscreen=False),
                 cg(7, 601, "Code", (100, 100, 900, 700))]
    ax.windows[601] = [AXWin(6, AXMinimized=True, AXTitle="notes.md"), AXWin(7, AXTitle="main.py")]
    desk.app(601, "Code")
    w = windows.MacWindows()
    assert w.is_minimized(6) and not w.is_cloaked(6) and w.title(6) == "notes.md"
    assert not w.is_minimized(7) and w.title(7) == "main.py"
    assert windows.MacWindows().app_windows(ME) == [AppWindow(6, "notes.md", "Code", True, False, "", 601),
                                                    AppWindow(7, "main.py", "Code", False, False, "", 601)]


def test_bring_to_front_without_accessibility_activates_the_app(desk, no_wait):  # type: ignore[no-untyped-def]
    desk.rows = [cg(6, 601, "Code", (100, 100, 900, 700))]
    code = desk.app(601, "Code")
    desk.front = 999
    assert windows.MacWindows().bring_to_front(6) is True     # no PermissionNeeded
    assert code.activated == [2]
    assert windows.MacWindows().bring_to_front(42) is False


def test_bring_to_front_raises_the_window_through_accessibility(desk, ax, no_wait):  # type: ignore[no-untyped-def]
    desk.rows = [cg(6, 601, "Code", (100, 100, 900, 700), onscreen=False)]
    win = AXWin(6, AXMinimized=True)
    ax.windows[601] = [win]
    desk.app(601, "Code")
    assert windows.MacWindows().bring_to_front(6) is True
    assert win.attrs["AXMinimized"] is False and win.actions == ["AXRaise"]
    assert ax.apps[601].attrs["AXFrontmost"] is True


def test_a_hung_app_is_not_responding(desk, ax, no_wait):  # type: ignore[no-untyped-def]
    desk.rows = [cg(6, 601, "Code", (100, 100, 900, 700))]
    ax.hung.add(601)
    with pytest.raises(RuntimeError, match="not responding"):
        windows.MacWindows().bring_to_front(6)
    with pytest.raises(RuntimeError, match="Code is not responding"):
        windows.MacWindows().window_action(AppWindow(6, "x", "Code", False, False, "", 601), "minimize")


def test_window_actions_need_accessibility(desk, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(permissions, "accessibility", lambda: False)
    w = AppWindow(6, "x", "Code", False, False, "", 601)
    with pytest.raises(PermissionNeeded) as err:
        windows.MacWindows().window_action(w, "minimize")
    assert err.value.permission == permissions.ACCESSIBILITY
    with pytest.raises(PermissionNeeded):
        windows.MacWindows().close_windows([w])
    assert windows.MacWindows().close_windows([]) == 0
    with pytest.raises(ValueError):
        windows.MacWindows().window_action(w, "explode")


def test_window_actions_through_accessibility(desk, ax):  # type: ignore[no-untyped-def]
    desk.rows = [cg(6, 601, "Code", (100, 100, 900, 700)), cg(7, 601, "Code", (50, 50, 400, 400))]
    close6, close7 = AXWin(0), AXWin(0)
    six, seven = AXWin(6, AXCloseButton=close6), AXWin(7, AXCloseButton=close7)
    six.attrs.update(AXPosition=(100, 100), AXSize=(800, 600))
    ax.windows[601] = [six, seven]
    w6, w7 = (AppWindow(n, "x", "Code", False, False, "", 601) for n in (6, 7))
    mw = windows.MacWindows()
    mw.window_action(w6, "minimize")
    assert six.attrs["AXMinimized"] is True
    mw.window_action(w6, "restore")
    assert six.attrs["AXMinimized"] is False and six.actions == ["AXRaise"]
    mw.window_action(w6, "maximize")
    assert six.attrs["AXPosition"] == (1, (0, 33)) and six.attrs["AXSize"] == (2, (1728, 1084))
    assert mw.close_windows([w6, w7, AppWindow(99, "gone", "Code", False, False, "", 601)]) == 2
    assert close6.actions == ["AXPress"] and close7.actions == ["AXPress"]


def test_active_popup_finds_a_sheet(desk, ax):  # type: ignore[no-untyped-def]
    desk.rows = [cg(9, 601, "Code", (300, 100, 700, 300)), cg(6, 601, "Code", (100, 100, 900, 700))]
    sheet = AXWin(9, AXRole="AXSheet")
    ax.windows[601] = [AXWin(6, AXChildren=[AXWin(0, AXRole="AXButton"), sheet])]
    assert windows.MacWindows().active_popup(6) == 9
    ax.windows[601] = [AXWin(6), AXWin(9, AXSubrole="AXDialog")]
    assert windows.MacWindows().active_popup(6) == 9
    ax.windows[601] = [AXWin(6)]
    assert windows.MacWindows().active_popup(6) == 6


def test_active_popup_without_accessibility_is_the_window(desk):  # type: ignore[no-untyped-def]
    desk.rows = [cg(6, 601, "Code", (100, 100, 900, 700))]
    assert windows.MacWindows().active_popup(6) == 6


# ------------------------------------------------------------------ the real Mac (read-only)
@pytest.mark.macos
def test_the_real_window_list_needs_no_permission():
    windows.clear_caches()
    w = windows.MacWindows()
    wins = w.top_level(ME)
    assert all(x.rect[2] > x.rect[0] and x.rect[3] > x.rect[1] and x.hwnd < 1 << 32 for x in wins)
    if wins:
        first = wins[0].hwnd
        assert w.exists(first) and w.frame_bounds(first) == wins[0].rect and w.pid(first) > 0
    fg = w.foreground()
    assert fg == 0 or w.exists(fg)
    assert isinstance(w.app_windows(ME), list)


@pytest.mark.macos
def test_the_real_assertion_reader_sees_caffeinate():
    import subprocess
    import time

    proc = subprocess.Popen(["/usr/bin/caffeinate", "-d", "-t", "5"])
    try:
        time.sleep(0.5)
        windows.clear_caches()
        assert proc.pid in windows.awake_pids_from(windows._iopm()(None)[1])
        out = subprocess.run(["/usr/bin/pmset", "-g", "assertions"], capture_output=True, text=True, timeout=5)
        assert proc.pid in windows.awake_pids_from_pmset(out.stdout)
    finally:
        proc.terminate()
        proc.wait(5)


@pytest.mark.macos
def test_the_real_assertions_dictionary_isnt_leaked():
    import CoreFoundation as CF

    err, by_pid = windows._iopm()(None)
    assert err == 0 and CF.CFGetRetainCount(by_pid) == 1          # (2 without already_cfretained: never freed)
