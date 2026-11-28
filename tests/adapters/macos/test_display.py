"""pixelcat.adapters.macos.display: the screens in Quartz points, the work area flipped from Cocoa, full-screen
games (captured displays, shielding windows), the lock screen."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, display
from pixelcat.domain import FALLBACK_MONITOR, UserState


def _rect(x: float, y: float, w: float, h: float) -> SimpleNamespace:
    return SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=h))


def _screen(number: int, frame: SimpleNamespace, visible: SimpleNamespace, name: str) -> SimpleNamespace:
    return SimpleNamespace(deviceDescription=lambda: {"NSScreenNumber": number}, frame=lambda: frame,
                           visibleFrame=lambda: visible, localizedName=lambda: name)


# Quartz: the built-in display (1) at the origin, an external one (2) right of it and 200 pt higher.
BOUNDS = {1: _rect(0, 0, 1728, 1117), 2: _rect(1728, -200, 1920, 1080)}
# Cocoa (y up from the bottom of the menu bar's screen): menu bar 33 pt on the built-in one, a Dock of 25 pt at the
# bottom of the external one.
SCREENS = [_screen(1, _rect(0, 0, 1728, 1117), _rect(0, 0, 1728, 1084), "Built-in Retina Display"),
           _screen(2, _rect(1728, 237, 1920, 1080), _rect(1728, 262, 1920, 1055), "LG HDR 4K")]


@pytest.fixture
def screens(monkeypatch):  # type: ignore[no-untyped-def]
    state = {"captured": set(), "windows": [], "session": {}, "mirrors": set()}
    q = FakeModule(CGGetActiveDisplayList=lambda n, a, b: (0, (2, 1), 2), CGDisplayMirrorsDisplay=lambda d: 1 if d in state["mirrors"] else 0,
                   CGMainDisplayID=lambda: 1, CGDisplayBounds=lambda d: BOUNDS[d], CGDisplayIsMain=lambda d: d == 1,
                   CGDisplayIsCaptured=lambda d: d in state["captured"],
                   CGWindowListCopyWindowInfo=lambda option, rel: state["windows"],
                   CGSessionCopyCurrentDictionary=lambda: state["session"])
    monkeypatch.setattr(_objc, "Q", q)
    monkeypatch.setattr(_objc, "AK", FakeModule(NSScreen=SimpleNamespace(screens=lambda: SCREENS)))
    monkeypatch.setattr(display, "_last", [])
    return state


def test_monitors_are_quartz_points_with_the_work_area_flipped_from_cocoa(screens):  # type: ignore[no-untyped-def]
    mons = display.MacDisplay().monitors()
    assert [(m.handle, m.rect, m.work, m.dpi, m.primary, m.device) for m in mons] == [
        (1, (0, 0, 1728, 1117), (0, 33, 1728, 1117), 96, True, "Built-in Retina Display"),
        (2, (1728, -200, 3648, 880), (1728, -200, 3648, 855), 96, False, "LG HDR 4K"),
    ]
    assert display.cached_monitors() == mons
    assert display.MacDisplay().virtual_screen() == (0, -200, 3648, 1117)


def test_a_mirroring_display_is_left_out_and_no_screen_means_the_display_bounds(screens, monkeypatch):  # type: ignore[no-untyped-def]
    screens["mirrors"].add(2)
    monkeypatch.setattr(_objc, "AK", FakeModule(NSScreen=SimpleNamespace(screens=lambda: [])))
    [m] = display.MacDisplay().monitors()
    assert (m.rect, m.work, m.device) == ((0, 0, 1728, 1117), (0, 0, 1728, 1117), "Display 1")


def test_without_quartz_there_is_the_fallback_monitor():  # the conftest's stubs fail on use
    assert display.MacDisplay().monitors() == [FALLBACK_MONITOR]
    assert display.MacDisplay().virtual_screen() == FALLBACK_MONITOR.rect
    assert display.MacDisplay().user_state() is UserState.NORMAL
    assert display.MacDisplay().session_locked() is False


def test_a_captured_display_is_a_full_screen_game(screens):  # type: ignore[no-untyped-def]
    assert display.MacDisplay().user_state() is UserState.NORMAL
    screens["captured"].add(2)
    assert display.MacDisplay().user_state() is UserState.FULLSCREEN_APP


def test_a_shielding_window_over_a_display_is_a_full_screen_game(screens):  # type: ignore[no-untyped-def]
    def win(layer: int, x: float, y: float, w: float, h: float) -> dict:
        return {"kCGWindowLayer": layer, "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}
    screens["windows"] = [win(display.SHIELDING_LEVEL, 100, 100, 300, 200), win(25, 0, 0, 1728, 1117)]
    assert display.MacDisplay().user_state() is UserState.NORMAL      # small, or below the shielding level
    screens["windows"].append(win(display.SHIELDING_LEVEL, 1728, -200, 1920, 1080))
    assert display.MacDisplay().user_state() is UserState.FULLSCREEN_APP


def test_the_lock_screen(screens):  # type: ignore[no-untyped-def]
    assert display.MacDisplay().session_locked() is False
    screens["session"] = {"CGSSessionScreenIsLocked": 1}
    assert display.MacDisplay().session_locked() is True


@pytest.mark.macos
def test_the_real_monitors_match_qt_screens(qapp):  # type: ignore[no-untyped-def]
    from PySide6.QtGui import QGuiApplication

    mons = display.MacDisplay().monitors()
    assert mons[0].primary and all(m.dpi == 96 for m in mons)
    if QGuiApplication.platformName() == "cocoa":       # (offscreen Qt makes up its own screen)
        qt = sorted((s.geometry().getCoords(), s.availableGeometry().getCoords()) for s in qapp.screens())
        ours = sorted(((m.rect[0], m.rect[1], m.rect[2] - 1, m.rect[3] - 1),
                       (m.work[0], m.work[1], m.work[2] - 1, m.work[3] - 1)) for m in mons)
        assert ours == qt
    display.MacDisplay().user_state()
    assert display.MacDisplay().session_locked() in (True, False)
