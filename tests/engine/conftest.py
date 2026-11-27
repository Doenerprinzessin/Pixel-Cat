"""Fixtures for the engine tests: fake monitors / windows and ready-made brains.

``Monitor`` and ``WinInfo`` are imported (and constructed) only here, so moving them
to another package only needs the import line below to change.
"""
from __future__ import annotations

from typing import Callable

import pytest

from pixelcat.domain import Monitor, WinInfo, monitor_at
from pixelcat.engine.brain import Brain, Ctx
from pixelcat.engine.needs import Needs
from pixelcat.engine.surfaces import SurfaceMap
from pixelcat.settings import Tuning

Rect = tuple[int, int, int, int]


def make_monitor(rect: Rect, work: Rect | None = None, dpi: int = 96, primary: bool = False,
                 handle: int = 1, device: str = "M") -> Monitor:
    return Monitor(handle, rect, work if work is not None else rect, dpi, primary, device)


def make_window(hwnd: int, rect: Rect, z: int, title: str = "W", cls: str = "X",
                maximized: bool = False, topmost: bool = False) -> WinInfo:
    return WinInfo(hwnd, rect, z, title, cls, maximized, topmost)


def scale3(_m: Monitor) -> int:
    return 3


def scale_by_dpi(m: Monitor) -> int:
    return max(1, round(3 * m.dpi / 96))


def sim_monitors() -> list[Monitor]:
    """The layout of tools/sim_test.py: a 150 % primary and an offset 100 % secondary."""
    return [make_monitor((0, 0, 2560, 1440), (0, 0, 2560, 1392), 144, True, 1, "M1"),
            make_monitor((2560, 200, 4480, 1280), (2560, 200, 4480, 1240), 96, False, 2, "M2")]


def sim_windows(t: float) -> list[WinInfo]:
    """A few windows; one moves slowly, one opens/closes every 90 s."""
    wins = [
        make_window(101, (300, 500, 1300, 1300), 1, "Editor"),
        make_window(102, (1100, 300, 1900, 900), 0, "Browser"),
        make_window(103, (2700 + int((t * 20) % 600), 600, 3500 + int((t * 20) % 600), 1100), 2, "Mover"),
    ]
    if int(t / 90) % 2 == 0:
        wins.append(make_window(104, (1700, 1000, 2400, 1380), 3, "Popup"))
    return wins


def ball_monitors() -> list[Monitor]:
    """The layout of tools/ball_test.py."""
    return [make_monitor((0, 0, 2560, 1600), (0, 0, 2560, 1552), 144, True, 1, "M1"),
            make_monitor((2560, 200, 4480, 1280), (2560, 200, 4480, 1240), 96, False, 2, "M2")]


def ball_windows() -> list[WinInfo]:
    return [make_window(101, (300, 500, 1300, 1300), 1, "Editor"),
            make_window(102, (1500, 300, 2300, 900), 0, "Browser")]


def single_monitor() -> list[Monitor]:
    return [make_monitor((0, 0, 1920, 1080), (0, 0, 1920, 1040), 96, True)]


def build_map(monitors: list[Monitor], windows: list[WinInfo], scale_of: Callable[[Monitor], int] = scale3,
              ledges: dict[int, list[tuple[int, int, int]]] | None = None) -> SurfaceMap:
    return SurfaceMap.build(monitors, windows, scale_of, Tuning().min_surface_len, ledges)


def rects(windows: list[WinInfo]) -> dict[int, Rect]:
    return {w.hwnd: w.rect for w in windows}


def ctx(cursor: tuple[int, int] = (-5000, -5000), busy: bool = False, user_idle: float = 0.0, hour: int = 14,
        quiet: bool = False, scale: int = 3, cursor_over_cat: bool = False) -> Ctx:
    return Ctx(cursor, busy, user_idle, hour, quiet, scale, cursor_over_cat)


def new_brain(seed: int = 3, needs: Needs | None = None) -> Brain:
    brain = Brain(Tuning(), needs or Needs(0.9, 0.3, 0.7))
    brain.rng.seed(seed)
    return brain


def settle(brain: Brain, seconds: float = 6.0, c: Ctx | None = None, dt: float = 1 / 30) -> bool:
    """Tick until the cat stands still on something; False if it never did."""
    c = c or ctx(scale=brain.scale)
    for _ in range(int(seconds / dt)):
        brain.update(dt, c)
        if brain.mode == "ground" and not brain.oneshot and not brain.plan:
            return True
    return False


class World:
    """Factories for the test layouts (handed out by the ``world`` fixture)."""

    monitor = staticmethod(make_monitor)
    window = staticmethod(make_window)
    monitor_at = staticmethod(monitor_at)
    scale3 = staticmethod(scale3)
    scale_by_dpi = staticmethod(scale_by_dpi)
    sim_monitors = staticmethod(sim_monitors)
    sim_windows = staticmethod(sim_windows)
    ball_monitors = staticmethod(ball_monitors)
    ball_windows = staticmethod(ball_windows)
    single_monitor = staticmethod(single_monitor)
    build_map = staticmethod(build_map)
    rects = staticmethod(rects)
    ctx = staticmethod(ctx)
    brain = staticmethod(new_brain)
    settle = staticmethod(settle)


@pytest.fixture
def world() -> type[World]:
    return World


@pytest.fixture
def grounded_brain() -> Brain:
    """A seeded, settled cat on the floor of one 1920x1080 monitor (work area to y=1040),
    with one window (hwnd 10) at (600, 500, 1300, 900)."""
    mons = single_monitor()
    wins = [make_window(10, (600, 500, 1300, 900), 0, "Win")]
    brain = new_brain()
    brain.scale = 3
    brain.set_world(build_map(mons, wins), rects(wins))
    brain.place(300, 1000)
    settle(brain)
    assert brain.mode == "ground" and brain.surf is not None and brain.surf.kind == "floor"
    return brain


@pytest.fixture(autouse=True)
def engine_stays_off_the_desktop(monkeypatch: pytest.MonkeyPatch, fake_dll):
    """The engine is pure logic: give the Win32 layer fresh fakes (so nothing real is ever
    reached, on Windows too) and fail if engine code called into it during the test."""
    from pixelcat.desktop import win32

    fakes = {}
    for name in ("user32", "kernel32", "dwmapi", "shell32", "shcore"):
        fakes[name] = fake_dll(name)
        monkeypatch.setattr(win32, name, fakes[name])
    yield
    called = sorted(f"{dll}.{fn}" for dll, d in fakes.items() for fn, f in vars(d).items()
                    if not fn.startswith("_") and getattr(f, "calls", None))
    assert not called, f"engine code called the desktop layer: {called}"
