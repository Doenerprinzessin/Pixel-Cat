"""KnownFolders, SaveMarks and InstallerPort on macOS: the user's folders under the home folder (``$HOME``, so
tests stay isolated), the protected-save marks as a JSON file next to the data folder, and no installer (the
app bundle comes with PR 6)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# the domain's KNOWN_FOLDERS -> their folders in the home folder (macOS doesn't redirect them)
FOLDERS = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
           "music": "Music", "videos": "Movies"}
NO_INSTALLER = "there's no installer on macOS yet - the macOS app bundle comes later (PR 6)"


class MacKnownFolders:
    def known_folder(self, name: str) -> Path | None:
        sub = FOLDERS.get(name)
        return Path.home() / sub if sub else None


class MacSaveMarks:
    """Skeleton: nothing is marked."""

    def is_marked(self, folder_id: str) -> bool:
        return False

    def mark(self, folder_id: str) -> None:
        pass

    def forget(self, folder_id: str) -> None:
        pass


class MacInstaller:
    def is_setup_name(self, path: str | Path) -> bool:
        return False

    def run_quiet(self, args: Any) -> int:
        log.warning(NO_INSTALLER)
        print(NO_INSTALLER)
        return 1

    def run_window(self, mode: str) -> int:
        log.warning(NO_INSTALLER)
        print(NO_INSTALLER)
        return 1
