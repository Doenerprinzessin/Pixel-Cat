"""Helpers for the minigame tests: fake monitors/windows, a settled cat and a host.

``Monitor`` and ``WinInfo`` are only constructed here (``make_monitor`` / ``make_window``),
so moving those types only needs this import changed.  Games run against the in-memory
``Host`` from games/base.py; nothing here touches Win32 or Qt.
"""
from __future__ import annotations

import collections
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from pixelcat.desktop.monitors import Monitor
from pixelcat.desktop.windows import WinInfo
from pixelcat.engine.brain import Brain, Ctx
from pixelcat.engine.needs import Needs
from pixelcat.engine.surfaces import SurfaceMap
from pixelcat.games import GAMES
from pixelcat.games.base import Host, MiniGame
from pixelcat.settings import Tuning

DT = 1 / 60
SCALE = 3


def make_monitor(rect: tuple[int, int, int, int] = (0, 0, 2560, 1600),
                 work: tuple[int, int, int, int] | None = None, dpi: int = 144, primary: bool = True,
                 handle: int = 1, device: str = "M1") -> Monitor:
    l, t, r, b = rect
    return Monitor(handle, rect, work or (l, t, r, b - 48), dpi, primary, device)


def make_window(hwnd: int, rect: tuple[int, int, int, int], z: int = 0, title: str = "Win",
                maximized: bool = False, topmost: bool = False, cls: str = "X") -> WinInfo:
    return WinInfo(hwnd, rect, z, title, cls, maximized, topmost)


def default_monitors() -> list[Monitor]:
    return [make_monitor()]


def default_windows() -> list[WinInfo]:
    return [make_window(101, (300, 600, 1200, 1300), 1, "Editor"),
            make_window(102, (1500, 380, 2300, 1000), 0, "Browser")]


def low_windows() -> list[WinInfo]:
    """Two windows low enough for the cat to jump onto (and to put things on)."""
    return [make_window(201, (300, 1250, 1000, 1540), 0, "Low A"),
            make_window(202, (1400, 1200, 2200, 1540), 1, "Low B")]


@dataclass
class World:
    brain: Brain
    host: Host
    monitors: list[Monitor]
    windows: list[WinInfo]
    ended: list[Any] = field(default_factory=list)

    def ctx(self, cursor: tuple[int, int] = (0, 0)) -> Ctx:
        return Ctx(cursor, False, 0, 14, False, SCALE, False)

    def settle(self, seconds: float = 2.5) -> None:
        for _ in range(int(seconds / DT)):
            self.brain.update(DT, self.ctx())

    def start(self, key: str, seed: int = 1) -> MiniGame:
        game = GAMES[key](self.host)
        game.rng.seed(seed)
        self.brain.game = game
        game.start()
        return game

    def step(self, game: MiniGame, cursor: tuple[int, int] | None = None, dt: float = DT) -> None:
        self.brain.update(dt, self.ctx(cursor or game.cursor))
        game.update(dt, cursor)

    def run(self, game: MiniGame, seconds: float, cursor: tuple[int, int] | None = None,
            until: Callable[[], bool] | None = None) -> bool:
        """Advance cat and game together; True as soon as ``until()`` holds."""
        for _ in range(int(seconds / DT)):
            if not game.active:
                return False
            self.step(game, cursor)
            if until is not None and until():
                return True
        return False

    def set_windows(self, windows: list[WinInfo]) -> None:
        """The user moved/closed windows: rebuild the cat's world like the app does."""
        self.windows[:] = windows
        self.host.world_changed()

    def floor_y(self) -> float:
        return self.brain.smap.floor_at(self.brain.x, self.brain.y).y


def build_world(seed: int = 1, windows: list[WinInfo] | None = None, monitors: list[Monitor] | None = None,
                place: tuple[float, float] = (700, 1400), settle: float = 2.5) -> World:
    tun = Tuning()
    brain = Brain(tun, Needs(0.9, 0.3, 0.7))
    brain.rng.seed(seed)
    brain.scale = SCALE
    mons = monitors if monitors is not None else default_monitors()
    wins = list(windows) if windows is not None else default_windows()
    smap = SurfaceMap.build(mons, wins, lambda m: SCALE, tun.min_surface_len)
    brain.set_world(smap, {w.hwnd: w.rect for w in wins})
    brain.place(*place)
    world = World(brain, None, mons, wins)  # type: ignore[arg-type]
    host = Host(brain, lambda: list(world.windows), lambda m: SCALE, tun.min_surface_len)
    host.ctx = {"fish_book": {}}
    host.ended = world.ended.append
    world.host = host
    world.settle(settle)
    return world





