"""The chat window and the saved chats: the window (made the first time it's needed), where it opens and its
size, and the chat history - the open chat saved when it changed, the list of saved chats, deleting them."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer

from ..chats import new_id
from ..domain import monitor_at
from ..domain.secrets import SecretStoreUnavailable
from ..llm import models
from ..ui.chat import GREETING, ChatWindow

if TYPE_CHECKING:
    from ..chats import ChatStore
    from ..engine.brain import Brain
    from ..llm.model_status import ModelStatus
    from ..llm.watch import WatchManager
    from ..persistence import SavedState
    from ..ports.desktop import DisplayPort
    from ..sprites.library import SpriteLibrary
    from ..ui.overlay import CatOverlay
    from .models_menu import ModelChoice
    from .state import ChatState, WorldState
    from .world import World

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)


class ChatUI(QObject):
    """The chat window (``chat``; None until it's first needed) and the saved chats (``chats``).  ``chat_id``,
    ``chat_created``: the open chat in the chat history (None, 0: not saved yet); ``chat_sig``: what was saved last
    (unchanged: not written again).  A new chat or a saved chat opened (the session) sets them too.

    The window's buttons call back into the controller at call time: ``on_user_message(text)``, ``cancel_chat()``,
    ``new_chat()``, ``set_auto_mode(on)``, ``cancel_watch(wid)``, ``open_saved_chat(cid)``; and ``save()``,
    ``show_model_banner()``, ``selftest()``."""

    def __init__(self, chat_state: ChatState, chats: ChatStore, saved: SavedState, sprites: SpriteLibrary,
                 brain: Brain, overlay: CatOverlay, world_state: WorldState, world: World, watches: WatchManager,
                 model_choice: ModelChoice, model_status: ModelStatus, display: DisplayPort, *,
                 parent: QObject | None = None, on_user_message: Callable[[str], None],
                 cancel_chat: Callable[[], None], new_chat: Callable[[], None], set_auto_mode: Callable[[bool], None],
                 cancel_watch: Callable[[int], None], open_saved_chat: Callable[[str], None],
                 save: Callable[[], None], show_model_banner: Callable[[], None],
                 selftest: Callable[[], Any]) -> None:
        super().__init__(parent)
        self.chat_state = chat_state
        self.chats = chats
        self.saved = saved
        self.sprites = sprites
        self.brain = brain
        self.overlay = overlay
        self.world_state = world_state
        self.world = world
        self.watches = watches
        self.model_choice = model_choice
        self.model_status = model_status
        self.display = display
        self._on_user_message = on_user_message
        self._cancel_chat = cancel_chat
        self._new_chat = new_chat
        self._set_auto_mode = set_auto_mode
        self._cancel_watch = cancel_watch
        self._open_saved_chat = open_saved_chat
        self._save = save
        self._show_model_banner = show_model_banner
        self._selftest = selftest
        self._told_locked = False
        self.chat: ChatWindow | None = None
        self.chat_id: str | None = None                  # the open chat's id there (None: not saved yet)
        self.chat_created = 0.0
        self.chat_sig = ""                               # what was saved last (unchanged: not written again)

    def ensure_chat(self) -> ChatWindow:
        if self.chat is None:
            self.chat = ChatWindow(self.sprites, display=self.display)
            self.chat.set_layout(self.saved.chat_size or ChatWindow.SIZE_U, self.saved.chat_full)
            self.chat.layout_changed.connect(self._save_chat_layout)
            self.chat.send_requested.connect(self._on_user_message)
            self.chat.stop_requested.connect(self._cancel_chat)
            self.chat.new_chat_requested.connect(self._new_chat)
            self.chat.auto_toggled.connect(lambda: self._set_auto_mode(not self.saved.auto_mode))
            self.chat.watch_cancel_requested.connect(self._cancel_watch)
            self.chat.model_menu_requested.connect(self.model_choice.open_model_menu)
            self.chat.history_toggled.connect(self.toggle_history)
            self.chat.history_open.connect(self._open_saved_chat)
            self.chat.history_delete.connect(self._delete_saved_chat)
            self.chat.history_delete_all.connect(self._delete_all_chats)
            self.chat.set_auto_mode(self.saved.auto_mode)
            self.chat.set_watches([(w.id, w.short()) for w in self.watches.active()])
            self._show_model_banner()
            self.model_choice.update_model_chip()
        return self.chat

    def on_model_status(self) -> None:
        """(A slot: the model status changed, from any thread.)  The chat's banner and the tray follow."""
        if self.chat is not None:
            self._show_model_banner()
        self.world.sync_tray()

    def _save_chat_layout(self) -> None:
        """The user resized the chat or switched fullscreen: it opens like that next time."""
        if self.chat is None:
            return
        w, h = self.chat.size_u
        self.saved.chat_size = [round(w, 2), round(h, 2)]
        self.saved.chat_full = self.chat.is_full
        self._save()

    def show_chat_quietly(self) -> None:
        """Show the chat without taking the keyboard from the app the user is typing in."""
        chat = self.ensure_chat()
        if self.world_state.hidden_fullscreen:
            self.world_state.chat_was_visible = True     # shown when the fullscreen app is gone
            return
        if not chat.isVisible():
            mon = monitor_at(self.world_state.monitors, self.brain.x, self.brain.y - 1)
            chat.show_near(self.overlay.cat_rect(), mon.work, self.world.ui_unit(mon), activate=False)

    def open_chat(self) -> None:
        self.ensure_chat()
        assert self.chat is not None
        if not getattr(self, "_too_big_said", False) and self.model_choice.model_fit(self.model_choice.effective_model()) == "too_big":
            self._too_big_said = True
            m = models.find(self.model_choice.effective_model())
            adv = self.model_choice.advice()
            better = ("an online AI (Settings → AI model → External AI models)" if adv.online or adv.model is None
                      else f"{adv.model.name} {adv.model.short} (marked BEST in Settings → AI model)")
            self.chat.add("info", f"Heads up: {m.name} {m.short} is too big for this PC - while it runs, the PC gets "
                                  f"very slow. {better[0].upper() + better[1:]} suits it better.")
        mon = monitor_at(self.world_state.monitors, self.brain.x, self.brain.y - 1)
        self.chat.show_near(self.overlay.cat_rect(), mon.work, self.world.ui_unit(mon))
        QTimer.singleShot(60, self.world.refresh_windows)
        if not self._selftest():
            self.model_status.ensure()   # Ollama and the model up? (if not: started, and the chat says so)

    # ---------------------------------------------------------------- the chat history
    def chat_signature(self) -> str:
        assert self.chat is not None
        shape = ([(m.role, m.text) for m in self.chat.msgs], [(m.get("role"), len(str(m.get("content") or "")),
                                                                len(m.get("tool_calls") or [])) for m in self.chat_state.history],
                 self.chat_state.chat_model, self.chat_state.chat_effort)
        return hashlib.sha1(json.dumps(shape, default=str).encode("utf-8")).hexdigest()

    def store_chat(self) -> None:
        """The open chat goes into the chat history (by ``save``; written only when it changed).
        A chat in which the user hasn't asked anything yet isn't kept."""
        if self.chat is None or not any(m.get("role") == "user" and not m.get("_tool") and not m.get("_wake")
                                         for m in self.chat_state.history):
            return
        sig = self.chat_signature()
        if sig == self.chat_sig:
            return
        data = {"id": self.chat_id or new_id(), "created": self.chat_created or time.time(),
                "msgs": [[m.role, m.text] for m in self.chat.msgs if not (m.role == "cat" and m.text == GREETING)],
                "history": self.chat_state.history, "model": self.model_choice.effective_model(), "chat_model": self.chat_state.chat_model,
                "chat_effort": self.chat_state.chat_effort}
        try:
            self.chats.save(data)
        except SecretStoreUnavailable as e:
            log.warning("the chat isn't saved to the chat history: %s", e)
            self._tell_locked()
            return
        except Exception:  # noqa: BLE001 - the chat keeps working without its history
            log.exception("couldn't save the chat to the chat history")
            return
        self.chat_id, self.chat_created, self.chat_sig = data["id"], data["created"], sig

    def history_items(self) -> list[Any]:
        try:
            items = [dc_replace(i, model=models.label(i.model) if i.model else "") for i in self.chats.index()]
        except Exception:  # noqa: BLE001
            log.exception("couldn't read the chat history")
            return []
        self._tell_locked()
        return items

    def _tell_locked(self) -> None:
        """Once per run, in the chat: the saved chats can't be opened (the secret store can't be used now)."""
        if self._told_locked or self.chat is None or not getattr(self.chats, "locked", False):
            return
        self._told_locked = True
        self.chat.add("error", "The saved chats can't be opened right now (the Keychain didn't let Pixel Cat in), "
                               "so chats aren't saved until you restart Pixel Cat. Quit it, allow access, start it again.")

    def toggle_history(self) -> None:
        """The chat's clock button (Ctrl+H, /history): the saved chats, or back to the chat."""
        chat = self.ensure_chat()
        if chat.history_shown:
            chat.hide_history()
            return
        self._save()                                      # (the open chat is in the list, up to date)
        chat.show_history(self.history_items(), self.chat_id)

    def _delete_saved_chat(self, cid: str) -> None:
        try:
            self.chats.delete(cid)
        except Exception:  # noqa: BLE001
            log.exception("couldn't delete chat %s", cid)
        if cid == self.chat_id:                         # the open one: it goes, a fresh chat starts
            self.chat_state.history.clear()
            self._new_chat()
        if self.chat is not None:
            self.chat.set_history(self.history_items(), self.chat_id)
        log.info("saved chat %s deleted", cid)

    def _delete_all_chats(self) -> None:
        try:
            n = self.chats.delete_all()
        except Exception:  # noqa: BLE001
            log.exception("couldn't delete the chat history")
            n = 0
        self.chat_state.history.clear()                             # (the open chat goes too)
        self._new_chat()
        if self.chat is not None:
            self.chat.set_history(self.history_items(), self.chat_id)
        log.info("chat history deleted (%d chats)", n)
