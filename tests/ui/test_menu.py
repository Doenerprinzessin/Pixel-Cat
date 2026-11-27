"""ui.menu: the right-click menu - layout, folding sections, activation, keyboard, scrolling."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QWheelEvent

from pixelcat.ui import theme
from pixelcat.ui.menu import MenuItem, PixelMenu

pytestmark = pytest.mark.qt
CAT = (800, 500, 896, 596)
WORK = (0, 0, 1920, 1040)


@pytest.mark.parametrize(("item", "stays_open", "inert"), [
    (MenuItem("a", "Action"), False, False),
    (MenuItem("t", "Toggle", checked=False), True, False),
    (MenuItem("r", "Radio", radio=True), True, False),
    (MenuItem("s", "Swatch", swatch=QColor("red")), True, False),
    (MenuItem("l", "Locked", locked=True), True, True),
    (MenuItem("i", "Info", info=True), True, True),
    (MenuItem("ls", "Locked section", locked=True, children=[MenuItem("x", "X")]), True, False),
])
def test_item_kinds(item: MenuItem, stays_open: bool, inert: bool) -> None:
    assert item.stays_open is stays_open
    assert item.inert is inert


def _items() -> list[MenuItem]:
    return [
        MenuItem("pause", "Pause", "pause"),
        MenuItem("games", "Minigames", "gamepad", separator_before=True, children=[
            MenuItem("game:ball", "Play ball", note="NEW!"),
            MenuItem("game:mice", "Mouse hunt", locked=True, note="STAGE 5")]),
        MenuItem("settings", "Settings", "gear", children=[
            MenuItem("quiet", "Quiet mode", checked=False),
            MenuItem("set_style", "Style", children=[MenuItem("style:a", "A", radio=True, checked=True),
                                                     MenuItem("style:b", "B", radio=True)]),
            MenuItem("pass", "Cat Pass", info=True, bar=0.5, note="STAGE 2"),
            MenuItem("accent:red", "Red", swatch=QColor("red"), checked=True)]),
        MenuItem("quit", "Quit", "power", separator_before=True),
    ]


@pytest.fixture
def menu(qapp, sprites, track):  # type: ignore[no-untyped-def]
    m = track(PixelMenu(sprites))
    m.items = _items()
    m.title = "PIXEL CAT"
    return m


def _keys(m: PixelMenu) -> list[str]:
    return [it.key for it, _d, _r in m._rows]


def _row(m: PixelMenu, key: str) -> int:
    return next(i for i, (it, _d, _r) in enumerate(m._rows) if it.key == key)


def test_sections_start_folded(menu: PixelMenu) -> None:
    w, h = menu._layout()
    assert _keys(menu) == ["pause", "games", "settings", "quit"]
    assert w > 0 and h > 0
    tops = [r.top() for _it, _d, r in menu._rows]
    assert tops == sorted(tops)


def test_separators_add_space(menu: PixelMenu) -> None:
    menu._layout()
    r = [r for _it, _d, r in menu._rows]
    row_h = r[0].height()
    assert r[1].top() - r[0].bottom() - 1 == 3 * menu.u          # "games" has a separator before it
    assert r[2].top() - r[1].top() == row_h


def test_opening_a_section_shows_its_children_one_section_at_a_time(menu: PixelMenu) -> None:
    menu._layout()
    menu._activate(_row(menu, "games"))
    assert _keys(menu) == ["pause", "games", "game:ball", "game:mice", "settings", "quit"]
    assert [d for _it, d, _r in menu._rows] == [0, 0, 1, 1, 0, 0]
    menu._activate(_row(menu, "settings"))
    assert menu.expanded == {"settings"}
    menu._activate(_row(menu, "set_style"))
    assert {"settings", "set_style"} <= menu.expanded and "style:b" in _keys(menu)
    menu._activate(_row(menu, "settings"))
    assert _keys(menu) == ["pause", "games", "settings", "quit"]


def test_the_menu_grows_with_open_sections_and_long_labels(menu: PixelMenu) -> None:
    w0, h0 = menu._layout()
    menu.expanded = {"settings"}
    w1, h1 = menu._layout()
    assert h1 > h0 and w1 >= w0
    menu.subtitle = "sitting"
    assert menu._layout()[1] > h1


def test_an_action_closes_the_menu_and_triggers(menu: PixelMenu) -> None:
    got: list[str] = []
    menu.triggered.connect(got.append)
    menu.popup_near(CAT, WORK, 2)
    assert menu.isVisible()
    menu._activate(_row(menu, "quit"))
    assert got == ["quit"] and not menu.isVisible()


@pytest.mark.parametrize("key", ["quiet", "style:b", "accent:red"])
def test_a_setting_triggers_and_keeps_the_menu_open(menu: PixelMenu, key: str) -> None:
    got: list[str] = []
    menu.triggered.connect(got.append)
    menu.popup_near(CAT, WORK, 2)
    menu.expanded = {"settings", "set_style"}
    menu._reposition()
    menu._activate(_row(menu, key))
    assert got == [key] and menu.isVisible()


@pytest.mark.parametrize("key", ["game:mice", "pass"])
def test_locked_and_info_rows_do_nothing(menu: PixelMenu, key: str) -> None:
    got: list[str] = []
    menu.triggered.connect(got.append)
    menu.popup_near(CAT, WORK, 2)
    menu.expanded = {"games", "settings"}
    menu._reposition()
    menu._activate(_row(menu, key))
    assert got == [] and menu.isVisible()


def test_popup_near_places_the_menu_beside_the_cat_inside_the_work_area(menu: PixelMenu) -> None:
    menu.popup_near(CAT, WORK, 3)
    g = menu.geometry()
    assert menu.u == 3
    assert g.left() >= CAT[2] or g.right() < CAT[0] or g.bottom() < CAT[1] or g.top() >= CAT[3]
    assert 0 <= g.left() and g.right() < 1920 and 0 <= g.top() and g.bottom() < 1040


def test_refresh_replaces_the_items_and_the_size(menu: PixelMenu) -> None:
    menu.popup_near(CAT, WORK, 2)
    h = menu.height()
    menu.refresh(menu.items[:2], u=4)
    assert menu.u == 4 and _keys(menu) == ["pause", "games"] and menu.isVisible()
    menu.refresh([MenuItem("x", "X")])
    assert _keys(menu) == ["x"] and menu.height() < h * 2


def _key(m: PixelMenu, key: Qt.Key) -> None:
    m.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def test_keyboard_moves_over_rows_skips_inert_ones_and_activates(menu: PixelMenu) -> None:
    got: list[str] = []
    menu.triggered.connect(got.append)
    menu.popup_near(CAT, WORK, 2)
    menu.expanded = {"games"}
    menu._reposition()
    seen = []
    for _ in range(5):
        _key(menu, Qt.Key.Key_Down)
        seen.append(menu._rows[menu._hover][0].key)
    assert seen == ["pause", "games", "game:ball", "settings", "quit"]     # (Mouse hunt is locked)
    _key(menu, Qt.Key.Key_Up)
    assert menu._rows[menu._hover][0].key == "settings"
    _key(menu, Qt.Key.Key_Up)
    _key(menu, Qt.Key.Key_Return)
    assert got == ["game:ball"] and not menu.isVisible()


def test_escape_closes(menu: PixelMenu) -> None:
    menu.popup_near(CAT, WORK, 2)
    _key(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()


def _mouse(m: PixelMenu, kind: QEvent.Type, pos: QPoint, button=Qt.MouseButton.LeftButton) -> None:  # type: ignore[no-untyped-def]
    ev = QMouseEvent(kind, QPointF(pos), QPointF(m.mapToGlobal(pos)), button,
                     button if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseButtonPress: m.mousePressEvent, QEvent.Type.MouseMove: m.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: m.mouseReleaseEvent}[kind](ev)


def test_mouse_hover_lights_up_a_row_and_a_left_release_activates_it(menu: PixelMenu) -> None:
    got: list[str] = []
    menu.triggered.connect(got.append)
    menu.popup_near(CAT, WORK, 2)
    centre = menu._rows[_row(menu, "pause")][2].center()
    _mouse(menu, QEvent.Type.MouseMove, centre)
    assert menu._hover == _row(menu, "pause")
    _mouse(menu, QEvent.Type.MouseButtonRelease, centre, Qt.MouseButton.RightButton)
    assert got == [] and menu.isVisible()            # (the right release that opened the menu)
    _mouse(menu, QEvent.Type.MouseButtonRelease, centre)
    assert got == ["pause"]


def test_the_menu_can_be_dragged_by_its_title_and_stays_on_screen(menu: PixelMenu) -> None:
    menu.popup_near(CAT, WORK, 2)
    start = QPoint(10, 5)
    _mouse(menu, QEvent.Type.MouseButtonPress, start)
    assert menu._drag_off is not None
    g0 = menu.mapToGlobal(start)
    to = QPoint(-5000, -5000) - g0
    _mouse(menu, QEvent.Type.MouseMove, start + to)
    assert (menu.x(), menu.y()) == (0, 0) and menu._user_pos == QPoint(0, 0)
    _mouse(menu, QEvent.Type.MouseButtonRelease, start)
    assert menu._drag_off is None
    menu.refresh(menu.items)
    assert (menu.x(), menu.y()) == (0, 0)                     # it stays where it was put


def _tall() -> list[MenuItem]:
    return [MenuItem("hats", "Hats", children=[MenuItem(f"hat:{i}", f"Hat number {i}", radio=True)
                                                for i in range(120)])]


def test_a_menu_taller_than_the_screen_scrolls(menu: PixelMenu) -> None:
    menu.items = _tall()
    menu.expanded = {"hats"}
    menu.popup_near(CAT, (0, 0, 1920, 600), 2)
    assert menu.height() <= 600 and menu._scroll_max > 0
    wheel = QWheelEvent(QPointF(20, 100), QPointF(20, 100), QPoint(), QPoint(0, -120 * 3), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    menu.wheelEvent(wheel)
    assert menu._scroll > 0
    menu._scroll_to(10**6)
    assert menu._scroll == menu._scroll_max
    for _ in range(3):
        _key(menu, Qt.Key.Key_Down)
    assert menu._scroll < menu._scroll_max                # the row under the keyboard is scrolled into view
    assert menu._row_at(QPoint(20, 1)) == -1               # the title is not a row


def test_wheel_does_nothing_when_everything_fits(menu: PixelMenu) -> None:
    menu.popup_near(CAT, WORK, 2)
    wheel = QWheelEvent(QPointF(20, 100), QPointF(20, 100), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    menu.wheelEvent(wheel)
    assert menu._scroll == 0


@pytest.mark.parametrize("style", ["catchat", "modern_black"])
def test_paints_every_kind_of_row(menu: PixelMenu, sprites, style: str) -> None:  # type: ignore[no-untyped-def]
    theme.configure(style=style)
    menu.items[1].children[0].picture = sprites.skin_icon("charcoal", 18)
    menu.items[1].children[1].picture = sprites.skin_icon("charcoal", 18)
    menu.expanded = {"games", "settings", "set_style"}
    menu.subtitle = "sitting"
    menu.popup_near(CAT, WORK, 2)
    menu._hover = 0
    img = menu.grab().toImage()
    assert img.width() == menu.width()
    assert img.pixelColor(menu.width() // 2, menu.height() // 2).alpha() > 0


def test_paints_scroll_arrows_when_scrolled(menu: PixelMenu) -> None:
    menu.items = _tall()
    menu.expanded = {"hats"}
    menu.popup_near(CAT, (0, 0, 1920, 600), 2)
    menu._scroll_to(menu._scroll_max // 2)
    assert not menu.grab().isNull()
