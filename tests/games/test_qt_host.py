"""games/qt_host.py: the Qt host that shows the games on the desktop.

All Win32 calls the host makes go through ``fake_win32`` (one place to update when they
move behind a desktop port), so these tests are safe on a real Windows desktop too.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from gamekit import DT, SCALE, World, build_world
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from pixelcat.desktop import inputs, win32
from pixelcat.games import GAMES, art
from pixelcat.games import qt_host as qh
from pixelcat.games.base import Host
from pixelcat.sprites.library import SpriteLibrary

pytestmark = pytest.mark.qt

WORK = (0, 0, 2560, 1552)


@pytest.fixture
def fake_win32(monkeypatch: pytest.MonkeyPatch, fake_dll) -> SimpleNamespace:
    """Every Win32 entry point the game windows use, recorded instead of called."""
    st = SimpleNamespace(calls=[], cursor=(0, 0), released=False, passthrough=False, user32=fake_dll("user32"))

    def rec(name: str):
        return lambda *a, **k: st.calls.append((name, a, k))

    monkeypatch.setattr(win32, "user32", st.user32)
    for name in ("make_tool_noactivate", "set_click_through", "keep_topmost", "move_window", "set_exstyle"):
        monkeypatch.setattr(win32, name, rec(name))
    monkeypatch.setattr(win32, "get_exstyle", lambda h: 0)
    monkeypatch.setattr(win32, "cursor_pos", lambda: st.cursor)
    monkeypatch.setattr(win32, "left_button_down", lambda: False)
    monkeypatch.setattr(win32, "release_went_elsewhere", lambda h: st.released)
    monkeypatch.setattr(inputs, "passthrough_active", lambda: st.passthrough)
    st.named = lambda name: [c for c in st.calls if c[0] == name]
    return st


@pytest.fixture(scope="module")
def sprite_lib(qapp) -> SpriteLibrary:
    return SpriteLibrary("charcoal")


@pytest.fixture
def no_leftover_windows(qapp):
    before = set(QApplication.topLevelWidgets())
    yield
    for w in QApplication.topLevelWidgets():
        if w not in before:
            w.hide()
            w.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def qt_world(qapp, fake_win32, sprite_lib, no_leftover_windows) -> World:
    w = build_world()
    st = SimpleNamespace(cat=7777, hud_closed=0)

    def hud_closed() -> None:
        st.hud_closed += 1
    host = qh.QtHost(w.brain, sprite_lib, lambda: list(w.windows), lambda m: SCALE, w.brain.t.min_surface_len,
                     on_world=lambda: Host.world_changed(host), work_area=lambda: WORK, ui_unit=lambda: 2,
                     cat_hwnd=lambda: st.cat, on_hud_close=hud_closed)
    host.ctx = {"fish_book": {}}
    host.ended = w.ended.append
    w.host = host
    w.qt = st  # type: ignore[attr-defined]
    return w


def windows_of(host: Host) -> list[Any]:
    return [sp.win for sp in list(host.sprites) + list(host.props)]


def paint(widget: QWidget) -> QImage:
    img = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    widget.render(img)
    return img


def opaque(img: QImage) -> int:
    return sum(1 for y in range(0, img.height(), 2) for x in range(0, img.width(), 2)
               if img.pixelColor(x, y).alpha() > 0)


def opaque_point(win: Any) -> tuple[int, int]:
    img, (gx, gy, _w, _h) = win._img, win._geom
    for y in range(img.height() // 2, img.height()):
        for x in range(img.width() // 2, img.width()):
            if img.pixelColor(x, y).alpha() > 200:
                return gx + x, gy + y
    raise AssertionError("no opaque pixel")


def mouse(win: QWidget, kind: QMouseEvent.Type, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
    buttons = Qt.MouseButton.NoButton if kind == QMouseEvent.Type.MouseButtonRelease else button
    ev = QMouseEvent(kind, QPointF(1, 1), QPointF(1, 1), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(win, ev)


# ------------------------------------------------------------------ every game in the real host
@pytest.mark.parametrize("key", sorted(GAMES))
def test_each_game_is_shown_ticked_painted_and_closed(key: str, qt_world: World, fake_win32,
                                                       qapp) -> None:
    w, host = qt_world, qt_world.host
    g = w.start(key)
    for _ in range(40):
        w.step(g, (900, 1400))
        qapp.processEvents()
    wins = windows_of(host)
    if key == "hide":                      # (it hides behind a real window: nothing of its own to show)
        assert host.cat_under and fake_win32.user32.SetWindowPos.calls[-1][:2] == (7777, host.cat_under)
    else:
        assert wins, "the game shows something"
    for sp, win in zip(list(host.sprites) + list(host.props), wins):
        assert isinstance(win, qh.SpriteWin) and win.isVisible()
        assert win._hwnd == int(win.winId()) == sp.hwnd
        assert (win.width(), win.height()) == tuple(int(v) for v in sp.size)
        assert opaque(paint(win)) > 0
    assert host.hud.isVisible() and opaque(paint(host.hud)) > 0
    assert host.hud.title == g.title
    g.stop()
    qapp.processEvents()
    assert not any(win.isVisible() for win in wins)
    assert not host.hud.isVisible()
    assert host.sprites == [] and host.props == [] and w.ended == [g]


# ------------------------------------------------------------------ pixmaps
def test_art_pixmaps_are_cached_and_scaled(qapp) -> None:
    pm, img = qh.art_pixmap("mug", 3)
    assert qh.art_pixmap("mug", 3)[0] is pm
    w, h = art.size("mug")
    assert (pm.width(), pm.height()) == (w * 3, h * 3) == (img.width(), img.height())
    assert qh.art_pixmap("mug", 2)[0].width() == w * 2


def test_the_pixmap_cache_is_bounded(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qh, "_PM", {(str(i), 1): None for i in range(801)})
    qh.art_pixmap("pot", 1)
    assert list(qh._PM) == [("pot", 1)]


# ------------------------------------------------------------------ sprite windows
def test_a_sprite_window_appears_as_a_click_through_tool_window(qt_world: World, fake_win32) -> None:
    sp = qt_world.host.sprite("mug", 500, 600)
    win = sp.win
    h = win._hwnd
    l, t, _r, _b = sp.rect()
    assert h and sp.hwnd == h and win.isVisible()
    assert fake_win32.calls == [
        ("make_tool_noactivate", (h,), {}), ("set_click_through", (h, True), {}),
        ("move_window", (h, round(l), round(t), win.width(), win.height()), {"topmost": True})]
    assert win._poll is None
    flags = win.windowFlags()
    for f in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.Tool, Qt.WindowType.WindowStaysOnTopHint,
              Qt.WindowType.WindowDoesNotAcceptFocus):
        assert flags & f
    assert win.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_moving_keeps_the_z_order_and_skips_no_ops(qt_world: World, fake_win32) -> None:
    sp = qt_world.host.sprite("mug", 500, 600)
    fake_win32.calls.clear()
    sp.move(500, 600.2)                    # (same whole-pixel spot)
    assert fake_win32.calls == []
    sp.move(520, 600)
    h = sp.win._hwnd
    assert fake_win32.calls == [("move_window", (h, round(sp.rect()[0]), round(sp.rect()[1]),
                                                 sp.win.width(), sp.win.height()), {"topmost": False})]


def test_new_art_resizes_the_window_through_win32(qt_world: World, fake_win32) -> None:
    sp = qt_world.host.sprite("mug", 500, 600)
    sp.set_art("phone@1")
    w, h = art.size("phone@1")
    l, t, _r, _b = sp.rect()
    # (only the native window is resized - by SetWindowPos; the Qt geometry is not touched)
    assert fake_win32.calls[-1] == ("move_window", (sp.win._hwnd, round(l), round(t), w * SCALE, h * SCALE),
                                    {"topmost": False})


def test_closing_hides_the_window(qt_world: World, qapp) -> None:
    sp = qt_world.host.sprite("mug", 500, 600)
    win = sp.win
    sp.close()
    assert not win.isVisible() and sp not in qt_world.host.sprites


def test_props_get_windows_and_count_as_surfaces(qt_world: World) -> None:
    p = qt_world.host.prop("table", 1800, 1552)
    assert isinstance(p, qh.QtProp) and p.win.isVisible() and p.hwnd == p.win._hwnd
    assert p.hwnd in {s.hwnd for s in qt_world.brain.smap.tops}


def test_hit_test_uses_the_pixels(qt_world: World) -> None:
    sp = qt_world.host.sprite("mug", 500, 600, clickable=True)
    x, y = opaque_point(sp.win)
    assert sp.win.hit(x, y)
    l, t, r, b = sp.rect()
    assert not sp.win.hit(int(r) + 20, int(b) + 20)
    sp.win.hide()
    assert not sp.win.hit(x, y)


def test_clicking_a_sprite(qt_world: World, fake_win32) -> None:
    sp = qt_world.host.sprite("mug", 500, 600, clickable=True)
    clicks = []
    sp.on_click = lambda x, y: clicks.append((x, y))
    fake_win32.cursor = (0, 0)
    mouse(sp.win, QMouseEvent.Type.MouseButtonPress)
    assert clicks == []
    fake_win32.cursor = opaque_point(sp.win)
    mouse(sp.win, QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton)
    assert clicks == []
    mouse(sp.win, QMouseEvent.Type.MouseButtonPress)
    assert clicks == [fake_win32.cursor] and not sp.win._pressing


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def monotonic(self) -> float:
        return self.t


@pytest.mark.parametrize("pause, velocity", [(0.0, (200.0, -200.0)), (0.5, (0.0, 0.0))])
def test_dragging_and_throwing(qt_world: World, fake_win32, monkeypatch: pytest.MonkeyPatch,
                               pause: float, velocity: tuple[float, float]) -> None:
    clock = Clock()
    monkeypatch.setattr(qh, "time", clock)
    sp = qt_world.host.sprite("jar", 500, 600, clickable=True, draggable=True)
    got: list[tuple[str, float, float]] = []
    sp.on_press = lambda x, y: got.append(("press", x, y))
    sp.on_drag = lambda x, y: got.append(("drag", x, y))
    sp.on_release = lambda vx, vy: got.append(("release", vx, vy))
    x, y = opaque_point(sp.win)
    fake_win32.cursor = (x, y)
    mouse(sp.win, QMouseEvent.Type.MouseButtonPress)
    clock.t += 0.05
    fake_win32.cursor = (x + 10, y - 10)
    mouse(sp.win, QMouseEvent.Type.MouseMove)
    clock.t += 0.01 + pause
    mouse(sp.win, QMouseEvent.Type.MouseButtonRelease)
    assert got[:2] == [("press", x, y), ("drag", x + 10, y - 10)]
    assert got[2][0] == "release" and got[2][1:] == pytest.approx(velocity)
    mouse(sp.win, QMouseEvent.Type.MouseButtonRelease)
    assert len(got) == 3


def test_the_cursor_poll_makes_opaque_pixels_clickable(qt_world: World, fake_win32) -> None:
    sp = qt_world.host.sprite("jar", 500, 600, clickable=True, draggable=True)
    win, h = sp.win, sp.win._hwnd
    assert win._poll is not None and win._poll.isActive() and win._poll.interval() == 40
    fake_win32.calls.clear()
    fake_win32.cursor = opaque_point(win)
    win._check_cursor()
    assert fake_win32.calls == [("set_click_through", (h, False), {})]
    assert win.cursor().shape() == Qt.CursorShape.OpenHandCursor
    win._check_cursor()
    fake_win32.cursor = (0, 0)
    win._check_cursor()
    assert fake_win32.calls == [("set_click_through", (h, False), {}), ("set_click_through", (h, True), {})]


def test_the_cursor_poll_lets_clicks_through_while_the_agent_clicks(qt_world: World, fake_win32) -> None:
    sp = qt_world.host.sprite("mug", 500, 600, clickable=True)
    fake_win32.passthrough = True
    fake_win32.cursor = opaque_point(sp.win)
    fake_win32.calls.clear()
    sp.win._check_cursor()
    assert fake_win32.calls == []


def test_a_release_elsewhere_ends_the_drag(qt_world: World, fake_win32) -> None:
    sp = qt_world.host.sprite("jar", 500, 600, clickable=True, draggable=True)
    got = []
    sp.on_release = lambda vx, vy: got.append((vx, vy))
    fake_win32.cursor = opaque_point(sp.win)
    mouse(sp.win, QMouseEvent.Type.MouseButtonPress)
    fake_win32.released = True
    sp.win._check_cursor()
    assert got == [(0.0, 0.0)] and not sp.win._pressing


def test_cursor_poll_errors_are_swallowed(qt_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    sp = qt_world.host.sprite("mug", 500, 600, clickable=True)

    def boom() -> tuple[int, int]:
        raise OSError("gone")
    monkeypatch.setattr(win32, "cursor_pos", boom)
    sp.win._check_cursor()


# ------------------------------------------------------------------ z-order, hiding
def test_cat_below_a_window_and_back_on_top(qt_world: World, fake_win32) -> None:
    host = qt_world.host
    sp = host.sprite("mug", 500, 600)
    host.hud_set("T", "x")
    fake_win32.calls.clear()
    host.cat_below(4242)
    assert host.cat_under == 4242
    assert fake_win32.user32.SetWindowPos.calls == [(7777, 4242, 0, 0, 0, 0, qh.HWND_TOP_FLAGS)]
    assert fake_win32.calls == []
    host.cat_below(None)
    assert host.cat_under is None
    assert [c[1][0] for c in fake_win32.named("keep_topmost")] == [7777, sp.win._hwnd, int(host.hud.winId())]


def test_no_cat_window_no_z_order_changes(qt_world: World, fake_win32) -> None:
    qt_world.qt.cat = 0  # type: ignore[attr-defined]
    qt_world.host.cat_below(4242)
    assert qt_world.host.cat_under == 4242
    assert fake_win32.user32.SetWindowPos.calls == [] and fake_win32.named("keep_topmost") == []


def test_a_fullscreen_app_hides_the_game(qt_world: World) -> None:
    host = qt_world.host
    sp = host.sprite("mug", 500, 600)
    p = host.prop("table", 1800, 1552)
    host.hud_set("Laser pointer", "Pounces 0")
    host.set_hidden(True)
    assert not sp.win.isVisible() and not p.win.isVisible() and not host.hud.isVisible()
    before = set(QApplication.topLevelWidgets())
    host.burst("star", 100, 100)
    host.float_text("+1", 100, 100)
    host.hud_set("Laser pointer", "Pounces 1")
    assert set(QApplication.topLevelWidgets()) == before and not host.hud.isVisible()
    assert host.bursts[-1] == ("star", 100, 100) and host.texts[-1] == ("+1", 100, 100)
    host.set_hidden(False)
    assert sp.win.isVisible() and p.win.isVisible() and host.hud.isVisible()
    assert host.hud.text == "Pounces 1"


def test_bursts_and_texts_get_their_own_windows(qt_world: World, fake_win32) -> None:
    host = qt_world.host
    host.burst("star", 300, 400, 6)
    host.float_text("+1", 300, 400)
    # (QtHost keeps no reference to these windows - see the report; only their creation is checked)
    made = [c[1] for c in fake_win32.named("move_window")]
    assert [m[1:] for m in made] == [(300 - 105, 400 - 105, 210, 210), (300 - 180, 400 - 180, 360, 360)]
    assert len(fake_win32.named("make_tool_noactivate")) == 2


# ------------------------------------------------------------------ effects
@pytest.mark.parametrize("kind, n, text", [("star", 8, ""), ("heart", 4, ""), ("confetti", 12, ""),
                                           ("splash", 6, ""), ("dust", 5, ""), ("", 0, "CRASH!")])
def test_effect_windows_paint_and_fade_away(qt_world: World, fake_win32, sprite_lib, kind: str, n: int,
                                            text: str) -> None:
    fx = qh.FxWin(300, 400, SCALE, kind, n, None, text=text, sprites=sprite_lib)
    size = (120 if text else 70) * SCALE
    assert fx.org == (300 - size // 2, 400 - size // 2) and fx.width() == size
    assert ("move_window", (int(fx.winId()), fx.org[0], fx.org[1], size, size), {"topmost": True}) in fake_win32.calls
    fx._step()
    assert opaque(paint(fx)) > 0
    for _ in range(40):
        fx._step()
    assert not fx.isVisible() and not fx._t.isActive()


# ------------------------------------------------------------------ the score panel
def test_hud_sits_centred_at_the_top_of_the_work_area(qt_world: World, fake_win32) -> None:
    hud = qt_world.host.hud
    hud.set("Mouse hunt", "You 0 : 0 Cat · 60 s", (100, 50, 900, 700), 2)
    g = hud.geometry()
    assert hud.isVisible() and g.y() == 50 + 12 and g.x() == 500 - g.width() // 2
    h = int(hud.winId())
    assert ("make_tool_noactivate", (h,), {}) in fake_win32.calls
    assert ("move_window", (h, g.x(), g.y(), g.width(), g.height()), {"topmost": True}) in fake_win32.calls
    fake_win32.calls.clear()
    hud.set("Mouse hunt", "You 0 : 0 Cat · 60 s", (100, 50, 900, 700), 2)
    assert fake_win32.calls == []
    hud.restyle()
    assert hud.title == "Mouse hunt" and hud.isVisible()


def test_hud_close_button(qt_world: World, fake_win32, qapp) -> None:
    host = qt_world.host
    host.hud_set("Box fort", "Boxes 4")
    hud = host.hud
    r = hud._close_rect()
    g = hud.geometry()
    fake_win32.calls.clear()
    fake_win32.cursor = (g.x() + r.center().x(), g.y() + r.center().y())
    hud._check_cursor()
    assert fake_win32.calls == [("set_click_through", (int(hud.winId()), False), {})]
    for pos, closes in ((QPointF(2, 2), 0), (QPointF(r.center()), 1)):
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(hud, ev)
        assert qt_world.qt.hud_closed == closes  # type: ignore[attr-defined]
    host.hud_close()
    assert not hud.isVisible() and host.hud_lines == ("", "") and not hud._poll.isActive()


# ------------------------------------------------------------------ popups
@pytest.mark.parametrize("button, answer", [("ok", True), ("no", False)])
def test_popup_answers(qt_world: World, sprite_lib, qtbot, button: str, answer: bool) -> None:
    pop = qh.PopupWin(sprite_lib, "Laser pointer", "<b>Hi</b>", "Play", "Later",
                      picture=qh.art_pixmap("laser0", 4)[0])
    pop.show_near((1000, 1300, 1100, 1552), WORK, 2, 144)
    assert pop.isVisible() and pop.width() == 340
    x, y = pop.pos().x(), pop.pos().y()
    assert 0 <= x <= WORK[2] - pop.width() and 0 <= y <= WORK[3] - pop.height()
    assert opaque(paint(pop)) > 0
    got = []
    pop.answered.connect(got.append)
    getattr(pop, button).click()
    pop._finish(True)
    assert got == [answer]


def test_popup_without_cancel_closes_itself_later(qt_world: World, sprite_lib, qtbot) -> None:
    pop = qh.PopupWin(sprite_lib, "Unlocked!", "A hat", "Wear it")
    assert not hasattr(pop, "no")
    pop.show_near((1000, 1300, 1100, 1552), WORK, 1, 96)
    with qtbot.waitSignal(pop.answered, timeout=1000) as sig:
        pop.close_later(0.01)
    assert sig.args == [False]


# ------------------------------------------------------------------ a game with input through the windows
def test_treat_toss_through_the_jar_window(qt_world: World, fake_win32, qapp) -> None:
    w = qt_world
    g = w.start("treats")
    jar = g.jar.win
    fake_win32.cursor = opaque_point(jar)
    mouse(jar, QMouseEvent.Type.MouseButtonPress)
    assert g.held is not None and g.left == 9 and g.held.win.isVisible()
    x, y = fake_win32.cursor
    fake_win32.cursor = (x - 30, y - 40)
    mouse(jar, QMouseEvent.Type.MouseMove)
    assert (g.held.x, g.held.y) == (x - 30, y - 40)
    mouse(jar, QMouseEvent.Type.MouseButtonRelease)
    assert g.flying is not None and g.held is None
    for _ in range(10):
        w.step(g, fake_win32.cursor, DT)
    g.stop()
    qapp.processEvents()
    assert not jar.isVisible()


# ------------------------------------------------------------------ the real Win32 window styles
@pytest.mark.windows
def test_real_game_windows_are_topmost_click_through_tool_windows(qapp, sprite_lib,
                                                                  no_leftover_windows) -> None:
    w = build_world()
    host = qh.QtHost(w.brain, sprite_lib, lambda: [], lambda m: SCALE, w.brain.t.min_surface_len,
                     on_world=lambda: None, work_area=lambda: (0, 0, 800, 600), ui_unit=lambda: 2,
                     cat_hwnd=lambda: 0, on_hud_close=lambda: None)
    sp = host.sprite("mug", 200, 200)
    sp.move(260, 220)
    host.hud_set("Laser pointer", "Pounces 0")
    qapp.processEvents()
    try:
        for h in (sp.win._hwnd, int(host.hud.winId())):
            ex = win32.get_exstyle(h)
            for flag in (win32.WS_EX_TOOLWINDOW, win32.WS_EX_NOACTIVATE, win32.WS_EX_TOPMOST,
                         win32.WS_EX_TRANSPARENT):
                assert ex & flag, hex(flag)
            assert not ex & win32.WS_EX_APPWINDOW
    finally:
        host.clear()
        qapp.processEvents()
