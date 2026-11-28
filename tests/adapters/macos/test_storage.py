"""The protected-save marks file, the known folders and the missing installer on macOS."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelcat import persistence
from pixelcat.adapters.macos import storage


def test_everything_stays_in_the_test_home(isolated_dirs: Path) -> None:
    assert storage.marks_file() == isolated_dirs / "home/Library/Application Support/PixelCat-marks.json"
    assert storage.MacKnownFolders().known_folder("videos") == isolated_dirs / "home/Movies"


def test_marks_survive_a_new_run(isolated_dirs: Path) -> None:
    marks = storage.MacSaveMarks()
    assert marks.is_marked("abc") is False                             # no file yet
    marks.mark("abc")
    marks.mark("def")
    marks.mark("abc")
    again = storage.MacSaveMarks()
    assert again.is_marked("abc") and again.is_marked("def") and not again.is_marked("xyz")
    again.forget("abc")
    again.forget("never-marked")
    assert json.loads(storage.marks_file().read_text()) == ["def"]
    assert [p.name for p in storage.marks_file().parent.iterdir()] == ["PixelCat-marks.json"]   # no temp left


def test_the_instance_has_its_own_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    storage.MacSaveMarks().mark("abc")
    monkeypatch.setenv("PIXELCAT_INSTANCE", "-test")
    assert storage.marks_file().name == "PixelCat-marks-test.json"
    assert storage.MacSaveMarks().is_marked("abc") is False


def test_a_damaged_marks_file_raises_and_is_mended(isolated_dirs: Path) -> None:
    path = storage.marks_file()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    marks = storage.MacSaveMarks()
    with pytest.raises(OSError):
        marks.is_marked("abc")
    marks.mark("abc")
    assert marks.is_marked("abc")


def test_a_failed_write_raises_oserror_and_keeps_the_old_file(monkeypatch: pytest.MonkeyPatch) -> None:
    marks = storage.MacSaveMarks()
    marks.mark("abc")

    def fail(src: str, dst: str) -> None:
        raise PermissionError("read-only")
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError):
        marks.mark("def")
    assert json.loads(storage.marks_file().read_text()) == ["abc"]
    assert len(list(storage.marks_file().parent.iterdir())) == 1


def test_persistence_uses_the_marks(tmp_path: Path) -> None:
    marks = storage.MacSaveMarks()
    folder = tmp_path / "data"
    folder.mkdir()
    persistence._mark_converted(folder, marks)
    assert persistence._converted(folder, marks)
    persistence.forget_folder(folder, marks)
    assert not persistence._converted(folder, marks)


def test_no_installer_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    assert storage.MacInstaller().run_quiet(None) == 1
    out = capsys.readouterr().out
    assert "no installer on macOS" in out and "PR 6" not in out
