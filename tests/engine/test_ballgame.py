"""Ball game: ball physics, the hunt, rounds and the whole game (ported from tools/ball_test.py)."""
from __future__ import annotations

import collections
import math
import random

import pytest

from pixelcat.engine import ballgame
from pixelcat.engine.animations import ANIMS
from pixelcat.engine.ballgame import BALL_R, BallGame, Fx, continue_chance
from pixelcat.engine.states import S

DT = 1 / 60


def make(world, seed: int = 1, x: float | None = None):
    mons, wins = world.ball_monitors(), world.ball_windows()
    brain = world.brain(seed)
    brain.scale = 3
    brain.set_world(world.build_map(mons, wins), world.rects(wins))
    brain.place(x if x is not None else 900 + seed % 700, 400)
    game = BallGame(brain)
    game.rng.seed(seed * 7 + 1)
    brain.game = game
    for _ in range(int(2.0 / DT)):          # let the cat land first
        brain.update(DT, world.ctx(cursor=(0, 0)))
    return brain, game


def tick(world, brain, game, seconds: float, cursor=(0, 0), until=None) -> bool:
    for _ in range(int(seconds / DT)):
        brain.update(DT, world.ctx(cursor=cursor))
        game.update(DT, cursor)
        if until is not None and until():
            return True
    return until is None


@pytest.fixture
def game_world(world):
    return make(world)


# ---------------------------------------------------------------- rounds
def test_the_chance_of_another_round_shrinks_to_zero():
    chances = [continue_chance(k) for k in range(1, 14)]
    assert chances[:3] == pytest.approx([0.75, 0.68, 0.61])
    assert all(a > b for a, b in zip(chances[:11], chances[1:11]))
    assert chances[11:] == [0.0, 0.0]


@pytest.mark.parametrize("rnd, style, crown, trail", [(1, "yarn", False, False), (2, "yarn", False, True),
                                                      (3, "rainbow", False, True), (7, "rainbow", True, True),
                                                      (8, "gold", True, True)])
def test_each_round_unlocks_more(game_world, rnd, style, crown, trail):
    _brain, game = game_world
    game.start()
    game.round = rnd
    assert game.ball_style() == style
    assert game.crown() is crown
    assert game.has("trail") is trail


# ---------------------------------------------------------------- start / stop
def test_start_pops_a_ball_in_front_of_the_cat(game_world):
    brain, game = game_world
    assert game.cat_anim() is None and game.look_target() is None
    game.start()
    assert game.active and game.phase == "intro"
    assert game.ball.visible and game.ball.r == BALL_R * 3
    assert brain.state == S.BALL and brain.floor_only
    assert brain.bubble.kind == "bubble_exclaim"
    assert game.cat_anim() == ("ball_watch", 0.0)
    assert len(game.fx) == 10
    ball = game.ball
    game.start()                                     # already running: nothing happens
    assert game.ball is ball


def test_start_needs_a_world(world):
    brain = world.brain()
    game = BallGame(brain)
    game.start()
    assert not game.active


def test_start_wakes_a_sleeping_cat_and_unpauses_it(game_world):
    brain, game = game_world
    brain.set_paused(True)
    game.start()
    assert not brain.paused and brain.state == S.BALL
    game.stop(poof=False)
    brain.set_state(S.SLEEPING, enter=(), travel=False)
    game.start()
    assert brain.oneshot == "wake"


def test_a_cat_on_a_window_top_hops_down_to_play(world):
    brain, game = make(world, x=1900)
    brain.place(1900, 200)
    tick(world, brain, game, 3, until=lambda: brain.mode == "ground")
    assert brain.surf.kind == "top"
    game.start()
    assert brain.mode == "air"
    tick(world, brain, game, 5, until=lambda: brain.mode == "ground")
    assert brain.surf.kind == "floor"


