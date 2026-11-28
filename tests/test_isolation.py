"""The suite's own safety net: tests never reach the real profile or the network."""
from __future__ import annotations

import ctypes
import os
import socket
import sys
import types
from pathlib import Path

import pytest

from pixelcat import paths
from pixelcat.adapters.windows import installer
from pixelcat.adapters.windows.platform import WindowsKnownFolders

REAL = "C:\\Users\\Someone\\RealProfile"


@pytest.fixture
def shell_answers_the_real_profile(monkeypatch: pytest.MonkeyPatch, fake_dll):  # type: ignore[no-untyped-def]
    """A shell that answers every Known Folder with a folder of the 'real' profile."""
    shell32, ole32 = fake_dll("shell32"), fake_dll("ole32")

    def answer(guid_ref, flags, token, out_ref):  # type: ignore[no-untyped-def]
        out_ref._obj.value = REAL + "\\" + bytes(guid_ref._obj).hex()
        return 0

    shell32.SHGetKnownFolderPath.result = answer
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(shell32=shell32, ole32=ole32), raising=False)
    return shell32


def _inside(path: Path | None, folder: Path) -> bool:
    return path is not None and os.path.normcase(str(path)).startswith(os.path.normcase(str(folder)))


@pytest.mark.parametrize("alias", ["desktop", "documents", "downloads", "pictures", "music", "videos", "notes"])
def test_known_folders_stay_inside_the_test_folder(shell_answers_the_real_profile, isolated_dirs: Path,
                                                   alias: str) -> None:
    p = paths.known_folder(alias, WindowsKnownFolders())
    assert _inside(p, isolated_dirs), p
    assert shell_answers_the_real_profile.SHGetKnownFolderPath.calls == []


def test_installer_layout_stays_inside_the_test_folder(shell_answers_the_real_profile,
                                                       isolated_dirs: Path) -> None:
    lay = installer.layout()
    for folder in (lay.install_dir, lay.programs_dir, lay.desktop_dir, lay.data_dir, lay.tools_dir):
        assert _inside(folder, isolated_dirs), folder
    assert "Microsoft" not in lay.uninstall_key and "Microsoft" not in lay.run_key
    assert shell_answers_the_real_profile.SHGetKnownFolderPath.calls == []


@pytest.mark.parametrize("connect", ["connect", "connect_ex"])
def test_outside_connections_are_refused(connect: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.05)
        with pytest.raises(OSError, match="must not use the network"):
            getattr(s, connect)(("203.0.113.1", 9))


def test_localhost_is_allowed() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            assert s.connect_ex(server.getsockname()) == 0


def test_real_profile_unsets_what_was_not_set(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    module = next(m for m in list(sys.modules.values())
                  if getattr(m, "REAL_PROFILE_ENV", None) is not None and hasattr(m, "_PROFILE_VARS"))
    monkeypatch.setattr(module, "REAL_PROFILE_ENV", {"USERPROFILE": "C:\\Users\\Someone"})
    request.getfixturevalue("real_profile")
    assert os.environ["USERPROFILE"] == "C:\\Users\\Someone"
    for var in ("HOME", "APPDATA", "LOCALAPPDATA", "PIXELCAT_TEST_ROOT"):
        assert var not in os.environ
