"""Long seeded simulations of cat life (ported from tools/sim_test.py).

Fake monitors and windows (one moving, one opening and closing), a wandering cursor,
quiet hours, night, random user events.  Invariants: no exceptions, always on screen,
standing exactly on its surface, never stuck in the air, only known animations - and the
cat really jumps, drops, climbs and lands.
"""
from __future__ import annotations

import collections
import math
import random

import pytest

from pixelcat.engine.animations import ANIMS
from pixelcat.engine.states import STATES, S

DT = 1 / 30


def simulate(world, seed: int, minutes: float) -> dict:
    mons = world.sim_monitors()
    brain = world.brain(seed)
    rng = random.Random(seed * 13 + 7)
    smap = world.build_map(mons, world.sim_windows(0), world.scale_by_dpi)
    brain.set_world(smap, world.rects(world.sim_windows(0)))
    brain.place(1000, 1391)
    entered = collections.Counter()
    states = collections.Counter()
    anims: set[str] = set()
    air, max_air, win_t, t = 0.0, 0.0, 0.0, 0.0
    prev_mode = brain.mode
    problems: list[str] = []
    for _ in range(int(minutes * 60 / DT)):
        t += DT
        win_t += DT
        if win_t >= 0.25:
            win_t = 0.0
            wins = world.sim_windows(t)
            brain.set_world(world.build_map(mons, wins, world.scale_by_dpi), world.rects(wins))
        mon = next((m for m in mons if m.contains(brain.x, brain.y - 1)), mons[0])
        c = world.ctx(cursor=(int(1500 + 1200 * math.sin(t / 40)), 900), busy=int(t / 30) % 3 == 0,
                      user_idle=t % 900, hour=int(t / 600) % 24, quiet=1200 < t < 1500,
                      scale=world.scale_by_dpi(mon))
        brain.update(DT, c)
        r = rng.random()
        if r < 0.0005:
            brain.poke()
        elif r < 0.0007:
            brain.double_click()
        elif r < 0.0008:
            brain.feed()
        elif r < 0.0009:
            brain.play()
        elif r < 0.0011:
            brain.command(rng.choice(["sit", "sleep", "come_here", "trick", "zoomies", "groom", "wake", "climb"]))
        elif r < 0.0013:
            brain.pick_up()
            for _ in range(15):
                brain.drag_to(rng.randint(100, 4400), rng.randint(250, 900))
                brain.update(DT, c)
            brain.drop(rng.uniform(-900, 900), rng.uniform(-600, 300))
        elif r < 0.0014:
            brain.set_paused(True)
        elif r < 0.02 and brain.paused:
            brain.set_paused(False)
        if rng.random() < 0.002:
            for _ in range(30):
                brain.petting(DT)
        states[brain.state] += 1
        if brain.mode != prev_mode:
            entered[(prev_mode, brain.mode, brain.vy < 0)] += 1
            prev_mode = brain.mode
        air = air + DT if brain.mode == "air" else 0.0
        max_air = max(max_air, air)
        if not any(m.rect[0] - 200 <= brain.x <= m.rect[2] + 200 and m.rect[1] - 2000 <= brain.y <= m.rect[3] + 5
                   for m in mons):
            problems.append(f"off screen t={t:.1f} x={brain.x:.0f} y={brain.y:.0f} mode={brain.mode}")
            break
        if brain.mode == "ground" and brain.surf is not None and abs(brain.y - brain.surf.y) > 1:
            problems.append(f"not on its surface t={t:.1f} y={brain.y} surf.y={brain.surf.y}")
            break
        if brain.state not in STATES:
            problems.append(f"unknown state {brain.state}")
        name, _ = brain.current_anim()
        anims.add(name)
        brain.pose()
        brain.fps_hint()
    if brain.paused:
        brain.set_paused(False)
    return {"problems": problems, "max_air": max_air, "entered": entered, "states": states, "anims": anims,
            "brain": brain}


