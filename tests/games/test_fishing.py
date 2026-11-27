"""Fishing: walk to the pond, cast, wait for a bite, click, reel in the green zone, fish book."""
from __future__ import annotations

import pytest
from gamekit import SCALE, World

from pixelcat.games import art
from pixelcat.games.art import SPECIES_BY_KEY
from pixelcat.games.fishing import CASTS, METER_W, Fishing

DT = 1 / 60


def start(w: World, seed: int = 1) -> Fishing:
    g = w.start("fishing", seed)
    assert isinstance(g, Fishing)
    return g


def ticks(g: Fishing, seconds: float) -> None:
    for _ in range(int(round(seconds / DT))):
        g.update(DT)


def to_phase(g: Fishing, phase: str, limit: float = 10.0) -> None:
    for _ in range(int(limit / DT)):
        if g.phase == phase:
            return
        g.update(DT)
    raise AssertionError(f"never reached {phase} (at {g.phase})")


def at_bite(w: World, species: str = "minnow") -> Fishing:
    g = start(w)
    w.brain.plan.clear()
    to_phase(g, "wait")
    to_phase(g, "bite")
    g.species = SPECIES_BY_KEY[species]
    return g


def in_zone(g: Fishing) -> None:
    g.pos = 1 + g.zone * 5 + 4.0


def test_setup_puts_a_pond_next_to_the_cat(world: World) -> None:
    g = start(world)
    pw, ph = art.size("pond")
    px = 700 + pw * SCALE / 2 + 26 * SCALE
    assert (g.pond.art, g.pond.x, g.pond.y) == ("pond", px, 1552 - ph * SCALE / 2 + 2 * SCALE)
    assert g.pond.clickable and g.side == 1
    assert g.stand_x == px - (pw * SCALE / 2 + 10 * SCALE)
    assert (g.phase, g.casts, g.caught) == ("walk", 0, [])
    assert g.book is world.host.ctx["fish_book"]
    assert world.brain.plan
    assert world.host.hud_lines == ("Fishing", "Cast 0/6 · Fish 0 · Book 0/13")
    assert g.look_target() == (g.pond.x, g.pond.y)


def test_the_cat_walks_to_the_pond_and_casts(world: World) -> None:
    g = start(world)
    assert world.run(g, 6, until=lambda: g.phase == "cast")
    assert abs(world.brain.x - g.stand_x) < 4 * SCALE and world.brain.facing == 1
    assert g.casts == 1 and g.anim == "swat"
    l, t, r, _b = g.pond.rect()
    tx, ty = g.target
    assert l + 18 * SCALE <= tx <= r - 18 * SCALE and ty == t + 11 * SCALE


def test_the_bobber_lands_and_the_wait_begins(world: World) -> None:
    g = start(world)
    world.brain.plan.clear()
    g.update(DT)
    assert g.phase == "cast"
    to_phase(g, "wait")
    assert g.bobber.art == "bobber" and (g.bobber.x, g.bobber.y) == g.target
    assert world.host.bursts[-1][0] == "splash"
    assert 1.8 <= g.phase_t + DT <= 6.0 and g.anim == "fish_wait"
    assert g.look_target() == (g.bobber.x, g.bobber.y)


def test_clicking_too_early(world: World) -> None:
    g = start(world)
    world.brain.plan.clear()
    to_phase(g, "wait")
    g.pond.on_click(0, 0)
    assert g.phase == "wait" and world.host.texts[-1][0] == "not yet..."


def test_a_bite(world: World) -> None:
    g = at_bite(world)
    tx, ty = g.target
    assert g.bobber.y == ty + 3 * SCALE
    assert world.brain.bubble.kind == "bubble_exclaim"
    assert g.anim == "fish_tug"


def test_too_slow(world: World) -> None:
    g = at_bite(world)
    bobber = g.bobber
    ticks(g, 1.2)
    assert g.phase == "rest" and g.bobber is None and bobber.closed
    assert "Too slow - it got away!" in [t[0] for t in world.host.texts]
    assert g.anim == "annoyed" and g.caught == []


@pytest.mark.parametrize("species, need", [("minnow", 1), ("boot", 1), ("goldfish", 2), ("salmon", 2),
                                           ("koi", 3), ("golden_koi", 3)])
def test_clicking_a_bite_starts_reeling(world: World, species: str, need: int) -> None:
    g = at_bite(world, species)
    g.pond.on_click(0, 0)
    assert g.phase == "reel" and g.need == need and (g.hits, g.misses) == (0, 0)
    assert g.meter.art == f"meter{g.zone}" and 0 <= g.zone < 8 and g.marker.art == "marker"
    assert g.meter.clickable


def test_the_marker_swings_back_and_forth(world: World) -> None:
    g = at_bite(world, "golden_koi")
    g.pond.on_click(0, 0)
    seen = []
    for _ in range(180):
        g.update(DT)
        seen.append(g.pos)
    assert min(seen) == 1.0 and max(seen) == METER_W - 1.0
    l, _t, _r, _b = g.meter.rect()
    assert g.marker.x == l + (1 + g.pos) * SCALE


