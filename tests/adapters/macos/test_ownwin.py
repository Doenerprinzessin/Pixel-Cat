"""pixelcat.adapters.macos.ownwin: the cat's own windows (level, Spaces, click-through, moving through Qt, z-order),
stale ids never dereferenced, every call answered on the GUI thread or with its default."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, ownwin, windows

OVERLAY, CHAT, BALL, MENU, GONE = ((1 << 32) + n * 0x100 for n in range(1, 6))


class NSWin:
    def __init__(self, number: int, visible: bool = True, ignores: bool = False) -> None:
        self.number, self.visible, self.ignores = number, visible, ignores
        self.lvl = 3
        self.calls: list[Any] = []

    def windowNumber(self) -> int:
        return self.number

    def level(self) -> int:
        return self.lvl

    def setLevel_(self, level: int) -> None:
        self.lvl = level

    def setCollectionBehavior_(self, value: int) -> None:
        self.collection = value

    def setHidesOnDeactivate_(self, value: bool) -> None:
        self.hides = value

    def setCanHide_(self, value: bool) -> None:
        self.can_hide = value

    def setAnimationBehavior_(self, value: int) -> None:
        self.animation = value

    def setIgnoresMouseEvents_(self, value: bool) -> None:
        self.ignores = value

    def ignoresMouseEvents(self) -> bool:
        return self.ignores

    def isVisible(self) -> bool:
        return self.visible

    def orderFrontRegardless(self) -> None:
        self.calls.append("front")

    def orderWindow_relativeTo_(self, place: int, other: int) -> None:
        self.calls.append(("order", place, other))


class Widget:
    def __init__(self, wid: int, visible: bool = True) -> None:
        self.wid, self.visible = wid, visible
        self.geometry: tuple[int, int, int, int] | None = None

    def internalWinId(self) -> int:
        return self.wid

    def isVisible(self) -> bool:
        return self.visible

    def isMinimized(self) -> bool:
        return False

    def setGeometry(self, x: int, y: int, w: int, h: int) -> None:
        self.geometry = (x, y, w, h)

    def frameGeometry(self) -> SimpleNamespace:
        x, y, w, h = self.geometry or (0, 0, 0, 0)
        return SimpleNamespace(x=lambda: x, y=lambda: y, width=lambda: w, height=lambda: h)

    def windowTitle(self) -> str:
        return "Pixel Cat"


@pytest.fixture
def cat(monkeypatch):  # type: ignore[no-untyped-def]
    """The cat's windows: overlay, chat, ball (already click-through) and a hidden menu; GONE was deleted."""
    s = SimpleNamespace(
        widgets={OVERLAY: Widget(OVERLAY), CHAT: Widget(CHAT), BALL: Widget(BALL), MENU: Widget(MENU, visible=False)},
        ns={OVERLAY: NSWin(101), CHAT: NSWin(102), BALL: NSWin(103, ignores=True), MENU: NSWin(104, visible=False)},
        policy=[])

    def view_window(wid: int) -> NSWin:
        assert wid in s.widgets, "dereferenced a window id whose widget is gone"
        return s.ns[wid]
    monkeypatch.setattr(ownwin, "_widget", lambda win: s.widgets.get(win))
    monkeypatch.setattr(ownwin, "_view_window", view_window)
    monkeypatch.setattr(ownwin, "_top_level_widgets", lambda: list(s.widgets.values()))
    monkeypatch.setattr(ownwin, "_cocoa", lambda: True)
    app = SimpleNamespace(setActivationPolicy_=s.policy.append)
    monkeypatch.setattr(_objc, "AK", FakeModule(NSApplication=SimpleNamespace(sharedApplication=lambda: app)))
    ownwin.reset()
    yield s
    ownwin.reset()


def test_a_tool_window_floats_over_everything_on_every_space(cat):  # type: ignore[no-untyped-def]
    ownwin.make_tool_window(OVERLAY)
    ownwin.make_tool_window(CHAT)
    ns = cat.ns[OVERLAY]
    assert ns.lvl == 25 and ns.hides is False and ns.can_hide is False and ns.animation == 2
    assert ns.collection == 1 | 16 | 64 | 256      # all Spaces, stationary, not in Cmd+`, over full-screen apps
    assert cat.policy == [1]                         # Accessory, once
    assert ownwin.own_numbers() == {101: OVERLAY, 102: CHAT}


