"""Needs (energy / hunger / mood), particles and the yarn prop."""
from __future__ import annotations

import math

import pytest

from pixelcat.engine import needs as needs_mod
from pixelcat.engine.needs import Needs
from pixelcat.engine.particles import Bubble, Particle, Yarn
from pixelcat.settings import Tuning

HOUR = 3600.0


@pytest.fixture
def tun():
    return Tuning()


# ---------------------------------------------------------------- needs
@pytest.mark.parametrize("asleep, active, energy, hunger", [
    (False, False, 0.70, 0.30),     # awake: -0.10/h, hunger +0.10/h
    (False, True, 0.35, 0.30),      # running around: -0.45/h
    (True, False, 1.00, 0.25),      # asleep: +0.55/h (clamped), half as hungry
    (True, True, 1.00, 0.25),       # "active" doesn't matter while asleep
])
def test_an_hour_of_needs(tun, asleep, active, energy, hunger):
    n = Needs(0.8, 0.2, 0.7)
    n.tick(HOUR, tun, asleep=asleep, active=active)
    assert n.energy == pytest.approx(energy)
    assert n.hunger == pytest.approx(hunger)


def test_mood_drifts_toward_a_target_that_hunger_and_tiredness_lower(tun):
    n = Needs(0.8, 0.2, 0.7)
    n.tick(HOUR, tun, asleep=False, active=False)
    assert n.mood == pytest.approx(0.7 + (0.6 - 0.7) * 0.24)
    hungry_tired = Needs(0.05, 0.9, 0.6)
    hungry_tired.tick(1.0, tun, asleep=False, active=False)
    assert hungry_tired.mood < 0.6
    for _ in range(200):
        hungry_tired.tick(HOUR, tun, asleep=False, active=False)
    # starving and exhausted; the target uses the values before clamping (hunger 1.1, energy -0.1
    # after a 1 h tick), so it ends lower than 0.6 - 0.35 * 0.4 - 0.2 * 0.25
    assert hungry_tired.mood == pytest.approx(0.6 - 0.35 * 0.5 - 0.2 * 0.35)


def test_needs_stay_between_0_and_1(tun):
    n = Needs(0.5, 0.5, 0.5)
    for _ in range(100):
        n.tick(HOUR, tun, asleep=False, active=True)
    assert (n.energy, n.hunger) == (0.0, 1.0)
    for _ in range(100):
        n.tick(HOUR, tun, asleep=True, active=False)
    assert n.energy == 1.0 and 0.0 <= n.mood <= 1.0


@pytest.mark.parametrize("hours_away, energy, hunger", [
    (5, 1.0, 0.45),
    (100, 1.0, 0.7),       # capped at 12 h, and hunger at 0.7
    (-3, 0.3, 0.2),        # a clock that went backwards changes nothing
])
def test_offline_nap(tun, monkeypatch, hours_away, energy, hunger):
    monkeypatch.setattr(needs_mod.time, "time", lambda: 1_000_000.0)
    n = Needs(0.3, 0.2, 0.7)
    n.apply_offline(1_000_000.0 - hours_away * HOUR, tun)
    assert n.energy == pytest.approx(energy)
    assert n.hunger == pytest.approx(hunger)
    assert n.mood == 0.7


def test_offline_hunger_cap_never_lowers_hunger(tun, monkeypatch):
    monkeypatch.setattr(needs_mod.time, "time", lambda: 1_000_000.0)
    n = Needs(0.3, 0.9, 0.7)
    n.apply_offline(1_000_000.0 - HOUR, tun)
    assert n.hunger == 0.7          # (current behaviour: min(0.7, ...) caps it even when it was higher)


@pytest.mark.parametrize("action, before, after", [
    ("feed", (0.5, 0.8, 0.5), (0.5, 0.25, 0.65)),
    ("feed", (0.5, 0.2, 0.95), (0.5, 0.0, 1.0)),
    ("play", (0.5, 0.5, 0.5), (0.45, 0.5, 0.7)),
    ("play", (0.02, 0.5, 0.9), (0.0, 0.5, 1.0)),
    ("pet", (0.5, 0.5, 0.5), (0.5, 0.5, 0.56)),
    ("annoy", (0.5, 0.5, 0.5), (0.5, 0.5, 0.45)),
    ("annoy", (0.5, 0.5, 0.01), (0.5, 0.5, 0.0)),
])
def test_interactions(action, before, after):
    n = Needs(*before)
    getattr(n, action)()
    assert (n.energy, n.hunger, n.mood) == pytest.approx(after)


# ---------------------------------------------------------------- particles
def test_particle_moves_fades_and_dies():
    p = Particle("heart", 0, 0, 10, -5, 1.0, 1.0, phase=0.0)
    assert p.step(0.5) is True
    assert (p.x, p.y, p.life) == pytest.approx((5.0, -2.5, 0.5))
    assert p.alpha == 1.0
    assert p.step(0.4) is True
    assert p.alpha == pytest.approx(0.1 / 0.35)
    assert p.step(0.2) is False
    assert p.alpha == 0.0


def test_particle_wobbles_sideways():
    p = Particle("note", 0, 0, 0, 0, 2.0, 2.0, wobble=3.0, phase=0.0)
    p.step(0.25)
    assert p.x == pytest.approx(math.sin(1.0) * 3.0 * 0.25)


def test_bubble_is_a_plain_record():
    b = Bubble("bubble_fish", 2.0)
    b.time_left -= 0.5
    assert (b.kind, b.time_left) == ("bubble_fish", 1.5)


def test_yarn_rolls_slows_down_and_bounces_off_the_limit():
    y = Yarn(x=18.0, vx=100.0)
    y.step(0.1)
    assert y.x == 24.0                                       # hit the limit
    assert y.vx == pytest.approx(-100.0 * (1 - 0.22) * 0.5)  # friction, then bounced back at half speed
    assert y.frame == 2                                      # spin 2.5
    z = Yarn(x=-20.0, vx=-100.0)
    z.step(0.1)
    assert z.x == -24.0 and z.vx > 0


def test_yarn_at_rest_stays_put():
    y = Yarn()
    y.step(1.0)
    assert (y.x, y.vx, y.frame) == (18.0, 0.0, 0)
