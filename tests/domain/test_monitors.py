"""pixelcat.domain.monitors: Monitor geometry, monitor_at and primary (plain data, no platform calls)."""
from __future__ import annotations

import pytest

from pixelcat.domain import Monitor, monitor_at, primary

LEFT = Monitor(2, (-1920, 0, 0, 1080), (-1920, 0, 0, 1040), 96, False, r"\\.\DISPLAY2")
MAIN = Monitor(1, (0, 0, 2560, 1440), (0, 0, 2560, 1392), 144, True, r"\\.\DISPLAY1")
ABOVE = Monitor(3, (0, -1080, 1920, 0), (0, -1080, 1920, -40), 120, False, r"\\.\DISPLAY3")


@pytest.mark.parametrize("x, y, inside", [
    (0, 0, True), (2559, 1439, True), (2560, 0, False), (0, 1440, False), (-1, 5, False), (1280.5, 720.5, True),
])
def test_contains_is_half_open(x, y, inside):
    assert MAIN.contains(x, y) is inside


@pytest.mark.parametrize("x, y, d2", [
    (100, 100, 0), (2559, 1439, 0), (2560, 0, 1), (-3, -4, 25), (2569, 1449, 200),
])
def test_distance2_to_the_nearest_pixel(x, y, d2):
    assert MAIN.distance2(x, y) == d2


def test_scale_factor():
    assert MAIN.scale_factor == 1.5 and LEFT.scale_factor == 1.0


@pytest.mark.parametrize("x, y, expected", [
    (10, 10, MAIN), (-10, 10, LEFT), (10, -10, ABOVE),
    (5000, 100, MAIN), (-5000, 500, LEFT), (100, -5000, ABOVE),
])
def test_monitor_at_containing_or_nearest(x, y, expected):
    assert monitor_at([LEFT, MAIN, ABOVE], x, y) is expected


def test_primary_or_first():
    assert primary([LEFT, MAIN, ABOVE]) is MAIN
    assert primary([ABOVE, LEFT]) is ABOVE
