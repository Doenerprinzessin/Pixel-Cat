"""paths: data folders, config.json candidates and Windows Known Folders."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from pixelcat import domain, paths
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


# ---------------------------------------------------------------- data folders
def test_data_dir_is_appdata_pixelcat_and_created(isolated_dirs: Path) -> None:
    d = paths.data_dir()
    assert d == Path(os.environ["APPDATA"]) / "PixelCat"
    assert d.is_dir()


def test_data_dir_without_appdata_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", "")
    assert paths.data_dir() == Path.home() / "AppData" / "Roaming" / "PixelCat"
    assert paths.data_dir().is_dir()


def test_cache_dir_and_log_file_live_in_the_data_dir() -> None:
    assert paths.cache_dir() == paths.data_dir() / "cache"
    assert paths.cache_dir().is_dir()
    assert paths.log_file() == paths.data_dir() / "log.txt"


# ---------------------------------------------------------------- app dir / config.json
def test_app_dir_from_source_is_the_repo_root() -> None:
    assert (paths.app_dir() / "main.py").is_file()


def test_app_dir_when_frozen_is_the_exe_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "PixelCat.exe"))
    (tmp_path / "bin").mkdir()
    assert paths.app_dir() == (tmp_path / "bin").resolve()


def test_config_candidates_lowest_priority_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert paths.config_candidates() == [paths.app_dir() / "config.json", paths.data_dir() / "config.json"]


def test_config_candidates_start_with_the_bundled_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    assert paths.config_candidates() == [tmp_path / "bundle" / "config.json", paths.app_dir() / "config.json",
                                         paths.data_dir() / "config.json"]


# ---------------------------------------------------------------- Known Folders (mocked)
def test_shell_folder_asks_windows_for_the_known_folder(known_folders, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[DOCUMENTS] = str(tmp_path / "OneDrive" / "Dokumente")
    assert paths.shell_folder("documents") == tmp_path / "OneDrive" / "Dokumente"
    assert known_folders.asked == [DOCUMENTS]
    assert known_folders.freed == 1


def test_known_folder_ids_cover_exactly_the_domain_folders() -> None:
    assert tuple(paths._KNOWN_FOLDER_IDS) == domain.KNOWN_FOLDERS


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_shell_folder_uses_the_right_guid(known_folders, name: str) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[KNOWN[name]] = f"X:\\{name}"
    assert paths.shell_folder(name) == Path(f"X:\\{name}")


def test_shell_folder_is_cached(known_folders) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[DESKTOP] = "D:\\Desk"
    assert paths.shell_folder("desktop") == paths.shell_folder("desktop") == Path("D:\\Desk")
    assert known_folders.asked == [DESKTOP]


def test_shell_folder_failure_is_none_and_cached(known_folders) -> None:  # type: ignore[no-untyped-def]
    known_folders.hresult = -2147024891          # E_ACCESSDENIED
    assert paths.shell_folder("desktop") is None
    assert paths.shell_folder("desktop") is None
    assert len(known_folders.asked) == 1
    assert known_folders.freed == 1              # freed even when it failed


def test_shell_folder_empty_answer_is_none(known_folders) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[DESKTOP] = ""
    assert paths.shell_folder("desktop") is None


def test_shell_folder_without_windows_is_none(no_windll) -> None:  # type: ignore[no-untyped-def]
    assert paths.shell_folder("documents") is None


def test_shell_folder_unknown_name_is_none(known_folders) -> None:  # type: ignore[no-untyped-def]
    assert paths.shell_folder("recycle bin") is None
    assert known_folders.asked == []


def test_notes_dir_inside_the_real_documents(known_folders, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[DOCUMENTS] = str(tmp_path / "Docs")
    assert paths.notes_dir() == tmp_path / "Docs" / "PixelCat Notes"


def test_notes_dir_falls_back_to_home_documents(no_windll) -> None:  # type: ignore[no-untyped-def]
    assert paths.notes_dir() == Path.home() / "Documents" / "PixelCat Notes"


# ---------------------------------------------------------------- known_folder aliases
@pytest.mark.parametrize("alias, folder", [
    ("desktop", "desktop"), ("Desktop", "desktop"), ("  DESKTOP ", "desktop"), ("my desktop", "desktop"),
    ("my_desktop", "desktop"), ("schreibtisch", "desktop"),
    ("documents", "documents"), ("docs", "documents"), ("my documents", "documents"), ("dokumente", "documents"),
    ("downloads", "downloads"), ("download", "downloads"),
    ("pictures", "pictures"), ("photos", "pictures"), ("Bilder", "pictures"),
    ("music", "music"), ("videos", "videos"),
])
def test_known_folder_aliases_resolve_to_known_folders(known_folders, alias: str, folder: str) -> None:  # type: ignore[no-untyped-def]
    known_folders.folders[KNOWN[folder]] = f"K:\\{folder}"
    assert paths.known_folder(alias) == Path(f"K:\\{folder}")


@pytest.mark.parametrize("folder", sorted(KNOWN))
def test_known_folder_falls_back_to_home_subfolder(no_windll, folder: str) -> None:  # type: ignore[no-untyped-def]
    assert paths.known_folder(folder) == Path.home() / folder.capitalize()


def test_known_folder_home_and_notes(no_windll) -> None:  # type: ignore[no-untyped-def]
    assert paths.known_folder("home") == Path.home()
    assert paths.known_folder("notes") == paths.known_folder("PixelCat Notes") == paths.notes_dir()


@pytest.mark.parametrize("alias", ["", "system32", "c:", "appdata", "../etc"])
def test_known_folder_unknown_alias_is_none(no_windll, alias: str) -> None:  # type: ignore[no-untyped-def]
    assert paths.known_folder(alias) is None


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
    p = paths.shell_folder(name)
    if configured.is_dir():
        assert p is not None and p.is_dir(), f"{name} exists at {configured} but shell_folder gave {p}"
        assert os.path.normcase(str(p)) == os.path.normcase(str(configured))
        assert paths.known_folder(name) == p
    else:
        assert p is None
        assert paths.known_folder(name) == Path.home() / name.capitalize()


def _inside(path: Path, folder: Path) -> bool:
    return os.path.normcase(str(path)).startswith(os.path.normcase(str(folder)))


@pytest.mark.windows
def test_real_documents_folder_exists(real_profile) -> None:  # type: ignore[no-untyped-def]
    p = paths.shell_folder("documents")
    assert p is not None and p.is_dir(), f"Documents: {p}, Windows has it at {_configured_folder('documents')}"
