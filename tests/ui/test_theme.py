"""ui.theme: switching the look, sizes per DPI, panels, labels and stylesheets."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter, Qt

from pixelcat.ui import theme


def test_defaults() -> None:
    assert theme.current().key == "catchat"
    assert [theme.choice(k) for k in ("style", "accent", "size", "font")] == ["catchat", "default", "normal",
                                                                             "default"]
    assert theme.size_factor() == 1.0


@pytest.mark.parametrize("style", sorted(theme.STYLES))
def test_configure_switches_the_style_and_the_module_colours(style: str) -> None:
    theme.configure(style=style)
    s = theme.STYLES[style]
    assert theme.current() == s
    assert theme.choice("style") == style
    assert (theme.INK.name(), theme.PAPER.name(), theme.ACCENT.name(), theme.TEXT.name()) == (
        QColor(s.ink).name(), QColor(s.paper).name(), QColor(s.accent).name(), QColor(s.text).name())


@pytest.mark.parametrize(("kind", "value", "fallback"), [
    ("style", "nope", "catchat"), ("accent", "chartreuse", "default"), ("size", "gigantic", "normal"),
    ("font", "comic", "default")])
def test_unknown_values_fall_back_to_the_default(kind: str, value: str, fallback: str) -> None:
    theme.configure(style="midnight", accent="red", size="huge", font="pixel")
    theme.configure(**{kind: value})
    assert theme.choice(kind) == fallback


def test_configure_keeps_what_isnt_given() -> None:
    theme.configure(style="mint", size="large")
    theme.configure(accent="blue")
    assert (theme.choice("style"), theme.choice("size"), theme.choice("accent")) == ("mint", "large", "blue")


@pytest.mark.parametrize(("style", "accent", "on_accent"), [
    ("catchat", "black", "#ffffff"),           # a dark accent: white text
    ("catchat", "yellow", "#2b1b2e"),          # a light accent in a pixel style: the style's ink
    ("modern_white", "yellow", "#111114"),     # ...and in a smooth style: near-black
])
def test_an_accent_replaces_the_style_accent(style: str, accent: str, on_accent: str) -> None:
    theme.configure(style=style, accent=accent)
    s = theme.current()
    assert (s.accent, s.accent_dark, s.on_accent) == (*theme.ACCENTS[accent], on_accent)
    assert theme.ACCENT.name() == QColor(theme.ACCENTS[accent][0]).name()


@pytest.mark.parametrize(("style", "font", "pixel_font"), [
    ("catchat", "default", True), ("modern_white", "default", False), ("catchat", "clean", False),
    ("modern_black", "pixel", True)])
def test_the_font_follows_the_style_unless_chosen(style: str, font: str, pixel_font: bool) -> None:
    theme.configure(style=style, font=font)
    assert theme.current().uses_pixel_font is pixel_font


def test_listeners_hear_every_change_and_a_broken_one_doesnt_stop_the_others() -> None:
    heard: list[str] = []

    def broken() -> None:
        raise RuntimeError("deleted window")

    theme.on_change(broken)
    theme.on_change(lambda: heard.append(theme.current().key))
    theme.configure(style="sakura")
    theme.configure(style="ocean")
    assert heard == ["sakura", "ocean"]


@pytest.mark.parametrize(("dpi", "size", "unit", "font_px"), [
    (96, "normal", 2, 13), (144, "normal", 3, 20), (192, "normal", 4, 27), (96, "small", 2, 10),
    (96, "large", 3, 18), (96, "huge", 3, 22), (48, "small", 1, 9)])
def test_units_and_font_sizes_per_dpi_and_size(dpi: int, size: str, unit: int, font_px: int) -> None:
    theme.configure(size=size)
    assert theme.unit_for_dpi(dpi) == unit
    assert theme.font_px_for_dpi(dpi) == font_px


def test_font_px_has_a_minimum() -> None:
    assert theme.font_px_for_dpi(10, 1.0) == 9


def _panel(style: str, u: int = 2, shadow: bool = True) -> QImage:
    theme.configure(style=style)
    img = QImage(60, 40, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    theme.draw_pixel_panel(p, QRect(0, 0, 60, 40), u, shadow=shadow)
    p.end()
    return img


@pytest.mark.qt
def test_pixel_panel_has_notched_corners_a_border_and_the_paper_inside(qapp) -> None:  # type: ignore[no-untyped-def]
    img = _panel("catchat", shadow=False)
    s = theme.STYLES["catchat"]
    assert img.pixelColor(0, 0).alpha() == 0                       # the notch
    assert img.pixelColor(2, 0).name() == QColor(s.ink).name()     # the border
    assert img.pixelColor(30, 20).name() == QColor(s.paper).name()


@pytest.mark.qt
def test_pixel_panel_shadow_is_drawn_bottom_right(qapp) -> None:  # type: ignore[no-untyped-def]
    with_shadow, without = _panel("catchat"), _panel("catchat", shadow=False)
    assert with_shadow.pixelColor(59, 39).alpha() > 0
    assert without.pixelColor(59, 39).alpha() == 0


@pytest.mark.qt
def test_smooth_panel_is_rounded_and_filled(qapp) -> None:  # type: ignore[no-untyped-def]
    img = _panel("modern_white", u=4, shadow=False)
    assert img.pixelColor(0, 0).alpha() == 0
    assert img.pixelColor(30, 20).name() == QColor(theme.STYLES["modern_white"].paper).name()


@pytest.mark.qt
@pytest.mark.parametrize("style", ["catchat", "ocean"])
def test_title_bar_draws_the_accent_strip(qapp, sprites, style: str) -> None:  # type: ignore[no-untyped-def]
    theme.configure(style=style)
    img = QImage(200, 40, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    theme.draw_title_bar(p, 200, 2, "MEOW", sprites.face_icon(18))
    p.end()
    assert img.pixelColor(190, 6).name() == QColor(theme.current().accent).name()


@pytest.mark.qt
def test_text_pixmap_is_cached_per_font_and_text_size_matches(qapp) -> None:  # type: ignore[no-untyped-def]
    a = theme.text_pixmap("HELLO", 2, QColor("red"))
    assert theme.text_pixmap("HELLO", 2, QColor("red")) is a
    assert theme.text_size("HELLO", 2) == (a.width(), a.height())
    wide = theme.text_size("HELLO HELLO", 2)[0]
    assert wide > a.width()
    assert theme.text_size("HELLO", 4)[0] > a.width()
    theme.configure(font="clean")
    clean = theme.text_pixmap("HELLO", 2, QColor("red"))
    assert clean is not a and not clean.isNull()


@pytest.mark.qt
def test_text_cache_is_bounded(qapp, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(theme, "_TEXT_CACHE", {})
    for i in range(605):
        theme.text_pixmap(f"T{i}", 1, QColor("black"))
    assert len(theme._TEXT_CACHE) <= 601


@pytest.mark.parametrize(("style", "font", "button_font"), [("catchat", "default", "'Consolas'"),
                                                            ("modern_white", "default", "'Segoe UI'")])
def test_stylesheets_use_the_style_colours_and_sizes(style: str, font: str, button_font: str) -> None:
    theme.configure(style=style, font=font)
    s = theme.current()
    css = theme.chat_stylesheet(3, 17)
    assert f"background: {s.field}" in css and "font-size: 17px" in css and "border: 3px solid" in css
    assert f"font-family: {button_font}" in css
    assert ("border-radius" in css) is (not s.pixel)
    tray = theme.tray_menu_stylesheet()
    assert s.paper in tray and s.accent in tray and button_font in tray
