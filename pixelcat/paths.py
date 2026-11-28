"""Filesystem locations used by Pixel Cat."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .domain.folders import KNOWN_FOLDERS

if TYPE_CHECKING:
    from .ports import KnownFolders


def app_dir() -> Path:
    """Directory holding main.py (source run) or the .exe (PyInstaller build)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_candidates() -> list[Path]:
    """config.json locations, lowest priority first: bundled copy (inside the
    PyInstaller archive), next to main.py / the .exe, then %APPDATA%/PixelCat."""
    out: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        out.append(Path(bundle) / "config.json")
    out.append(app_dir() / "config.json")
    out.append(data_dir() / "config.json")
    return out


def _default_base() -> Path:
    """Where the per-user app data goes when %APPDATA% isn't set (it always is on Windows)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / "AppData" / "Roaming"


def data_dir() -> Path:
    """``%APPDATA%/PixelCat`` (macOS: ``~/Library/Application Support/PixelCat``) - created on demand."""
    base = os.environ.get("APPDATA") or str(_default_base())
    path = Path(base) / "PixelCat"
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_data_dir() -> Path:
    """``%LOCALAPPDATA%`` (macOS: ``~/Library/Application Support``), the base for big machine-local
    things such as downloaded tools - callers add their ``PixelCat/...`` subfolder.  Not created here."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / "AppData" / "Local"


def log_file() -> Path:
    return data_dir() / "log.txt"


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def notes_dir(folders: KnownFolders) -> Path:
    return (folders.known_folder("documents") or Path.home() / "Documents") / "PixelCat Notes"


def known_folder(alias: str, folders: KnownFolders) -> Path | None:
    """Resolve friendly folder aliases used by the chat tools to the user's real folders."""
    alias = alias.strip().lower().replace(" ", "_")
    alias = {"docs": "documents", "my_documents": "documents", "my_desktop": "desktop",
             "download": "downloads", "photos": "pictures", "bilder": "pictures", "schreibtisch": "desktop",
             "dokumente": "documents"}.get(alias, alias)
    if alias == "home":
        return Path.home()
    if alias in ("pixelcat_notes", "notes"):
        return notes_dir(folders)
    if alias in KNOWN_FOLDERS:
        return folders.known_folder(alias) or Path.home() / alias.capitalize()
    return None
