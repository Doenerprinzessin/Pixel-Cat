"""SurfaceMap: floors, window tops, walls and ledges built from monitors + windows."""
from __future__ import annotations

import pytest

from pixelcat.engine.surfaces import FLOOR_Z, Surface, SurfaceMap, Wall


@pytest.fixture
def one(world):
    """One 1920x1080 monitor (taskbar: work area ends at y=1040), scale 3."""
    return world.single_monitor()


def tops(smap, hwnd=None):
    return [(t.x1, t.x2, t.y) for t in smap.tops if hwnd is None or t.hwnd == hwnd]


# ---------------------------------------------------------------- floors
def test_floor_is_the_bottom_of_the_work_area_above_the_taskbar(world, one):
    smap = world.build_map(one, [])
    assert smap.floors == [Surface("floor", 0, 1920, 1040)]
    assert smap.floors[0].z == FLOOR_Z
    assert smap.surfaces == smap.floors and smap.tops == [] and smap.walls == [] and smap.ledges == []


@pytest.mark.parametrize("second_work, merged", [
    ((1920, 0, 3840, 1040), [("floor", 0, 3840, 1040)]),                       # same height: one floor
    ((1920, 0, 3840, 1042), [("floor", 0, 3840, 1040)]),                       # 2 px off still merges
    ((1920, 0, 3840, 1043), [("floor", 0, 1920, 1040), ("floor", 1920, 3840, 1043)]),
    ((1923, 0, 3840, 1040), [("floor", 0, 1920, 1040), ("floor", 1923, 3840, 1040)]),  # a gap
    ((1922, 0, 3840, 1040), [("floor", 0, 3840, 1040)]),
])
def test_adjacent_monitor_floors_merge_only_when_level_and_touching(world, second_work, merged):
    mons = [world.monitor((0, 0, 1920, 1080), (0, 0, 1920, 1040)),
            world.monitor((second_work[0], 0, 3840, 1080), second_work, handle=2)]
    smap = world.build_map(mons, [])
    assert [(f.kind, f.x1, f.x2, f.y) for f in smap.floors] == merged


def test_sim_layout_has_one_floor_per_monitor_sorted_top_down(world):
    smap = world.build_map(world.sim_monitors(), [], world.scale_by_dpi)
    assert [(f.x1, f.x2, f.y) for f in smap.floors] == [(2560, 4480, 1240), (0, 2560, 1392)]


# ---------------------------------------------------------------- window tops
def test_window_top_and_both_walls(world, one):
    w = world.window(10, (600, 500, 1300, 900), 0)
    smap = world.build_map(one, [w])
    assert smap.tops == [Surface("top", 600, 1300, 500, 10, 0)]
    assert smap.walls == [Wall(10, 600, 500, 900, "left"), Wall(10, 1300, 500, 900, "right")]
    assert smap.surfaces == smap.floors + smap.tops


def test_wall_bottom_is_clamped_to_the_floor(world, one):
    smap = world.build_map(one, [world.window(10, (600, 500, 1300, 1400), 0)])
    assert {w.y_bottom for w in smap.walls} == {1040}


@pytest.mark.parametrize("top, has_top", [
    (89, False),     # no room above it (needs 30 art px = 90 px below the screen top)
    (90, True),
    (1021, True),
    (1022, False),   # sits on the floor (within 6 art px of the work area's bottom)
])
def test_window_tops_need_room_above_and_must_be_off_the_floor(world, one, top, has_top):
    smap = world.build_map(one, [world.window(10, (600, top, 1300, top + 300), 0)])
    assert bool(smap.tops) is has_top


@pytest.mark.parametrize("width, kept", [(77, False), (78, True)])   # min_surface_len 26 art px x scale 3
def test_window_tops_shorter_than_the_minimum_are_dropped(world, one, width, kept):
    smap = world.build_map(one, [world.window(10, (600, 500, 600 + width, 900), 0)])
    assert bool(smap.tops) is kept
    assert bool(smap.walls) is kept


