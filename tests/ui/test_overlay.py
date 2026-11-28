"""ui.overlay: the cat's click-through window - placement, hit testing, mouse input, painting."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from pixelcat.engine.brain import Brain
from pixelcat.engine.needs import Needs
from pixelcat.engine.particles import Yarn
from pixelcat.ui.overlay import CatOverlay

pytestmark = pytest.mark.qt
L, R = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton


@pytest.fixture
def brain(tuning):  # type: ignore[no-untyped-def]
    b = Brain(tuning, Needs())
    b.rng.seed(1)
    b.x, b.y = 600.0, 700.0
    b.mode = "ground"
    return b


@pytest.fixture
def overlay(desktop, fake_platform, brain, sprites, tuning, track):  # type: ignore[no-untyped-def]
    ov = track(CatOverlay(brain, sprites, tuning, windows=fake_platform.windows, input=fake_platform.input))
    ov.scale = 2
    ov.start()
    ov.sync(2)
    return ov


def _win(platform) -> list[tuple]:  # type: ignore[no-untyped-def]
    """The window port's calls as (method, *args, *keyword values)."""
    return [(name, *args, *kwargs.values()) for name, args, kwargs in platform.windows.calls]


def _on_cat(ov: CatOverlay) -> tuple[int, int]:
    """A physical point on an opaque pixel of the cat."""
    s = ov.scale
    for ay in range(31, 0, -1):
        for ax in range(32):
            if ov.frame.hit(ax, ay, 0):
                return int(round(ov.brain.x)) - 16 * s + ax * s, int(round(ov.brain.y)) - 32 * s + ay * s
    raise AssertionError("no opaque pixel")


def _ev(kind: QEvent.Type, button: Qt.MouseButton = L) -> QMouseEvent:
    return QMouseEvent(kind, QPointF(1, 1), QPointF(1, 1), button,
                       Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button,
                       Qt.KeyboardModifier.NoModifier)


def _signals(ov: CatOverlay) -> list[tuple]:
    got: list[tuple] = []
    ov.clicked.connect(lambda: got.append(("clicked",)))
    ov.double_clicked.connect(lambda: got.append(("double",)))
    ov.drag_started.connect(lambda: got.append(("drag_started",)))
    ov.dragged.connect(lambda x, y: got.append(("dragged", x, y)))
    ov.dropped.connect(lambda vx, vy: got.append(("dropped",)))
    ov.menu_requested.connect(lambda: got.append(("menu",)))
    ov.petting.connect(lambda dt: got.append(("petting",)))
    return got


def test_start_makes_a_click_through_tool_window(fake_platform, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    hwnd = overlay.hwnd()
    assert hwnd == int(overlay.winId()) != 0 and overlay.isVisible()
    calls = _win(fake_platform)
    assert ("make_tool_window", hwnd) in calls and ("set_click_through", hwnd, True) in calls
    assert overlay._poll.isActive()


def test_canvas_and_cat_rect_follow_the_anchor(overlay: CatOverlay, tuning) -> None:  # type: ignore[no-untyped-def]
    s = overlay.scale
    assert overlay.canvas_origin() == (600 - tuning.anchor_x * s, 700 - tuning.anchor_y * s)
    assert overlay.cat_rect() == (600 - 16 * s, 700 - 32 * s, 600 + 16 * s, 700)


def test_sync_moves_the_window_only_when_it_changes(fake_platform, overlay: CatOverlay, tuning) -> None:  # type: ignore[no-untyped-def]
    moves = lambda: [c for c in _win(fake_platform) if c[0] == "move"]  # noqa: E731
    first = moves()
    assert first[-1] == ("move", overlay.hwnd(), *overlay.canvas_origin(), tuning.canvas_w * 2,
                         tuning.canvas_h * 2, True)
    overlay.sync(2)
    assert moves() == first
    overlay.brain.x += 10
    overlay.raise_on_move = False
    overlay.sync(3)
    assert moves()[-1] == ("move", overlay.hwnd(), *overlay.canvas_origin(), tuning.canvas_w * 3,
                           tuning.canvas_h * 3, False)
    assert overlay.frame is not None and overlay.scale == 3


def test_hit_only_on_the_cats_pixels_and_only_while_shown(overlay: CatOverlay) -> None:
    x, y = _on_cat(overlay)
    assert overlay.hit(x, y)
    assert not overlay.hit(x + 2000, y)
    overlay.hide()
    assert not overlay.hit(x, y)


def test_cursor_poll_turns_click_through_off_over_the_cat(desktop, fake_platform, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    hwnd = overlay.hwnd()
    desktop.cursor = _on_cat(overlay)
    overlay._poll_cursor()
    assert overlay.cursor_over and _win(fake_platform)[-1] == ("set_click_through", hwnd, False)
    desktop.cursor = (5000, 5000)
    overlay._poll_cursor()
    assert not overlay.cursor_over and _win(fake_platform)[-1] == ("set_click_through", hwnd, True)
    n = len(fake_platform.windows.calls)
    overlay._poll_cursor()
    assert len(fake_platform.windows.calls) == n   # (only changes are sent)


def test_passthrough_keeps_the_window_click_through(desktop, fake_platform, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    fake_platform.input.returns("passthrough_active", True)
    desktop.cursor = _on_cat(overlay)
    overlay._poll_cursor()
    assert overlay.cursor_over and ("set_click_through", overlay.hwnd(), False) not in _win(fake_platform)


def test_stroking_the_cat_with_the_pointer_pets_it(desktop, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    got = _signals(overlay)
    x, y = _on_cat(overlay)
    for i in range(80):
        desktop.cursor = (x + (i % 2) * 2, y)
        overlay._poll_cursor()
    assert ("petting",) in got


def test_click(desktop, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    got = _signals(overlay)
    desktop.cursor = _on_cat(overlay)
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    assert overlay.is_pressing()
    overlay.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease))
    assert got == [("clicked",)] and not overlay.is_pressing()


def test_a_press_next_to_the_cat_does_nothing(desktop, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    got = _signals(overlay)
    desktop.cursor = (5000, 5000)
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    overlay.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease))
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, R))
    assert got == []


