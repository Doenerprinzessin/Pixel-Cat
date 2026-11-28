"""Your progress: export it to a signed file and import one (Menu -> Settings -> Your progress), and the
messages when the save couldn't be read or opened."""
from __future__ import annotations

import html
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import paths, persistence, progress
from ..domain import WINDOWS
from .popups import paras

if TYPE_CHECKING:
    from ..engine.ballgame import BallGame
    from ..persistence import SavedState
    from ..ports import KnownFolders
    from ..ui.menu import MenuItem, PixelMenu
    from .popups import Popups

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)


class ProgressIO:
    """Export / import of the progress.  After an import the controller's ``show_progress()`` applies it to
    the cat (skin, hat, records, look); ``stop_minigame``, ``save`` and ``menu_items`` are the controller's too."""

    def __init__(self, saved: SavedState, popups: Popups, folders: KnownFolders, menu: PixelMenu, game: BallGame,
                 *, save: Callable[[], None], stop_minigame: Callable[[], None], show_progress: Callable[[], None],
                 menu_items: Callable[[], list[MenuItem]], other_account: str = WINDOWS.other_account) -> None:
        self.saved = saved
        self.popups = popups
        self.folders = folders
        self.menu = menu
        self.game = game
        self._save = save
        self._stop_minigame = stop_minigame
        self._show_progress = show_progress
        self._menu_items = menu_items
        self.other_account = other_account     # "another Windows account or PC" (the platform's words)

    def _ask_file(self, save: bool, title: str, start: str) -> str:
        """A Windows file dialog ("" = cancelled).  The tests replace this."""
        from PySide6.QtWidgets import QFileDialog

        filt = f"Pixel Cat progress (*{persistence.EXPORT_EXT});;All files (*)"
        if save:
            return QFileDialog.getSaveFileName(None, title, start, filt)[0]
        return QFileDialog.getOpenFileName(None, title, start, filt)[0]

    def _documents(self) -> Path:
        return self.folders.known_folder("documents") or Path.home() / "Documents"

    def export_progress(self) -> None:
        """Menu -> Settings -> Your progress -> Export: a signed file that loads on any PC."""
        self.menu.close()
        self._save()
        start = self._documents() / f"PixelCat-progress-{datetime.now():%Y-%m-%d}{persistence.EXPORT_EXT}"
        name = self._ask_file(True, "Export Pixel Cat progress", str(start))
        if not name:
            return
        path = Path(name)
        if path.suffix.lower() != persistence.EXPORT_EXT:
            path = path.with_name(path.name + persistence.EXPORT_EXT)
        try:
            persistence.export_progress(self.saved, path)
        except OSError as e:
            log.exception("export failed")
            self.popups.popup("Export", paras([f"Couldn't save it there ({html.escape(e.strerror or str(e))})."]),
                              "OK")
            return
        log.info("progress exported to %s", path)
        stage = progress.state(self.saved.xp).stage
        self.popups.popup("Progress exported", paras([
            f"Cat Pass stage {stage}, your skins, hats and records are saved in",
            f"<b>{html.escape(str(path))}</b>",
            "Load it on any PC with Import progress. Editing the file makes it unreadable."]), "NICE!")

    def import_progress(self) -> None:
        """Menu -> Settings -> Your progress -> Import: load an exported file (asks first)."""
        self.menu.close()
        name = self._ask_file(False, "Import Pixel Cat progress", str(self._documents()))
        if not name:
            return
        try:
            data = persistence.read_export(Path(name))
        except persistence.SaveError as e:
            self.popups.popup("Import", paras([html.escape(str(e))]), "OK")
            return
        st = self.saved
        now, new = progress.state(st.xp).stage, progress.state(data["xp"]).stage
        pop = self.popups.popup("Import progress?", paras([
            f"Now: Cat Pass stage {now} ({st.xp} XP).",
            f"In the file: stage {new} ({data['xp']} XP).",
            "Loading it replaces your Cat Pass, skins, hats, records and look. Chat settings and auto mode "
            "stay as they are. Your current progress is backed up first."]), "LOAD IT", "CANCEL")
        pop.answered.connect(lambda ok, d=data: self._do_import(d) if ok else None)

    def _do_import(self, data: dict[str, Any]) -> None:
        self._stop_minigame()
        if self.game.active:
            self.game.stop()
        folder = paths.data_dir() / "progress-backups"
        backup = folder / f"before-import-{datetime.now():%Y%m%d-%H%M%S}{persistence.EXPORT_EXT}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            persistence.export_progress(self.saved, backup)
        except OSError as e:
            log.exception("couldn't back up the progress before importing")
            self.popups.popup("Import", paras([
                f"Nothing changed: the current progress couldn't be backed up first "
                f"({html.escape(e.strerror or str(e))})."]), "OK")
            return
        persistence.apply_progress(self.saved, data)
        self._show_progress()
        self._save()
        log.info("progress imported: %d XP (the old one is in %s)", self.saved.xp, backup)
        self.popups.popup("Progress loaded", paras([
            f"Cat Pass stage {progress.state(self.saved.xp).stage}. Welcome back!",
            f"The progress you had before is in <b>{html.escape(str(backup))}</b>."]), "NICE!")
        if self.menu.isVisible():
            self.menu.refresh(self._menu_items())

    def tell_unreadable_save(self) -> None:
        self.popups.popup("A fresh start", paras([
            f"The cat couldn't read its save: it was changed by hand, or it's from {self.other_account}.",
            "If you exported your progress, load it with Menu &gt; Settings &gt; Your progress &gt; Import progress.",
            "The old file is kept in the PixelCat folder, just in case."]), "OK")

    def tell_locked_save(self, saving: bool = False) -> None:
        """The save couldn't be opened (or, ``saving``, protected) because the secret store (the Keychain) can't
        be used right now."""
        if saving:
            self.popups.popup("Not saving this time", paras([
                "The cat couldn't use the Keychain, so your progress wasn't saved.",
                "Until you restart Pixel Cat it doesn't try again (so you aren't asked over and over): your "
                "progress isn't saved this time.",
                "To fix it: quit Pixel Cat, unlock the Keychain or allow Pixel Cat to use it when asked, then start "
                "the cat again."]), "OK")
            return
        self.popups.popup("Not saving this time", paras([
            "The cat couldn't open the Keychain, so its save wasn't loaded - your progress is still there, "
            "and the cat won't overwrite it.",
            "Until you restart Pixel Cat nothing is saved: not the progress, the saved chats or new API keys.",
            "To fix it: quit Pixel Cat, unlock the Keychain or allow Pixel Cat to use it when asked, then start "
            "the cat again."]), "OK")