def test_reeling_in_a_new_fish(world: World) -> None:
    g = at_bite(world, "minnow")
    bobber = g.bobber
    g.pond.on_click(0, 0)
    meter, marker = g.meter, g.marker
    in_zone(g)
    g.meter.on_click(0, 0)
    assert g.phase == "catch" and g.caught == ["minnow"] and g.score == 1
    assert g.book == {"minnow": 1} and world.host.ctx["_new_minnow"] is True
    assert world.host.texts[-1][0] == "NEW! Minnow *"
    assert meter.closed and marker.closed and bobber.closed and g.meter is None and g.bobber is None
    assert g.fish.art == "fish_minnow!"
    assert g.hud_text() == "Cast 1/6 · Fish 1 · Book 1/13"


def test_a_known_fish_is_not_new(world: World) -> None:
    world.host.ctx["fish_book"]["carp"] = 2
    g = at_bite(world, "carp")
    g.pond.on_click(0, 0)
    for _ in range(2):
        in_zone(g)
        g.meter.on_click(0, 0)
    assert g.book["carp"] == 3 and "_new_carp" not in world.host.ctx
    assert world.host.texts[-1][0] == "Carp **"


def test_a_hit_moves_the_green_zone(world: World) -> None:
    g = at_bite(world, "salmon")
    g.pond.on_click(0, 0)
    in_zone(g)
    g.meter.on_click(0, 0)
    assert g.phase == "reel" and g.hits == 1 and g.meter.art == f"meter{g.zone}"


def test_two_misses_and_it_got_away(world: World) -> None:
    g = at_bite(world, "salmon")
    g.pond.on_click(0, 0)
    meter = g.meter
    g.pos = 1 + ((g.zone + 4) % 8) * 5 + 4.0
    g.meter.on_click(0, 0)
    assert g.misses == 1 and world.host.texts[-1][0] == "oops"
    g.meter.on_click(0, 0)
    assert g.phase == "rest" and meter.closed and g.caught == []
    assert world.host.texts[-1][0] == "It got away!"


@pytest.mark.parametrize("species, anim, fed", [("minnow", "chew", False), ("trout", "chew", True),
                                                ("boot", "annoyed", False)])
def test_the_catch_flies_to_the_cat(world: World, species: str, anim: str, fed: bool) -> None:
    g = at_bite(world, species)
    g.pond.on_click(0, 0)
    for _ in range(g.need):
        in_zone(g)
        g.meter.on_click(0, 0)
    fish = g.fish
    hunger = world.brain.needs.hunger
    ticks(g, 0.85)
    assert fish.closed and g.fish is None and g.phase == "rest" and g.anim == anim
    assert (world.brain.needs.hunger < hunger) is fed
    ticks(g, 1.35)
    assert g.phase == "cast" and g.casts == 2


@pytest.mark.parametrize("caught, book, result", [
    ([], {}, "0 catches - fish book 0/13"),
    (["minnow"], {"minnow": 1}, "1 catch - fish book 1/13"),
    (["minnow", "koi", "koi"], {"minnow": 1, "koi": 2, "boot": 1}, "3 catches - fish book 3/13"),
])
def test_after_six_casts_it_is_over(world: World, caught: list[str], book: dict, result: str) -> None:
    g = start(world)
    g.casts, g.phase, g.phase_t = CASTS, "rest", 0.0
    g.caught = caught
    g.book.update(book)
    g.update(DT)
    assert g.finished and g.result == result


def test_rarer_fish_bite_more_often_in_later_casts(world: World) -> None:
    g = start(world)
    early = []
    for _ in range(3000):
        g._pick_species()
        early.append(g.species.rarity >= 3)
    g.casts = 6
    late = []
    for _ in range(3000):
        g._pick_species()
        late.append(g.species.rarity >= 3)
    assert sum(late) > sum(early)


@pytest.mark.parametrize("caught, new, xp", [
    ([], [], 10), (["minnow", "minnow"], ["minnow"], 20), (["golden_koi"], ["golden_koi"], 29),
    (["boot"], [], 10), (["golden_koi"] * 4, [], 65),
])
def test_xp(world: World, caught: list[str], new: list[str], xp: int) -> None:
    g = start(world)
    g.caught = caught
    for k in new:
        world.host.ctx["_new_" + k] = True
    assert g.xp() == xp


def test_new_fish_flags_stay_in_the_host_ctx_for_later_games(world: World) -> None:
    g = at_bite(world, "minnow")
    g.pond.on_click(0, 0)
    in_zone(g)
    g.meter.on_click(0, 0)
    g.stop()
    again = start(world)
    again.caught = ["minnow"]
    assert again.xp() == 10 + 3 + 4          # (still counted as new)