@pytest.mark.parametrize("catches, state", [(0, S.SITTING), (2, S.CONTENT)])
def test_stop_ends_the_game_and_reports_the_catches(game_world, catches, state):
    brain, game = game_world
    ended = []
    game.on_end = ended.append
    game.start()
    game.catches = catches
    mood = brain.needs.mood
    game.stop()
    assert ended == [catches]
    assert not game.active and game.phase == "off" and not game.ball.visible
    assert brain.state == state and not brain.floor_only
    assert (brain.needs.mood > mood) is (catches > 0)
    assert any(f.kind == "poof" for f in game.fx)
    game.stop()
    assert ended == [catches]                        # only once


# ---------------------------------------------------------------- the ball
def test_a_free_ball_falls_bounces_and_comes_to_rest_on_the_floor(game_world, world):
    brain, game = game_world
    game.start()
    floor = brain.smap.floor_at(game.ball.x, brain.y).y
    bounced = []
    prev_vy = [0.0]

    def watch():
        if prev_vy[0] > 0 and game.ball.vy < 0:
            bounced.append(prev_vy[0])
        prev_vy[0] = game.ball.vy
        return game.phase == "wait"

    assert tick(world, brain, game, 10, until=watch)
    assert bounced
    assert game.ball.on_floor and game.ball.vx == 0 and game.ball.y == floor - game.ball.r


def test_ball_bounces_off_the_screen_sides(game_world):
    brain, game = game_world
    game.start()
    ball = game.ball
    ball.x, ball.y, ball.vx, ball.vy = 4470.0, 900.0, 900.0, 0.0     # the right edge of the second monitor
    game.update(0.05)
    assert ball.x == 4480 - ball.r and ball.vx == pytest.approx(-900 * ballgame.WALL_BOUNCE)


def test_grab_drag_and_throw(game_world):
    brain, game = game_world
    game.start()
    assert game.can_grab()
    game.grab(1000, 700)
    assert game.ball.held and game.phase == "chase"
    game.drag(-500, -500)                                    # clamped to the screen
    assert (game.ball.x, game.ball.y) == (game.ball.r, game.ball.r)
    game.drag(1000, 5000)                                    # ... and above the floor
    assert game.ball.y == 1552 - game.ball.r
    game.throw(99999, -99999)
    assert not game.ball.held and (game.ball.vx, game.ball.vy) == (2600, -2600)


def test_nothing_happens_without_holding_the_ball(game_world):
    _brain, game = game_world
    game.drag(5, 5)
    game.throw(100, 100)
    assert (game.ball.x, game.ball.vx) == (0.0, 0.0)
    assert not game.can_grab()
    game.grab(5, 5)
    assert not game.ball.held


def test_held_velocity_follows_the_hand_and_a_still_hand_is_still(game_world):
    _brain, game = game_world
    game.start()
    game.clock = 10.0
    game.grab(1000, 700)
    game.clock = 10.1
    game.drag(1020, 690)
    assert game.held_velocity() == pytest.approx((200.0, -100.0))
    game.clock = 10.6
    assert game.held_velocity() == (0.0, 0.0)


def test_prediction_of_a_held_ball_is_capped(game_world):
    _brain, game = game_world
    game.start()
    game.clock = 10.0
    game.grab(1000, 700)
    game.clock = 10.05
    game.drag(1400, 700)                                     # a jerk of the mouse
    x, y = game.predict_ball(1.0)
    assert math.hypot(x - 1400, y - 700) == pytest.approx(70 * 3)


def test_prediction_of_a_resting_and_a_falling_ball(game_world):
    brain, game = game_world
    game.start()
    ball = game.ball
    floor = brain.smap.floor_at(1000, brain.y).y
    ball.x, ball.y, ball.vx, ball.vy = 1000.0, floor - ball.r, 0.0, 0.0
    assert game.predict_ball(1.0) == pytest.approx((1000.0, floor - ball.r))
    ball.y = 500.0
    x, y = game.predict_ball(0.2)
    assert x == 1000.0 and 500.0 < y < floor


