"""pixelcat.adapters.macos.platform: build() fills every port with the Mac classes; the skeleton is safe."""
from __future__ import annotations

from pathlib import Path

import pytest

from pixelcat.adapters.macos import (apps, autostart, ax, capture, clipboard, display, inputs, keychain, notifications,
                                     platform, processes, storage, system, windows)
from pixelcat.adapters.macos.profile import MAC
from pixelcat.domain import KNOWN_FOLDERS, Unreadable


@pytest.fixture
def plat():  # type: ignore[no-untyped-def]
    return platform.build()


def test_build_names_the_platform_and_uses_the_mac_profile(plat):  # type: ignore[no-untyped-def]
    assert plat.name == "macos" and plat.profile is MAC
    expected = {"display": display.MacDisplay, "windows": windows.MacWindows, "input": inputs.MacInput,
                "clipboard": clipboard.MacClipboard, "accessibility": ax.MacAccessibility,
                "capture": capture.MacCapture, "system": system.MacSystem, "apps": apps.MacApps,
                "processes": processes.MacProcesses, "notifications": notifications.MacNotifications,
                "secrets": keychain.MacSecretStore, "folders": storage.MacKnownFolders,
                "autostart": autostart.MacAutostart, "save_marks": storage.MacSaveMarks,
                "installer": storage.MacInstaller}
    assert {name: type(getattr(plat, name)) for name in expected} == expected


def test_coordinates_are_qt_points(plat):  # type: ignore[no-untyped-def]
    assert plat.display.qt_logical() is True and plat.display.window_dpi(5) == 96
    assert plat.display.monitors() and plat.display.monitors()[0].primary


def test_the_notification_feed_explains_why_it_cant_read(plat):  # type: ignore[no-untyped-def]
    feed = plat.notifications.open_feed()
    assert feed.available() is False
    for read in (feed.last_id, lambda: feed.since(0), feed.badges):
        with pytest.raises(OSError, match="^macOS doesn't let apps read other apps' notifications$"):
            read()


def test_the_secret_store_never_stores_plain_text(plat):  # type: ignore[no-untyped-def]
    with pytest.raises(Unreadable):
        plat.secrets.unprotect(b"PXK1...")


def test_the_keychain_service_of_the_tests():
    assert keychain.service() == "PixelCat-tests-never"


def test_no_admin_on_macos(plat):  # type: ignore[no-untyped-def]
    assert plat.apps.needs_admin(["x"]) is False
    ok, why = plat.apps.run_as_admin("/Applications/X.app")
    assert not ok and "macOS asks for a password itself" in why
    with pytest.raises(OSError, match="administrator"):
        plat.apps.open_elevated("x", [])


def test_known_folders_are_in_the_home_folder(plat, isolated_dirs):  # type: ignore[no-untyped-def]
    home = Path.home()
    assert home == isolated_dirs / "home"
    assert {name: plat.folders.known_folder(name) for name in KNOWN_FOLDERS} == {
        "desktop": home / "Desktop", "documents": home / "Documents", "downloads": home / "Downloads",
        "pictures": home / "Pictures", "music": home / "Music", "videos": home / "Movies"}
    assert plat.folders.known_folder("startup") is None


def test_no_installer_yet(plat):  # type: ignore[no-untyped-def]
    assert plat.installer.is_setup_name("PixelCat-Setup.exe") is False
    assert plat.installer.run_quiet(None) == 1 and plat.installer.run_window("install") == 1


def test_the_passthrough_flag_and_key_names(plat):  # type: ignore[no-untyped-def]
    plat.input.set_passthrough_until(1e12)
    assert plat.input.passthrough_active()
    assert plat.input.parse_combo("Cmd + Shift + S") == ["cmd", "shift", "s"]
    with pytest.raises(ValueError):
        plat.input.parse_combo(" ")
    assert plat.input.pinch(0, 0, 10, 90) == "failed"
