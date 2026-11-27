"""What the cat needs from the platform to keep its data: secrets, the user's folders, starting with the
PC, the protected-save marks and the installer.

Plain interfaces (``typing.Protocol``); see :mod:`pixelcat.ports.desktop`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class SecretStore(Protocol):
    """Encrypts data so only this user on this PC can read it (Windows data protection)."""

    def protect(self, data: bytes) -> bytes:
        """Encrypt for this user; raises OSError if the platform can't."""
        ...

    def unprotect(self, blob: bytes) -> bytes:
        """Decrypt a :meth:`protect` result; raises Unreadable if it was changed or is another user's."""
        ...


class KnownFolders(Protocol):
    """Where the user's own folders really are (they are often redirected)."""

    def known_folder(self, name: str) -> Path | None:
        """The real location of one of the domain's KNOWN_FOLDERS; None if the platform can't tell."""
        ...


class AutostartPort(Protocol):
    """Starting the cat when the user signs in."""

    def enabled(self) -> bool:
        """True if the cat starts with the PC."""
        ...

    def set(self, enabled: bool) -> bool:
        """Start the cat with the PC or not; False if that couldn't be changed."""
        ...


class SaveMarks(Protocol):
    """Remembers which data folders already use the protected save (raises OSError if it can't)."""

    def is_marked(self, folder_id: str) -> bool:
        """True if the folder is marked."""
        ...

    def mark(self, folder_id: str) -> None:
        """Mark the folder."""
        ...

    def forget(self, folder_id: str) -> None:
        """Drop the folder's mark."""
        ...


class InstallerPort(Protocol):
    """Installing and uninstalling the cat (the built program only)."""

    def is_setup_name(self, path: str | Path) -> bool:
        """True if the running program is the installer (named like "PixelCat-Setup.exe")."""
        ...

    def run_quiet(self, args: Any) -> int:
        """Install or uninstall without a window, as the command line says; returns the exit code."""
        ...

    def run_window(self, mode: str) -> int:
        """Show the installer window ("install" or "uninstall"); returns the exit code."""
        ...
