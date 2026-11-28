"""KnownFolders, SaveMarks and InstallerPort on macOS: the user's folders under the home folder (``$HOME``, so
tests stay isolated), the protected-save marks as a JSON file next to the data folder, and no installer (the
app bundle comes with PR 6)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# the domain's KNOWN_FOLDERS -> their folders in the home folder (macOS doesn't redirect them)
FOLDERS = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
           "music": "Music", "videos": "Movies"}
NO_INSTALLER = ("There's no installer on macOS yet - Pixel Cat runs without one: just start it "
                "(a macOS app comes later).")


class MacKnownFolders:
    def known_folder(self, name: str) -> Path | None:
        sub = FOLDERS.get(name)
        return Path.home() / sub if sub else None


def marks_file() -> Path:
    """``~/Library/Application Support/PixelCat-marks<instance>.json``: outside the data folder, so deleting
    that folder keeps the marks (like the HKCU values on Windows)."""
    name = f"PixelCat-marks{os.environ.get('PIXELCAT_INSTANCE', '')}.json"
    return Path.home() / "Library" / "Application Support" / name   # (HOME even with APPDATA set: on purpose)


def _write_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Write ``path`` through a temp file in the same folder (never half-written); raises OSError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _Damaged(OSError):
    pass


class MacSaveMarks:
    """A JSON list of folder ids in :func:`marks_file` (raises OSError if it can't be read or written)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @staticmethod
    def _load() -> set[str]:
        try:
            raw = marks_file().read_bytes()
        except FileNotFoundError:
            return set()
        try:
            ids = json.loads(raw.decode("utf-8"))
        except ValueError as e:
            raise _Damaged(f"the save marks file is damaged ({e})") from e
        if not isinstance(ids, list):
            raise _Damaged("the save marks file is damaged (not a list)")
        return {i for i in ids if isinstance(i, str)}

    @staticmethod
    def _store(ids: set[str]) -> None:
        _write_atomic(marks_file(), json.dumps(sorted(ids)).encode("utf-8"))

    def is_marked(self, folder_id: str) -> bool:
        with self._lock:
            return folder_id in self._load()

    def mark(self, folder_id: str) -> None:
        with self._lock:
            try:
                ids = self._load()
            except _Damaged:
                log.warning("the save marks file is damaged - starting it anew", exc_info=True)
                ids = set()
            if folder_id not in ids:
                self._store(ids | {folder_id})

    def forget(self, folder_id: str) -> None:
        with self._lock:
            ids = self._load()
            if folder_id in ids:
                self._store(ids - {folder_id})


class MacInstaller:
    """No installer on macOS yet (reached only from a built app: ``--install`` / ``--uninstall``)."""

    def is_setup_name(self, path: str | Path) -> bool:
        return False

    def run_quiet(self, args: Any) -> int:
        log.warning("no installer on macOS yet")
        print(NO_INSTALLER)
        return 1

    def run_window(self, mode: str) -> int:
        log.warning("no installer on macOS yet")
        print(NO_INSTALLER)
        return 1
