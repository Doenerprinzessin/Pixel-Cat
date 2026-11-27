"""Shared data for the sprite tests: the poses the engine actually asks for."""
from __future__ import annotations

import pytest

from pixelcat.engine.animations import all_poses


@pytest.fixture(scope="session")
def poses():
    """Every distinct pose any animation uses."""
    return list(dict.fromkeys(all_poses()))


@pytest.fixture(scope="session")
def pose_sample(poses):
    """One pose per (body, mid-air rotation) - enough to put every hat on every kind of head."""
    return list({(p.body, p.rot % 4): p for p in poses}.values())
