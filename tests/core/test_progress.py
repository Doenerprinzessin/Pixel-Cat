"""progress: the Cat Pass - stage costs, XP -> stage, what each stage unlocks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelcat import progress
from pixelcat.sprites import hats

DATA = Path(__file__).parent / "data"


@pytest.mark.parametrize("stage, cost", [(1, 110), (2, 125), (3, 140), (10, 235), (29, 500), (30, 515), (31, 520),
                                         (100, 795), (101, 800), (131, 800), (500, 800)])
def test_stage_cost(stage: int, cost: int) -> None:
    assert progress.stage_cost(stage) == cost


def test_stage_costs_grow_and_are_multiples_of_five() -> None:
    costs = [progress.stage_cost(s) for s in range(1, progress.MAX_STAGE + 1)]
    assert costs == sorted(costs)
    assert all(c % 5 == 0 and 110 <= c <= 800 for c in costs)


def test_xp_to_reach_is_the_sum_of_the_costs_before() -> None:
    assert progress.xp_to_reach(1) == 0
    assert progress.xp_to_reach(2) == 110
    assert progress.xp_to_reach(3) == 235
    for s in range(2, progress.MAX_STAGE + 1):
        assert progress.xp_to_reach(s) - progress.xp_to_reach(s - 1) == progress.stage_cost(s - 1)


def test_xp_to_reach_is_clamped() -> None:
    assert progress.xp_to_reach(0) == progress.xp_to_reach(-5) == 0
    assert progress.xp_to_reach(10_000) == progress.xp_to_reach(progress.MAX_STAGE + 1)


@pytest.mark.parametrize("xp, stage, into, cost", [
    (0, 1, 0, 110), (-50, 1, 0, 110), (109, 1, 109, 110), (110, 2, 0, 125), (234, 2, 124, 125), (235, 3, 0, 140),
])
def test_state(xp: int, stage: int, into: int, cost: int) -> None:
    st = progress.state(xp)
    assert (st.stage, st.into, st.cost) == (stage, into, cost)
    assert st.total == max(0, xp)
    assert st.fraction == pytest.approx(into / cost)


def test_state_at_each_stage_start() -> None:
    for s in range(1, progress.MAX_STAGE + 1):
        assert progress.state(progress.xp_to_reach(s)).stage == s
        assert progress.state(progress.xp_to_reach(s) - 1).stage == max(1, s - 1)


def test_the_last_stage_is_full() -> None:
    st = progress.state(progress.xp_to_reach(progress.MAX_STAGE) + 10_000)
    assert (st.stage, st.into, st.cost, st.fraction) == (progress.MAX_STAGE, 0, 0, 1.0)


def test_state_accepts_floats_and_strings_of_ints() -> None:
    assert progress.state(110.9).stage == 2
    assert progress.state("235").stage == 3  # type: ignore[arg-type]


# ---------------------------------------------------------------- rewards
def test_everything_is_unlocked_exactly_once() -> None:
    every = {("game", k) for k in progress.GAMES} | {("skin", k) for k in progress.SKINS} | \
            {("hat", k) for k in hats.HATS}
    assert len(progress.REWARDS) == len(set(progress.REWARDS))
    assert set(progress.REWARDS) | progress.START == every
    assert not set(progress.REWARDS) & progress.START
    assert progress.MAX_STAGE == len(progress.REWARDS) + 1


def test_fixed_stages_and_the_final_crown() -> None:
    assert progress.REWARDS[0] == ("hat", "party")
    for stage, game in ((3, "knock"), (5, "mice"), (8, "fishing"), (11, "hide"), (14, "boxes")):
        assert progress.REWARDS[stage - 2] == ("game", game)
    assert progress.REWARDS[118 - 2] == ("skin", "galaxy")
    assert progress.REWARDS[-1] == ("hat", progress.FINAL_HAT)


def test_reward_order_is_unchanged() -> None:
    """Only the XP is saved: a different order would change what every user has unlocked."""
    golden = [tuple(r) for r in json.loads((DATA / "rewards_v1.json").read_text("utf-8"))]
    assert progress.REWARDS == golden


@pytest.mark.parametrize("kind, key, stage", [
    ("game", "ball", 1), ("game", "laser", 1), ("game", "treats", 1), ("skin", "charcoal", 1),
    ("hat", "party", 2), ("game", "knock", 3), ("skin", "ginger", 4), ("skin", "galaxy", 118),
    ("hat", "crown", 131), ("hat", "no-such-hat", 132), ("game", "chess", 132),
])
def test_stage_of(kind: str, key: str, stage: int) -> None:
    assert progress.stage_of(kind, key) == stage


def test_is_unlocked() -> None:
    assert progress.is_unlocked(0, "game", "ball")
    assert not progress.is_unlocked(0, "hat", "party")
    assert progress.is_unlocked(110, "hat", "party")
    assert not progress.is_unlocked(10 ** 9, "hat", "no-such-hat")
    assert progress.is_unlocked(10 ** 9, "hat", "crown")


def test_new_unlocks() -> None:
    assert progress.new_unlocks(0, 109) == []
    assert progress.new_unlocks(100, 240) == [(2, "hat", "party"), (3, "game", "knock")]
    assert progress.new_unlocks(240, 100) == []
    assert len(progress.new_unlocks(0, 10 ** 9)) == len(progress.REWARDS)


@pytest.mark.parametrize("kind, key, name", [
    ("game", "fishing", "Fishing"), ("skin", "galaxy", "Galaxy"), ("hat", "crown", hats.HATS["crown"].name),
    ("hat", "mystery", "mystery"),
])
def test_name_of(kind: str, key: str, name: str) -> None:
    assert progress.name_of(kind, key) == name


def test_kind_label() -> None:
    assert progress.kind_label("game", "mice") == "minigame"
    assert progress.kind_label("skin", "snow") == "skin"
    assert progress.kind_label("other", "x") == "other"
    assert progress.kind_label("hat", "no-such-hat") == "hat"
    labels = {progress.kind_label("hat", k) for k in hats.HATS}
    assert labels == {"hat"} | {f"{t} hat" for t in hats.TIERS.values() if t != "common"}
    assert progress.kind_label("hat", "crown") == f"{hats.TIERS[hats.HATS['crown'].tier]} hat"