def test_the_cat_learns_from_misses():
    game = BallGame.__new__(BallGame)
    game.round, game.misses = 1, 0
    first = game.skill()
    game.misses = 6
    later = game.skill()
    assert later["p"] == 1.0 and first["p"] == pytest.approx(0.35)
    assert later["err"] == 0.0 < first["err"]
    assert later["home"] > first["home"] + 400 and later["reach"] > first["reach"]
    assert later["recover"] == 0.15


def test_effects_fade_and_expire(game_world):
    _brain, game = game_world
    fx = Fx("spark", 0, 0, 10, 0, life=1.0, max_life=1.0)
    assert fx.alpha == 1.0
    fx.life = 0.2
    assert fx.alpha == pytest.approx(0.5)
    game.start()
    assert game.fx
    for _ in range(int(1.5 / DT)):
        game.update(DT)
    assert not any(f.kind == "spark" and f.max_life < 1.0 for f in game.fx)


# ---------------------------------------------------------------- the cat while playing
def test_the_cat_watches_the_ball(game_world, world):
    brain, game = game_world
    game.start()
    tick(world, brain, game, 0.2)
    assert game.look_target() == (game.ball.x, game.ball.y)
    hx, hy = brain.head_world()
    expect_x = 0 if abs(game.ball.x - hx) < 30 else (1 if game.ball.x > hx else -1)
    assert brain.look[0] == expect_x


def test_clicks_and_petting_dont_interrupt_the_game(game_world):
    brain, game = game_world
    game.start()
    brain.poke()
    brain.double_click()
    for _ in range(30):
        brain.petting(0.1)
    assert brain.state == S.BALL and brain.playing()
    assert any(p.kind.startswith("heart") for p in brain.particles)


def test_brain_play_starts_the_ball_game(game_world):
    brain, game = game_world
    brain.play()
    assert game.active and brain.yarn is None
    assert brain.fps_hint() == brain.t.fps_game


def test_flip_frames(game_world):
    _brain, game = game_world
    game.start()
    game.anim, game.flip_t = "flip", 0.08
    assert game.cat_anim() == ("flip1", 0.0)
    game.flip_t = -1.0
    assert game.flip_frame() == 0


def test_an_ignored_ball_ends_the_game(world):
    brain, game = make(world, 77)
    ended = []
    game.on_end = ended.append
    game.start()
    tick(world, brain, game, ballgame.WAIT_LIMIT + 60, until=lambda: bool(ended))
    assert ended == [0]
    assert brain.state != S.BALL and not brain.floor_only


# ---------------------------------------------------------------- whole games with simulated players
class Player:
    """A simulated user: grabs the ball when it's offered and plays in one style."""

    def __init__(self, style: str, rng: random.Random) -> None:
        self.style, self.rng = style, rng
        self.t, self.rest_t = 0.0, 0.0
        self.holding = False
        self.anchor = self.last = (0.0, 0.0)
        self.throw_at = 0.0

    def step(self, game: BallGame, brain, dt: float) -> tuple[int, int]:
        self.t += dt
        ball = game.ball
        resting = ball.on_floor and abs(ball.vx) < 1
        self.rest_t = self.rest_t + dt if resting else 0.0
        if not self.holding and game.can_grab() and self.t > 0.6 and (
                (game.phase in ("intro", "wait") and resting) or self.rest_t > 2.5):
            self.holding, self.t = True, 0.0
            game.grab(ball.x, ball.y)
            side = self.rng.choice((-1, 1))
            self.anchor = (brain.x + side * self.rng.uniform(80, 400), brain.y - self.rng.uniform(150, 700))
            self.throw_at = self.rng.uniform(0.6, 1.6)
        if self.holding and not ball.held:
            self.holding, self.t = False, 0.0
        if not self.holding:
            return int(ball.x), int(ball.y)
        ax, ay = self.anchor
        if self.t < 0.5:
            f = self.t / 0.5
            x, y = ball.x + (ax - ball.x) * f, ball.y + (ay - ball.y) * f
        elif self.style == "still":
            x, y = ax, ay
        elif self.style == "wave":
            x, y = ax + math.sin(self.t * 2.2) * 260, ay + math.sin(self.t * 3.1) * 90
        else:
            x, y = ax + self.t * 900 * (1 if ax < brain.x else -1), ay - self.t * 300
            if self.t >= self.throw_at:
                vx, vy = (x - self.last[0]) / dt, (y - self.last[1]) / dt
                game.drag(x, y)
                game.throw(vx, vy)
                self.holding, self.t = False, 0.0
                return int(x), int(y)
        self.last = (x, y)
        game.drag(x, y)
        return int(x), int(y)


