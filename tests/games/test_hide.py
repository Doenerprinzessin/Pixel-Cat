"""Hide and seek: the cat hides behind a window (or furniture), only tail or ears peek out."""
from __future__ import annotations

import pytest
from gamekit import SCALE, World, build_world, make_window

from pixelcat.games.hide import EARS, FURNITURE, ROUNDS, TAIL, HideSeek

DT = 1 / 60


def start(w: World, seed: int = 1) -> HideSeek:
    g = w.start("hide", seed)
    assert isinstance(g, HideSeek)
    return g


def ticks(g: HideSeek, seconds: float) -> None:
    for _ in range(int(round(seconds / DT))):
        g.update(DT)


def hidden(w: World, seed: int = 1) -> HideSeek:
    g = start(w, seed)
    ticks(g, 0.45)
    assert g.phase == "count"
    ticks(g, 1.25)
    assert g.phase == "hidden"
    return g


def test_setup_with_windows(world: World) -> None:
    g = start(world)
    assert (g.round, g.phase, g.furniture) == (0, "start", [])
    assert world.host.props == []
    assert world.host.hud_lines == ("Hide and seek", "Round 1/5 · close your eyes!")


def test_setup_without_windows_brings_furniture(bare_world: World) -> None:
    g = start(bare_world)
    kinds = [p.kind for p in g.furniture]
    assert len(set(kinds)) == 3 and set(kinds) <= set(FURNITURE)
    assert [p.x for p in g.furniture] == [2560 * 0.2, 2560 * 0.5, 2560 * 0.8]
    assert all(p.y == 1552 for p in g.furniture) and bare_world.host.props == g.furniture


def test_hiding_spots_around_windows(world: World) -> None:
    g = start(world)
    spots = {(s["hwnd"], s["side"]) for s in g._spots()}
    assert spots == {(h, side) for h in (101, 102) for side in ("left", "right", "top")}
    assert all(s["real"] for s in g._spots())


@pytest.mark.parametrize("extra, gone", [
    (make_window(103, (100, 600, 280, 1300), 0, "covers the editor's left side"), {(101, "left")}),
    (make_window(104, (1500, 380, 2300, 1000), 0, "Browser", maximized=True), set()),
])
def test_covered_sides_and_maximized_windows_are_no_hiding_spots(extra, gone) -> None:
    w = build_world()
    wins = [x for x in w.windows if x.hwnd != extra.hwnd]
    if extra.maximized:
        wins = [x for x in wins if x.hwnd != 102]
        gone = {(102, s) for s in ("left", "right", "top")}
    w.set_windows(wins + [extra])
    g = start(w)
    spots = {(s["hwnd"], s["side"]) for s in g._spots()}
    every = {(h, side) for h in (101, 102) for side in ("left", "right", "top")}
    assert spots == every - gone


def test_small_windows_are_no_hiding_spots() -> None:
    w = build_world(windows=[make_window(1, (300, 600, 500, 1300)), make_window(2, (800, 600, 1300, 730))])
    g = start(w)
    assert len(g.furniture) == 3
    assert {s["hwnd"] for s in g._spots()} == {p.hwnd for p in g.furniture}


def test_furniture_spots(bare_world: World) -> None:
    g = start(bare_world)
    sides = {}
    for s in g._spots():
        assert not s["real"]
        sides.setdefault(s["prop"].kind, []).append(s["side"])
    expect = {"sofa": ["left", "right", "top"], "bookcase": ["left", "right"], "armchair": ["left", "right"],
              "box_l": ["top"]}
    assert sides == {k: expect[k] for k in sides}


@pytest.mark.parametrize("side, facing", [("right", -1), ("left", 1), ("top", None)])
def test_placing_the_cat_behind_a_spot(world: World, side: str, facing: int | None) -> None:
    g = start(world)
    b = world.brain
    spot = {"rect": (300, 600, 1200, 1300), "side": side}
    g._place(spot, 5.0)
    assert b.mode == "fixed"
    if side == "right":
        assert b.x == 1200 + 5 * SCALE - TAIL * SCALE and 720 <= b.y <= 840 and b.facing == -1
    elif side == "left":
        assert b.x == 300 - 5 * SCALE + TAIL * SCALE and 720 <= b.y <= 840 and b.facing == 1
    else:
        assert 480 <= b.x <= 1020 and b.y == 600 - 5 * SCALE + EARS * SCALE and b.facing in (-1, 1)
    first = (b.x, b.y, b.facing)
    g._place(spot, 5.0)
    assert (b.x, b.y, b.facing) == first


@pytest.mark.parametrize("round_, hint, peek", [(1, 0, 6.5), (2, 0, 5.7), (5, 0, 3.3), (1, 1, 9.0), (9, 0, 3.0)])
def test_how_far_it_peeks_out(world: World, round_: int, hint: int, peek: float) -> None:
    g = start(world)
    g.round, g.hint = round_, hint
    assert g._peek() == pytest.approx(peek)


