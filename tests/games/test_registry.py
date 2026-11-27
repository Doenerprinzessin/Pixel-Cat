"""The game registry (pixelcat.games): menu order, icons, pictures, titles, tutorials."""
from __future__ import annotations

import pytest

from pixelcat import games
from pixelcat.games import GAMES, ICONS, ORDER, PICTURES, art
from pixelcat.games.base import MiniGame

KNOWN_GAMES = {"laser", "treats", "knock", "mice", "fishing", "hide", "boxes"}


def test_the_set_of_games_is_known() -> None:
    # A new or removed game must be added to (or removed from) these tests too.
    assert set(GAMES) == KNOWN_GAMES


def test_menu_order_is_ball_then_every_game_once() -> None:
    assert ORDER[0] == "ball"
    assert len(ORDER) == len(set(ORDER))
    assert set(ORDER) == set(GAMES) | {"ball"}


def test_every_menu_entry_has_an_icon_and_every_game_a_known_picture() -> None:
    assert set(ICONS) == set(ORDER)
    assert set(PICTURES) == set(GAMES)
    for key, pic in PICTURES.items():
        assert art.known(pic), key


@pytest.mark.parametrize("key", sorted(GAMES))
def test_each_game_class_is_registered_under_its_key(key: str) -> None:
    cls = GAMES[key]
    assert issubclass(cls, MiniGame)
    assert cls.key == key
    assert cls.icon == ICONS[key]
    assert cls.title
    assert len(cls.tutorial) == 3 and all(cls.tutorial)


@pytest.mark.parametrize("key", sorted(GAMES))
def test_title_and_tutorial_come_from_the_game(key: str) -> None:
    assert games.title(key) == GAMES[key].title
    assert games.tutorial(key) == GAMES[key].tutorial


def test_play_ball_is_not_a_minigame_but_has_title_and_tutorial() -> None:
    assert "ball" not in GAMES
    assert games.title("ball") == "Play ball"
    assert games.tutorial("ball") == games.BALL_TUTORIAL
    assert len(games.BALL_TUTORIAL) == 3


def test_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        games.title("nope")
    with pytest.raises(KeyError):
        games.tutorial("nope")


@pytest.mark.parametrize("key, duration, lower_is_better", [
    ("laser", 75.0, False), ("treats", 0.0, False), ("knock", 90.0, False), ("mice", 60.0, False),
    ("fishing", 0.0, False), ("hide", 0.0, True), ("boxes", 0.0, False),
])
def test_durations_and_score_direction(key: str, duration: float, lower_is_better: bool) -> None:
    assert GAMES[key].duration == duration
    assert GAMES[key].lower_is_better is lower_is_better
