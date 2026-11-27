"""ui.geometry: placing popups next to the cat, physical <-> Qt coordinates."""
from __future__ import annotations

import pytest

from pixelcat.ui import geometry
from pixelcat.ui.geometry import place_near

WORK = (0, 0, 1920, 1040)


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return geometry._overlap(a, b) > 0


@pytest.mark.parametrize(("a", "b", "area"), [
    ((0, 0, 10, 10), (5, 5, 15, 15), 25),
    ((0, 0, 10, 10), (10, 0, 20, 10), 0),
    ((0, 0, 10, 10), (20, 20, 30, 30), 0),
    ((0, 0, 10, 10), (2, 2, 4, 4), 4),
])
def test_overlap_is_the_shared_area(a, b, area) -> None:  # type: ignore[no-untyped-def]
    assert geometry._overlap(a, b) == area


def test_place_near_prefers_the_right_of_the_cat_with_the_gap() -> None:
    cat = (800, 500, 900, 600)
    assert place_near(cat, (100, 100), WORK, gap=8) == (908, 500)


def test_place_near_picks_the_closest_free_spot() -> None:
    cat = (800, 500, 900, 600)
    assert place_near(cat, (200, 100), WORK, gap=8) == (750, 392)      # above: its centre is nearest


def test_place_near_goes_left_when_the_right_is_off_screen() -> None:
    cat = (1800, 500, 1900, 600)
    x, y = place_near(cat, (100, 100), WORK, gap=8)
    assert (x, y) == (1692, 500)
    assert not _overlaps((x, y, x + 100, y + 100), cat)


@pytest.mark.parametrize("cat", [(0, 0, 100, 100), (1820, 940, 1920, 1040), (900, 0, 1000, 60),
                                 (900, 980, 1000, 1040), (0, 500, 40, 560)])
def test_place_near_stays_inside_the_work_area_and_off_the_cat(cat) -> None:  # type: ignore[no-untyped-def]
    w, h = 300, 200
    x, y = place_near(cat, (w, h), WORK)
    assert WORK[0] <= x and x + w <= WORK[2] and WORK[1] <= y and y + h <= WORK[3]
    assert not _overlaps((x, y, x + w, y + h), cat)


def test_place_near_a_popup_bigger_than_the_screen_is_clamped_to_the_top_left() -> None:
    assert place_near((100, 100, 200, 200), (3000, 3000), WORK) == (0, 0)


def test_place_near_on_a_second_monitor_left_of_the_primary() -> None:
    work = (-1920, 0, 0, 1040)
    x, y = place_near((-500, 400, -400, 500), (200, 100), work)
    assert -1920 <= x and x + 200 <= 0 and 0 <= y and y + 100 <= 1040


@pytest.mark.qt
def test_phys_to_qt_is_the_identity_without_qt_scaling(qapp) -> None:  # type: ignore[no-untyped-def]
    sc = qapp.primaryScreen()
    g = sc.geometry()
    assert sc.devicePixelRatio() == pytest.approx(1.0)     # QT_ENABLE_HIGHDPI_SCALING=0
    p = geometry.phys_to_qt(g.x() + 17, g.y() + 23)
    assert (p.x(), p.y()) == (g.x() + 17, g.y() + 23)
    assert geometry.dpr_at(g.x() + 1, g.y() + 1) == pytest.approx(1.0)


@pytest.mark.qt
def test_screen_for_phys_finds_the_screen_or_the_nearest_one(qapp) -> None:  # type: ignore[no-untyped-def]
    screens = qapp.screens()
    g = screens[0].geometry()
    assert geometry.screen_for_phys(g.x() + 5, g.y() + 5) is screens[0]
    far = geometry.screen_for_phys(-10**7, -10**7)
    assert far in screens


@pytest.mark.qt
def test_phys_to_qt_without_screens_passes_points_through(qapp, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(geometry.QGuiApplication, "screens", staticmethod(lambda: []))
    assert geometry.screen_for_phys(5, 5) is None
    p = geometry.phys_to_qt(123, 456)
    assert (p.x(), p.y()) == (123, 456)
    assert geometry.dpr_at(1, 1) == 1.0
