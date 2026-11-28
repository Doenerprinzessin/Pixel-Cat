"""AppsPort on macOS: the installed .app bundles, ``open`` for apps, files, folders and URLs, System Settings
pages (``x-apple.systempreferences:``), the editors and Ollama.

Skeleton: nothing is found or opened yet.
"""
from __future__ import annotations

from pathlib import Path

NOT_YET = "not supported on this Mac yet"
NO_ADMIN = "macOS doesn't start apps as administrator"
RUN_AS_ADMIN = ("macOS doesn't start apps as administrator here - open it normally; macOS asks for a password "
                "itself if the app needs one.")


class MacApps:
    def prefetch(self) -> None:
        pass

    def find_shortcut(self, name: str) -> tuple[str, Path | str] | None:
        return None

    def open(self, target: str | Path) -> None:
        raise OSError(NOT_YET)

    def open_elevated(self, file: str, args: list[str]) -> None:
        raise OSError(NO_ADMIN)

    def open_settings(self, page: str) -> None:
        raise OSError(NOT_YET)

    def needs_admin(self, target: object) -> bool:
        return False

    def editor_command(self, app: str) -> list[str] | None:
        return None

    def find_ollama(self) -> Path | None:
        return None

    def start_ollama(self, exe: Path) -> None:
        pass

    def run_as_admin(self, target: str | Path) -> tuple[bool, str]:
        return (False, RUN_AS_ADMIN)
