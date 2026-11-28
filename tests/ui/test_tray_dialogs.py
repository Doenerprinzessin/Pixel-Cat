"""ui.tray and ui.dialogs: the tray icon's menu and the small pixel dialogs."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLineEdit, QSystemTrayIcon

from pixelcat import naming, progress
from pixelcat.ui import theme
from pixelcat.ui.dialogs import AmountDialog, KeyDialog, NameDialog, PixelDialog
from pixelcat.ui.tray import Tray

pytestmark = pytest.mark.qt
CAT = (800, 500, 896, 596)
WORK = (0, 0, 1920, 1040)


@pytest.fixture
def tray(qapp, sprites):  # type: ignore[no-untyped-def]
    t = Tray(sprites)
    yield t
    t.hide()
    t.deleteLater()


def _texts(tray: Tray) -> list[str]:
    return [a.text() for a in tray.menu.actions() if not a.isSeparator()]


def test_tray_menu_entries(tray: Tray) -> None:
    assert _texts(tray) == ["Pause", "Chat", "Find my cat", "Quiet mode", "Quit Pixel Cat"]
    assert [a.isCheckable() for a in tray.menu.actions() if not a.isSeparator()] == [False, False, False, True, False]
    assert not tray.icon().isNull()
    assert tray.contextMenu() is tray.menu


def test_tray_actions_emit_their_keys(tray: Tray) -> None:
    got: list[str] = []
    tray.action.connect(got.append)
    for a in tray.menu.actions():
        if not a.isSeparator():
            a.trigger()
    assert got == ["pause", "chat", "find", "quiet", "quit"]


@pytest.mark.parametrize(("reason", "emits"), [
    (QSystemTrayIcon.ActivationReason.Trigger, ["find"]), (QSystemTrayIcon.ActivationReason.DoubleClick, ["find"]),
    (QSystemTrayIcon.ActivationReason.Context, []), (QSystemTrayIcon.ActivationReason.MiddleClick, [])])
def test_clicking_the_icon_finds_the_cat(tray: Tray, reason, emits) -> None:  # type: ignore[no-untyped-def]
    got: list[str] = []
    tray.action.connect(got.append)
    tray._on_activated(reason)
    assert got == emits


def test_tray_sync_shows_pause_state_quiet_and_status(tray: Tray) -> None:
    tray.sync(True, True, "sleeping")
    assert _texts(tray)[0] == "Resume"
    assert tray._quiet.isChecked()
    assert tray.toolTip() == "Pixel Cat - sleeping"
    tray.sync(False, False, "x" * 300)
    assert _texts(tray)[0] == "Pause" and not tray._quiet.isChecked()
    assert len(tray.toolTip()) == 120


def test_tray_rename_follows_the_cats_name(tray: Tray) -> None:
    tray.sync(False, False, "walking")
    naming.set_name("Mochi")
    tray.rename()
    assert "Find Mochi" in _texts(tray) and "Quit Mochi" in _texts(tray)
    assert tray.toolTip() == "Mochi - walking"


def test_tray_restyle_and_refresh_icon(tray: Tray, sprites) -> None:  # type: ignore[no-untyped-def]
    theme.configure(style="midnight")
    tray.restyle()
    assert theme.STYLES["midnight"].paper in tray.menu.styleSheet()
    before = tray.icon().pixmap(32).toImage()
    sprites.set_skin(list(progress.SKINS)[1])
    tray.refresh_icon()
    assert tray.icon().pixmap(32).toImage() != before
    assert not tray.icon().isNull()


# ---------------------------------------------------------------- dialogs
def _inside(w, work=WORK) -> bool:  # type: ignore[no-untyped-def]
    g = w.geometry()
    return work[0] <= g.left() and g.right() < work[2] and work[1] <= g.top() and g.bottom() < work[3]


def test_pixel_dialog_answers_once_and_closes(track) -> None:  # type: ignore[no-untyped-def]
    dlg = PixelDialog("Start with Windows?", "Should I?", "YES PLEASE", "NO THANKS")
    track(dlg)
    got: list[bool] = []
    dlg.answered.connect(got.append)
    dlg.show_near(CAT, WORK, 2)
    assert dlg.isVisible() and _inside(dlg) and dlg.width() == 300
    assert (dlg.yes.text(), dlg.no.text()) == ("YES PLEASE", "NO THANKS")
    assert "Start with Windows?" in dlg.label.text()
    dlg.yes.click()
    dlg._finish(False)
    assert got == [True]
    assert not dlg.isVisible()


def test_pixel_dialog_no(track) -> None:  # type: ignore[no-untyped-def]
    dlg = PixelDialog("t", "x")
    track(dlg)
    got: list[bool] = []
    dlg.answered.connect(got.append)
    dlg.show_near(CAT, WORK, 3)
    assert dlg.width() == 450
    dlg.no.click()
    assert got == [False]


def test_pixel_dialog_paints(track) -> None:  # type: ignore[no-untyped-def]
    dlg = PixelDialog("t", "x")
    track(dlg)
    dlg.show_near(CAT, WORK, 2)
    img = dlg.grab().toImage()
    assert img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 0


@pytest.mark.parametrize("make", [lambda: PixelDialog("t", "x"), lambda: KeyDialog("t", "x", "p", has_key=False),
                                  lambda: AmountDialog("t", "x", "p"), lambda: NameDialog("t", "x", "Cat", 12)])
def test_dialogs_stay_shown_on_macos_while_another_app_is_active(track, make) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt

    assert track(make()).testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)


@pytest.fixture
def key_dialog(track):  # type: ignore[no-untyped-def]
    dlg = KeyDialog("Claude API key", "Paste it", "sk-ant-...", has_key=True)
    track(dlg)
    dlg.show_near(CAT, WORK, 2)
    return dlg


def test_key_dialog_hides_the_key_and_offers_get_save_cancel_remove(key_dialog: KeyDialog) -> None:
    d = key_dialog
    assert d.field.echoMode() == QLineEdit.EchoMode.Password
    assert d.field.placeholderText() == "sk-ant-..."
    assert [b.text() for b in (d.get, d.save, d.cancel, d.remove)] == ["GET A KEY", "SAVE", "CANCEL", "REMOVE KEY"]
    assert d.remove.isVisibleTo(d) and _inside(d) and d.width() == 340


def test_key_dialog_without_a_key_has_no_remove(track) -> None:  # type: ignore[no-untyped-def]
    d = KeyDialog("t", "x", "p", has_key=False, noun="token")
    track(d)
    assert not d.remove.isVisibleTo(d)
    assert d.get.text() == "GET A TOKEN"


def test_key_dialog_empty_key_asks_for_one(key_dialog: KeyDialog) -> None:
    got: list[str] = []
    key_dialog.submitted.connect(got.append)
    key_dialog.field.setText("   ")
    key_dialog.save.click()
    assert got == [] and key_dialog.status.text() == "Paste the key first."


def test_key_dialog_submits_the_trimmed_key_and_waits(key_dialog: KeyDialog) -> None:
    got: list[str] = []
    key_dialog.submitted.connect(got.append)
    key_dialog.field.setText("  sk-123  ")
    key_dialog.field.returnPressed.emit()
    assert got == ["sk-123"]
    assert not key_dialog.field.isEnabled() and not key_dialog.save.isEnabled()
    assert key_dialog.status.text() == "Checking the key…"


def test_key_dialog_refused_shows_why_and_lets_the_user_retry(key_dialog: KeyDialog) -> None:
    key_dialog.field.setText("bad")
    key_dialog.save.click()
    key_dialog.checked(False, "That key didn't work.")
    assert key_dialog.field.isEnabled() and key_dialog.save.isEnabled()
    assert key_dialog.status.text() == "That key didn't work."
    assert "color:#" in key_dialog.status.styleSheet()
    key_dialog.checked(True, "")
    assert not key_dialog.isVisible()


def test_key_dialog_links_and_removal(key_dialog: KeyDialog) -> None:
    events: list[str] = []
    key_dialog.link.connect(lambda: events.append("link"))
    key_dialog.removed.connect(lambda: events.append("removed"))
    key_dialog.get.click()
    key_dialog.remove.click()
    assert events == ["link", "removed"] and not key_dialog.isVisible()


def test_amount_dialog_shows_what_is_typed(track) -> None:  # type: ignore[no-untyped-def]
    d = AmountDialog("Token limit", "How many?", "e.g. 3.5M", "750k")
    track(d)
    d.show_near(CAT, WORK, 2)
    got: list[str] = []
    d.submitted.connect(got.append)
    assert d.field.echoMode() == QLineEdit.EchoMode.Normal and d.field.text() == "750k"
    assert d.save.text() == "SET" and not d.get.isVisibleTo(d) and not d.remove.isVisibleTo(d)
    d.field.setText("")
    d.save.click()
    assert d.status.text() == "Type an amount first." and got == []
    d.field.setText("2M")
    d.save.click()
    assert got == ["2M"] and d.status.text() == "Checking…"


def test_name_dialog_allows_an_empty_name_and_limits_the_length(track) -> None:  # type: ignore[no-untyped-def]
    d = NameDialog("Your cat's name", "What should I be called?", "Mochi", 20)
    track(d)
    got: list[str] = []
    d.submitted.connect(got.append)
    assert d.field.text() == "Mochi" and d.field.maxLength() == 20
    assert d.field.placeholderText() == "e.g. Mochi"
    d.field.setText("  ")
    d.save.click()
    d.field.setText(" Sir Fluff ")
    d.field.returnPressed.emit()
    assert got == ["", "Sir Fluff"]
    assert d.field.isEnabled()                 # (the owner checks the name and calls checked())
