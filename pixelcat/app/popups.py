"""The popups next to the cat (questions, progress, messages) and the background runner whose results come back
on the GUI thread."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from ..games.qt_host import PopupWin
from ..sprites.library import SpriteLibrary
from ..ui.geometry import Rect

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)

# where a popup shows: (the cat's rect, its monitor's work area, the ui unit there, that monitor's dpi)
Anchor = Callable[[], tuple[Rect, Rect, int, int]]


class _Later(QObject):
    """Hands what a background thread found to a callback on the GUI thread."""

    ran = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.ran.connect(self._run)

    def _run(self, cb: Any, result: Any) -> None:
        try:
            cb(result)
        except Exception:  # noqa: BLE001
            log.exception("background result handler failed")


class Popups:
    """The open popups (``open``) and ``in_background`` (work off the GUI thread, the answer back on it).

    ``anchor()`` and ``hidden()`` are asked each time a popup opens: where the cat is now and whether the app
    hides behind a fullscreen window."""

    def __init__(self, sprites: SpriteLibrary, windows: Any, anchor: Anchor, hidden: Callable[[], bool]) -> None:
        self.sprites = sprites
        self.windows = windows
        self._anchor = anchor
        self._hidden = hidden
        self.open: list[PopupWin] = []
        self.later = _Later()

    def in_background(self, fn: Any, cb: Any) -> None:
        """Run ``fn`` in a thread; ``cb(result or exception)`` on the GUI thread."""
        def work() -> None:
            try:
                res = fn()
            except Exception as exc:  # noqa: BLE001 - handed to cb
                res = exc
            self.later.ran.emit(cb, res)
        threading.Thread(target=work, name="pixelcat-ext", daemon=True).start()

    def dismiss(self, pop: Any) -> None:
        """Close a popup nobody answered (it was only showing progress)."""
        if pop is None:
            return
        try:
            pop._done = True
            pop.close()
        except RuntimeError:
            pass
        if pop in self.open:
            self.open.remove(pop)

    def popup(self, title: str, text: str, ok: str, cancel: str | None = None,
              picture: QPixmap | None = None) -> PopupWin:
        pop = PopupWin(self.sprites, title, text, ok, cancel, picture, windows=self.windows)
        self.open.append(pop)
        pop.answered.connect(lambda _ok, pp=pop: self.open.remove(pp) if pp in self.open else None)
        pop.show_near(*self._anchor())
        if self._hidden():
            pop.hide()
        return pop
