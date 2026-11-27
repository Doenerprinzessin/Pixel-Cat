"""Brain: locomotion, riding windows, the state machine and every user event."""
from __future__ import annotations

import pytest

from pixelcat.engine.animations import ANIMS
from pixelcat.engine.brain import Brain, Ctx
from pixelcat.engine.states import STATES, S
from pixelcat.sprites.cat_art import Pose

DT = 1 / 30
WIN = (600, 500, 1300, 900)   # window 10 of the grounded_brain fixture


def run(brain: Brain, seconds: float, c=None, until=None, each=None) -> bool:
    c = c or Ctx((-5000, -5000), False, 0.0, 14, False, brain.scale, False)
    for _ in range(int(seconds / DT)):
        brain.update(DT, c)
        if each is not None:
            each(brain)
        if until is not None and until(brain):
            return True
    return until is None


def still(brain: Brain) -> bool:
    return brain.mode == "ground" and not brain.plan and not brain.oneshot


def calm(brain: Brain, state: S) -> None:
    """Switch state without enter animations or trips."""
    brain.plan.clear()
    brain.set_state(state, enter=(), travel=False)


@pytest.fixture
def on_top(world):
    """A settled cat sitting on window 10's title bar (y=500)."""
    wins = [world.window(10, WIN, 0)]
    brain = world.brain()
    brain.set_world(world.build_map(world.single_monitor(), wins), world.rects(wins))
    brain.place(900, 400)
    assert world.settle(brain)
    assert brain.surf.kind == "top" and brain.y == 500
    calm(brain, S.LOAFING)
    brain.state_dur = 1e6
    return brain


# ---------------------------------------------------------------- falling and landing
def test_a_placed_cat_falls_and_lands_on_the_floor(world):
    wins = [world.window(10, WIN, 0)]
    brain = world.brain()
    brain.set_world(world.build_map(world.single_monitor(), wins), world.rects(wins))
    brain.place(300, 100)
    assert brain.mode == "air" and brain.surf is None
    anims = set()
    landed = run(brain, 5, until=lambda b: b.mode == "ground", each=lambda b: anims.add(b.current_anim()[0]))
    assert landed and brain.surf.kind == "floor" and brain.y == 1040
    assert "fall" in anims
    assert brain.oneshot == "land"
    assert any(p.kind.startswith("dust") for p in brain.particles)   # a big fall kicks up dust


def test_a_cat_dropped_above_a_window_lands_on_its_title_bar(world):
    wins = [world.window(10, WIN, 0)]
    brain = world.brain()
    brain.set_world(world.build_map(world.single_monitor(), wins), world.rects(wins))
    brain.place(900, 450)
    assert run(brain, 3, until=lambda b: b.mode == "ground")
    assert (brain.surf.kind, brain.surf.hwnd, brain.y) == ("top", 10, 500)


def test_without_a_world_the_cat_just_falls(world):
    brain = world.brain()
    brain.place(100, 100)
    run(brain, 1)
    assert brain.mode == "air" and brain.y > 100


# ---------------------------------------------------------------- window snapshots (set_world)
def test_a_moved_window_carries_the_cat_along(world, on_top):
    x0 = on_top.x
    wins = [world.window(10, (650, 480, 1350, 880), 0)]
    on_top.set_world(world.build_map(world.single_monitor(), wins), world.rects(wins))
    assert on_top.mode == "ground" and on_top.x == x0 + 50 and on_top.y == 480
    assert on_top.surf.y == 480 and on_top.surf.hwnd == 10


@pytest.mark.parametrize("new_wins", [
    [],                                                       # closed
    [(10, (0, 0, 1920, 1040), 0)],                            # maximised: it jumped
])
def test_a_closed_or_jumping_window_drops_a_startled_cat(world, on_top, new_wins):
    wins = [world.window(*w) for w in new_wins]
    on_top.set_world(world.build_map(world.single_monitor(), wins), world.rects(wins))
    assert on_top.mode == "air" and on_top.surf is None
    assert on_top.bubble.kind == "bubble_exclaim"
    assert run(on_top, 5, until=lambda b: b.mode == "ground")
    assert on_top.state == S.STARTLED