# ------------------------------------------------------------------ a simulated player
class SimUser:
    """Plays like a person: a bit late, a bit off (ported from tools/games_test.py).
    Input goes through the sprites' own callbacks, as the Qt host would deliver it."""

    def __init__(self, game: Any, rng: random.Random) -> None:
        self.g = game
        self.rng = rng
        self.t = 0.0
        self.next = 0.5
        self.cur = (1200.0, 900.0)
        self.goal = (1200.0, 900.0)
        self.state = ""
        self.found_after = rng.uniform(2, 14)

    def step(self, dt: float) -> tuple[int, int]:
        self.t += dt
        fn = getattr(self, "_" + self.g.key, None)
        if fn is not None:
            fn(dt)
        return int(self.cur[0]), int(self.cur[1])

    def _towards(self, x: float, y: float, speed: float, dt: float) -> None:
        cx, cy = self.cur
        d = math.hypot(x - cx, y - cy)
        k = min(1.0, speed * dt / max(1.0, d))
        self.cur = (cx + (x - cx) * k, cy + (y - cy) * k)

    def _laser(self, dt: float) -> None:
        g = self.g
        self.next -= dt
        if self.next <= 0:
            self.next = self.rng.uniform(1.0, 3.0)
            sf = self.rng.choice(g.surfaces())
            self.goal = (self.rng.uniform(sf.x1 + 20, sf.x2 - 20), sf.y - self.rng.choice((4, 4, 60)))
        self._towards(*self.goal, 700, dt)

    def _treats(self, dt: float) -> None:
        g = self.g
        if g.held is None and g.flying is None and g.left > 0 and g.chew_t <= 0 and not g.moving:
            self.next -= dt
            if self.next <= 0:
                self.next = self.rng.uniform(0.6, 1.5)
                g.jar.on_press(g.jar.x, g.jar.y)
                mx, my = g.mouth_point()
                x0, y0 = g.jar.x, g.jar.y - 40
                g.jar.on_drag(x0, y0)
                grav = g.brain.t.gravity * g.s
                T = self.rng.uniform(0.7, 1.1)
                vx = (mx - x0) / T - g.wind * 55 * g.s * T / 2
                vy = (my - y0 - 0.5 * grav * T * T) / T
                g.jar.on_release(vx * self.rng.gauss(1.0, 0.06), vy * self.rng.gauss(1.0, 0.05))

    def _knock(self, dt: float) -> None:
        g = self.g
        falling = [th for th in g.things if th.state == "fall"]
        if falling:
            th = max(falling, key=lambda t: t.sprite.y)
            floor = g.brain.smap.floor_at(th.sprite.x).y
            self.goal = (th.sprite.x + th.vx * 0.15 + self.rng.gauss(0, 6), min(floor - 60, th.sprite.y + 120))
            self._towards(*self.goal, 900 if self.rng.random() < 0.85 else 300, dt)

    def _mice(self, dt: float) -> None:
        g = self.g
        self.next -= dt
        if self.next <= 0 and g.mice:
            self.next = self.rng.uniform(0.7, 1.8)
            m = self.rng.choice(g.mice)
            if self.rng.random() < 0.7 and m.sprite.on_click:
                m.sprite.on_click(m.x, m.y)

    def _hide(self, dt: float) -> None:
        g = self.g
        if g.phase == "hidden" and g.t - g.start_t > self.found_after:
            self.found_after = self.rng.uniform(2, 14)
            g.on_cat_click()

    def _fishing(self, dt: float) -> None:
        g = self.g
        if g.phase == "bite":
            if self.state != "bite":
                self.state, self.next = "bite", self.rng.uniform(0.3, 1.0)
            self.next -= dt
            if self.next <= 0:
                g.pond.on_click(g.pond.x, g.pond.y)
        elif g.phase == "reel":
            self.state = "reel"
            zl = 1 + g.zone * 5
            if zl + 1 <= g.pos <= zl + 8 and self.rng.random() < 0.25:
                g.meter.on_click(g.meter.x, g.meter.y)
            elif self.rng.random() < 0.004:
                g.meter.on_click(g.meter.x, g.meter.y)
        else:
            self.state = g.phase

    def _boxes(self, dt: float) -> None:
        g = self.g
        self.next -= dt
        if self.next <= 0 and len(g.boxes) >= 2:
            self.next = self.rng.uniform(3, 7)
            if self.rng.random() < 0.15:
                g.pile.on_click(g.pile.x, g.pile.y)
            bx = self.rng.choice(g.boxes)
            others = [o for o in g.boxes if o is not bx and not o.held and not o.falling and g._free_top(o)]
            if not others or g.inside is bx:
                return
            on = max(others, key=lambda o: g._height(o))
            p = bx.prop
            p.on_press(p.x, p.y)
            p.on_drag(on.prop.x + self.rng.gauss(0, 4), on.prop.top() - 30)
            p.on_release(0.0, 0.0)


@dataclass
class PlayResult:
    game: Any
    world: World
    errors: list[str]
    seconds: float
    props_at_start: int
    stats: collections.Counter


def play(key: str, seed: int, windows: list[WinInfo] | None, limit: float = 200.0,
         stop_after: float | None = None) -> PlayResult:
    """A whole game with a simulated player (``stop_after``: press the HUD's ✕ then)."""
    w = build_world(seed, windows, place=(700 + seed % 900, 1400))
    game = w.start(key, seed)
    props0 = len(w.host.props)
    user = SimUser(game, random.Random(seed))
    stats: collections.Counter = collections.Counter()
    errors: list[str] = []
    t = 0.0
    b = w.brain
    while t < limit and game.active:
        t += DT
        cur = user.step(DT)
        b.update(DT, w.ctx(cur))
        game.update(DT, cur)
        if b.mode == "fixed":
            stats["fixed"] += 1
        if w.host.cat_under:
            stats["behind"] += 1
        if not (math.isfinite(b.x) and math.isfinite(b.y)):
            errors.append("NaN")
            break
        if not (-50 <= b.x <= 2610) or b.y > 1700 or b.y < -300:
            errors.append(f"cat off screen {b.x:.0f},{b.y:.0f} {b.mode}")
            break
        if stop_after is not None and t > stop_after:
            game.stop()
    if game.active:
        errors.append(f"still running after {t:.0f}s")
        game.stop()
    w.settle(3)
    return PlayResult(game, w, errors, t, props0, stats)