def run_game(world, seed: int, style: str, limit: float = 600.0) -> dict:
    brain, game = make(world, seed)
    player = Player(style, random.Random(seed))
    ended: list[int] = []
    game.on_end = ended.append
    brain.play()
    phases, anims, errs = set(), set(), []
    misses_before_catch: list[int] = []
    prev_misses, last_catches, last_phase, last_change, t = 0, 0, "", 0.0, 0.0
    mons = world.ball_monitors()
    left, right = min(m.rect[0] for m in mons), max(m.rect[2] for m in mons)
    while t < limit and game.active:
        t += DT
        cur = player.step(game, brain, DT)
        brain.update(DT, world.ctx(cursor=cur))
        game.update(DT, cur)
        phases.add(game.phase)
        anims.add(brain.current_anim()[0])
        if game.misses < prev_misses and game.catches > last_catches:
            misses_before_catch.append(prev_misses)
        prev_misses = game.misses
        if game.phase != last_phase or game.catches != last_catches:
            last_change, last_phase, last_catches = t, game.phase, game.catches
        if t - last_change > 120:
            errs.append(f"stuck in {game.phase}/{game.task}")
            break
        ball = game.ball
        if not all(math.isfinite(v) for v in (brain.x, brain.y, ball.x, ball.y)):
            errs.append("NaN position")
            break
        if not (left - 5 <= brain.x <= right + 5) or not (-400 <= brain.y <= 1605):
            errs.append(f"cat off screen at {brain.x:.0f},{brain.y:.0f}")
            break
        if game.active and ball.visible and not (left <= ball.x <= right and ball.y <= 1600):
            errs.append(f"ball off screen at {ball.x:.0f},{ball.y:.0f}")
            break
        if len(game.fx) > 220:
            errs.append("too many effects")
    return {"errs": errs, "ended": ended, "catches": game.catches, "round": game.round, "phases": phases,
            "anims": anims, "misses": misses_before_catch, "brain": brain, "active": game.active}


@pytest.mark.parametrize("style", ["still", "wave", "throw"])
@pytest.mark.parametrize("seed", [1000, 1001, 1002, 1003])
def test_whole_games_end_by_themselves_with_a_catch(world, style, seed):
    r = run_game(world, seed, style)
    assert r["errs"] == []
    assert not r["active"] and r["ended"] == [r["catches"]] and r["catches"] >= 1
    assert r["round"] - 1 <= r["catches"] <= r["round"]          # its own batted ball is not a new catch
    assert r["brain"].state != S.BALL and not r["brain"].floor_only
    assert {"chase", "caught", "end"} <= r["phases"]
    assert r["anims"] <= set(ANIMS)
    assert all(m <= 12 for m in r["misses"])


def test_many_games_play_out_as_designed(world):
    rounds = collections.Counter()
    phases, misses = set(), []
    for i in range(24):
        r = run_game(world, 2000 + i, ("still", "wave", "throw")[i % 3])
        assert r["errs"] == [] and r["ended"]
        rounds[r["catches"]] += 1
        phases |= r["phases"]
        misses += r["misses"]
    assert {"intro", "wait", "chase", "caught", "play", "end"} <= phases
    assert max(rounds) <= 12
    assert any(m > 0 for m in misses)                # it has to learn...
    assert sum(misses) / len(misses) <= 4            # ...but catches it after a few tries
    assert rounds[1] < 24                            # some games go on after round 1

