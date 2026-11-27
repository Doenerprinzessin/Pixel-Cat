"""Treat toss: take a treat from the jar, throw it, the cat catches it (streaks, wind, misses)."""
from __future__ import annotations

import pytest
from gamekit import SCALE, World, build_world

from pixelcat.games.treats import TreatGame

DT = 1 / 60


def start(w: World, seed: int = 1) -> TreatGame:
    g = w.start("treats", seed)
    assert isinstance(g, TreatGame)
    return g


def feed_one(w: World, g: TreatGame) -> None:
    """Drop a treat right into the cat's mouth and let it be eaten."""
    mx, my = g.mouth_point()
    g.jar.on_press(g.jar.x, g.jar.y)
    g.jar.on_drag(mx, my)
    g.jar.on_release(0.0, 0.0)
    g.update(DT)


def chew(g: TreatGame) -> None:
    for _ in range(60):
        g.update(DT)


@pytest.mark.parametrize("cat_x, jar_x", [(700, 700 + 2560 * 0.35), (2400, 2400 - 2560 * 0.35)])
def test_the_jar_goes_to_the_side_with_more_room(cat_x: float, jar_x: float) -> None:
    w = build_world(place=(cat_x, 1400))
    g = start(w)
    assert g.jar.x == pytest.approx(jar_x)
    assert g.jar.y == 1552 - 11 * SCALE and g.floor_y == 1552
    assert g.jar.clickable and g.jar.draggable
    assert [p.kind for p in w.host.props] == ["table"]
    assert (g.left, g.caught, g.streak) == (10, 0, 0)


def test_pressing_the_jar_takes_one_treat(world: World) -> None:
    g = start(world)
    g.jar.on_press(100, 200)
    assert g.left == 9 and (g.held.art, g.held.x, g.held.y) == ("treat", 100, 200)
    g.jar.on_press(100, 200)
    assert g.left == 9
    assert g.look_target() == (100, 200)


def test_no_treat_when_the_jar_is_empty(world: World) -> None:
    g = start(world)
    g.left = 0
    g.jar.on_press(1, 1)
    assert g.held is None


def test_drag_moves_the_held_treat_and_release_throws_it(world: World) -> None:
    g = start(world)
    g.jar.on_drag(5, 5)
    g.jar.on_release(1, 1)
    assert g.held is None and g.flying is None
    g.jar.on_press(100, 200)
    sp = g.held
    g.jar.on_drag(300, 400)
    assert (sp.x, sp.y) == (300, 400)
    g.jar.on_release(5000, -5000)
    assert g.flying is sp and g.held is None and g.vel == (2400.0, -2400.0)


def test_catch(world: World) -> None:
    g = start(world)
    hunger = world.brain.needs.hunger
    feed_one(world, g)
    assert (g.caught, g.streak, g.best_streak, g.score) == (1, 1, 1, 1)
    assert g.flying is None and g.chew_t == pytest.approx(0.9)
    assert [s.art for s in world.host.sprites] == ["jar"]
    assert world.host.bursts[-1][0] == "crumb" and world.host.texts[-1][0] == "+1"
    assert world.brain.needs.hunger == hunger
    g.update(DT)
    assert g.anim == "chew"


def test_three_in_a_row_count_double(world: World) -> None:
    g = start(world)
    for _ in range(3):
        feed_one(world, g)
        if g.caught < 3:
            chew(g)
    assert (g.caught, g.streak, g.best_streak, g.score) == (3, 3, 3, 4)
    assert world.host.texts[-1][0] == "+2 STREAK!"
    assert world.host.hud_lines[1].startswith("Treats 7 · Caught 3 · Streak 3 · Wind ")


def test_every_fourth_catch_feeds_the_cat(world: World) -> None:
    g = start(world)
    g.caught = 3
    needs = world.brain.needs
    needs.hunger, needs.mood = 0.3, 0.7
    feed_one(world, g)
    assert needs.hunger == 0.0 and needs.mood == pytest.approx(0.85)


def test_three_catches_send_the_cat_somewhere_harder(world: World) -> None:
    g = start(world)
    g.caught = 2
    feed_one(world, g)
    chew(g)
    assert g.moving and world.brain.plan


def test_a_miss_lands_on_the_floor_and_breaks_the_streak(world: World) -> None:
    g = start(world)
    g.streak = 2
    g.jar.on_press(2000, 1500)
    sp = g.held
    g.jar.on_release(0, 0)
    for _ in range(60):
        g.update(DT)
        if g.flying is None:
            break
    assert g.flying is None and g.streak == 0 and g.caught == 0
    assert sp.y == 1552 - 3 * SCALE and not sp.closed
    assert ("miss", sp.x, sp.y - 15 * SCALE) in world.host.texts
    for _ in range(80):
        g.update(DT)
    assert sp.closed


def test_a_treat_thrown_off_the_screen_lands_at_its_edge(world: World) -> None:
    g = start(world)
    g.jar.on_press(2500, 1000)
    sp = g.held
    g.jar.on_release(2400, 0)
    for _ in range(60):
        g.update(DT)
        if g.flying is None:
            break
    assert sp.x == 2560 - 5


@pytest.mark.parametrize("wind", [-1.0, 1.0])
def test_wind_pushes_the_treat(world: World, wind: float) -> None:
    g = start(world)
    g.wind = wind
    g.jar.on_press(2000, 500)
    sp = g.held
    g.jar.on_release(0, 0)
    for _ in range(20):
        g.update(DT)
    assert (sp.x - 2000) * wind > 0


def test_the_cat_begs_at_the_jar(world: World) -> None:
    g = start(world)
    g.update(DT)
    assert g.anim == "beg_open" and world.brain.facing == 1


def test_game_over_when_the_jar_is_empty_and_nothing_flies(world: World) -> None:
    g = start(world)
    g.left, g.caught = 0, 7
    g.update(DT)
    assert g.finished and g.result == "7 of 10 treats caught!"


@pytest.mark.parametrize("wind, arrows", [(-0.9, "<<<"), (-0.5, "<<"), (0.05, "-"), (0.1, "-"), (0.5, ">>"),
                                          (1.0, ">>>")])
def test_hud_shows_the_wind(world: World, wind: float, arrows: str) -> None:
    g = start(world)
    g.wind = wind
    assert g.hud_text() == f"Treats 10 · Caught 0 · Streak 0 · Wind {arrows}"


@pytest.mark.parametrize("caught, best, xp", [(0, 0, 15), (1, 1, 22), (10, 10, 80), (5, 7, 54)])
def test_xp(world: World, caught: int, best: int, xp: int) -> None:
    g = start(world)
    g.caught, g.best_streak = caught, best
    assert g.xp() == xp