def test_windows_higher_in_the_z_order_cut_the_title_bar(world, one):
    back = world.window(1, (200, 500, 1100, 900), 1)
    front = world.window(2, (400, 300, 700, 800), 0)
    smap = world.build_map(one, [back, front])
    assert tops(smap, 1) == [(200, 400, 500), (700, 1100, 500)]
    assert tops(smap, 2) == [(400, 700, 300)]
    # walls only where the title bar reaches the window's edge
    assert {(w.hwnd, w.side) for w in smap.walls} == {(1, "left"), (1, "right"), (2, "left"), (2, "right")}


def test_a_window_whose_bottom_is_above_the_title_bar_does_not_cut_it(world, one):
    back = world.window(1, (100, 500, 1100, 900), 1)
    front = world.window(2, (400, 200, 700, 500), 0)          # ends exactly at the back window's top
    smap = world.build_map(one, [back, front])
    assert tops(smap, 1) == [(100, 1100, 500)]


def test_a_fully_covered_window_has_no_top_and_no_walls(world, one):
    back = world.window(1, (400, 500, 700, 900), 1)
    front = world.window(2, (300, 300, 800, 950), 0)
    smap = world.build_map(one, [back, front])
    assert tops(smap, 1) == []
    assert all(w.hwnd != 1 for w in smap.walls)


def test_input_order_does_not_matter_only_z(world, one):
    back = world.window(1, (100, 500, 1100, 900), 1)
    front = world.window(2, (400, 300, 700, 800), 0)
    a = world.build_map(one, [back, front])
    b = world.build_map(one, [front, back])
    assert a.tops == b.tops and a.walls == b.walls


def test_a_window_hanging_off_screen_is_clipped_and_loses_that_wall(world, one):
    smap = world.build_map(one, [world.window(10, (-200, 500, 400, 900), 0)])
    assert tops(smap) == [(0, 400, 500)]
    assert [w.side for w in smap.walls] == ["right"]


@pytest.mark.parametrize("rect, sides", [
    ((50, 500, 700, 900), ["right"]),             # no room (34 art px) left of it to climb
    ((102, 500, 700, 900), ["left", "right"]),
    ((1300, 500, 1850, 900), ["left"]),
    ((1300, 500, 1818, 900), ["left", "right"]),
])
def test_walls_need_climbing_room_beside_the_window(world, one, rect, sides):
    smap = world.build_map(one, [world.window(10, rect, 0)])
    assert [w.side for w in smap.walls] == sides


# ---------------------------------------------------------------- monitors and DPI
def test_each_window_uses_the_scale_of_the_monitor_under_its_title_bar(world):
    mons = world.sim_monitors()                    # M1 144 dpi -> scale 4, M2 96 dpi -> scale 3
    narrow_m1 = world.window(1, (200, 400, 300, 800), 0)
    narrow_m2 = world.window(2, (2700, 600, 2800, 1000), 1)
    smap = world.build_map(mons, [narrow_m1, narrow_m2], world.scale_by_dpi)
    assert tops(smap, 1) == []                     # 100 px < 26 x 4
    assert tops(smap, 2) == [(2700, 2800, 600)]    # 100 px >= 26 x 3


def test_a_window_across_two_monitors_has_one_top_segment_per_monitor(world):
    smap = world.build_map(world.sim_monitors(), [world.window(7, (2400, 500, 2800, 900), 0)], world.scale_by_dpi)
    assert tops(smap, 7) == [(2400, 2560, 500), (2560, 2800, 500)]


def test_a_window_in_a_gap_between_monitors_uses_the_nearest_monitor(world):
    left = world.monitor((0, 0, 1000, 1000), (0, 0, 1000, 960), handle=1, device="L")
    right = world.monitor((1200, 0, 2200, 1000), (1200, 0, 2200, 960), handle=2, device="R")
    asked = []

    def scale_of(m):
        asked.append(m.device)
        return 3

    world.build_map([left, right], [world.window(5, (800, 300, 1300, 600), 0)], scale_of)
    assert asked and set(asked) == {"L"}            # title-bar centre x=1050 is nearer the left monitor
    assert world.monitor_at([left, right], 1050, 300) is left
    assert world.monitor_at([left, right], 1500, 300) is right


def test_sim_layout_surfaces(world):
    smap = world.build_map(world.sim_monitors(), world.sim_windows(0), world.scale_by_dpi)
    assert [(t.hwnd, t.x1, t.x2, t.y) for t in smap.tops] == [
        (102, 1100, 1900, 300), (101, 300, 1100, 500), (103, 2700, 3500, 600), (104, 1700, 2400, 1000)]
    assert [(w.hwnd, w.side) for w in smap.walls] == [
        (102, "left"), (102, "right"), (101, "left"), (103, "left"), (103, "right"), (104, "left"), (104, "right")]


