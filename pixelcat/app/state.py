"""State that more than one part of the app reads and writes (no Qt): the parts get these holders, never copies."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..games.base import MiniGame


class GameState:
    """The running minigame (``mini``; None: none, or only Play ball).  Set by ``Minigames``; the world
    (the game loop, the windows the cat walks on, hiding behind fullscreen windows) reads it."""

    def __init__(self) -> None:
        self.mini: MiniGame | None = None