@pytest.mark.parametrize("seed", [3, 11, 42])
def test_an_hour_of_cat_life(world, seed):
    r = simulate(world, seed, 60)
    assert r["problems"] == []
    assert r["max_air"] < 8
    assert r["anims"] <= set(ANIMS)
    e = r["entered"]
    jumps = sum(n for (a, b, up), n in e.items() if a == "ground" and b == "air" and up)
    lands = sum(n for (a, b, _), n in e.items() if b == "ground")
    climbs = sum(n for (a, b, _), n in e.items() if b == "climb")
    falls = sum(n for (a, b, up), n in e.items() if a == "ground" and b == "air" and not up)
    assert jumps > 0 and lands > 0 and climbs > 0 and falls > 0, dict(e)
    assert len(r["states"]) >= 10                 # it really lives its life
    assert 0.0 <= r["brain"].needs.energy <= 1.0


def test_quiet_hours_only_use_calm_states(world):
    mons = world.single_monitor()
    wins = [world.window(10, (600, 500, 1300, 900), 0)]
    brain = world.brain(5)
    brain.needs.hunger = 0.9
    brain.set_world(world.build_map(mons, wins), world.rects(wins))
    brain.place(300, 1000)
    world.settle(brain)
    seen = set()
    c = world.ctx(quiet=True, user_idle=10)
    for _ in range(int(30 * 60 / DT)):
        brain.update(DT, c)
        seen.add(brain.state)
    assert all(STATES[s].calm for s in seen), seen
    assert S.SLEEPING in seen or S.SLEEPY in seen or S.LOAFING in seen


def test_riding_a_window_dragged_around_then_closed(world):
    """tools/ride_test.py without real windows: the window moves every frame (ride_window)
    and the app refreshes the surface map four times a second (set_world)."""
    mons = world.single_monitor()
    x0, y0 = 500, 500
    brain = world.brain(9)
    wins = [world.window(10, (x0, y0, x0 + 900, y0 + 400), 0)]
    brain.set_world(world.build_map(mons, wins), world.rects(wins))
    brain.place(x0 + 450, y0 - 40)
    world.settle(brain)
    brain.set_state(S.LOAFING, enter=(), travel=False)
    brain.state_dur = 1e6
    assert brain.surf.hwnd == 10
    dt = 1 / 60
    offsets, modes = [], set()
    rect = wins[0].rect
    for k in range(150):
        dx, dy = int(260 * math.sin(k / 30)), int(-120 * math.sin(k / 45))
        rect = (x0 + dx, y0 + dy, x0 + dx + 900, y0 + dy + 400)
        brain.ride_window(10, rect)
        if k % 15 == 0:
            wins = [world.window(10, rect, 0)]
            brain.set_world(world.build_map(mons, wins), world.rects(wins))
        brain.update(dt, world.ctx())
        offsets.append((brain.x - rect[0], brain.y - rect[1]))
        modes.add(brain.mode)
    assert modes == {"ground"}
    xs, ys = [o[0] for o in offsets], [o[1] for o in offsets]
    assert max(xs) - min(xs) <= 30 and max(ys) - min(ys) <= 30
    y_close = brain.y
    brain.ride_window(10, None)
    brain.set_world(world.build_map(mons, []), {})
    lowest = [brain.y]
    for _ in range(int(6 / dt)):
        brain.update(dt, world.ctx())
        lowest.append(brain.y)
        if brain.mode == "ground":
            break
    assert brain.mode == "ground" and brain.surf.kind == "floor"
    assert min(lowest) >= y_close - 60


def test_a_curious_cat_explores_ledges_inside_windows(world):
    mons = world.single_monitor()
    wins = [world.window(10, (300, 300, 1500, 1000), 0)]
    ledges = {10: [(350, 700, 520), (800, 1400, 640), (400, 900, 780), (1000, 1450, 900)]}
    brain = world.brain(21)
    brain.set_world(world.build_map(mons, wins, ledges=ledges), world.rects(wins))
    brain.place(150, 1000)
    world.settle(brain)
    visited = set()
    c = world.ctx()
    for _ in range(int(20 * 60 / DT)):
        brain.update(DT, c)
        if brain.mode == "ground" and brain.surf is not None:
            visited.add((brain.surf.kind, brain.surf.y))
            assert brain.y == brain.surf.y
        if brain.state == S.SITTING and not brain.plan and brain.state_t > 5:
            brain.set_state(S.CURIOUS)
    assert sum(1 for k, _ in visited if k == "ledge") >= 2, visited
