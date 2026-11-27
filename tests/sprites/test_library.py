"""SpriteLibrary: canvases -> crisp Qt images, the frame cache, sheets, icons and pixel text."""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage

from pixelcat import paths
from pixelcat.sprites import cat_art, effects_art, hats, palettes, pixelfont
from pixelcat.sprites.cat_art import PAD, SIZE, Pose
from pixelcat.sprites.library import CatFrame, SpriteLibrary, apply_pattern, canvas_to_image, render_text, scaled_pixmap
from pixelcat.sprites.raster import Canvas

pytestmark = pytest.mark.qt

SIT = Pose(body="sit")
FRAME = SIZE + 2 * PAD


@pytest.fixture
def lib(qapp):
    return SpriteLibrary("charcoal")


def rgba(img: QImage, x: int, y: int) -> tuple[int, int, int, int]:
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue(), c.alpha()


def opaque(img: QImage) -> set[tuple[int, int]]:
    return {(x, y) for y in range(img.height()) for x in range(img.width()) if img.pixelColor(x, y).alpha()}


# ---------------------------------------------------------------- conversion
def test_canvas_to_image_with_a_colour_table_and_a_skin(qapp):
    cv = Canvas(3, 1)
    cv.px[0] = [1, 0, 7]
    img = canvas_to_image(cv, {1: (10, 20, 30, 255)})
    assert (img.width(), img.height()) == (3, 1)
    assert rgba(img, 0, 0) == (10, 20, 30, 255)
    assert rgba(img, 1, 0)[3] == 0 and rgba(img, 2, 0)[3] == 0      # unknown index: transparent
    skin_img = canvas_to_image(cv, palettes.CAT)
    assert rgba(skin_img, 0, 0) == palettes.CAT.rgba(1)


def test_scaled_pixmap_is_a_crisp_integer_upscale(qapp):
    cv = Canvas(2, 1)
    cv.px[0] = [1, 2]
    pm = scaled_pixmap(canvas_to_image(cv, {1: (255, 0, 0, 255), 2: (0, 0, 255, 255)}), 4)
    img = pm.toImage()
    assert (img.width(), img.height()) == (8, 4)
    assert {rgba(img, x, y) for x in range(4) for y in range(4)} == {(255, 0, 0, 255)}
    assert {rgba(img, x, y) for x in range(4, 8) for y in range(4)} == {(0, 0, 255, 255)}


# ---------------------------------------------------------------- cat frames
def test_cat_frames_are_scaled_and_cached(lib):
    fr = lib.cat(SIT, False, 3)
    assert isinstance(fr, CatFrame)
    assert (fr.pixmap.width(), fr.pixmap.height(), fr.scale, fr.ox, fr.oy) == (FRAME * 3, FRAME * 3, 3, PAD, PAD)
    assert lib.cat(SIT, False, 3) is fr
    assert lib.cat(SIT, False, 2) is not fr


def test_left_facing_frames_are_mirrored(lib):
    right = lib.cat(SIT, False, 1).pixmap.toImage()
    left = lib.cat(SIT, True, 1).pixmap.toImage()
    w = right.width()
    assert {(w - 1 - x, y) for x, y in opaque(right)} == opaque(left)


def test_hit_test_uses_the_art_mask(lib):
    fr = lib.cat(SIT, False, 3)
    assert fr.hit(16, 20)                          # the body
    assert not fr.hit(0, 0)                        # the empty corner of the 32x32 box
    assert fr.hit(-PAD - 1, -PAD - 1, tolerance=1) is False


