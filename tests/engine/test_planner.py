"""Planner: walk / jump / drop / climb routes over the surface graph."""
from __future__ import annotations

import pytest

from pixelcat.engine.planner import Planner
from pixelcat.engine.surfaces import Surface, SurfaceMap
from pixelcat.settings import Tuning

S = 3   # scale: max jump 225 px sideways, 126 px up; edge margin 30 px


@pytest.fixture
def planner():
    return Planner(Tuning())


def summary(steps):
    return [(st.kind, round(st.x), st.target.kind if st.target else None, round(st.land_x)) for st in steps]


def window_map(world, rect):
    wins = [world.window(10, rect, 0)]
    return world.build_map(world.single_monitor(), wins)


def test_same_surface_is_a_single_walk_kept_off_the_edges(world, planner):
    smap = world.build_map(world.single_monitor(), [])
    floor = smap.floors[0]
    assert summary(planner.plan(smap, floor, 300, floor, 10, S)) == [("walk", 30, None, 0)]
    assert summary(planner.plan(smap, floor, 300, floor, 900, S)) == [("walk", 900, None, 0)]


def test_low_window_top_overlapping_the_floor_is_a_straight_hop_up(world, planner):
    smap = window_map(world, (600, 940, 1300, 1200))         # top 100 px above the floor
    floor, top = smap.floors[0], smap.tops[0]
    steps = planner.plan(smap, floor, 300, top, 900, S)
    assert summary(steps) == [("walk", 630, None, 0), ("jump", 630, "top", 630), ("walk", 900, None, 0)]


def test_high_window_top_is_reached_by_climbing_the_nearer_wall(world, planner):
    smap = window_map(world, (600, 500, 1300, 900))
    floor, top = smap.floors[0], smap.tops[0]
    steps = planner.plan(smap, floor, 300, top, 900, S)
    assert summary(steps) == [("walk", 552, None, 0), ("climb", 552, "top", 636), ("walk", 900, None, 0)]
    assert steps[1].wall == smap.wall(10, "left")
    from_right = planner.plan(smap, floor, 1700, top, 900, S)
    assert from_right[1].wall == smap.wall(10, "right")


def test_a_wall_out_of_reach_from_the_floor_makes_the_top_unreachable(world, planner):
    smap = window_map(world, (600, 500, 1300, 580))           # bottom 460 px above the floor > 150 art px
    assert planner.plan(smap, smap.floors[0], 300, smap.tops[0], 900, S) is None


def test_going_down_hops_off_the_nearer_edge(world, planner):
    smap = window_map(world, (600, 500, 1300, 900))
    floor, top = smap.floors[0], smap.tops[0]
    assert summary(planner.plan(smap, top, 700, floor, 300, S)) == [
        ("walk", 609, None, 0), ("drop", 609, "floor", 558), ("walk", 300, None, 0)]
    assert summary(planner.plan(smap, top, 1250, floor, 1700, S)) == [
        ("walk", 1291, None, 0), ("drop", 1291, "floor", 1342), ("walk", 1700, None, 0)]


def test_jump_across_a_gap_between_two_title_bars(planner):
    a, b = Surface("top", 100, 500, 600, 1, 0), Surface("top", 600, 1000, 600, 2, 1)
    smap = SurfaceMap([], [a, b], [], [])
    assert summary(planner.plan(smap, a, 200, b, 800, S)) == [
        ("walk", 470, None, 0), ("jump", 470, "top", 630), ("walk", 800, None, 0)]
    far = Surface("top", 800, 1000, 600, 2, 1)                 # 300 px > 75 art px x 3
    assert planner.plan(SurfaceMap([], [a, far], [], []), a, 200, far, 900, S) is None


def test_hop_down_onto_a_ledge_inside_the_same_window(world, planner):
    wins = [world.window(10, (600, 500, 1300, 900), 0)]
    smap = world.build_map(world.single_monitor(), wins, ledges={10: [(650, 1250, 700)]})
    top, ledge = smap.tops[0], smap.ledges[0]
    assert summary(planner.plan(smap, top, 900, ledge, 1000, S)) == [
        ("walk", 900, None, 0), ("drop", 900, "ledge", 900), ("walk", 1000, None, 0)]


def test_goal_not_in_the_map_has_no_plan(world, planner):
    smap = window_map(world, (600, 500, 1300, 900))
    ghost = Surface("top", 0, 100, 300, 99, 5)
    assert planner.plan(smap, smap.floors[0], 300, ghost, 50, S) is None


def test_a_start_that_is_not_in_the_map_still_plans(world, planner):
    smap = window_map(world, (600, 500, 1300, 900))
    moved_floor = Surface("floor", 0, 1920, 1040)
    steps = planner.plan(smap, moved_floor, 300, smap.tops[0], 900, S)
    assert steps is not None and steps[-1].kind == "walk"


def test_many_ledges_are_pruned_to_the_nearby_ones_and_still_plan(world, planner):
    wins = [world.window(10, (100, 200, 1800, 1020), 0)]
    rows = {10: [(150, 400, y) for y in range(260, 1000, 9)]}      # ~80 text lines
    smap = world.build_map(world.single_monitor(), wins, ledges=rows)
    assert len(smap.surfaces) > 70
    goal = smap.ledges[-1]
    steps = planner.plan(smap, smap.floors[0], 1500, goal, 300, S)
    assert steps is not None
    assert steps[-1].kind == "walk" and goal.same(steps[-2].target)
