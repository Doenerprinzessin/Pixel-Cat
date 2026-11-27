"""games/base.py: sprites, props, the in-memory host and the MiniGame framework."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from gamekit import SCALE, World, build_world, low_windows, make_monitor

from pixelcat.engine.states import S
from pixelcat.games import art
from pixelcat.games.base import Host, MiniGame, Sprite



class Dummy(MiniGame):
    key = "dummy"
    title = "Dummy"
    duration = 5.0

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self.calls: list[str] = []

    def setup(self) -> None:
        self.calls.append("setup")

    def tick(self, dt: float) -> None:
        self.calls.append("tick")

    def held(self, dt: float) -> None:
        self.calls.append("held")

    def teardown(self) -> None:
        self.calls.append("teardown")


class RecSprite(Sprite):
    def __init__(self, *a, **k) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*a, **k)
        self.hooks: list[str] = []

    def _changed(self) -> None:
        self.hooks.append("changed")

    def _moved(self) -> None:
        self.hooks.append("moved")

    def _closed(self) -> None:
        self.hooks.append("closed")


def start_dummy(w: World) -> Dummy:
    g = Dummy(w.host)
    w.brain.game = g
    g.start()
    return g


# ------------------------------------------------------------------ sprites and props
def test_sprite_rect_is_centred_and_scaled(world: World) -> None:
    sp = world.host.sprite("mug", 100, 200)
    w, h = art.size("mug")
    assert sp.size == (w * SCALE, h * SCALE)
    assert sp.rect() == (100 - w * SCALE / 2, 200 - h * SCALE / 2, 100 + w * SCALE / 2, 200 + h * SCALE / 2)
    assert world.host.sprites == [sp]


def test_bottom_anchored_sprite_stands_on_its_point(world: World) -> None:
    sp = world.host.sprite("mug", 100, 200, anchor="bottom")
    l, t, r, b = sp.rect()
    assert b == 200 and (l + r) / 2 == 100 and b - t == art.size("mug")[1] * SCALE


def test_sprite_hit_with_padding(world: World) -> None:
    sp = world.host.sprite("mug", 100, 200)
    l, t, r, b = sp.rect()
    assert sp.hit(100, 200)
    assert sp.hit(l, t) and sp.hit(r, b)
    assert not sp.hit(r + 5, 200)
    assert sp.hit(r + 5, 200, pad=5)


def test_sprite_hooks_fire_only_on_real_changes(world: World) -> None:
    sp = RecSprite(world.host, "mug", 10, 20)
    world.host.sprites.append(sp)
    sp.move(10, 20)
    sp.set_art("mug")
    assert sp.hooks == []
    sp.move(11, 20)
    sp.set_art("pot")
    assert sp.hooks == ["moved", "changed"]
    assert (sp.x, sp.y, sp.art) == (11.0, 20.0, "pot")
    sp.close()
    sp.close()
    assert sp.hooks == ["moved", "changed", "closed"]
    assert sp.closed and sp not in world.host.sprites


def test_sprites_get_unique_negative_placeholder_handles(world: World) -> None:
    hwnds = [world.host.sprite("mug", 0, 0).hwnd for _ in range(5)]
    assert all(h < 0 for h in hwnds) and len(set(hwnds)) == 5


@pytest.mark.parametrize("kind, inset, margin", [("shelf", 1, 1), ("sofa", 3, 2), ("plant", 9, 4)])
def test_prop_top_and_window_info(world: World, kind: str, inset: int, margin: int) -> None:
    p = world.host.prop(kind, 500, 1552)
    l, t, r, b = p.rect()
    assert b == 1552 and p.anchor == "bottom" and p.kind == kind
    assert p.top() == t + (inset + 1) * SCALE
    wi = p.win_info(-7)
    assert wi.hwnd == p.hwnd and wi.z == -7
    assert wi.rect == (int(l + (margin + 1) * SCALE), int(p.top()), int(r - (margin + 1) * SCALE), int(b))
    assert (wi.title, wi.cls, wi.maximized, wi.topmost) == ("Pixel Cat prop", "PixelCatProp", False, True)


def test_draggable_prop_is_clickable(world: World) -> None:
    assert world.host.prop("box_m", 500, 1552, draggable=True).clickable
    assert not world.host.prop("shelf", 500, 1552).clickable


def test_props_become_surfaces_for_the_cat_and_go_away_again(world: World) -> None:
    p = world.host.prop("table", 1800, 1552)
    tops = [s for s in world.brain.smap.tops if s.hwnd == p.hwnd]
    assert len(tops) == 1
    assert tops[0].y == int(p.top()) and tops[0].x1 >= p.rect()[0] and tops[0].x2 <= p.rect()[2]
    p.close()
    assert p not in world.host.props
    assert not [s for s in world.brain.smap.tops if s.hwnd == p.hwnd]


def test_newest_prop_is_on_top_of_the_z_order(world: World) -> None:
    a = world.host.prop("table", 1800, 1552)
    b = world.host.prop("shelf", 600, 1400)
    zs = {wi.hwnd: wi.z for wi in world.host.prop_infos()}
    assert zs == {b.hwnd: -1000, a.hwnd: -999}


def test_host_clear_removes_everything(world: World) -> None:
    h = world.host
    h.sprite("mug", 0, 0)
    h.prop("table", 1800, 1552)
    h.hud_set("T", "x")
    h.cat_below(123)
    h.clear()
    assert (h.sprites, h.props, h.hud_lines, h.cat_under) == ([], [], ("", ""), None)


def test_host_records_effects_and_text(world: World) -> None:
    h = world.host
    h.burst("star", 1, 2, 5, "#fff")
    h.float_text("+1", 3, 4, "#000")
    assert h.bursts == [("star", 1, 2)] and h.texts == [("+1", 3, 4)]


def test_host_without_a_world_yet() -> None:
    h = Host(SimpleNamespace(smap=None, scale=0))
    assert h.scale == 1
    h.prop("shelf", 10, 10)
    assert len(h.props) == 1


# ------------------------------------------------------------------ lifecycle
def test_start_puts_the_cat_into_the_game(world: World) -> None:
    g = start_dummy(world)
    b = world.brain
    assert g.active and b.game is g and b.state == S.GAME
    assert not b.plan and b.oneshot is None and not b.floor_only
    assert g.calls == ["setup"]
    assert world.host.hud_lines == ("Dummy", "Score 0 · 5 s")


def test_start_wakes_a_sleeping_cat(world: World) -> None:
    world.brain.set_state(S.SLEEPING, travel=False)
    start_dummy(world)
    assert world.brain.state == S.GAME and world.brain.oneshot == "wake"


def test_start_unpauses(world: World) -> None:
    world.brain.set_paused(True)
    start_dummy(world)
    assert not world.brain.paused and world.brain.state == S.GAME


def test_update_clamps_dt_and_keeps_the_cursor(world: World) -> None:
    g = start_dummy(world)
    g.update(1.0, (5, 6))
    assert g.t == pytest.approx(0.05) and g.cursor == (5, 6)
    g.update(0.01)
    assert g.t == pytest.approx(0.06) and g.cursor == (5, 6)
    assert g.calls == ["setup", "tick", "tick"]
    assert world.host.hud_lines == ("Dummy", "Score 0 · 4 s")


def test_time_up_finishes_the_game(world: World) -> None:
    g = start_dummy(world)
    for _ in range(99):
        g.update(0.05)
    assert g.active
    for _ in range(10):
        g.update(0.05)
    assert not g.active and g.finished and g.result == ""
    assert world.ended == [g]
    assert 100 <= g.calls.count("tick") <= 101 and g.calls[-1] == "teardown"
    assert world.brain.state == S.SITTING
    g.update(0.05)
    assert g.calls[-1] == "teardown"


def test_a_score_leaves_the_cat_content(world: World) -> None:
    g = start_dummy(world)
    g.score = 3
    g.finish("done")
    assert world.brain.state == S.CONTENT and g.result == "done" and g.finished


def test_stop_is_not_finished_and_only_once(world: World) -> None:
    g = start_dummy(world)
    world.host.sprite("mug", 0, 0)
    g.stop()
    g.stop()
    assert not g.finished and not g.active
    assert world.ended == [g] and g.calls.count("teardown") == 1
    assert world.host.sprites == [] and world.host.hud_lines == ("", "")


def test_stop_drops_a_held_in_place_cat(world: World) -> None:
    g = start_dummy(world)
    g.fix_cat(900, 1300)
    g.stop()
    b = world.brain
    assert b.mode == "air" and (b.vx, b.vy) == (0.0, 0.0) and b.air_start_y == 1300


def test_cleanup_happens_even_if_teardown_fails(world: World) -> None:
    g = start_dummy(world)
    world.host.sprite("mug", 0, 0)

    def boom() -> None:
        raise RuntimeError("boom")
    g.teardown = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        g.stop()
    assert world.host.sprites == [] and world.ended == [g] and not g.active


def test_carried_cat_calls_held_instead_of_tick(world: World) -> None:
    g = start_dummy(world)
    world.brain.set_state(S.HELD, travel=False)
    g.update(0.02)
    assert g.calls == ["setup", "held"]


def test_update_brings_the_cat_back_into_the_game(world: World) -> None:
    g = start_dummy(world)
    world.brain.set_state(S.SITTING, travel=False)
    g.update(0.02)
    assert world.brain.state == S.GAME


def test_just_landed_is_true_for_one_tick(world: World) -> None:
    g = start_dummy(world)
    b = world.brain
    b.mode = "air"
    g.update(0.02)
    assert not g.just_landed
    b.mode = "ground"
    g.update(0.02)
    assert g.just_landed
    g.update(0.02)
    assert not g.just_landed


def test_anim_and_cat_anim(world: World) -> None:
    g = start_dummy(world)
    assert g.cat_anim() is None
    g.set_anim("aim")
    g.update(0.02)
    assert g.cat_anim() == ("aim", pytest.approx(0.02))
    g.set_anim("aim")
    assert g.anim_t == pytest.approx(0.02)
    g.set_anim("swat")
    assert g.cat_anim() == ("swat", 0.0)
    g.stop()
    assert g.cat_anim() is None


def test_defaults(world: World) -> None:
    g = Dummy(world.host)
    assert (g.xp(), g.on_cat_click(), g.crown(), g.look_target(), g.hud_text()) == (10, False, False, None,
                                                                                   "Score 0")
    assert g.ctx is world.host.ctx


# ------------------------------------------------------------------ the cat
def test_face_ignores_tiny_differences(world: World) -> None:
    g = Dummy(world.host)
    b = world.brain
    b.facing = 1
    g.face(b.x - 2 * SCALE)
    assert b.facing == 1
    g.face(b.x - 2 * SCALE - 1)
    assert b.facing == -1
    g.face(b.x + 100)
    assert b.facing == 1


def test_paw_and_mouth_points_follow_facing(world: World) -> None:
    g = Dummy(world.host)
    b = world.brain
    b.facing = -1
    assert g.paw_point() == (b.x - 9 * SCALE, b.y - 4 * SCALE)
    hx, hy = b.head_world()
    assert g.mouth_point() == (hx - 4 * SCALE, hy + 3 * SCALE)


def test_fix_and_release_cat(world: World) -> None:
    g = Dummy(world.host)
    b = world.brain
    b.plan.append(object())
    g.fix_cat(1000, 1200)
    assert (b.mode, b.surf, b.x, b.y, b.vx, b.vy) == ("fixed", None, 1000.0, 1200.0, 0.0, 0.0) and not b.plan
    g.release_cat()
    assert b.mode == "air" and b.air_start_y == 1200
    g.release_cat()
    assert b.mode == "air"


def test_leap_lands_on_the_target(world: World) -> None:
    g = start_dummy(world)
    b = world.brain
    floor = world.floor_y()
    g.leap_to(b.x + 200, floor)
    assert b.mode == "air" and b.vy < 0 and b.vx > 0 and b.facing == 1
    for _ in range(300):
        b.update(1 / 60, world.ctx())
        if b.mode == "ground":
            break
    assert b.mode == "ground"
    assert b.x == pytest.approx(700 + 200, abs=12)


def test_go_to_where_the_cat_already_is_just_stops(world: World) -> None:
    g = Dummy(world.host)
    b = world.brain
    b.plan.append(object())
    assert g.go(b.surf, b.x + 1)
    assert not b.plan


def test_go_plans_a_walk(world: World) -> None:
    g = Dummy(world.host)
    assert g.go(world.brain.surf, 1500, "walk")
    assert world.brain.plan


# ------------------------------------------------------------------ the world
def test_monitor_is_the_one_the_cat_is_on() -> None:
    m1 = make_monitor((0, 0, 2560, 1600), handle=1)
    m2 = make_monitor((2560, 0, 4480, 1080), handle=2, primary=False, device="M2")
    w = build_world(monitors=[m1, m2], windows=[], place=(3000, 900))
    g = Dummy(w.host)
    assert g.monitor() is m2
    floor = g.surfaces()[0]
    assert floor.kind == "floor" and floor.y == 1032 and floor.x1 >= 2560


def test_surfaces_lists_floor_then_tops_on_the_cats_monitor(world: World) -> None:
    g = Dummy(world.host)
    surfs = g.surfaces()
    assert surfs[0].kind == "floor"
    assert {s.hwnd for s in surfs[1:]} == {101, 102}


@pytest.mark.parametrize("x, y, hwnd", [(700, 500, 101), (700, 598, 101), (700, 700, 0), (100, 500, 0),
                                        (1800, 300, 102)])
def test_surface_under(world: World, x: float, y: float, hwnd: int) -> None:
    assert Dummy(world.host).surface_under(x, y).hwnd == hwnd


def test_find_surface(world: World) -> None:
    g = Dummy(world.host)
    assert g.find_surface(0, 50).kind == "floor"
    assert g.find_surface(999, 50) is None
    far = g.find_surface(101, 5000)
    assert far.hwnd == 101 and far.y == 600


def test_ensure_platforms_puts_up_stairs_when_there_are_no_windows(bare_world: World) -> None:
    g = Dummy(bare_world.host)
    made = g.ensure_platforms(2)
    assert [(p.kind, round(p.x, 1), p.y) for p in made] == [("table", 716.8, 1552.0), ("shelf", 896.8, 1390.0)]
    assert bare_world.host.props == made
    assert len(g.tops_on_monitor()) == 2


def test_ensure_platforms_cycles_through_its_spots(bare_world: World) -> None:
    made = Dummy(bare_world.host).ensure_platforms(5)
    assert [p.kind for p in made] == ["table", "shelf", "shelf", "shelf", "table"]


def test_ensure_platforms_ignores_kinds(bare_world: World) -> None:
    made = Dummy(bare_world.host).ensure_platforms(1, ("sofa",))
    assert [p.kind for p in made] == ["table"]


def test_ensure_platforms_uses_low_windows_instead() -> None:
    w = build_world(windows=low_windows())
    g = Dummy(w.host)
    assert {s.hwnd for s in g.tops_on_monitor()} == {201, 202}
    assert g.ensure_platforms(2) == [] and w.host.props == []


def test_windows_high_up_are_not_platforms(world: World) -> None:
    g = Dummy(world.host)
    assert g.tops_on_monitor() == []
    assert g.tops_on_monitor(max_up_art=1000, reachable=False) != []


def test_reachable_and_quick(world: World) -> None:
    w = build_world(windows=low_windows())
    g = Dummy(w.host)
    floor = g.surfaces()[0]
    low = w.brain.smap.tops_of(201)[0]
    assert g.reachable(floor) and g.quick(floor)
    assert g.reachable(low) and g.quick(low)
    high = world.brain.smap.tops_of(102)[0]
    assert not Dummy(world.host).quick(high)
