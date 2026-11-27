"""Cat frames, skins and effect sprites (pure Python, no Qt)."""
from __future__ import annotations

import pytest

from pixelcat.sprites import cat_art, effects_art, hats, palettes
from pixelcat.sprites.cat_art import BODIES, HEAD_POS, PAD, SIZE, Pose, render, render_icon, render_worn
from pixelcat.sprites.palettes import CAT, EARIN, OUT, RIM, SKINS, T, get_skin

FRAME = SIZE + 2 * PAD
MAGENTA = (255, 0, 255, 255)


def used(cv) -> set[int]:
    return {v for row in cv.px for v in row}


# ---------------------------------------------------------------- skins
def test_skin_colours():
    assert CAT.rgba(T) == (0, 0, 0, 0)
    assert CAT.rgba(OUT) == (0x1B, 0x1A, 0x21, 255)
    assert CAT.rgba(RIM) == (0xFD, 0xFA, 0xFF, 0xD0)          # the light rim is semi-transparent
    assert CAT.rgba(palettes.STAR) == MAGENTA                  # not part of the charcoal skin
    assert CAT.rgba(hats.INDEX_BASE + 5) == hats.COLORS[5]     # hat pixels inside a cat frame


def test_get_skin_falls_back_to_charcoal():
    assert get_skin() is CAT and get_skin("ginger").name == "Ginger" and get_skin("nope") is CAT


def test_recoloured_skins_keep_the_base_colours_they_dont_change():
    ginger = SKINS["ginger"]
    assert ginger.colors[palettes.BASE] == "#e8913a"
    assert ginger.colors[palettes.RIM] == CAT.colors[palettes.RIM]


def test_every_skin_colours_every_pixel_of_every_frame(poses):
    indices = set()
    for p in poses:
        indices |= used(render(p))
    indices |= used(render_icon())
    pattern_indices = {"patches": {palettes.PATCH_A, palettes.PATCH_B}, "points": {palettes.STRIPE},
                       "stars": {palettes.STAR, palettes.STAR2}}
    for skin in SKINS.values():
        for i in indices | pattern_indices.get(skin.pattern, set()):
            assert skin.rgba(i) != MAGENTA, (skin.key, i)


# ---------------------------------------------------------------- the cat
def test_every_pose_the_engine_uses_renders_a_padded_frame(poses):
    for p in poses:
        cv = render(p)
        assert (cv.w, cv.h) == (FRAME, FRAME), p
        idx = used(cv)
        assert OUT in idx and RIM in idx and max(idx) < hats.INDEX_BASE, p


def test_nothing_is_cut_at_the_frame_edge(poses):
    for p in poses:
        cv = render(p)
        edge = cv.px[0] + cv.px[-1] + [r[0] for r in cv.px] + [r[-1] for r in cv.px]
        assert set(edge) <= {0, RIM}, p


@pytest.mark.parametrize("body", sorted(BODIES))
def test_every_body_renders_and_has_a_head_position(body):
    assert render(Pose(body=body)).bbox() is not None
    assert body in HEAD_POS


def test_unknown_bodies_render_standing():
    assert render(Pose(body="moonwalk")).px == render(Pose(body="stand")).px


def test_rendering_is_deterministic_and_expressions_change_pixels():
    p = Pose(body="sit")
    assert render(p).px == render(p).px
    for change in ({"eyes": "closed"}, {"mouth": "o"}, {"ears": "back"}, {"tail": 1.0}, {"look": (1, 0)}):
        assert render(p.with_(**change)).px != render(p).px, change


def test_pose_is_hashable_and_with_returns_a_copy():
    p = Pose(body="sit")
    q = p.with_(eyes="happy")
    assert p.eyes == "open" and q.eyes == "happy" and q.body == "sit"
    assert len({p, q, Pose(body="sit")}) == 2


@pytest.mark.parametrize("rot", [1, 2, 3])
def test_mid_air_flips_turn_the_whole_cat(rot):
    base = render(Pose(body="air"))
    turned = render(Pose(body="air", rot=rot))
    assert turned.w == turned.h
    count = lambda cv: sum(1 for row in cv.px for v in row if v)  # noqa: E731
    assert count(turned) == count(base)
    assert turned.px != base.px


def test_the_app_icon_is_a_centred_symmetric_portrait():
    cv = render_icon()
    assert (cv.w, cv.h) == (cat_art.ICON_SIZE, cat_art.ICON_SIZE)
    assert all(row == row[::-1] for row in cv.px)
    assert EARIN in used(cv) and RIM in used(cv)


def test_render_worn_without_a_hat_is_the_bare_cat():
    p = Pose(body="sit")
    cv, ox, oy = render_worn(p, None)
    assert (ox, oy) == (PAD, PAD) and cv.px == render(p).px
    assert render_worn(p, "no-such-hat")[0].px == render(p).px


def test_render_worn_with_a_hat_grows_the_canvas_and_leaves_no_trace():
    p = Pose(body="sit")
    cv, ox, oy = render_worn(p, "wizard")
    assert cv.h > FRAME and oy > PAD
    assert any(v >= hats.INDEX_BASE for v in used(cv))
    assert render(p).px == render_worn(p, None)[0].px        # the next frame is bare again


# ---------------------------------------------------------------- effects and icons
ENGINE_EFFECTS = ("heart", "heart_small", "z_big", "z_small", "sparkle", "sparkle_small", "note", "anger", "sweat",
                  "dust0", "dust1", "star", "crown", "fish", "fish_half", "fish_bones", "cushion", "shadow",
                  "yarn0", "yarn1", "yarn2", "yarn3", "bubble_exclaim", "bubble_question", "bubble_fish",
                  "bubble_heart", "bubble_note", "bubble_zzz", "bubble_angry", "bubble_dots")


@pytest.fixture(scope="module")
def fx():
    return effects_art.build_effects()


@pytest.mark.parametrize("name", ENGINE_EFFECTS)
def test_effect_sprites_exist_and_use_known_colours(fx, name):
    cv = fx[name]
    assert cv.bbox() is not None
    assert used(cv) - {0} <= set(effects_art.FX_COLORS)


def test_outlined_effects_have_an_outline_and_the_zzz_dont(fx):
    assert 1 in used(fx["heart"]) and 1 in used(fx["bubble_fish"])
    assert 1 not in used(fx["z_big"]) and 1 not in used(fx["shadow"])


def test_speech_bubbles_share_a_size_and_differ_by_glyph(fx):
    bubbles = [fx[n] for n in ENGINE_EFFECTS if n.startswith("bubble_")]
    assert {(b.w, b.h) for b in bubbles} == {(13, 11)}
    assert len({tuple(map(tuple, b.px)) for b in bubbles}) == len(bubbles)


def test_yarn_frames_rotate(fx):
    assert len({tuple(map(tuple, fx[f"yarn{i}"].px)) for i in range(4)}) == 4


@pytest.mark.parametrize("name", sorted(effects_art.ICONS))
def test_menu_icons_are_7x7_with_an_outline(name):
    assert len(effects_art.ICONS[name]) == 7 and all(len(r) == 7 for r in effects_art.ICONS[name])
    cv = effects_art.build_icon(name)
    assert (cv.w, cv.h) == (9, 9)
    assert used(cv) - {0} <= set(effects_art.FX_COLORS)


def test_unknown_icon_is_a_dot():
    assert effects_art.build_icon("nope").px == effects_art.build_icon("dot").px