def test_a_title_bar_covered_by_another_window_drops_the_cat(world, on_top):
    wins = [world.window(10, WIN, 1), world.window(11, (400, 300, 1500, 1000), 0)]
    on_top.set_world(world.build_map(world.single_monitor(), wins), world.rects(wins))
    assert on_top.mode == "air"
    assert run(on_top, 5, until=lambda b: b.mode == "ground")
    assert on_top.surf.kind == "floor" and on_top.state == S.STARTLED


def test_a_lower_floor_drops_the_cat_and_a_higher_one_lifts_it(world, grounded_brain):
    b = grounded_brain
    up = [world.monitor((0, 0, 1920, 1080), (0, 0, 1920, 1000), primary=True)]
    b.set_world(world.build_map(up, []), {})
    assert b.mode == "ground" and b.y == 1000
    down = [world.monitor((0, 0, 1920, 1080), (0, 0, 1920, 1080), primary=True)]
    b.set_world(world.build_map(down, []), {})
    assert b.mode == "air"
    assert run(b, 3, until=lambda br: br.mode == "ground") and b.y == 1080
    assert b.state != S.STARTLED


# ---------------------------------------------------------------- riding a dragged window (ride_window)
@pytest.mark.parametrize("old, new, jumped", [
    ((600, 500, 1300, 900), (900, 100, 1600, 500), False),     # dragged far, same size
    ((600, 500, 1300, 900), (610, 505, 1330, 910), False),     # size read off by a few px during a move
    ((600, 500, 1300, 900), (600, 500, 1500, 900), False),     # resized at the right edge
    ((600, 500, 1300, 900), (600, 400, 1300, 900), False),     # resized at the top edge
    ((600, 500, 1300, 900), (600, 300, 1300, 900), True),      # top moved >= 120 px: not a plain resize
    ((600, 500, 1300, 900), (0, 0, 1920, 1040), True),         # maximised
    ((600, 500, 1300, 900), (0, 0, 960, 1040), True),          # snapped to half the screen
    ((600, 500, 1300, 900), (600, 500, 602, 900), True),       # hidden (closing)
])
def test_window_jumped(old, new, jumped):
    assert Brain.window_jumped(old, new) is jumped


def test_dragging_the_window_moves_the_cat_with_it_and_it_holds_on(on_top, world):
    off = on_top.x - WIN[0]
    for i in range(1, 30):
        rect = (WIN[0] + 7 * i, WIN[1] - 3 * i, WIN[2] + 7 * i, WIN[3] - 3 * i)
        on_top.ride_window(10, rect)
        on_top.update(DT, world.ctx())
        assert on_top.mode == "ground" and on_top.y == rect[1]
        assert on_top.x - rect[0] == pytest.approx(off)
    assert on_top.current_anim()[0] == "hold_on"
    assert on_top.fps_hint() == on_top.t.fps_game
    run(on_top, 0.6)
    assert on_top.current_anim()[0] != "hold_on"


def test_the_window_closing_under_the_cat_drops_it_from_where_it_sat(on_top):
    on_top.ride_window(10, None)
    assert on_top.mode == "air" and on_top.y == 500
    assert on_top.smap.tops_of(10) == [] and on_top.smap.wall(10, "left") is None
    assert 10 not in on_top.win_rects
    lowest = [on_top.y]
    assert run(on_top, 5, until=lambda b: b.mode == "ground", each=lambda b: lowest.append(b.y))
    assert min(lowest) >= 500 and on_top.surf.kind == "floor" and on_top.state == S.STARTLED


def test_a_window_that_snaps_drops_the_cat_in_place(on_top):
    on_top.ride_window(10, (0, 0, 960, 1040))
    assert on_top.mode == "air" and on_top.y == 500


def test_a_window_carried_off_the_top_of_the_screen_drops_the_cat(on_top):
    on_top.ride_window(10, (600, 20, 1300, 420))
    assert on_top.mode == "air"


def test_other_windows_moving_only_update_their_rect(on_top):
    on_top.win_rects[11] = (0, 0, 100, 100)
    x, y = on_top.x, on_top.y
    on_top.ride_window(11, (50, 50, 150, 150))
    on_top.ride_window(12, (50, 50, 150, 150))              # unknown: ignored
    assert on_top.win_rects[11] == (50, 50, 150, 150) and 12 not in on_top.win_rects
    assert (on_top.x, on_top.y, on_top.mode, on_top.ride_t) == (x, y, "ground", 0.0)


