"""Knock it off!: things on windows, the cat pushes them off, the basket (pointer) catches them."""
from __future__ import annotations

import pytest
from gamekit import SCALE, World, build_world, low_windows, make_window

from pixelcat.games import art
from pixelcat.games.knock import KnockGame, Thing

DT = 1 / 60


def start(w: World, seed: int = 1) -> KnockGame:
    g = w.start("knock", seed)
    assert isinstance(g, KnockGame)
    return g


def drop(g: KnockGame, th: Thing, x: float, y: float) -> None:
    th.state, th.vx, th.vy = "fall", 0.0, 0.0
    th.sprite.move(x, y)


def fall(g: KnockGame, th: Thing, cursor: tuple[int, int], max_ticks: int = 240) -> None:
    for _ in range(max_ticks):
        g.update(DT, cursor)
        if th not in g.things or not g.active:
            return


@pytest.fixture
def low() -> World:
    return build_world(windows=low_windows())


def test_setup_puts_things_on_the_windows(low: World) -> None:
    g = start(low)
    assert (g.lives, g.saved, g.speed, g.phase) == (3, 0, 1.0, "pick")
    assert g.basket.art == "basket"
    assert len(g.things) == 3 and low.host.props == []
    for th in g.things:
        surf = low.brain.smap.tops_of(th.hwnd)[0]
        assert th.kind in art.ITEMS and th.sprite.art == th.kind and th.state == "rest"
        assert th.sprite.anchor == "bottom" and th.sprite.y == surf.y
        assert th.off == pytest.approx(th.sprite.x - surf.x1)
    for a in g.things:
        for b in g.things:
            if a is not b and a.hwnd == b.hwnd:
                assert abs(a.sprite.x - b.sprite.x) > 30 * SCALE
    assert low.host.hud_lines == ("Knock it off!", "Saved 0 · Lives ♥♥♥ · 90 s")


def test_with_no_windows_things_stand_on_furniture(bare_world: World) -> None:
    g = start(bare_world)
    props = {p.hwnd for p in bare_world.host.props}
    assert len(props) == 2 and g.things
    assert {th.hwnd for th in g.things} <= props


def test_the_basket_is_the_pointer(low: World) -> None:
    g = start(low)
    g.update(DT, (500, 600))
    assert (g.basket.x, g.basket.y) == (500, 600 + 4 * SCALE)


def test_catching_a_falling_thing(low: World) -> None:
    g = start(low)
    th = g.things[0]
    drop(g, th, 1000, 700)
    fall(g, th, (1000, 1000))
    assert (g.saved, g.score, g.lives) == (1, 1, 3)
    assert th.sprite.closed and th not in g.things
    assert world_text(low) == "+1"
    assert low.host.bursts[-1][0] == "star"


def world_text(w: World) -> str:
    return w.host.texts[-1][0]


def test_every_fourth_catch_speeds_the_cat_up(low: World) -> None:
    g = start(low)
    g.saved = 3
    th = g.things[0]
    drop(g, th, 1000, 700)
    fall(g, th, (1000, 1000))
    assert g.saved == 4 and g.speed == pytest.approx(1.18)


def test_speed_is_capped(low: World) -> None:
    g = start(low)
    g.saved, g.speed = 7, 2.5
    th = g.things[0]
    drop(g, th, 1000, 700)
    fall(g, th, (1000, 1000))
    assert g.speed == 2.6


@pytest.mark.parametrize("kind, burst, text", [("mug", "shard", "CRASH!"), ("books", "dust", "BONK")])
def test_a_dropped_thing_smashes(low: World, kind: str, burst: str, text: str) -> None:
    g = start(low)
    th = g.things[0]
    th.kind = kind
    drop(g, th, 2400, 1300)
    fall(g, th, (10, 10))
    assert g.lives == 2 and th.sprite.closed and th not in g.things
    assert low.host.bursts[-1][0] == burst and world_text(low) == text
    assert g.active


def test_three_dropped_and_it_is_over(low: World) -> None:
    g = start(low)
    g.lives, g.saved = 1, 6
    th = g.things[0]
    drop(g, th, 2400, 1300)
    fall(g, th, (10, 10))
    assert g.finished and g.result == "6 saved before 3 got broken!"


def test_time_up(low: World) -> None:
    g = start(low)
    g.saved = 9
    g.t = 89.99
    g.update(DT, (10, 10))
    assert g.finished and g.result == "Time! 9 things saved."


def test_things_ride_along_with_their_window(low: World) -> None:
    g = start(low)
    th = next(t for t in g.things if t.hwnd == 202)
    x0, y0 = th.sprite.x, th.sprite.y
    low.set_windows([low.windows[0], make_window(202, (1300, 1100, 2100, 1540), 1, "Low B")])
    g.update(DT, (10, 10))
    assert th.state == "rest"
    assert th.sprite.x == pytest.approx(x0 - 100) and th.sprite.y == y0 - 100


def test_things_fall_when_their_window_closes(low: World) -> None:
    g = start(low)
    gone = [t for t in g.things if t.hwnd == 202]
    low.set_windows([low.windows[0]])
    g.update(DT, (10, 10))
    assert gone and all(t.state == "fall" for t in gone if t in g.things)


def test_new_things_appear_when_few_are_left(low: World) -> None:
    g = start(low)
    for th in list(g.things):
        th.sprite.close()
    g.things.clear()
    low.brain.mode = "fixed"
    g.update(DT, (10, 10))
    assert len(g.things) == 1 and g.spawn_t == pytest.approx(1.2)


def test_the_cat_walks_up_stares_and_pushes_something_off(low: World) -> None:
    g = start(low, seed=2)
    g.speed = 2.5
    phases: list[str] = []
    looks = []

    def watch() -> bool:
        if not phases or phases[-1] != g.phase:
            phases.append(g.phase)
        if g.phase == "stare":
            looks.append(g.look_target())
        return any(th.state == "fall" for th in g.things)

    assert low.run(g, 30, cursor=(10, 10), until=watch)
    assert phases[:2] == ["walk", "stare"] and "push" in phases
    assert looks and all(lk == (10, 10) for lk in looks)


def push_at(low: World, off: float) -> tuple[KnockGame, Thing]:
    g = start(low)
    th = next(t for t in g.things if t.hwnd == 201)
    th.off = off
    g.target, g.phase, g.phase_t = th, "push", 0.0
    g.update(DT, (10, 10))
    return g, th


def test_a_push_right_at_the_edge_flicks_it_off(low: World) -> None:
    g, th = push_at(low, 698.5)                     # window 201 spans x 300..1000
    assert th.state == "fall" and th.vx == 30 * SCALE and th.vy == -20 * SCALE
    assert g.phase == "proud" and g.anim == "sit_happy"


def test_a_push_over_the_edge_from_further_in_just_drops_it(low: World) -> None:
    # (current behaviour: the thing falls straight down and the cat does not look proud)
    g, th = push_at(low, 690)
    assert th.state == "fall" and (th.vx, th.vy) == (0.0, 0.0)
    assert g.phase in ("walk", "stare")


@pytest.mark.parametrize("saved, xp", [(0, 15), (10, 55), (17, 83), (18, 85), (40, 85)])
def test_xp(low: World, saved: int, xp: int) -> None:
    g = start(low)
    g.saved = saved
    assert g.xp() == xp
