"""paths: data folders, config.json candidates and the user's folders by name (Known Folders: see
tests/adapters/windows/test_known_folders.py)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fakes.platform import FakeKnownFolders
from pixelcat import domain, paths


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


# ---------------------------------------------------------------- the user's folders (from the KnownFolders port)
@pytest.fixture
def folders():  # type: ignore[no-untyped-def]
    """A KnownFolders port that knows the folders in ``folders.table`` (none: the platform can't tell)."""
    port = FakeKnownFolders()
    port.table = {}
    port.answers("known_folder", lambda name: port.table.get(name))
    return port


def test_notes_dir_inside_the_real_documents(folders, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    folders.table["documents"] = tmp_path / "Docs"
    assert paths.notes_dir(folders) == tmp_path / "Docs" / "PixelCat Notes"
    assert folders.called("known_folder") == [(("documents",), {})]


def test_notes_dir_falls_back_to_home_documents(folders) -> None:  # type: ignore[no-untyped-def]
    assert paths.notes_dir(folders) == Path.home() / "Documents" / "PixelCat Notes"


# ---------------------------------------------------------------- known_folder aliases
@pytest.mark.parametrize("alias, folder", [
    ("desktop", "desktop"), ("Desktop", "desktop"), ("  DESKTOP ", "desktop"), ("my desktop", "desktop"),
    ("my_desktop", "desktop"), ("schreibtisch", "desktop"),
    ("documents", "documents"), ("docs", "documents"), ("my documents", "documents"), ("dokumente", "documents"),
    ("downloads", "downloads"), ("download", "downloads"),
    ("pictures", "pictures"), ("photos", "pictures"), ("Bilder", "pictures"),
    ("music", "music"), ("videos", "videos"),
])
def test_known_folder_aliases_resolve_to_known_folders(folders, alias: str, folder: str) -> None:  # type: ignore[no-untyped-def]
    folders.table[folder] = Path(f"K:\\{folder}")
    assert paths.known_folder(alias, folders) == Path(f"K:\\{folder}")
    assert folders.called("known_folder") == [((folder,), {})]


@pytest.mark.parametrize("folder", domain.KNOWN_FOLDERS)
def test_known_folder_falls_back_to_home_subfolder(folders, folder: str) -> None:  # type: ignore[no-untyped-def]
    assert paths.known_folder(folder, folders) == Path.home() / folder.capitalize()


def test_known_folder_home_and_notes(folders) -> None:  # type: ignore[no-untyped-def]
    assert paths.known_folder("home", folders) == Path.home()
    assert paths.known_folder("notes", folders) == paths.known_folder("PixelCat Notes", folders) \
        == paths.notes_dir(folders)


@pytest.mark.parametrize("alias", ["", "system32", "c:", "appdata", "../etc"])
def test_known_folder_unknown_alias_is_none(folders, alias: str) -> None:  # type: ignore[no-untyped-def]
    assert paths.known_folder(alias, folders) is None
    assert folders.calls == []