# ---------------------------------------------------------------- trips: jump, drop, climb
def test_hopping_up_onto_a_low_window(world):
    wins = [world.window(10, (600, 940, 1300, 1200), 0)]
    brain = world.brain()
    brain.set_world(world.build_map(world.single_monitor(), wins), world.rects(wins))
    brain.place(300, 1000)
    world.settle(brain)
    top = brain.smap.tops[0]
    assert brain.go_to(top, 900, "walk")
    seen = set()
    assert run(brain, 30, until=lambda b: still(b) and b.surf.hwnd == 10, each=lambda b: seen.add(b.current_anim()[0]))
    assert {"walk", "prejump", "leap"} <= seen
    assert brain.x == pytest.approx(900)


def test_climbing_up_to_a_high_window(world, grounded_brain):
    b = grounded_brain
    calm(b, S.PLAYFUL)
    b.state_dur = 1e6
    modes = set()
    assert b.climb_something()
    assert [st.kind for st in b.plan] == ["walk", "climb", "walk"]
    assert run(b, 60, until=lambda br: still(br) and br.surf.hwnd == 10, each=lambda br: modes.add(br.mode))
    assert {"climb", "hang"} <= modes
    assert b.y == 500


def test_dropping_down_from_a_window(on_top):
    assert on_top.go_to(on_top.smap.floors[0], 200, "walk")
    modes = set()
    assert run(on_top, 30, until=lambda b: still(b) and b.surf.kind == "floor", each=lambda b: modes.add(b.mode))
    assert "air" in modes and on_top.x == pytest.approx(200)


def test_climbing_the_screen_edge_clinging_and_leaping_down(grounded_brain):
    b = grounded_brain
    calm(b, S.PLAYFUL)
    b.state_dur = 1e6
    assert b.start_edge_climb()
    assert b.plan[1].kind == "climb" and b.plan[1].wall.hwnd == 0 and b.plan[1].wall.side == "right"
    anims, mirrored = set(), set()

    def watch(br):
        anims.add(br.current_anim()[0])
        if br.mode == "climb":
            mirrored.add(br.pose()[1])

    assert run(b, 60, until=lambda br: br.mode == "hang", each=watch)
    assert b.cling and b.current_anim()[0] == "cling"
    assert run(b, 30, until=lambda br: br.mode == "ground", each=watch)
    assert {"run", "climb", "cling"} <= anims
    assert mirrored == {True}                     # on the left screen edge it faces the wall
    assert b.surf.kind == "floor"


def test_no_edge_climb_from_a_window_top(on_top):
    assert not on_top.start_edge_climb()


def test_go_to_needs_a_world_and_ground(world):
    brain = world.brain()
    assert not brain.go_to(None, 0)                      # no world yet
    assert not brain.climb_something()


# ---------------------------------------------------------------- the state machine
@pytest.mark.parametrize("state, nxt", [
    (S.SLEEPY, {S.SLEEPING}), (S.STARTLED, {S.CURIOUS}), (S.GRUMPY, {S.SITTING}), (S.STRETCHING, {S.SITTING}),
    (S.EATING, {S.CONTENT}), (S.CUDDLY, {S.CONTENT}), (S.ZOOMIES, {S.SITTING, S.GROOMING, S.LOAFING}),
    (S.PAUSED, {S.PAUSED}),
])
def test_forced_state_transitions(grounded_brain, world, state, nxt):
    b = grounded_brain
    calm(b, state)
    b.state_dur = 0.0
    b.update(DT, world.ctx())
    assert b.state in nxt


@pytest.mark.parametrize("state, energy, hour, nxt", [
    (S.SLEEPING, 0.99, 14, S.STRETCHING),       # fully rested: wakes up and stretches
    (S.DEEP_SLEEP, 0.99, 3, S.STRETCHING),
    (S.DEEP_SLEEP, 0.5, 3, S.SLEEPING),
])
def test_waking_up(grounded_brain, world, state, energy, hour, nxt):
    b = grounded_brain
    b.needs.energy = energy
    calm(b, state)
    b.state_dur = 0.0
    b.update(DT, world.ctx(hour=hour))
    assert b.state == nxt
    if nxt == S.STRETCHING:
        assert b.oneshot == "wake" and b.queue == ["stretch"]


