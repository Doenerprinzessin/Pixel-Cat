"""Box fort: boxes to drag, throw and stack; the cat sits in them and climbs the towers."""
from __future__ import annotations

import pytest
from gamekit import SCALE, World

from pixelcat.games.boxes import KINDS, Box, BoxFort

DT = 1 / 60


def start(w: World, seed: int = 1) -> BoxFort:
    g = w.start("boxes", seed)
    assert isinstance(g, BoxFort)
    return g


def settle(g: BoxFort, seconds: float = 2.0) -> None:
    """Only the boxes move (the cat is held out of the way)."""
    g.brain.mode = "fixed"
    for _ in range(int(seconds / DT)):
        g.update(DT)


def stack(g: BoxFort, top: Box, on: Box) -> None:
    p = top.prop
    p.on_press(p.x, p.y)
    p.on_drag(on.prop.x, on.prop.top() - 30)
    p.on_release(0.0, 0.0)
    settle(g, 1.5)


def test_setup(world: World) -> None:
    g = start(world)
    assert g.ends_by_stop
    assert (g.pile.art, g.pile.x, g.pile.y) == ("box_s", 700 + 60 * SCALE, 1552 - 9 * SCALE)
    assert g.pile.clickable
    assert [(b.prop.kind, b.prop.x, b.prop.y) for b in g.boxes] == [
        (KINDS[i], 700 + (100 + 45 * i) * SCALE, 1552 - 140 * SCALE) for i in range(4)]
    assert all(b.prop.draggable and b.falling for b in g.boxes)
    assert world.host.props == [b.prop for b in g.boxes]
    assert world.host.hud_lines == ("Box fort", "Boxes 4 · Fits 0 · Tallest tower 1")


def test_boxes_fall_onto_the_floor_and_become_surfaces(world: World) -> None:
    g = start(world)
    settle(g)
    assert all(not b.falling and b.prop.y == 1552 for b in g.boxes)
    tops = {s.hwnd for s in world.brain.smap.tops}
    wide, narrow = [b.prop.hwnd for b in g.boxes[2:]], [b.prop.hwnd for b in g.boxes[:2]]
    assert set(wide) <= tops and not set(narrow) & tops     # box_m/box_s: narrower than the cat


@pytest.mark.parametrize("dt, dust", [(1 / 60, False), (0.05, True)])
def test_a_hard_landing_raises_dust(world: World, dt: float, dust: bool) -> None:
    # (the dust depends on the per-frame fall distance, so on the frame rate)
    g = start(world)
    g.brain.mode = "fixed"
    for _ in range(int(2 / dt)):
        g.update(dt)
    assert (("dust", g.boxes[0].prop.x, 1552 - 2 * SCALE) in world.host.bursts) is dust


def test_the_pile_gives_more_boxes_up_to_eight(world: World) -> None:
    g = start(world)
    for _ in range(4):
        g.pile.on_click(0, 0)
    assert [b.prop.kind for b in g.boxes] == list(KINDS)
    g.pile.on_click(0, 0)
    assert len(g.boxes) == 8 and world.host.texts[-1][0] == "that's enough boxes!"


def test_dragging_and_throwing_a_box(world: World) -> None:
    g = start(world)
    settle(g)
    bx = g.boxes[2]
    p = bx.prop
    p.on_press(p.x + 5, p.y - 10)
    assert bx.held and not bx.falling and (bx.grab_dx, bx.grab_dy) == (-5, 10)
    p.on_drag(1500, 900)
    assert (p.x, p.y) == (1495, 910)
    assert [s.y for s in world.brain.smap.tops if s.hwnd == p.hwnd] == [int(p.top())]
    p.on_release(2000, -1000)
    assert not bx.held and bx.falling and (bx.vx, bx.vy) == (900.0, -500.0)


def test_a_thrown_box_stays_on_the_screen(world: World) -> None:
    g = start(world)
    settle(g)
    p = g.boxes[3].prop
    p.on_press(p.x, p.y)
    p.on_drag(2400, 1000)
    p.on_release(5000, 0)
    settle(g, 3)
    half = p.size[0] / 2
    # clamped to the monitor while flying, but it slides on a bit after landing
    assert 2560 - half < p.x < 2560 and p.y == 1552