# ---------------------------------------------------------------- ledges
@pytest.fixture
def ledge_win(world):
    return world.window(10, (600, 500, 1300, 900), 1)


def ledges_of(world, one, wins, found):
    return [(g.x1, g.x2, g.y, g.hwnd, g.key) for g in world.build_map(one, wins, ledges=found).ledges]


def test_ledges_inside_a_window_belong_to_it_and_remember_their_depth(world, one, ledge_win):
    got = ledges_of(world, one, [ledge_win], {10: [(650, 1250, 700)]})
    assert got == [(650, 1250, 700, 10, 200)]


@pytest.mark.parametrize("y, kept", [(535, False), (536, True), (882, True), (883, False)])
def test_ledges_too_close_to_the_window_edges_are_ignored(world, one, ledge_win, y, kept):
    assert bool(ledges_of(world, one, [ledge_win], {10: [(650, 1250, y)]})) is kept


def test_ledges_are_clipped_to_their_window_and_need_a_minimum_length(world, one, ledge_win):
    assert ledges_of(world, one, [ledge_win], {10: [(500, 1400, 700)]}) == [(600, 1300, 700, 10, 200)]
    assert ledges_of(world, one, [ledge_win], {10: [(650, 697, 700)]}) == []     # 47 px < 16 art px x 3
    assert ledges_of(world, one, [ledge_win], {10: [(650, 698, 700)]}) == [(650, 698, 700, 10, 200)]


@pytest.mark.parametrize("y, spans", [
    (700, [(600, 800), (1000, 1300)]),            # covered by the window in front
    (600, [(600, 800), (1000, 1300)]),            # no head room under the front window's bottom part
    (550, [(600, 1300)]),
])
def test_ledges_are_cut_where_a_window_in_front_covers_them(world, one, ledge_win, y, spans):
    front = world.window(20, (800, 650, 1000, 1000), 0)
    got = ledges_of(world, one, [ledge_win, front], {10: [(600, 1300, y)]})
    assert [(a, b) for a, b, *_ in got] == spans


def test_ledges_for_unknown_windows_are_ignored(world, one, ledge_win):
    assert ledges_of(world, one, [ledge_win], {99: [(650, 1250, 700)]}) == []


# ---------------------------------------------------------------- queries
@pytest.fixture
def stacked(world):
    """Two monitors on top of each other: floors at y=1040 and y=2120 over the same x range."""
    mons = [world.monitor((0, 0, 1920, 1080), (0, 0, 1920, 1040), handle=1),
            world.monitor((0, 1080, 1920, 2160), (0, 1080, 1920, 2120), handle=2)]
    return world.build_map(mons, [])


@pytest.mark.parametrize("y, floor_y", [(None, 1040), (500, 1040), (1042, 1040), (1043, 2120), (1500, 2120),
                                        (3000, 2120)])
def test_floor_at_picks_the_floor_at_or_below_y_with_stacked_monitors(stacked, y, floor_y):
    assert stacked.floor_at(500, y).y == floor_y


def test_floor_at_in_a_gap_between_monitors_is_the_nearest_floor(world):
    mons = [world.monitor((0, 0, 1000, 1000), (0, 0, 1000, 960)),
            world.monitor((1200, 0, 2200, 1000), (1200, 0, 2200, 960), handle=2)]
    smap = world.build_map(mons, [])
    assert smap.floor_at(1050, 500).x2 == 1000
    assert smap.floor_at(1180, 500).x1 == 1200


def test_landing_between_returns_the_highest_line_crossed(world, one):
    smap = world.build_map(one, [world.window(10, (600, 500, 1300, 900), 0)])
    assert smap.landing_between(800, 400, 1100).kind == "top"
    assert smap.landing_between(800, 501, 1100).kind == "floor"
    assert smap.landing_between(800, 400, 499) is None
    assert smap.landing_between(400, 400, 1100).kind == "floor"


