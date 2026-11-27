"""Fixtures for the minigame tests (helpers live in gamekit.py, importable from the tests)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from gamekit import World, build_world  # noqa: E402


@pytest.fixture
def world() -> World:
    return build_world()


@pytest.fixture
def bare_world() -> World:
    """No windows at all: games put up their own furniture."""
    return build_world(windows=[])