def test_stacking_builds_a_tower(world: World) -> None:
    g = start(world)
    settle(g)
    a, b, c = g.boxes[0], g.boxes[2], g.boxes[1]
    stack(g, a, b)
    assert not a.falling and a.prop.y == b.prop.top()
    assert g._height(a) == 2 and g.tower == 2
    assert not g._free_top(b) and g._free_top(a)
    stack(g, c, a)
    assert g._height(c) == 3 and g.tower == 3
    assert world.host.hud_lines[1] == "Boxes 4 · Fits 0 · Tallest tower 3"


def test_a_box_without_support_falls(world: World) -> None:
    g = start(world)
    settle(g)
    a, b = g.boxes[0], g.boxes[2]
    stack(g, a, b)
    p = b.prop
    p.on_press(p.x, p.y)
    p.on_drag(2200, p.y)
    g.update(DT)
    assert a.falling
    p.on_release(0, 0)
    settle(g)
    assert a.prop.y == 1552


def test_a_pushed_box_slides_and_slows_down(world: World) -> None:
    g = start(world)
    settle(g)
    bx = g.boxes[3]
    x0 = bx.prop.x
    bx.vx = 300.0
    g.update(DT)
    assert bx.prop.x == pytest.approx(x0 + 5) and bx.vx == pytest.approx(300 * (1 - 4 * DT))
    settle(g, 2)
    assert abs(bx.vx) <= 1 and bx.prop.x > x0 + 50


def test_hopping_into_a_box(world: World) -> None:
    g = start(world)
    settle(g)
    world.brain.mode = "ground"
    bx = g.boxes[1]
    g.activity = "fit"
    g._hop_in(bx)
    assert g.inside is bx and (g.fits, g.score) == (1, 1) and g.anim == "box_sit"
    assert world.host.texts[-1][0] == "If I fits, I sits!"
    assert 4 <= g.act_t <= 9
    g.update(DT)
    b = world.brain
    assert b.mode == "fixed" and (b.x, b.y) == (bx.prop.x, bx.prop.y - 2 * SCALE)
    g.activity = "nap"
    g._hop_in(bx)
    assert g.fits == 2 and g.anim == "box_nap"
    assert [t[0] for t in world.host.texts].count("If I fits, I sits!") == 1
    g.update(DT)
    assert b.y == bx.prop.y - 5 * SCALE


def test_the_cat_rides_along_in_a_dragged_box(world: World) -> None:
    g = start(world)
    settle(g)
    bx = g.boxes[0]
    g._hop_in(bx)
    p = bx.prop
    p.on_press(p.x, p.y)
    p.on_drag(1800, 1000)
    g.update(DT)
    assert (world.brain.x, world.brain.y) == (1800, 1000 - 2 * SCALE)


def test_hopping_out_again(world: World) -> None:
    g = start(world)
    settle(g)
    g._hop_in(g.boxes[0])
    g.update(DT)
    g.act_t = 0.0
    g.update(DT)
    b = world.brain
    assert g.inside is None and g.activity == "rest" and g.anim is None
    assert b.mode == "air" and b.vy == -150 * SCALE and abs(b.vx) == 60 * SCALE


def test_picking_the_cat_up_takes_it_out_of_the_box(world: World) -> None:
    g = start(world)
    g._hop_in(g.boxes[0])
    g.held(DT)
    assert g.inside is None and (g.activity, g.goal, g.act_t) == ("rest", None, 2.0)


def test_stopping_takes_the_cat_out(world: World) -> None:
    g = start(world)
    settle(g)
    g._hop_in(g.boxes[0])
    g.update(DT)
    g.stop()
    assert g.inside is None and world.brain.mode == "air" and not g.finished


def test_a_sandbox_never_ends_by_itself(world: World) -> None:
    g = start(world)
    world.run(g, 100)
    assert g.active and not g.finished


@pytest.mark.parametrize("fits, tower, t, xp", [(0, 1, 0, 21), (2, 2, 30, 42), (10, 5, 300, 80), (0, 1, 13, 23)])
def test_xp(world: World, fits: int, tower: int, t: float, xp: int) -> None:
    g = start(world)
    g.fits, g.tower, g.t = fits, tower, t
    assert g.xp() == xp
