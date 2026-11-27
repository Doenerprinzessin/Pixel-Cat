"""Animation timing and that every animation the engine asks for exists."""
from __future__ import annotations

import pytest

from pixelcat.engine.animations import ANIMS, Anim, all_poses
from pixelcat.engine.states import STATES, S
from pixelcat.sprites.cat_art import BODIES, HEAD_POS, Pose

A, B, C = Pose(body="sit"), Pose(body="loaf"), Pose(body="lie")

# what brain.py / ballgame.py play by name
BRAIN_ONESHOTS = ("land", "prejump", "wake", "wake_grumpy", "stretch", "trick", "sneeze", "swat", "ear_flick",
                  "pounce", "happy_hop", "swat_ball")
BRAIN_LOOPS = ("dangle", "dangle_happy", "fall", "leap", "air", "climb", "cling", "hang", "hold_on", "walk", "run",
               "zoom", "crouch")
BALL_LOOPS = ("ball_watch", "aim", "reach", "carry_air", "flip0", "flip1", "flip2", "flip3", "proud", "play_roll",
              "hug", "offer")


@pytest.mark.parametrize("t, pose", [(0.0, A), (0.99, A), (1.0, B), (2.9, B), (3.0, C), (3.49, C),
                                     (3.5, A), (4.6, B), (35.0, A)])
def test_looping_animation_wraps(t, pose):
    anim = Anim(((A, 1.0), (B, 2.0), (C, 0.5)), loop=True)
    assert anim.length == 3.5
    assert anim.at(t) == pose


@pytest.mark.parametrize("t, pose", [(0.5, A), (1.5, B), (2.0, B), (99.0, B)])
def test_one_shot_animation_holds_its_last_frame(t, pose):
    assert Anim(((A, 1.0), (B, 1.0)), loop=False).at(t) == pose


def test_empty_animation_is_the_default_pose():
    assert Anim((), loop=True).at(3.0) == Pose()


@pytest.mark.parametrize("name", BRAIN_LOOPS + BALL_LOOPS)
def test_engine_loops_exist_and_loop(name):
    assert ANIMS[name].loop and ANIMS[name].length > 0


@pytest.mark.parametrize("name", BRAIN_ONESHOTS)
def test_engine_one_shots_exist_and_end(name):
    assert not ANIMS[name].loop and 0 < ANIMS[name].length < 10


@pytest.mark.parametrize("state", list(S))
def test_every_state_has_existing_animations(state):
    sd = STATES[state]
    assert ANIMS[sd.idle].loop
    assert all(not ANIMS[a].loop for a in sd.enter)
    assert 0 < sd.duration[0] <= sd.duration[1]


def test_every_frame_has_a_positive_duration_and_a_drawable_body():
    for name, anim in ANIMS.items():
        for pose, d in anim.frames:
            assert d > 0, name
            assert pose.body in BODIES and pose.body in HEAD_POS, name


def test_all_poses_lists_every_frame_of_every_animation():
    poses = all_poses()
    assert len(poses) == sum(len(a.frames) for a in ANIMS.values())
    assert set(poses) == {p for a in ANIMS.values() for p, _ in a.frames}


def test_backflip_frames_turn_a_quarter_each():
    assert [ANIMS[f"flip{k}"].at(0).rot for k in range(4)] == [0, 1, 2, 3]
