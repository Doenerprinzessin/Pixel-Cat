"""State that more than one part of the app reads and writes (no Qt): the parts get these holders, never copies."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..domain import Monitor
    from ..games.base import MiniGame


class GameState:
    """The running minigame (``mini``; None: none, or only Play ball).  Set by ``Minigames``; the world
    (the game loop, the windows the cat walks on, hiding behind fullscreen windows) reads it."""

    def __init__(self) -> None:
        self.mini: MiniGame | None = None


class WorldState:
    """The desktop the cat lives on.  ``monitors`` is replaced (not changed) when the layout changes - read it
    here every time.  ``hidden_fullscreen``: the cat hides behind a fullscreen app (set by the ``World``);
    ``chat_was_visible``: the chat comes back with the cat (set by the world, and by the chat opening while hidden)."""

    def __init__(self, monitors: list[Monitor]) -> None:
        self.monitors = monitors
        self.hidden_fullscreen = False
        self.chat_was_visible = False


class ChatState:
    """The open chat's own choices (None: the default): ``chat_model`` and ``chat_effort``.  Set by the model
    menu (``ModelChoice``) and by the chat (a new chat, a saved chat opened, a provider disconnected)."""

    def __init__(self) -> None:
        self.chat_model: str | None = None
        self.chat_effort: str | None = None
