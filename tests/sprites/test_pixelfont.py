"""The built-in 5x7 pixel font."""
from __future__ import annotations

import pytest

from pixelcat.engine import ballgame
from pixelcat.sprites import pixelfont
from pixelcat.sprites.pixelfont import ADVANCE, GLYPH_H, GLYPH_W, GLYPHS, glyph, text_pixels, text_width


def test_every_glyph_is_5x7():
    for ch, rows in GLYPHS.items():
        assert len(rows) == GLYPH_H, ch
        assert all(0 <= r < 1 << GLYPH_W for r in rows), ch


@pytest.mark.parametrize("ch, same_as", [("a", "A"), ("z", "Z"), ("ü", "U"), ("é", "E"), ("Ç", "C"),
                                         ("€", "?"), ("\u4e2d", "?"), ("♥", "♥")])
def test_glyph_lookup_and_fallbacks(ch, same_as):
    assert glyph(ch) == GLYPHS[same_as]


@pytest.mark.parametrize("text, width", [("", 0), ("A", 5), ("AB", 11), ("ROUND 1", 41)])
def test_text_width(text, width):
    assert text_width(text) == width


def test_text_pixels():
    one_i = text_pixels("I")
    assert len(one_i) == 3 + 5 + 3
    assert (1, 0) in one_i and (2, 3) in one_i and (0, 3) not in one_i
    two = text_pixels("II")
    assert sorted(two) == sorted(one_i + [(x + ADVANCE, y) for x, y in one_i])
    assert text_pixels(" ") == []


def banner_texts():
    out = [f"ROUND {k}" for k in range(1, 13)] + ["NEW RECORD!"]
    out += [t + "!" for t in ballgame.ROUND_TITLES.values()]
    return out


@pytest.mark.parametrize("text", banner_texts())
def test_ball_game_banners_only_use_real_glyphs(text):
    for ch in text:
        assert ch.upper() in pixelfont.GLYPHS, (text, ch)
