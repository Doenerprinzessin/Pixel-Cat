"""Laser pointer: the dot follows the pointer, the cat stalks and pounces, energy runs out."""
from __future__ import annotations

import pytest
from gamekit import SCALE, World

from pixelcat.games.laser import LaserGame


def start(w: World, seed: int = 1) -> LaserGame:
    g = w.start("laser", seed)
    assert isinstance(g, LaserGame)
    return g


def test_setup(bare_world: World) -> None:
    g = start(bare_world)
    assert (g.dot.art, g.dot.x, g.dot.y) == ("laser0", 0.0, 0.0)
    assert (g.energy, g.pounces, g.state) == (100.0, 0, "hunt")
    assert [p.kind for p in bare_world.host.props] == ["table", "shelf"]
    assert bare_world.host.hud_lines == ("Laser pointer", "Pounces 0 · Energy 100% · 75 s")


def test_dot_follows_the_pointer_and_blinks(world: World) -> None:
    g = start(world)
    arts = set()
    for i in range(10):
        g.update(1 / 60, (1000 + i, 1400))
        assert (g.dot.x, g.dot.y) == (1000 + i, 1400)
        arts.add(g.dot.art)
    assert arts == {"laser0", "laser1"}
    assert g.look_target() == (1009, 1400)


def test_energy_drains_slower_when_the_dot_stands_still(world: World) -> None:
    g = start(world)
    world.brain.mode = "fixed"                     # (the cat stays out of it)
    for i in range(60):
        g.update(1 / 60, (1000 + 10 * (i % 2), 400))
    assert g.energy == pytest.approx(100 - 0.55, abs=0.02)
    for _ in range(600):
        g.update(1 / 60, (1000, 400))
    assert g.energy == pytest.approx(100 - 0.55 * 5 - 0.2 * 6, abs=0.05)


def test_the_cat_gets_tired(world: World) -> None:
    g = start(world)
    g.pounces = 4
    g.energy = 0.001
    g.update(1 / 60, (1000, 400))
    assert g.finished and g.result == "The cat is tired - 4 pounces!"
    assert g.anim == "tired"
    assert world.ended == [g]


def test_time_up_after_75_seconds(world: World) -> None:
    g = start(world)
    world.brain.mode = "fixed"
    g.t = 74.99
    g.update(0.02, (1000, 400))
    assert g.finished and g.result == ""


def test_landing_on_the_dot_is_a_pounce(world: World) -> None:
    g = start(world)
    g.update(1 / 60, (1000, 400))
    px, py = g.paw_point()
    cursor = (int(px), int(py))
    g._was_air = True                               # the cat was mid-jump last tick
    e0 = g.energy
    g.update(1 / 60, cursor)
    assert (g.pounces, g.score) == (1, 1)
    assert g.energy == pytest.approx(e0 - 6 - 0.55 / 60)
    assert ("star", *cursor) in world.host.bursts
    assert ("+1", cursor[0], cursor[1] - 20 * SCALE) in world.host.texts
    assert g.anim == "proud" and g.cool == pytest.approx(0.55 - 1 / 60)
    assert world.host.hud_lines[1].startswith("Pounces 1 · Energy 93%")


def test_landing_away_from_the_dot_is_a_miss(world: World) -> None:
    g = start(world)
    g._was_air = True
    g.update(1 / 60, (2000, 400))
    assert g.pounces == 0 and g.cool == pytest.approx(0.25 - 1 / 60)


def test_dot_close_on_the_cats_floor_makes_it_aim_then_leap(world: World) -> None:
    g = start(world)
    b = world.brain
    target = (int(b.x + 100), int(world.floor_y() - 2))
    g.update(1 / 60, target)
    assert g.state == "aim" and g.anim == "aim" and b.facing == 1
    for _ in range(60):
        g.update(1 / 60, target)
        if b.mode == "air":
            break
    assert b.mode == "air" and b.vx > 0 and g.state == "hunt"


def test_dot_far_away_makes_the_cat_chase(world: World) -> None:
    g = start(world)
    b = world.brain
    g.update(1 / 60, (2200, int(world.floor_y() - 2)))
    assert g.state == "hunt" and b.plan


def test_dot_standing_still_bores_the_cat(world: World) -> None:
    g = start(world)
    world.brain.mode = "fixed"
    for _ in range(int(6.5 * 60)):
        g.update(1 / 60, (1000, 400))
    world.brain.mode = "ground"
    g.cool = 0
    g.update(1 / 60, (1000, 400))
    assert g.anim == "sit_wag"


def test_the_cat_pounces_on_a_wiggling_dot(world: World) -> None:
    g = start(world)
    b = world.brain
    x0, fy = b.x, world.floor_y()
    hit = False
    for i in range(600):
        world.step(g, (int(x0 + 150 + (4 if (i // 3) % 2 else -4)), int(fy - 2)))
        if g.pounces:
            hit = True
            break
    assert hit


@pytest.mark.parametrize("pounces, xp", [(0, 15), (1, 20), (13, 80), (40, 80)])
def test_xp(world: World, pounces: int, xp: int) -> None:
    g = start(world)
    g.pounces = pounces
    assert g.xp() == xp
