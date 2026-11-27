"""Every registered game, played start to end by a simulated player (ported from
tools/games_test.py): with windows and without, several seeds."""
from __future__ import annotations

from typing import Any, Callable

import pytest
from gamekit import DT, PlayResult, World, default_windows, play

from pixelcat.engine.states import S
from pixelcat.games import GAMES

SEEDS = (100, 101)
PLAYS: dict[tuple[str, int, bool], PlayResult] = {}

# what each game is about - it must have happened in every play
ACHIEVED: dict[str, Callable[[Any, PlayResult], bool]] = {
    "laser": lambda g, r: g.pounces > 0,
    "treats": lambda g, r: g.caught > 0 and g.left == 0,
    "knock": lambda g, r: g.saved > 0 and g.finished,
    "mice": lambda g, r: g.you > 0 and g.cat > 0,
    "hide": lambda g, r: len(g.times) == 5 and r.stats["behind"] > 0,
    "fishing": lambda g, r: g.casts == 6 and bool(g.caught) and len(r.world.host.ctx["fish_book"]) > 0,
    "boxes": lambda g, r: g.fits > 0 and g.tower >= 2,
}
FURNITURE_WHEN_NO_WINDOWS = {"laser", "knock", "mice", "hide"}


def played(key: str, seed: int, with_windows: bool) -> PlayResult:
    k = (key, seed, with_windows)
    if k not in PLAYS:
        stop = 90.0 if key == "boxes" else None          # a sandbox: the player stops it
        PLAYS[k] = play(key, seed, default_windows() if with_windows else [], stop_after=stop)
    return PLAYS[k]


def test_every_game_has_achievement_checks() -> None:
    assert set(ACHIEVED) == set(GAMES)


@pytest.mark.parametrize("with_windows", [True, False], ids=["windows", "no-windows"])
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("key", sorted(GAMES))
def test_a_whole_game(key: str, seed: int, with_windows: bool) -> None:
    r = played(key, seed, with_windows)
    g, w = r.game, r.world
    assert r.errors == []
    assert w.ended == [g]
    assert not g.active
    assert g.finished is (key != "boxes")
    if not (key == "laser" and with_windows):  # (the simulated pointer often parks the dot out of reach)
        assert ACHIEVED[key](g, r)
    assert 15 <= g.xp() <= 85
    # afterwards nothing is left on screen and the cat is back to normal
    assert w.host.sprites == [] and w.host.props == [] and w.host.cat_under is None
    assert w.host.hud_lines == ("", "")
    assert w.brain.state != S.GAME and w.brain.mode != "fixed"
    assert not [s for s in w.brain.smap.tops if s.hwnd < 0]
    if not with_windows and key in FURNITURE_WHEN_NO_WINDOWS:
        assert r.props_at_start >= 2


@pytest.mark.parametrize("key", sorted(GAMES))
def test_games_are_deterministic_for_a_seed(key: str) -> None:
    a = played(key, SEEDS[0], True)
    b = play(key, SEEDS[0], default_windows(), stop_after=90.0 if key == "boxes" else None)
    assert (a.seconds, a.game.score, a.game.result, a.game.xp()) == (b.seconds, b.game.score, b.game.result,
                                                                     b.game.xp())


@pytest.mark.parametrize("key", sorted(GAMES))
def test_stopping_early_cleans_up(key: str, world: World) -> None:
    g = world.start(key, seed=3)
    assert g.active and world.brain.game is g
    assert world.host.hud_lines[0] == g.title
    world.run(g, 3.0, cursor=(900, 1400))
    g.stop()
    assert not g.active and not g.finished and world.ended == [g]
    assert world.host.sprites == [] and world.host.props == [] and world.host.cat_under is None
    assert world.brain.mode != "fixed" and world.brain.state != S.GAME
    assert not g.cat_z_locked
    for _ in range(int(2 / DT)):
        world.brain.update(DT, world.ctx())
    assert world.brain.mode == "ground"


@pytest.mark.parametrize("key, xp", [("laser", 15), ("treats", 15), ("knock", 15), ("mice", 15),
                                     ("fishing", 10), ("hide", 15), ("boxes", 21)])
def test_xp_for_doing_nothing(key: str, xp: int, world: World) -> None:
    g = world.start(key)
    assert g.xp() == xp