def choices(brain, world, n=300, **ctx):
    seen = []
    for _ in range(n):
        calm(brain, S.SITTING)
        brain.state_dur = 0.0
        brain.update(DT, world.ctx(**ctx))
        seen.append(brain.state)
    return seen


def test_a_hungry_cat_begs_for_food(grounded_brain, world):
    grounded_brain.needs.hunger = 0.95
    seen = choices(grounded_brain, world)
    assert seen.count(S.HUNGRY) == 1                  # then on a 150 s cooldown
    assert grounded_brain.cooldown[S.HUNGRY] > grounded_brain.clock


def test_quiet_mode_only_picks_calm_states(grounded_brain, world):
    grounded_brain.needs.hunger = 0.95
    seen = choices(grounded_brain, world, quiet=True)
    assert all(STATES[s].calm for s in seen)


def test_no_zoomies_at_night_or_when_tired(grounded_brain, world):
    assert S.ZOOMIES not in choices(grounded_brain, world, hour=23)
    grounded_brain.needs.energy = 0.5
    assert S.ZOOMIES not in choices(grounded_brain, world)


def test_daytime_choices_vary(grounded_brain, world):
    seen = set(choices(grounded_brain, world))
    assert {S.SITTING, S.LOAFING, S.GROOMING, S.CONTENT, S.CURIOUS} <= seen


def test_quiet_mode_walks_to_the_bottom_right_corner(grounded_brain, world):
    run(grounded_brain, 120, c=world.ctx(quiet=True), until=lambda b: still(b) and b.x > 1700)
    assert grounded_brain.x == pytest.approx(1920 - 24 * 3, abs=30 * 3)


def test_the_user_coming_back_makes_the_cat_happy(grounded_brain, world):
    b = grounded_brain
    b.update(DT, world.ctx(user_idle=400))
    b.update(DT, world.ctx(user_idle=0.5))
    assert b.state == S.HAPPY and b.bubble.kind == "bubble_exclaim"


def test_a_busy_cursor_hovering_near_the_cat_makes_it_move_away(grounded_brain, world):
    b = grounded_brain
    x0 = b.x
    c = world.ctx(cursor=(int(x0) + 10, int(b.y) - 48), busy=True)
    assert run(b, 4, c=c, until=lambda br: bool(br.plan))
    assert b.goal[1] < x0


def test_a_playful_cat_swats_at_a_nearby_cursor(grounded_brain, world):
    b = grounded_brain
    calm(b, S.PLAYFUL)
    b.update(DT, world.ctx(cursor=(int(b.x) + 60, int(b.y) - 48)))
    assert b.oneshot == "swat"


@pytest.mark.parametrize("state, kinds", [(S.SLEEPING, {"z_small"}), (S.DEEP_SLEEP, {"z_big"}),
                                          (S.CONTENT, {"note"}), (S.CUDDLY, {"heart_small"})])
def test_resting_states_emit_their_particles(grounded_brain, state, kinds):
    calm(grounded_brain, state)
    run(grounded_brain, 4)
    assert kinds <= {p.kind for p in grounded_brain.particles}


def test_the_eyes_follow_a_nearby_cursor(grounded_brain, world):
    b = grounded_brain
    b.update(DT, world.ctx(cursor=(int(b.x) + 300, int(b.y) - 300)))
    assert b.look == (1, -1)
    b.update(DT, world.ctx(cursor=(int(b.x) + 5000, int(b.y))))
    assert b.look == (0, 0)


# ---------------------------------------------------------------- user events
def test_poke_makes_the_cat_happy_with_hearts(grounded_brain):
    b = grounded_brain
    mood = b.needs.mood
    b.poke()
    assert b.state == S.HAPPY
    assert b.needs.mood > mood
    assert sum(p.kind.startswith("heart") for p in b.particles) == 2


def test_too_many_pokes_make_it_grumpy(grounded_brain):
    b = grounded_brain
    for _ in range(b.t.pokes_to_grumpy):
        b.poke()
    assert b.state == S.GRUMPY
    b.poke()
    assert b.particles[-1].kind == "anger"


def test_poking_a_sleeping_cat_wakes_it_grumpy(grounded_brain):
    b = grounded_brain
    calm(b, S.SLEEPING)
    mood = b.needs.mood
    b.poke()
    assert b.state == S.GRUMPY and b.oneshot == "wake_grumpy" and b.needs.mood < mood


