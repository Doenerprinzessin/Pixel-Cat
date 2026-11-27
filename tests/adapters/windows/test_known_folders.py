"""pixelcat.adapters.windows.known_folders: the user's real folders, asked from Windows (SHGetKnownFolderPath).

``known_folders`` (the fixture, in tests/conftest.py) fakes ``ctypes.windll``; the module is ``kf`` here.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from pixelcat import domain, paths
from pixelcat.adapters.windows import known_folders as kf
from pixelcat.adapters.windows.platform import WindowsKnownFolders

DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"
DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
KNOWN = {
    "desktop": DESKTOP,
    "documents": DOCUMENTS,
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}


# ---------------------------------------------------------------- Known Folders (mocked)
def test_shell_folder_asks_windows_for_the_known_folder(known_folders, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[DOCUMENTS] = str(tmp_path / "OneDrive" / "Dokumente")
    assert kf.shell_folder("documents") == tmp_path / "OneDrive" / "Dokumente"
    assert known_folders.asked == [DOCUMENTS]
    assert known_folders.freed == 1


def test_known_folder_ids_cover_exactly_the_domain_folders() -> None:
    assert tuple(kf._KNOWN_FOLDER_IDS) == domain.KNOWN_FOLDERS


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_shell_folder_uses_the_right_guid(known_folders, name: str) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[KNOWN[name]] = f"X:\\{name}"
    assert kf.shell_folder(name) == Path(f"X:\\{name}")


def test_shell_folder_is_cached(known_folders) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[DESKTOP] = "D:\\Desk"
    assert kf.shell_folder("desktop") == kf.shell_folder("desktop") == Path("D:\\Desk")
    assert known_folders.asked == [DESKTOP]


def test_shell_folder_failure_is_none_and_cached(known_folders) -> None:  # type: ignore[no-untyped-def]
    known_folders.hresult = -2147024891          # E_ACCESSDENIED
    assert kf.shell_folder("desktop") is None
    assert kf.shell_folder("desktop") is None
    assert len(known_folders.asked) == 1
    assert known_folders.freed == 1              # freed even when it failed


def test_shell_folder_empty_answer_is_none(known_folders) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[DESKTOP] = ""
    assert kf.shell_folder("desktop") is None


def test_shell_folder_without_windows_is_none(no_windll) -> None:  # type: ignore[no-untyped-def]
    assert kf.shell_folder("documents") is None


def test_shell_folder_unknown_name_is_none(known_folders) -> None:  # type: ignore[no-untyped-def]
    assert kf.shell_folder("recycle bin") is None
    assert known_folders.asked == []


# ---------------------------------------------------------------- real Known Folders
KF_FLAG_DONT_VERIFY = 0x00004000


def _configured_folder(name: str) -> Path | None:
    """Where Windows says the known folder is, whether or not it exists on disk."""
    import ctypes
    import uuid
    from ctypes import wintypes

    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(KNOWN[name]).bytes_le)
    out = ctypes.c_wchar_p()
    fn = ctypes.WinDLL("shell32").SHGetKnownFolderPath
    fn.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    fn.restype = ctypes.c_long
    try:
        return Path(out.value) if fn(ctypes.byref(guid), KF_FLAG_DONT_VERIFY, None, ctypes.byref(out)) == 0 \
            and out.value else None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


@pytest.mark.windows
@pytest.mark.parametrize("name", ["desktop", "documents", "downloads", "pictures", "music", "videos"])
def test_real_shell_folder(real_profile, name: str) -> None:  # type: ignore[no-untyped-def]
    """shell_folder asks without KF_FLAG_DONT_VERIFY, so Windows only answers for folders
    that exist; a missing one gives None and the callers fall back to the usual place
    under the home folder."""
    configured = _configured_folder(name)
    assert configured is not None and configured.is_absolute()
    assert not _inside(configured.resolve(), Path(tempfile.gettempdir()).resolve()), \
        f"the shell resolved {name} against a test's temporary profile: {configured}"
    p = kf.shell_folder(name)
    if configured.is_dir():
        assert p is not None and p.is_dir(), f"{name} exists at {configured} but shell_folder gave {p}"
        assert os.path.normcase(str(p)) == os.path.normcase(str(configured))
        assert paths.known_folder(name, WindowsKnownFolders()) == p
    else:
        assert p is None
        assert paths.known_folder(name, WindowsKnownFolders()) == Path.home() / name.capitalize()


def _inside(path: Path, folder: Path) -> bool:
    return os.path.normcase(str(path)).startswith(os.path.normcase(str(folder)))


@pytest.mark.windows
def test_real_documents_folder_exists(real_profile) -> None:  # type: ignore[no-untyped-def]
    p = kf.shell_folder("documents")
    assert p is not None and p.is_dir(), f"Documents: {p}, Windows has it at {_configured_folder('documents')}"