def test_double_click_counts_on_release(desktop, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    got = _signals(overlay)
    desktop.cursor = _on_cat(overlay)
    overlay.mouseDoubleClickEvent(_ev(QEvent.Type.MouseButtonDblClick))
    assert got == []
    overlay.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease))
    assert got == [("double",)]


def test_drag_and_throw(desktop, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    got = _signals(overlay)
    x, y = desktop.cursor = _on_cat(overlay)
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    desktop.cursor = (x + 2, y)
    overlay.mouseMoveEvent(_ev(QEvent.Type.MouseMove))
    assert got == []                                   # (under the drag threshold)
    desktop.cursor = (x + 40, y + 10)
    overlay.mouseMoveEvent(_ev(QEvent.Type.MouseMove))
    overlay.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease))
    assert got == [("drag_started",), ("dragged", x + 40, y + 10), ("dropped",)]


def test_right_click_opens_the_menu_or_drops_a_carried_cat(desktop, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    got = _signals(overlay)
    x, y = desktop.cursor = _on_cat(overlay)
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, R))
    assert got == [("menu",)]
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    desktop.cursor = (x + 50, y)
    overlay.mouseMoveEvent(_ev(QEvent.Type.MouseMove))
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, R))
    assert got[-1] == ("dropped",) and not overlay.is_pressing()


def test_holding_the_button_pets_instead_of_clicking(desktop, overlay: CatOverlay, tuning) -> None:  # type: ignore[no-untyped-def]
    got = _signals(overlay)
    desktop.cursor = _on_cat(overlay)
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    overlay._press_t = time.monotonic() - tuning.pet_hold_s - 0.1
    overlay._poll_cursor()
    overlay.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease))
    assert ("petting",) in got and ("clicked",) not in got


def test_a_release_that_went_elsewhere_ends_the_drag(desktop, fake_platform, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    got = _signals(overlay)
    x, y = desktop.cursor = _on_cat(overlay)
    overlay.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    desktop.cursor = (x + 50, y)
    overlay.mouseMoveEvent(_ev(QEvent.Type.MouseMove))
    fake_platform.input.answers("release_went_elsewhere", lambda hwnd: hwnd == overlay.hwnd())
    overlay._poll_cursor()
    assert overlay.is_pressing()                       # (one poll could be a glitch)
    overlay._poll_cursor()
    assert not overlay.is_pressing() and got[-1] == ("dropped",)


def test_reshow_restores_the_window_styles(fake_platform, overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    overlay.hide()
    fake_platform.windows.calls.clear()
    overlay.reshow()
    assert overlay.isVisible()
    assert _win(fake_platform) == [("make_tool_window", overlay.hwnd()), ("set_click_through", overlay.hwnd(), True)]
    assert overlay._geom is None


def test_a_hat_is_drawn_into_the_frames(overlay: CatOverlay, sprites) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.sprites import hats

    key = next(iter(hats.HATS))
    overlay.hat = key
    assert overlay.hat == key == sprites.worn and overlay.frame is None
    overlay.sync(2)
    assert overlay.frame is not None


def _opaque(img) -> int:  # type: ignore[no-untyped-def]
    return sum(1 for y in range(0, img.height(), 2) for x in range(0, img.width(), 2) if img.pixelColor(x, y).alpha())


def test_paints_the_cat_and_its_effects(overlay: CatOverlay, brain) -> None:  # type: ignore[no-untyped-def]
    plain = _opaque(overlay.grab().toImage())
    assert plain > 0
    brain.show_bubble("bubble_heart", 2.0)
    brain.emit("heart", 0, -30, 0, 0, 1.0)
    brain.fish_t = 3.0
    brain.yarn = Yarn()
    busy = _opaque(overlay.grab().toImage())
    assert busy > plain


def test_paints_full_size_on_a_retina_screen_when_qt_is_logical(overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.ui import geometry

    plain = _opaque(overlay.grab().toImage())
    overlay.devicePixelRatioF = lambda: 2.0          # a Retina screen (macOS reports dpr 2 even unscaled)
    halved = _opaque(overlay.grab().toImage())        # physical pixels (Windows): the painter undoes the 2
    geometry.set_qt_logical(True)                     # macOS: the platform's points are Qt's, Cocoa scales
    assert _opaque(overlay.grab().toImage()) == plain > halved


def test_stays_shown_on_macos_while_another_app_is_active(overlay: CatOverlay) -> None:  # type: ignore[no-untyped-def]
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
