"""games/art.py: the minigame pixel art (palette canvases, cache, turns and mirroring)."""
from __future__ import annotations

import pytest

import pixelcat.games  # noqa: F401  (registers the fishing meter art, patches box surfaces)
from pixelcat.games import art

PROPS = ["shelf", "table", "sofa", "armchair", "plant", "bookcase", "box_s", "box_m", "box_l", "box_long"]
THINGS = ["pond", "jar", "basket", "treat", "laser0", "laser1", "bobber"]
MICE = [f"mouse_{f}{g}" for f in ("run0", "run1", "sniff") for g in ("", "_gold")]
FISH = [f"fish_{s.key}" for s in art.SPECIES]
FISHING_UI = [f"meter{z}" for z in range(8)] + ["marker"]
ALL_KEYS = PROPS + THINGS + MICE + list(art.ITEMS) + FISH + FISHING_UI


@pytest.mark.parametrize("key", ALL_KEYS)
def test_every_piece_draws_with_palette_colours_only(key: str) -> None:
    cv = art.canvas(key)
    assert cv.w > 0 and cv.h > 0
    assert art.size(key) == (cv.w, cv.h)
    used = {c for row in cv.px for c in row}
    assert used - {0}, "empty art"
    assert used - {0} <= set(art.COLORS)


def test_canvas_is_cached() -> None:
    assert art.canvas("mug") is art.canvas("mug")


def test_mirrored_piece_is_flipped_left_right() -> None:
    base, mirrored = art.canvas("mouse_run0"), art.canvas("mouse_run0!")
    assert (mirrored.w, mirrored.h) == (base.w, base.h)
    assert mirrored.px == [list(reversed(row)) for row in base.px]


@pytest.mark.parametrize("turns", [1, 3])
def test_quarter_turns_swap_width_and_height(turns: int) -> None:
    w, h = art.size("phone")
    assert art.size(f"phone@{turns}") == (h, w)


def test_two_quarter_turns_turn_it_upside_down_and_four_turn_it_back() -> None:
    base = art.canvas("books")
    half = art.canvas("books@2")
    assert half.px == [list(reversed(row)) for row in reversed(base.px)]
    assert art.canvas("books@4").px == base.px


def test_mirror_and_turn_combine() -> None:
    w, h = art.size("fish_koi")
    assert art.size("fish_koi!@1") == (h, w)


@pytest.mark.parametrize("key, known", [
    ("mug", True), ("mug@3", True), ("mouse_sniff_gold!", True), ("fish_golden_koi", True), ("meter7", True),
    ("nope", False), ("fish_shark", False), ("", False),
])
def test_known(key: str, known: bool) -> None:
    assert art.known(key) is known


def test_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        art.canvas("nope")


@pytest.mark.parametrize("maker", [art.plant, art.bookcase, art.jar, lambda: art.fish("trout"),
                                   lambda: art.fish("koi")])
def test_random_details_are_the_same_every_time(maker) -> None:
    assert maker().px == maker().px


def test_species_table() -> None:
    keys = [s.key for s in art.SPECIES]
    assert len(keys) == 13 == len(set(keys))
    assert art.SPECIES_BY_KEY == {s.key: s for s in art.SPECIES}
    assert {s.key for s in art.SPECIES if s.rarity == 0} == {"boot", "can"}
    assert max(art.SPECIES, key=lambda s: s.rarity).key == "golden_koi"
    assert all(0 <= s.rarity <= 5 and s.weight > 0 for s in art.SPECIES)


def test_fragile_items_are_items() -> None:
    assert art.FRAGILE < set(art.ITEMS)
    assert {"pencils", "books"} == set(art.ITEMS) - art.FRAGILE


def test_surface_table_covers_every_prop() -> None:
    assert set(art.SURFACE) == set(PROPS)
    # importing the box fort makes the cat stand inside the boxes (below the flaps)
    for box in ("box_s", "box_m", "box_l", "box_long"):
        assert art.SURFACE[box] == (5, 3, 3)
    assert art.SURFACE["sofa"] == (3, 2, 2)


def test_box_sizes_grow() -> None:
    sizes = [art.size(k) for k in ("box_s", "box_m", "box_l")]
    assert sizes == sorted(sizes)
    assert art.size("box_long")[0] > art.size("box_l")[0]
