"""Canvas: the palette-indexed raster every sprite is drawn on."""
from __future__ import annotations

import pytest

from pixelcat.sprites.raster import Canvas


def lit(cv: Canvas, c: int | None = None) -> set[tuple[int, int]]:
    return {(x, y) for y in range(cv.h) for x in range(cv.w) if (cv.px[y][x] if c is None else cv.px[y][x] == c)}


def from_rows(rows: list[str]) -> Canvas:
    cv = Canvas(len(rows[0]), len(rows))
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            cv.px[y][x] = 0 if ch == "." else int(ch)
    return cv


def rows_of(cv: Canvas) -> list[str]:
    return ["".join("." if v == 0 else str(v) for v in row) for row in cv.px]


def test_new_canvas_is_transparent_and_out_of_bounds_is_ignored():
    cv = Canvas(3, 2)
    assert (cv.w, cv.h) == (3, 2) and cv.px == [[0, 0, 0], [0, 0, 0]]
    cv.set(-1, 0, 5)
    cv.set(3, 1, 5)
    cv.set(0, 2, 5)
    assert lit(cv) == set()
    assert cv.get(-1, 0) == 0 and cv.get(9, 9) == 0
    cv.set(2, 1, 7)
    assert cv.get(2, 1) == 7


def test_set_if_only_paints_over_listed_colours():
    cv = from_rows(["12", ".."])
    for x in range(2):
        for y in range(2):
            cv.set_if(x, y, 9, (1,))
    cv.set_if(5, 5, 9, (0,))
    assert rows_of(cv) == ["92", ".."]


def test_rect_is_clipped():
    cv = Canvas(4, 4)
    cv.rect(-1, -1, 3, 3, 5)
    assert lit(cv) == {(0, 0), (1, 0), (0, 1), (1, 1)}


def test_ellipse_samples_pixel_centres_and_respects_only_on():
    cv = Canvas(5, 5)
    cv.ellipse(2, 2, 1, 1, 3)
    assert lit(cv) == {(1, 1), (2, 1), (1, 2), (2, 2)}
    cv.ellipse(2, 2, 0, 1, 4)                     # degenerate: nothing
    assert lit(cv, 4) == set()
    cv.ellipse(2.5, 2.5, 3, 3, 6, only_on=(3,))   # markings only on existing colour 3
    assert lit(cv, 6) == {(1, 1), (2, 1), (1, 2), (2, 2)} and lit(cv, 3) == set()
    assert Canvas(5, 5).px != cv.px


def test_disc_is_a_round_ellipse():
    a, b = Canvas(9, 9), Canvas(9, 9)
    a.disc(4.5, 4.5, 3, 2)
    b.ellipse(4.5, 4.5, 3, 3, 2)
    assert a.px == b.px and len(lit(a)) > 20


def test_outlined_ellipse_has_a_ring_of_outline():
    cv = Canvas(9, 9)
    cv.outlined_ellipse(4.5, 4.5, 2, 2, 2, outline=1)
    fill, ring = lit(cv, 2), lit(cv, 1)
    assert fill and ring and not fill & ring
    assert all((x + dx, y + dy) in fill | ring for x, y in fill for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))


def test_round_rect_cuts_the_corners():
    cv = Canvas(6, 6)
    cv.round_rect(0, 0, 6, 6, 2, 1)
    assert len(lit(cv)) == 32
    assert {(0, 0), (5, 0), (0, 5), (5, 5)}.isdisjoint(lit(cv))


def test_polygon_fills_the_inside():
    cv = Canvas(6, 6)
    cv.polygon([(0, 0), (4, 0), (4, 4), (0, 4)], 2)
    assert lit(cv) == {(x, y) for x in range(4) for y in range(4)}
    tri = Canvas(6, 6)
    tri.polygon([(0, 0), (6, 0), (0, 6)], 2)
    assert (0, 0) in lit(tri) and (5, 5) not in lit(tri)


@pytest.mark.parametrize("a, b, pixels", [
    ((0, 0), (3, 3), [(0, 0), (1, 1), (2, 2), (3, 3)]),
    ((0, 0), (3, 1), [(0, 0), (1, 0), (2, 1), (3, 1)]),
    ((3, 1), (0, 0), [(3, 1), (2, 1), (1, 0), (0, 0)]),
    ((2, 0), (2, 3), [(2, 0), (2, 1), (2, 2), (2, 3)]),
    ((1, 1), (1, 1), [(1, 1)]),
])
def test_bresenham_line(a, b, pixels):
    cv = Canvas(5, 5)
    cv.line(*a, *b, 4)
    assert lit(cv) == set(pixels)


def test_thick_path_covers_the_polyline():
    cv = Canvas(12, 6)
    cv.thick_path([(1.5, 2.5), (10.5, 2.5)], 1.0, 3)
    assert {(x, 2) for x in range(1, 11)} <= lit(cv)
    assert {(x, 0) for x in range(12)}.isdisjoint(lit(cv))


def test_blit_pattern_skips_unknown_characters():
    cv = Canvas(4, 3)
    cv.px[0][0] = 9
    cv.blit_pattern(0, 0, [" ?b", "a a", "aaaaa"], {"a": 1, "b": 2})
    assert rows_of(cv) == ["9.2.", "1.1.", "1111"]


def test_outline_surrounds_shapes_but_not_existing_outline():
    cv = from_rows([".....", ".....", "..2..", ".....", "....."])
    cv.outline(1)
    assert rows_of(cv) == [".....", "..1..", ".121.", "..1..", "....."]
    cv.outline(1)                              # outline pixels don't get another outline
    assert rows_of(cv) == [".....", "..1..", ".121.", "..1..", "....."]


def test_rim_goes_around_one_colour_only():
    cv = from_rows([".....", ".1...", ".....", "...2."])
    cv.rim(8, 1)
    assert lit(cv, 8) == {(1, 0), (0, 1), (2, 1), (1, 2)}
    assert cv.get(3, 3) == 2


def test_post_pixels_are_painted_after_and_then_cleared():
    cv = Canvas(3, 3)
    cv.post += [(0, 0, 1), (5, 5, 1), (2, 2, 3)]
    cv.apply_post()
    assert lit(cv) == {(0, 0), (2, 2)} and cv.post == []


def test_flipped_mirrors_rows_and_drops_post_pixels():
    cv = from_rows(["12.", "..3"])
    cv.post.append((0, 0, 1))
    out = cv.flipped()
    assert rows_of(out) == [".21", "3.."]
    assert out.post == [] and rows_of(cv) == ["12.", "..3"]


def test_rotated_ccw():
    cv = from_rows(["12", "34", "56"])
    out = cv.rotated_ccw()
    assert (out.w, out.h) == (3, 2)
    assert rows_of(out) == ["246", "135"]
    back = out.rotated_ccw().rotated_ccw().rotated_ccw()
    assert back.px == cv.px


def test_shifted_moves_and_drops_what_leaves_the_canvas():
    cv = from_rows(["1..", ".2.", "..3"])
    assert rows_of(cv.shifted(1, 0)) == [".1.", "..2", "..."]
    assert rows_of(cv.shifted(-1, -1)) == ["2..", ".3.", "..."]


def test_bbox():
    assert Canvas(4, 4).bbox() is None
    cv = from_rows(["....", ".1..", "...2", "...."])
    assert cv.bbox() == (1, 1, 3, 2)
