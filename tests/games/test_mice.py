"""Mouse hunt: mice run along windows and the floor; you click them, the cat pounces on them."""
from __future__ import annotations

import pytest
from gamekit import SCALE, World, build_world, low_windows

from pixelcat.games.mice import Mouse, MouseHunt

DT = 1 / 60


def start(w: World, seed: int = 1) -> MouseHunt:
    g = w.start("mice", seed)
    assert isinstance(g, MouseHunt)
    return g


def mouse(g: MouseHunt, x: float, y: float, hwnd: int = 0, d: int = 1, gold: bool = False) -> Mouse:
    g._spawn()
    m = g.mice[-1]
    m.x, m.y, m.hwnd, m.dir, m.gold, m.state, m.t = float(x), float(y), hwnd, d, gold, "run", 0.0
    return m


def ticks(g: MouseHunt, n: int) -> None:
    for _ in range(n):
        g.update(DT)


def test_setup(bare_world: World) -> None:
    g = start(bare_world)
    assert g.mice == [] and (g.you, g.cat) == (0, 0)
    assert len(bare_world.host.props) == 2
    assert bare_world.host.hud_lines == ("Mouse hunt", "You 0 : 0 Cat · 60 s")


def test_mice_appear_at_the_end_of_a_surface(world: World) -> None:
    g = start(world)
    world.brain.mode = "fixed"
    ticks(g, 30)
    assert g.mice == []
    ticks(g, 10)
    assert len(g.mice) == 1
    m = g.mice[0]
    assert m.sprite.clickable and m.sprite in world.host.sprites
    assert m.sprite.art.startswith("mouse_run") and m.sprite.art.endswith("" if m.dir > 0 else "!")
    assert 55 * SCALE <= m.speed <= 105 * SCALE and 9 <= m.life <= 14


def test_never_more_than_five_mice(world: World) -> None:
    g = start(world)
    world.brain.mode = "fixed"
    most = 0
    for _ in range(int(40 / DT)):
        g.update(DT)
        most = max(most, len(g.mice))
    assert most == 5


def test_clicking_a_mouse_catches_it(world: World) -> None:
    g = start(world)
    m = mouse(g, 1500, 1552)
    m.sprite.on_click(m.x, m.y)
    assert (g.you, g.score) == (1, 1) and m.gone and m.sprite.closed and m not in g.mice
    assert ("+1", m.x, m.y - 22 * SCALE) in world.host.texts
    assert world.host.bursts[-1][0] == "star"
    m.sprite.on_click(m.x, m.y)
    assert g.you == 1


def test_golden_mice_count_three(world: World) -> None:
    g = start(world)
    m = mouse(g, 1500, 1552, gold=True)
    m.sprite.on_click(m.x, m.y)
    assert g.you == 3 and world.host.texts[-1][0] == "+3"


def test_clicks_after_the_game_do_nothing(world: World) -> None:
    g = start(world)
    m = mouse(g, 1500, 1552)
    g.stop()
    m.sprite.on_click(m.x, m.y)
    assert g.you == 0


@pytest.mark.parametrize("frame, gold, d, key", [("run0", False, 1, "mouse_run0"), ("run1", True, 1, "mouse_run1_gold"),
                                                 ("sniff", False, -1, "mouse_sniff!"),
                                                 ("sniff", True, -1, "mouse_sniff_gold!")])
def test_mouse_art(frame: str, gold: bool, d: int, key: str) -> None:
    assert MouseHunt._art(frame, gold, d) == key


@pytest.mark.parametrize("x, speed, gone", [(2553, 300.0, True), (2552, 120.0, False)])
def test_a_mouse_at_the_screen_edge_runs_into_a_hole_or_turns(world: World, x: float, speed: float,
                                                               gone: bool) -> None:
    g = start(world)
    world.brain.mode = "fixed"
    m = mouse(g, x, 1552, d=1)
    m.speed, m.life = speed, 99.0
    ticks(g, 1 if gone else 2)
    assert m.gone is gone
    if gone:
        assert m not in g.mice and m.sprite.closed
    else:
        assert m.dir == -1 and m.x == 2560 - 3 * SCALE and m.sprite.y == 1552 - 4 * SCALE


def test_an_old_mouse_flees(world: World) -> None:
    g = start(world)
    world.brain.mode = "fixed"
    m = mouse(g, 1500, 1552)
    m.life = 0.0
    ticks(g, 1)
    assert m.state == "flee"


def test_a_mouse_falls_when_its_window_goes_away() -> None:
    w = build_world(windows=low_windows())
    g = start(w)
    w.brain.mode = "fixed"
    m = mouse(g, 1800, 1200, hwnd=202)
    w.set_windows([w.windows[0]])
    ticks(g, 1)
    assert m.state == "fall"
    for _ in range(120):
        g.update(DT)
        if m.state != "fall":
            break
    assert (m.state, m.hwnd, m.y) == ("run", 0, 1552.0)


def test_the_cat_lands_on_a_mouse(world: World) -> None:
    g = start(world)
    b = world.brain
    m = mouse(g, b.x + b.facing * 6 * SCALE, b.y)
    g._was_air = True
    g.update(DT)
    assert (g.cat, g.you, g.score) == (1, 0, 0) and m.gone
    assert world.host.texts[-1][0] == "cat +1" and world.host.bursts[-1][0] == "heart"
    assert g.anim == "sit_happy"


def test_the_cat_watches_when_no_mouse_is_in_reach(world: World) -> None:
    g = start(world)
    g.update(DT)
    assert g.anim == "sit_watch" and g.look_target() is None


def test_the_cat_hunts_the_nearest_mouse(world: World) -> None:
    g = start(world)
    b = world.brain
    near = mouse(g, b.x + 400, 1552)
    mouse(g, b.x + 1500, 1552)
    g.spawn_t = 99
    g.update(DT)
    assert g.target is near and g.look_target() == (near.x, near.y)


@pytest.mark.parametrize("you, cat, result", [(3, 1, "You win 3 : 1!"), (1, 2, "The cat wins 2 : 1!"),
                                              (2, 2, "A draw, 2 : 2!")])
def test_time_up(world: World, you: int, cat: int, result: str) -> None:
    g = start(world)
    g.you, g.cat = you, cat
    world.brain.mode = "fixed"
    g.t = 59.99
    g.update(DT)
    assert g.finished and g.result == result and g.score == you


@pytest.mark.parametrize("you, cat, xp", [(0, 0, 15), (3, 1, 31), (3, 5, 21), (30, 0, 70)])
def test_xp(world: World, you: int, cat: int, xp: int) -> None:
    g = start(world)
    g.you, g.cat = you, cat
    assert g.xp() == xp