def test_a_stale_id_is_never_dereferenced(cat):  # type: ignore[no-untyped-def]
    for call in (lambda: ownwin.make_tool_window(GONE), lambda: ownwin.set_click_through(GONE, True),
                 lambda: ownwin.move(GONE, 1, 2, 3, 4), lambda: ownwin.keep_topmost(GONE),
                 lambda: ownwin.place_below(GONE, 7), lambda: ownwin.end_own_click_through([GONE])):
        call()
    assert not ownwin.alive(GONE) and not ownwin.visible(GONE) and ownwin.frame(GONE) is None
    assert ownwin.title(GONE) == "" and cat.policy == []


def test_ids_that_arent_the_cats_need_no_qt():
    assert ownwin._widget(4242) is None and ownwin.numbers_for({4242}) == {}


def test_click_through(cat):  # type: ignore[no-untyped-def]
    ownwin.set_click_through(OVERLAY, True)
    assert cat.ns[OVERLAY].ignores and ownwin.click_through_numbers() == {101}
    ownwin.set_click_through(OVERLAY, False)
    assert not cat.ns[OVERLAY].ignores and ownwin.click_through_numbers() == set()


def test_move_goes_through_qt_and_orders_in_front_only_when_shown(cat):  # type: ignore[no-untyped-def]
    ownwin.move(OVERLAY, 10, 20, 30, 40)
    assert cat.widgets[OVERLAY].geometry == (10, 20, 30, 40) and cat.ns[OVERLAY].calls == ["front"]
    assert ownwin.frame(OVERLAY) == (10, 20, 40, 60)
    ownwin.move(MENU, 1, 2, 3, 4)
    assert cat.widgets[MENU].geometry == (1, 2, 3, 4) and cat.ns[MENU].calls == []   # (would show it behind Qt)
    ownwin.move(CHAT, 5, 6, 7, 8, topmost=False)
    assert cat.ns[CHAT].calls == [] and cat.ns[CHAT].lvl == 3


def test_place_below_another_apps_window_then_back_on_top(cat, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_objc, "Q", FakeModule(CGWindowListCopyWindowInfo=lambda option, number: [
        {"kCGWindowNumber": number, "kCGWindowLayer": 0}]))
    ns = cat.ns[OVERLAY]
    ownwin.make_tool_window(OVERLAY)
    ownwin.place_below(OVERLAY, 777)
    assert ns.lvl == 0 and ns.calls == [("order", -1, 777)]
    ownwin.keep_topmost(OVERLAY)
    assert ns.lvl == 25 and ns.calls[-1] == "front"
    ownwin.place_below(OVERLAY, CHAT)                # one of the cat's own windows: its number and level
    assert ns.lvl == 3 and ns.calls[-1] == ("order", -1, 102)


def test_own_click_through_and_back(cat):  # type: ignore[no-untyped-def]
    changed = ownwin.own_click_through(os.getpid(), OVERLAY)
    assert changed == [CHAT]                         # not the kept overlay, the ball already was, the menu is hidden
    assert cat.ns[CHAT].ignores and not cat.ns[OVERLAY].ignores
    del cat.widgets[CHAT]                            # (closed meanwhile: left alone)
    ownwin.end_own_click_through(changed + [BALL])
    assert cat.ns[CHAT].ignores and not cat.ns[BALL].ignores


def test_refresh_maps_the_live_windows_only(cat):  # type: ignore[no-untyped-def]
    ownwin.remember(999, GONE)
    assert ownwin.refresh() == {101: OVERLAY, 102: CHAT, 103: BALL, 104: MENU}
    assert ownwin.own_id(999) is None and ownwin.numbers_for({CHAT, GONE}) == {102: CHAT}


def test_refresh_reads_which_windows_let_clicks_through(cat):  # type: ignore[no-untyped-def]
    ownwin.set_click_through(OVERLAY, True)
    cat.ns[OVERLAY].ignores = False                   # (changed behind our back, e.g. by Qt)
    ownwin.refresh()
    assert ownwin.click_through_numbers() == {103}    # the ball was click-through all along


def test_a_window_without_a_number_yet_is_never_remembered(cat):  # type: ignore[no-untyped-def]
    cat.ns[CHAT].number = 0                           # (not on screen yet: no window number)
    cat.ns[OVERLAY].number = -1
    ownwin.remember(0, CHAT)
    ownwin.make_tool_window(CHAT)
    ownwin.set_click_through(CHAT, True)
    ownwin.own_click_through(os.getpid(), BALL)
    assert cat.ns[CHAT].ignores and cat.ns[OVERLAY].ignores
    assert ownwin.own_numbers() == {} and ownwin.click_through_numbers() == set()
    assert ownwin.own_id(0) is None and ownwin.own_id(-1) is None