def test_double_click_makes_it_playful_with_a_hop(grounded_brain):
    grounded_brain.double_click()
    assert grounded_brain.state == S.PLAYFUL and grounded_brain.oneshot == "happy_hop"
    assert sum(p.kind == "sparkle" for p in grounded_brain.particles) == 2


def test_double_clicking_a_sleeping_cat_is_a_poke(grounded_brain):
    calm(grounded_brain, S.DEEP_SLEEP)
    grounded_brain.double_click()
    assert grounded_brain.state == S.GRUMPY


def test_long_petting_makes_it_cuddly(grounded_brain):
    b = grounded_brain
    for _ in range(11):
        b.petting(0.1)
    assert b.state == S.SITTING
    for _ in range(3):
        b.petting(0.1)
    assert b.state == S.CUDDLY
    assert any(p.kind.startswith("heart") for p in b.particles)


def test_petting_does_not_wake_a_sleeping_cat(grounded_brain):
    calm(grounded_brain, S.SLEEPING)
    for _ in range(30):
        grounded_brain.petting(0.1)
    assert grounded_brain.state == S.SLEEPING


@pytest.mark.parametrize("mood, anim", [(0.7, "dangle"), (0.9, "dangle_happy")])
def test_picking_up_carrying_and_dropping(grounded_brain, world, mood, anim):
    b = grounded_brain
    b.needs.mood = mood
    b.pick_up()
    assert (b.state, b.mode) == (S.HELD, "held")
    assert b.current_anim()[0] == anim
    assert b.fps_hint() == b.t.fps_active
    b.drag_to(900, 300)
    b.update(DT, world.ctx())
    assert (b.x, b.y) == (900, 300 + 27 * 3)                 # held by the scruff: feet below the pointer
    b.drop(-5000, 100)
    assert b.vx == -1500 and b.mode == "air" and b.state != S.HELD
    assert run(b, 5, until=lambda br: br.mode == "ground")
    assert b.state in (S.SITTING, S.CURIOUS, S.HAPPY)


def test_dropping_the_cat_onto_a_window_top(grounded_brain):
    b = grounded_brain
    b.pick_up()
    b.drag_to(900, 380)
    b.drop(0, 0)
    assert run(b, 5, until=lambda br: br.mode == "ground")
    assert b.surf.kind == "top" and b.state == S.SITTING


def test_feeding(grounded_brain):
    b = grounded_brain
    b.feed()
    assert b.state == S.EATING and b.needs.hunger == 0.0 and b.fish_t == 5.0
    assert run(b, 5.1, until=lambda br: br.fish_t <= 0)
    assert b.bones_t > 0
    assert run(b, 3, until=lambda br: br.state == S.CONTENT)


def test_feeding_a_sleeping_cat_wakes_it(grounded_brain):
    calm(grounded_brain, S.SLEEPING)
    grounded_brain.feed()
    assert grounded_brain.oneshot == "wake"


def test_playing_with_yarn_without_a_ball_game(grounded_brain):
    b = grounded_brain
    b.play()
    assert b.state == S.PLAYFUL and b.yarn is not None and b.oneshot == "happy_hop"
    run(b, 1.5)
    assert b.current_anim()[0] in ("crouch", "pounce")
    calm(b, S.SITTING)
    assert b.yarn is None


def test_pausing_on_the_ground_and_resuming(grounded_brain):
    b = grounded_brain
    b.set_paused(True)
    assert b.state == S.PAUSED
    run(b, 10)
    assert b.state == S.PAUSED and still(b)
    b.set_paused(False)
    assert b.state == S.SITTING


def test_pausing_in_the_air_pauses_on_landing(grounded_brain):
    b = grounded_brain
    b.place(300, 500)
    b.set_paused(True)
    assert b.state != S.PAUSED
    assert run(b, 5, until=lambda br: br.mode == "ground")
    assert b.state == S.PAUSED


def test_startle(grounded_brain):
    b = grounded_brain
    b.startle()
    assert b.state == S.STARTLED and b.bubble.kind == "bubble_exclaim"
    calm(b, S.SLEEPING)
    b.startle()
    assert b.state == S.SLEEPING and b.twitch_left == 0.4


