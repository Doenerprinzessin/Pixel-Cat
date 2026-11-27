"""ui.ball: the ball game's window - the pixel ball, following the ball, grab/throw, painting."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent

from pixelcat.desktop.monitors import Monitor
from pixelcat.engine.ballgame import BallGame, Fx
from pixelcat.engine.brain import Brain
from pixelcat.engine.needs import Needs
from pixelcat.engine.surfaces import SurfaceMap
from pixelcat.ui import ball as ball_mod
from pixelcat.ui.ball import SIZE_ART, BallOverlay, ball_image

pytestmark = pytest.mark.qt
L = Qt.MouseButton.LeftButton
MONITOR = Monitor(1, (0, 0, 1920, 1080), (0, 0, 1920, 1040), 96, True, "TEST1")


@pytest.mark.parametrize("style", ["yarn", "rainbow", "gold"])
@pytest.mark.parametrize("frame", [0, 5])
def test_ball_image_is_a_13px_outlined_ball(qapp, style: str, frame: int) -> None:  # type: ignore[no-untyped-def]
    img = ball_image(style, frame, 120)
    assert (img.width(), img.height()) == (13, 13)
    assert img.pixelColor(0, 0).alpha() == 0 and img.pixelColor(12, 12).alpha() == 0
    assert img.pixelColor(6, 6).alpha() == 255
    assert img.pixelColor(6, 1) == ball_mod.OUTLINE          # the 1px outline on top
    assert img.pixelColor(4, 3).alpha() == 255               # the highlight


def test_ball_styles_differ_and_rainbow_follows_the_hue(qapp) -> None:  # type: ignore[no-untyped-def]
    yarn, gold = ball_image("yarn", 0, 0), ball_image("gold", 0, 0)
    assert yarn != gold
    assert ball_image("rainbow", 0, 0) != ball_image("rainbow", 0, 180)
    assert ball_image("yarn", 0, 0) != ball_image("yarn", 4, 0)       # the threads turn


def test_hsv_helper(qapp) -> None:  # type: ignore[no-untyped-def]
    assert ball_mod._hsv(0, 1.0, 1.0) == QColor(255, 0, 0)
    assert ball_mod._hsv(480, 1.0, 1.0) == ball_mod._hsv(120, 1.0, 1.0)


@pytest.fixture
def game(tuning):  # type: ignore[no-untyped-def]
    b = Brain(tuning, Needs())
    b.rng.seed(3)
    b.scale = 2
    smap = SurfaceMap.build([MONITOR], [], lambda m: 2, tuning.min_surface_len)
    b.set_world(smap, {})
    b.place(800, 1039)
    b.update(0.5, _ctx())
    g = BallGame(b)
    g.rng.seed(3)
    b.game = g
    return g


def _ctx():  # type: ignore[no-untyped-def]
    from pixelcat.engine.brain import Ctx

    return Ctx(cursor=(5000, 5000), busy=False, user_idle=0.0, hour=12, quiet=False, scale=2, cursor_over_cat=False)


@pytest.fixture
def ballwin(desktop, game, sprites, tuning, track):  # type: ignore[no-untyped-def]
    return track(BallOverlay(game, sprites, tuning))


def _ev(kind: QEvent.Type) -> QMouseEvent:
    return QMouseEvent(kind, QPointF(1, 1), QPointF(1, 1), L,
                       Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else L,
                       Qt.KeyboardModifier.NoModifier)


def test_hidden_without_a_game(desktop, ballwin: BallOverlay) -> None:  # type: ignore[no-untyped-def]
    assert not ballwin.wanted()
    ballwin.sync(2)
    assert not ballwin.isVisible() and desktop.calls == []


def test_a_game_shows_the_window_centred_on_the_ball(desktop, ballwin: BallOverlay, game) -> None:  # type: ignore[no-untyped-def]
    game.start()
    assert game.active and ballwin.wanted()
    ballwin.sync(2)
    hwnd = ballwin._hwnd
    assert ballwin.isVisible() and hwnd
    w = SIZE_ART * 2
    x, y = int(round(game.ball.x)) - w // 2, int(round(game.ball.y)) - w // 2
    assert desktop.calls[:2] == [("make_tool_noactivate", hwnd), ("set_click_through", hwnd, True)]
    assert desktop.calls[-1] == ("move_window", hwnd, x, y, w, w, True)
    ballwin.keep_on_top()
    assert desktop.calls[-1] == ("keep_topmost", hwnd)


def test_the_window_hides_after_the_game_and_comes_back(desktop, ballwin: BallOverlay, game) -> None:  # type: ignore[no-untyped-def]
    game.start()
    ballwin.sync(2)
    game.stop(poof=False)
    game.fx.clear()
    ballwin.sync(2)
    assert not ballwin.isVisible() and not ballwin._poll.isActive()
    desktop.calls.clear()
    game.start()
    ballwin.sync(2)
    assert ballwin.isVisible() and desktop.names()[:2] == ["make_tool_noactivate", "set_click_through"]


def test_grab_drag_and_throw(desktop, ballwin: BallOverlay, game) -> None:  # type: ignore[no-untyped-def]
    game.start()
    ballwin.sync(2)
    bx, by = int(game.ball.x), int(game.ball.y)
    desktop.cursor = (bx + 5000, by)
    assert not ballwin.hit(*desktop.cursor)
    ballwin.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    assert not game.ball.held
    desktop.cursor = (bx, by)
    assert ballwin.hit(bx, by)
    ballwin.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    assert game.ball.held and ballwin._pressing
    desktop.cursor = (bx + 30, by - 20)
    ballwin.mouseMoveEvent(_ev(QEvent.Type.MouseMove))
    assert (game.ball.x, game.ball.y) == (bx + 30, by - 20)
    ballwin.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease))
    assert not game.ball.held and not ballwin._pressing
    assert ballwin.cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_poll_makes_the_ball_clickable_under_the_pointer(desktop, ballwin: BallOverlay, game) -> None:  # type: ignore[no-untyped-def]
    game.start()
    ballwin.sync(2)
    desktop.cursor = (int(game.ball.x), int(game.ball.y))
    ballwin._poll_cursor()
    assert desktop.calls[-1] == ("set_click_through", ballwin._hwnd, False)
    assert ballwin.cursor().shape() == Qt.CursorShape.OpenHandCursor
    desktop.cursor = (9000, 9000)
    ballwin._poll_cursor()
    assert desktop.calls[-1] == ("set_click_through", ballwin._hwnd, True)


def test_a_lost_release_drops_the_ball(desktop, ballwin: BallOverlay, game, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.desktop import win32

    game.start()
    ballwin.sync(2)
    desktop.cursor = (int(game.ball.x), int(game.ball.y))
    ballwin.mousePressEvent(_ev(QEvent.Type.MouseButtonPress))
    monkeypatch.setattr(win32, "release_went_elsewhere", lambda hwnd: True)
    ballwin._poll_cursor()
    ballwin._poll_cursor()
    assert not game.ball.held and (game.ball.vx, game.ball.vy) == (0.0, 0.0)


def _opaque(img) -> int:  # type: ignore[no-untyped-def]
    return sum(1 for y in range(0, img.height(), 2) for x in range(0, img.width(), 2) if img.pixelColor(x, y).alpha())


def test_paints_the_ball_trail_effects_and_banner(ballwin: BallOverlay, game) -> None:  # type: ignore[no-untyped-def]
    game.start()
    game.fx.clear()
    ballwin.sync(2)
    ball_only = _opaque(ballwin.grab().toImage())
    assert ball_only > 0
    game.round = 11                                         # rainbow, glow, gold...
    game.ball.squash, game.ball.shake = 0.5, 0.2
    bx, by = game.ball.x, game.ball.y
    game.trail = [(bx - 20, by, 0.1, 30), (bx - 40, by, 0.3, 60)]
    game.fx = [Fx(k, bx + 10 * i - 30, by - 20, life=1.0, max_life=1.0)
               for i, k in enumerate(("star", "heart", "spark", "confetti", "poof", "dust", "dot"))]
    game.banner = (["ROUND 11", "NEW RECORD!"], 0.05, 2.0)
    busy = _opaque(ballwin.grab().toImage())
    assert busy > ball_only
    assert len(ballwin._text) == 2 and ballwin._cache


def test_ball_pixmaps_are_cached_and_the_cache_is_bounded(ballwin: BallOverlay) -> None:
    a = ballwin._ball_pm("yarn", 0, 0)
    assert ballwin._ball_pm("yarn", 0, 0) is a
    for hue in range(0, 360 * 30, 15):
        ballwin._ball_pm("rainbow", hue % 16, hue)
    assert len(ballwin._cache) <= 401
