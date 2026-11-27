"""Hats: the catalogue, their pictures, and wearing them in every kind of pose."""
from __future__ import annotations

import pytest

from pixelcat.sprites import hats
from pixelcat.sprites.cat_art import PAD, SIZE, Pose, render, render_worn
from pixelcat.sprites.palettes import EARIN, OUT, RIM

ONE_PER_SHAPE = sorted({h.shape: k for k, h in hats.HATS.items()}.values())
FEW = [Pose(body="sit"), Pose(body="stand", phase=1), Pose(body="curl", eyes="closed"), Pose(body="leap", rot=1),
       Pose(body="climb"), Pose(body="back")]


def hat_pixels(cv) -> list[tuple[int, int]]:
    return [(x, y) for y in range(cv.h) for x in range(cv.w) if cv.px[y][x] >= hats.INDEX_BASE]


def sits_on_the_cat(name: str, cv) -> bool:
    pix = hat_pixels(cv)
    cat = {(x, y) for y in range(cv.h) for x in range(cv.w) if 0 < cv.px[y][x] < hats.INDEX_BASE}
    reach = 4 if hats.HATS[name].shape in ("halo", "cloud") else 1      # those float just above the head
    return any((x + dx, y + dy) in cat for x, y in pix for dx in range(-reach, reach + 1)
               for dy in range(-reach, reach + 1))


def test_the_catalogue():
    assert len(hats.HATS) == 115 and len(hats.FIRST) == 15 and set(hats.FIRST) <= set(hats.HATS)
    assert len({h.name for h in hats.HATS.values()}) == 115
    for key, h in hats.HATS.items():
        assert h.group in hats.GROUPS and h.tier in hats.TIERS and h.shape in hats.SHAPES, key
        assert h.ears in ("show", "hide"), key


def test_lookup():
    assert hats.get("wizard") is hats.HATS["wizard"]
    assert hats.get("nope") is None
    with pytest.raises(KeyError):
        hats.hat_canvas("nope")


@pytest.mark.parametrize("key", sorted(hats.HATS))
def test_every_hat_picture_is_outlined_and_uses_known_colours(key):
    h = hats.HATS[key]
    cv = hats.hat_canvas(key)
    assert (cv.w, cv.h) == (max(len(r) for r in h.rows) + 2, len(h.rows) + 2)
    colours = {v for row in cv.px for v in row} - {0}
    assert 1 in colours
    assert colours <= set(hats.COLORS), key
    assert all(hats.hat_rgba(hats.INDEX_BASE + c) == hats.COLORS[c] for c in colours)


def test_colour_variants_keep_their_shape():
    for key, h in hats.HATS.items():
        shape = hats.SHAPES[h.shape]
        assert [[ch == " " for ch in r] for r in h.rows] == [[ch == " " for ch in r] for r in shape.rows], key
    assert hats.HATS["party_blue"].rows != hats.HATS["party"].rows


def test_unknown_hat_colour_is_magenta():
    assert hats.hat_rgba(hats.INDEX_BASE + 999) == (255, 0, 255, 255)


@pytest.mark.parametrize("key", ONE_PER_SHAPE)
def test_every_hat_shape_sits_on_the_head_in_every_kind_of_pose(key, pose_sample):
    for pose in pose_sample:
        cv, ox, oy = render_worn(pose, key)
        assert hat_pixels(cv), (key, pose)
        assert sits_on_the_cat(key, cv), (key, pose)
        assert 0 <= ox and 0 <= oy and ox + SIZE <= cv.w and oy + SIZE <= cv.h


@pytest.mark.parametrize("key", sorted(set(hats.HATS) - set(ONE_PER_SHAPE)))
def test_every_hat_is_drawn_into_the_frame(key):
    for pose in FEW:
        cv, _ox, _oy = render_worn(pose, key)
        assert hat_pixels(cv) and sits_on_the_cat(key, cv), (key, pose)


def test_big_hats_cover_the_ears_small_ones_dont():
    sit = Pose(body="sit")
    ears = lambda name: sum(row.count(EARIN) for row in render_worn(sit, name)[0].px)  # noqa: E731
    assert ears(None) > 0 and ears("party") > 0
    assert hats.HATS["wizard"].ears == "hide" and ears("wizard") == 0


def test_the_cat_stays_put_under_its_hat(pose_sample):
    """(ox, oy) from render_worn is where the bare 32x32 box landed in the bigger canvas."""
    for pose in pose_sample:
        if pose.rot % 4:
            continue
        cv, ox, oy = render_worn(pose, "party")
        bare = render(pose)
        for y in range(bare.h):
            for x in range(bare.w):
                b, w = bare.px[y][x], cv.px[y + oy - PAD][x + ox - PAD]
                if b not in (0, RIM) and w < hats.INDEX_BASE:
                    assert w in (b, OUT), (pose, x, y)