@pytest.mark.parametrize("name, reply, state, oneshot", [
    ("sit", "ok: sit", S.SITTING, None),
    ("  SLEEP ", "ok: sleep", S.SLEEPY, "yawn"),
    ("trick", "ok: trick", S.HAPPY, "trick"),
    ("groom", "ok: groom", S.GROOMING, None),
    ("stretch", "ok: stretch", S.STRETCHING, "stretch"),
    ("zoomies", "ok: zoomies", S.ZOOMIES, "pounce"),
    ("rest", "ok: rest", S.LOAFING, None),
    ("happy", "ok: happy", S.HAPPY, "happy_hop"),
    ("play", "ok: play", S.PLAYFUL, "happy_hop"),
    ("come", "ok: come", S.HAPPY, None),
    ("climb", "ok: climb", S.PLAYFUL, None),
    ("wake", "ok: wake", S.SITTING, None),         # awake already: nothing happens
    ("dance", "unknown cat action 'dance'", S.SITTING, None),
])
def test_chat_commands(grounded_brain, name, reply, state, oneshot):
    assert grounded_brain.command(name) == reply
    assert grounded_brain.state == state
    assert grounded_brain.oneshot == oneshot


def test_come_and_climb_commands_start_a_trip(grounded_brain):
    grounded_brain.command("climb")
    assert grounded_brain.plan and grounded_brain.goal[0].kind == "top"
    calm(grounded_brain, S.SITTING)
    grounded_brain.command("come")
    assert grounded_brain.plan


def test_commands_wake_a_sleeping_cat_first(grounded_brain):
    calm(grounded_brain, S.SLEEPING)
    assert grounded_brain.command("sit") == "ok: sit"
    assert grounded_brain.oneshot == "wake"
    calm(grounded_brain, S.SLEEPING)
    grounded_brain.command("wake")
    assert grounded_brain.state == S.STRETCHING and grounded_brain.queue == ["stretch"]


def test_commands_while_held_or_paused(grounded_brain):
    b = grounded_brain
    b.pick_up()
    assert b.command("sit") == "I'm being held right now!"
    b.drop(0, 0)
    run(b, 5, until=lambda br: br.mode == "ground")
    b.set_paused(True)
    assert b.command("sit").startswith("I'm paused")
    assert b.state == S.PAUSED
    assert b.command("wake") == "ok: wake"
    assert b.command("resume") == "unknown cat action 'resume'"    # (allowed through, but not a command)


def test_nothing_to_climb_from_a_window_top(on_top):
    on_top.smap.walls.clear()
    on_top.smap.tops.clear()
    assert on_top.command("climb") == "nothing to climb from here right now"


# ---------------------------------------------------------------- render queries
def test_pose_overlays_blink_and_twitch(grounded_brain):
    b = grounded_brain
    calm(b, S.SITTING)
    b.blink_left, b.twitch_left = 0.1, 0.1
    pose, mirrored = b.pose()
    assert isinstance(pose, Pose) and pose.eyes == "closed" and pose.ears == "twitch"
    b.facing = -1
    assert b.pose()[1] is True


def test_the_eyes_look_where_the_cat_looks(grounded_brain):
    b = grounded_brain
    calm(b, S.SITTING)
    b.blink_left, b.twitch_left = 0.0, 0.0
    b.look, b.facing = (1, -1), -1
    assert b.pose()[0].look == (-1, -1)               # mirrored later, so x flips with facing


def test_head_position_flips_with_facing(grounded_brain):
    b = grounded_brain
    b.facing = 1
    hx, hy = b.head_rel()
    b.facing = -1
    assert b.head_rel() == (-hx, hy)
    wx, wy = b.head_world()
    assert (wx, wy) == (b.x - hx * 3, b.y + hy * 3)


def test_prejump_and_walk_animations(grounded_brain):
    b = grounded_brain
    b.prejump = 0.1
    assert b.current_anim() == ("prejump", 0.0)
    b.prejump = 0.0
    b.go_to(b.surf, 900, "run")
    assert b.current_anim()[0] == "run"


@pytest.mark.parametrize("state, fps", [(S.SITTING, "fps_idle"), (S.SLEEPING, "fps_sleep")])
def test_fps_hint_when_resting(grounded_brain, state, fps):
    b = grounded_brain
    calm(b, state)
    b.particles.clear()
    assert b.fps_hint() == getattr(b.t, fps)
    b.emit("heart", 0, 0, 0, 0, 1.0)
    assert b.fps_hint() > getattr(b.t, fps)