def test_a_worker_gets_the_default_when_the_gui_thread_is_busy(cat, qapp, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(ownwin, "TIMEOUT", 0.1)
    got: list[Any] = []
    worker = threading.Thread(target=lambda: got.append(ownwin.own_click_through(os.getpid(), OVERLAY)))
    t = time.monotonic()
    worker.start()
    worker.join(5)                                   # (the GUI thread is busy right here)
    assert got == [[]] and time.monotonic() - t < 2
    qapp.processEvents()                             # the cancelled job never runs
    assert not cat.ns[CHAT].ignores


_REAL_WINDOWS = r'''
import json, os, sys, time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from pixelcat.adapters.macos import display, ownwin, windows

def pump(seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)

app = QApplication([])
def make(x, y, w, h):
    win = QWidget()
    win.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                       | Qt.WindowType.WindowDoesNotAcceptFocus)
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    win.setGeometry(x, y, w, h)
    win.show()
    return win
cat, chat = make(300, 200, 160, 90), make(500, 300, 300, 200)
pump()
out = {}
try:
    mw = windows.MacWindows()
    cid, wid = int(chat.winId()), int(cat.winId())
    mw.make_tool_window(wid)
    mw.set_click_through(wid, True)
    mw.move(wid, 320, 210, 170, 95, topmost=True)
    pump()
    ns = ownwin._view_window(wid)
    out.update(own=wid >= 1 << 32, level=int(ns.level()), collection=int(ns.collectionBehavior()),
               ignores=bool(ns.ignoresMouseEvents()), hides=bool(ns.hidesOnDeactivate()),
               geometry=[cat.geometry().x(), cat.geometry().y(), cat.geometry().width(), cat.geometry().height()],
               frame=list(mw.frame_bounds(wid)), stale=mw.exists((1 << 33) + 16))
    windows.clear_caches()
    out["top_level"] = [[w.hwnd == cid, list(w.rect)] for w in mw.top_level(os.getpid(), {cid}) if w.cls == "PixelCatOwn"]
    out["mapped"] = sorted(ownwin.own_numbers().values()) == sorted([wid, cid])
    changed = mw.own_click_through(os.getpid(), wid)
    out["changed"] = changed == [cid] and bool(ownwin._view_window(cid).ignoresMouseEvents())
    mw.end_own_click_through(changed)
    out["restored"] = not ownwin._view_window(cid).ignoresMouseEvents()
    out["monitors"] = [[list(m.rect), list(m.work)] for m in display.MacDisplay().monitors()]
    out["screens"] = [[[s.geometry().x(), s.geometry().y(), s.geometry().right() + 1, s.geometry().bottom() + 1],
                       [s.availableGeometry().x(), s.availableGeometry().y(), s.availableGeometry().right() + 1,
                        s.availableGeometry().bottom() + 1]] for s in app.screens()]
finally:
    cat.close()
    chat.close()
    pump(0.1)
    import AppKit
    if AppKit.NSApplication.sharedApplication().isActive():    # (Qt may have activated it: hand the focus back)
        AppKit.NSApplication.sharedApplication().deactivate()
    pump(0.1)
print(json.dumps(out))
'''


@pytest.mark.macos_live                       # (shows real windows on the screen for a moment)
def test_the_real_cocoa_windows(qapp):  # type: ignore[no-untyped-def]
    if windows.permissions.no_prompts() is False:
        pytest.skip("only with PIXELCAT_NO_PROMPTS=1 (the test run sets it)")
    from pixelcat.adapters.macos import display

    if display.MacDisplay().session_locked():
        pytest.skip("the screen is locked: no windows on it")
    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "QT_QPA_PLATFORM": "cocoa", "QT_ENABLE_HIGHDPI_SCALING": "0", "PYTHONPATH": str(root)}
    proc = subprocess.run([sys.executable, "-c", _REAL_WINDOWS], capture_output=True, text=True, timeout=60, env=env,
                          cwd=root)
    assert proc.returncode == 0, proc.stderr[-2000:]
    import json

    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["own"] and out["level"] == 25 and out["collection"] == 1 | 16 | 64 | 256
    assert out["ignores"] and out["hides"] is False and out["stale"] is False
    assert out["geometry"] == [320, 210, 170, 95] and out["frame"] == [320, 210, 490, 305]
    assert out["top_level"] == [[True, [500, 300, 800, 500]]] and out["mapped"]
    assert out["changed"] and out["restored"]
    assert sorted(out["monitors"]) == sorted(out["screens"])