def test_worn_hats_are_part_of_the_frame_and_clickable(lib):
    lib.set_worn("wizard")
    fr = lib.cat(SIT, False, 2)
    pix = [(x, y) for y in range(fr.mask.h) for x in range(fr.mask.w) if fr.mask.px[y][x] >= hats.INDEX_BASE]
    assert pix and fr.oy > PAD and fr.pixmap.height() == fr.mask.h * 2
    x, y = pix[len(pix) // 2]
    assert fr.hit(x - fr.ox, y - fr.oy, 0)
    mirrored = lib.cat(SIT, True, 2)
    assert (mirrored.ox, mirrored.oy) == (fr.mask.w - SIZE - fr.ox, fr.oy)
    lib.set_worn("")
    bare = lib.cat(SIT, False, 2)
    assert (bare.ox, bare.oy, bare.mask.px) == (PAD, PAD, cat_art.render(SIT).px)


def test_changing_skin_or_hat_redraws_the_frames(lib):
    fr = lib.cat(SIT, False, 2)
    lib.set_skin("charcoal")
    assert lib.cat(SIT, False, 2) is fr
    lib.set_skin("ginger")
    ginger = lib.cat(SIT, False, 2)
    assert ginger is not fr and lib.skin.key == "ginger"
    assert ginger.pixmap.toImage() != fr.pixmap.toImage()
    lib.set_worn("party")
    assert lib.cat(SIT, False, 2) is not ginger


def test_the_frame_cache_is_bounded(lib):
    lib.MAX_FRAMES = 2
    first = lib.cat(SIT, False, 1)
    lib.cat(SIT, False, 2)
    lib.cat(SIT, False, 3)
    assert lib.cat(SIT, False, 3) is lib.cat(SIT, False, 3)
    assert lib.cat(SIT, False, 1) is not first


@pytest.mark.parametrize("skin, marks", [("calico", {palettes.PATCH_A, palettes.PATCH_B}),
                                         ("siamese", {palettes.STRIPE})])
def test_skin_patterns_mark_the_fur(lib, skin, marks):
    lib.set_skin(skin)
    fr = lib.cat(SIT, False, 1)
    assert marks <= {v for row in fr.mask.px for v in row}


def test_patterns_only_touch_fur_and_calico_keeps_a_white_belly():
    cv = cat_art.render(Pose(body="stand"))
    for pattern in ("patches", "points", "stars"):
        out = apply_pattern(cv, pattern, PAD, PAD)
        for y in range(cv.h):
            for x in range(cv.w):
                if out.px[y][x] != cv.px[y][x]:
                    assert cv.px[y][x] in (palettes.BASE, palettes.SHADE, palettes.SHEEN), pattern
    patched = apply_pattern(cv, "patches")
    lights = [(x, y) for y in range(cv.h) for x in range(cv.w) if cv.px[y][x] == palettes.LIGHT]
    assert lights and all(patched.px[y][x] == palettes.LIGHT for x, y in lights)
    assert apply_pattern(cv, "").px == cv.px and apply_pattern(cv, "") is not cv


# ---------------------------------------------------------------- prewarm and the preview sheet
def test_prewarm_builds_both_directions_and_writes_the_sheet_once(lib):
    poses = [SIT, Pose(body="loaf"), SIT]
    assert lib.prewarm(poses, 2) == 2
    assert lib.cat(SIT, True, 2) is lib.cat(SIT, True, 2)
    sheet = paths.cache_dir() / "sheet_charcoal.png"
    img = QImage(str(sheet))
    assert (img.width(), img.height()) == (12 * FRAME * 4, FRAME * 4)
    stamp = sheet.stat().st_mtime_ns
    lib.prewarm(poses, 2)
    assert sheet.stat().st_mtime_ns == stamp


def test_prewarm_without_the_sheet(lib):
    lib.prewarm([SIT], 1, export_sheet=False)
    assert not (paths.cache_dir() / "sheet_charcoal.png").exists()


def test_a_failing_sheet_export_is_only_logged(lib, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(lib, "export_sheet", boom)
    assert lib.prewarm([SIT], 1) == 1


# ---------------------------------------------------------------- hats, effects, icons
def test_hat_pictures(lib):
    pm = lib.hat("crown", 3, False)
    cv = hats.hat_canvas("crown")
    assert (pm.width(), pm.height()) == (cv.w * 3, cv.h * 3)
    assert lib.hat("crown", 3, False) is pm
    left = lib.hat("crown", 3, True).toImage()
    w = left.width()
    assert {(w - 1 - x, y) for x, y in opaque(pm.toImage())} == opaque(left)


def test_effects(lib):
    w, h = lib.effect_size("heart")
    pm = lib.effect("heart", 2)
    assert (pm.width(), pm.height()) == (w * 2, h * 2)
    assert lib.effect("heart", 2) is pm
    assert lib.effect_size("nope") == lib.effect_size("sparkle")
    assert lib.effect("nope", 1).toImage() == lib.effect("sparkle", 1).toImage()


def test_menu_icons(lib):
    pm = lib.icon("pause", 2)
    assert (pm.width(), pm.height()) == (18, 18) and lib.icon("pause", 2) is pm


@pytest.mark.parametrize("size", [16, 20, 24, 25, 26, 32, 48, 50, 51, 64, 256])
def test_app_icon_sizes(lib, size):
    img = lib.icon_image(size)
    assert (img.width(), img.height()) == (size, size)
    assert img.pixelColor(size // 2, size // 2).alpha() > 0
    assert lib.face_icon(size).width() == size


@pytest.mark.parametrize("size, side", [(16, 16), (25, 25), (40, 25), (50, 50), (60, 50)])
def test_skin_portraits(lib, size, side):
    pm = lib.skin_icon("galaxy", size)
    assert (pm.width(), pm.height()) == (side, side)
    assert lib.skin_icon("galaxy", size) is pm


# ---------------------------------------------------------------- pixel text
def test_render_text(qapp):
    red = QColor(255, 0, 0)
    pm = render_text("HI", 2, red)
    assert (pm.width(), pm.height()) == (pixelfont.text_width("HI") * 2, pixelfont.GLYPH_H * 2)
    img = pm.toImage()
    lit = {(x * 2, y * 2) for x, y in pixelfont.text_pixels("HI")}
    assert lit == {(x, y) for x, y in opaque(img) if x % 2 == 0 and y % 2 == 0}
    assert all(img.pixelColor(x, y) == red for x, y in lit)


def test_render_text_with_a_shadow(qapp):
    red, black = QColor(255, 0, 0), QColor(0, 0, 0)
    img = render_text("I", 1, red, black).toImage()
    assert (img.width(), img.height()) == (6, 8)
    assert img.pixelColor(1, 0) == red and img.pixelColor(2, 1) == red
    assert img.pixelColor(4, 7) == black and img.pixelColor(1, 7).alpha() == 0   # shadow: 1 px down-right
    assert render_text("", 1, red).width() == 1


def test_effects_art_colours_render(qapp):
    img = canvas_to_image(effects_art.build_effects()["heart"], effects_art.FX_COLORS)
    colours = {rgba(img, x, y) for x in range(img.width()) for y in range(img.height())}
    assert effects_art.FX_COLORS[2] in colours and effects_art.FX_COLORS[1] in colours
