"""Autostart on macOS: a LaunchAgent in the (test) home folder, never launchctl."""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

from pixelcat.adapters.macos import autostart

MAIN = str(Path(autostart.__file__).resolve().parents[3] / "main.py")


def test_the_agent_goes_to_the_test_home(isolated_dirs: Path) -> None:
    assert autostart.plist_path() == isolated_dirs / "home/Library/LaunchAgents/com.pixelcat.PixelCat.plist"


def test_on_and_off(isolated_dirs: Path) -> None:
    auto = autostart.MacAutostart()
    assert auto.enabled() is False
    assert auto.set(True) is True                                      # makes ~/Library/LaunchAgents too
    assert auto.enabled() is True
    path = autostart.plist_path()
    agent = plistlib.loads(path.read_bytes())
    assert agent == {"Label": "com.pixelcat.PixelCat", "ProgramArguments": [sys.executable, MAIN],
                     "RunAtLoad": True, "KeepAlive": False, "ProcessType": "Interactive",
                     "LimitLoadToSessionType": "Aqua"}
    assert path.stat().st_mode & 0o777 == 0o644                        # launchd refuses group/world-writable
    assert auto.set(True) is True and auto.enabled() is True
    assert auto.set(False) is True and not path.exists() and auto.enabled() is False
    assert auto.set(False) is True
    assert list(path.parent.iterdir()) == []


def test_the_instance_has_its_own_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIXELCAT_INSTANCE", "-test")
    autostart.MacAutostart().set(True)
    agent = plistlib.loads(autostart.plist_path().read_bytes())
    assert autostart.plist_path().name == "com.pixelcat.PixelCat-test.plist"
    assert agent["Label"] == "com.pixelcat.PixelCat-test"
    assert agent["EnvironmentVariables"] == {"PIXELCAT_INSTANCE": "-test"}


def test_another_copys_agent_is_not_ours(isolated_dirs: Path) -> None:
    path = autostart.plist_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(plistlib.dumps({"Label": "com.pixelcat.PixelCat", "ProgramArguments": ["/elsewhere/cat"]}))
    assert autostart.MacAutostart().enabled() is False
    path.write_bytes(b"not a plist")
    assert autostart.MacAutostart().enabled() is False
    assert autostart.MacAutostart().set(True) is True and autostart.MacAutostart().enabled() is True


def test_the_built_app_starts_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "PixelCat.app/Contents/MacOS/PixelCat"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert autostart.launch_command() == [str(exe.resolve())]


def test_a_failed_write_says_false(monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path) -> None:
    (isolated_dirs / "home/Library").mkdir()
    (isolated_dirs / "home/Library/LaunchAgents").write_text("a file, not a folder")
    assert autostart.MacAutostart().set(True) is False
    assert autostart.MacAutostart().enabled() is False