def test_a_round_starts_with_the_cat_hiding(world: World) -> None:
    g = start(world)
    assert not g.on_cat_click()
    ticks(g, 0.45)
    b = world.brain
    assert g.round == 1 and g.phase == "count" and g.spot is not None
    assert b.mode == "fixed" and world.host.cat_under == g.spot["hwnd"] and g.cat_z_locked
    assert g.anim == "peek" and world.host.bursts[-1][0] == "poof"
    assert g.on_cat_click() and g.times == []


def test_finding_the_cat(world: World) -> None:
    g = hidden(world)
    ticks(g, 2.0)
    assert world.host.hud_lines[1] == "Round 1/5 · 2 s"
    side = g.spot["side"]
    assert g.on_cat_click()
    b = world.brain
    assert len(g.times) == 1 and g.times[0] == pytest.approx(2.0, abs=0.15)
    assert g.phase == "found" and world.host.cat_under is None and not g.cat_z_locked
    assert b.mode == "air" and b.vy == -170 * SCALE
    if side != "top":
        assert b.vx == {"left": -70, "right": 70}[side] * SCALE
    assert world.host.texts[-1][0] == f"{g.times[0]:.1f} s!"
    assert g.hud_text() == f"Round 1/5 · found in {g.times[0]:.1f} s"
    assert not g.on_cat_click()


def test_hints_after_a_while_and_found_after_40_seconds(world: World) -> None:
    g = hidden(world)
    ticks(g, 12.1)
    assert g.hint == 1
    ticks(g, 10.0)
    assert g.hint == 2
    n = len(world.host.texts)
    ticks(g, 5.0)
    mews = [t for t in world.host.texts[n:] if t[0] == "mew!"]
    assert len(mews) == 2
    ticks(g, 13.0)
    # current behaviour: 40 s are recorded, then _found() adds a second time of 0 s
    assert g.times == [40.0, 0.0] and g.phase == "found"


def test_five_rounds_and_it_is_over(world: World) -> None:
    g = start(world)
    g.round, g.times = ROUNDS, [1.0, 2.0, 3.0, 4.0, 5.5]
    g.phase, g.phase_t = "found", 0.0
    g.update(DT)
    assert g.finished and g.result == "Found it 5 times, 3.1 s on average!" and g.score == 3


def test_no_place_to_hide(bare_world: World) -> None:
    g = start(bare_world)
    for p in g.furniture:
        p.close()
    g.furniture = []
    ticks(g, 0.45)
    assert g.finished and g.result == "No place to hide!"


def hidden_behind_a_window(seed: int = 1) -> tuple[World, HideSeek]:
    w = build_world()
    for s in range(seed, seed + 20):
        g = hidden(w, s)
        if g.spot["real"]:
            return w, g
        g.stop()
        w.ended.clear()
    raise AssertionError("never hid behind a window")


def test_the_window_closes(world: World) -> None:
    w, g = hidden_behind_a_window()
    w.set_windows([x for x in w.windows if x.hwnd != g.spot["hwnd"]])
    g.update(DT)
    assert g.phase == "found" and g.times == []
    assert w.host.texts[-1][0] == "my spot vanished!" and w.host.cat_under is None


def test_the_window_gets_maximized(world: World) -> None:
    w, g = hidden_behind_a_window()
    hwnd = g.spot["hwnd"]
    w.set_windows([make_window(x.hwnd, x.rect, x.z, maximized=(x.hwnd == hwnd)) for x in w.windows])
    g.update(DT)
    assert g.phase == "found" and g.times == []


def test_the_cat_moves_along_with_its_window(world: World) -> None:
    w, g = hidden_behind_a_window()
    hwnd = g.spot["hwnd"]
    b = w.brain
    x0, y0 = b.x, b.y
    moved = []
    for x in w.windows:
        r = x.rect
        moved.append(make_window(x.hwnd, (r[0] + 50, r[1] - 20, r[2] + 50, r[3] - 20), x.z) if x.hwnd == hwnd
                     else x)
    w.set_windows(moved)
    g.update(DT)
    assert g.spot["rect"] == next(x.rect for x in moved if x.hwnd == hwnd)
    assert b.x == pytest.approx(x0 + 50) and b.y == pytest.approx(y0 - 20)
    assert w.host.cat_under == hwnd


def test_picking_the_cat_up_counts_as_found(world: World) -> None:
    g = hidden(world)
    ticks(g, 1.0)
    g.held(DT)
    assert len(g.times) == 1 and g.phase == "found" and g.phase_t == 2.0
    assert world.host.cat_under is None and not g.cat_z_locked


def test_stopping_while_hidden_brings_the_cat_back(world: World) -> None:
    g = hidden(world)
    g.stop()
    assert world.host.cat_under is None and not g.cat_z_locked and world.brain.mode == "air"


@pytest.mark.parametrize("times, xp", [([], 15), ([5.0], 27), ([0.0] * 5, 80), ([30.0, 40.0], 15),
                                       ([10.0, 12.5], 29)])
def test_xp(world: World, times: list[float], xp: int) -> None:
    g = start(world)
    g.times = times
    assert g.xp() == xp