def test_any_below(world, one):
    smap = world.build_map(one, [world.window(10, (600, 500, 1300, 900), 0)])
    assert smap.any_below(800, 100)
    assert smap.any_below(800, 1041)               # (1 px tolerance)
    assert not smap.any_below(800, 1042)
    assert not smap.any_below(-50, 100)


def test_top_and_wall_lookups(world, one):
    smap = world.build_map(one, [world.window(10, (600, 500, 1300, 900), 0)])
    assert smap.top_of(10, 700).hwnd == 10
    assert smap.top_of(10, 1400) is None
    assert smap.top_of(11, 700) is None
    assert len(smap.tops_of(10)) == 1 and smap.tops_of(11) == []
    assert smap.wall(10, "left").x == 600 and smap.wall(10, "right").x == 1300
    assert smap.wall(11, "left") is None


def test_resolve_finds_the_moved_window_top_and_ledge(world, one):
    w0 = world.window(10, (600, 500, 1300, 900), 0)
    old = world.build_map(one, [w0], ledges={10: [(650, 1250, 700)]})
    w1 = world.window(10, (700, 450, 1400, 850), 0)
    new = world.build_map(one, [w1], ledges={10: [(750, 1350, 651)]})
    top = new.resolve(old.tops[0], 800)
    assert (top.x1, top.x2, top.y) == (700, 1400, 450)
    ledge = new.resolve(old.ledges[0], 800)
    assert (ledge.y, ledge.key) == (651, 201)       # same depth (within 2 px) below the window top
    gone = world.build_map(one, [])
    assert gone.resolve(old.tops[0], 800) is None
    assert gone.resolve(old.ledges[0], 800) is None


def test_resolve_picks_the_segment_nearest_x(world, one):
    back = world.window(1, (100, 500, 1100, 900), 1)
    front = world.window(2, (400, 300, 700, 800), 0)
    smap = world.build_map(one, [back, front])
    ref = Surface("top", 0, 1, 500, 1, 1)
    assert smap.resolve(ref, 200).x2 == 400
    assert smap.resolve(ref, 1000).x1 == 700
    assert smap.resolve(ref, 600).x1 == 700        # in the gap: the nearer edge (700 is 100 away, 400 is 200)


def test_resolve_floor(world, one):
    smap = world.build_map(one, [])
    assert smap.resolve(Surface("floor", 10, 20, 1042), 15) is smap.floors[0]
    assert smap.resolve(Surface("floor", 10, 20, 1043), 15) is None


# ---------------------------------------------------------------- Surface
@pytest.mark.parametrize("a, b, same", [
    (Surface("top", 0, 10, 5, hwnd=1), Surface("top", 50, 90, 80, hwnd=1), True),
    (Surface("top", 0, 10, 5, hwnd=1), Surface("top", 0, 10, 5, hwnd=2), False),
    (Surface("ledge", 0, 10, 5, hwnd=1, key=40), Surface("ledge", 50, 90, 80, hwnd=1, key=42), True),
    (Surface("ledge", 0, 10, 5, hwnd=1, key=40), Surface("ledge", 0, 10, 5, hwnd=1, key=43), False),
    (Surface("floor", 0, 10, 5), Surface("floor", 9, 20, 7), True),
    (Surface("floor", 0, 10, 5), Surface("floor", 10, 20, 5), False),      # touching is not overlapping
    (Surface("floor", 0, 10, 5), Surface("floor", 0, 10, 8), False),
    (Surface("floor", 0, 10, 5), Surface("top", 0, 10, 5), False),
    (Surface("floor", 0, 10, 5), None, False),
])
def test_surface_identity(a, b, same):
    assert a.same(b) is same


def test_surface_contains_and_clamp():
    s = Surface("floor", 100, 200, 0)
    assert s.length == 100
    assert s.contains(100) and s.contains(200) and not s.contains(201)
    assert not s.contains(105, margin=10) and s.contains(110, margin=10)
    assert s.clamp(50) == 100 and s.clamp(250) == 200 and s.clamp(150) == 150
    assert s.clamp(50, 10) == 110 and s.clamp(250, 10) == 190
    assert s.clamp(0, 60) == 150                    # margin wider than half: the middle


def test_ledge_list_defaults_to_empty():
    floor = Surface("floor", 0, 10, 5)
    smap = SurfaceMap([floor], [], [], [])
    assert smap.ledges == [] and smap.surfaces == [floor]