def test_particles_are_capped(grounded_brain):
    for _ in range(40):
        grounded_brain.emit("heart", 0, 0, 0, 0, 5.0)
    assert len(grounded_brain.particles) == 24


def test_bubbles_and_particles_expire(grounded_brain):
    b = grounded_brain
    b.show_bubble("bubble_fish", 0.5)
    b.emit("heart", 0, 0, 0, 0, 0.5)
    run(b, 0.6)
    assert b.bubble is None and not any(p.kind == "heart" and p.max_life == 0.5 for p in b.particles)


def test_status_line(grounded_brain):
    assert grounded_brain.status() == "sitting | energy 90% | hunger 30% | mood 70%"
    calm(grounded_brain, S.DEEP_SLEEP)
    assert grounded_brain.status().startswith("deep sleep | ")


def test_every_animation_the_brain_picks_exists(grounded_brain):
    b = grounded_brain
    for st in S:
        calm(b, st)
        name, _ = b.current_anim()
        assert name in ANIMS, st


def test_sleeping_on_through_the_day_or_deeper_at_night(grounded_brain, world):
    b = grounded_brain
    b.needs.energy = 0.5
    calm(b, S.SLEEPING)
    b.state_dur = 0.0
    b.update(DT, world.ctx(hour=14))
    assert b.state == S.SLEEPING and b.state_dur > 0
    b.needs.energy = 0.3
    for _ in range(20):
        calm(b, S.SLEEPING)
        b.state_dur = 0.0
        b.update(DT, world.ctx(hour=3))
        if b.state == S.DEEP_SLEEP:
            break
    assert b.state == S.DEEP_SLEEP


# ---------------------------------------------------------------- ledges and walls move with their window
def test_a_cat_on_a_ledge_rides_along_with_its_window(world):
    wins = [world.window(10, WIN, 0)]
    brain = world.brain()
    brain.set_world(world.build_map(world.single_monitor(), wins, ledges={10: [(650, 1250, 700)]}),
                    world.rects(wins))
    brain.place(900, 650)
    world.settle(brain)
    assert brain.surf.kind == "ledge" and brain.y == 700
    x = brain.x
    brain.ride_window(10, (640, 530, 1340, 930))
    assert (brain.x, brain.y, brain.mode) == (x + 40, 730, "ground")
    assert brain.surf.key == 200


def climbing(world, brain):
    calm(brain, S.PLAYFUL)
    brain.state_dur = 1e6
    assert brain.climb_something()
    assert run(brain, 30, until=lambda b: b.mode == "climb")
    return brain.x, brain.y, brain.wall


def test_a_cat_climbing_a_window_side_moves_with_the_window(world, grounded_brain):
    b = grounded_brain
    x, y, wall = climbing(world, b)
    b.ride_window(10, (WIN[0] + 20, WIN[1] - 10, WIN[2] + 20, WIN[3] - 10))
    assert (b.x, b.y, b.wall.x, b.mode) == (x + 20, y - 10, wall.x + 20, "climb")
    assert b.ride_t > 0
    wins = [world.window(10, (WIN[0] + 50, WIN[1], WIN[2] + 50, WIN[3]), 0)]
    b.set_world(world.build_map(world.single_monitor(), wins), world.rects(wins))
    assert b.wall.x == WIN[0] + 50 and b.x == pytest.approx(x + 50)
    b.set_world(world.build_map(world.single_monitor(), []), {})
    assert b.mode == "air" and b.wall is None


def test_a_monitor_change_drops_a_cat_climbing_the_screen_edge(world, grounded_brain):
    b = grounded_brain
    calm(b, S.PLAYFUL)
    b.state_dur = 1e6
    assert b.start_edge_climb()
    assert run(b, 30, until=lambda br: br.mode == "climb")
    b.set_world(world.build_map(world.single_monitor(), []), {})
    assert b.mode == "climb"                         # same layout: keeps climbing
    moved = [world.monitor((100, 0, 2020, 1080), (100, 0, 2020, 1040), primary=True)]
    b.set_world(world.build_map(moved, []), {})
    assert b.mode == "air"
