"""The chat window and the saved chats without the app: a real chat store (fake data protection) in a temp
folder, a fake or a real chat window, and the controller's callbacks recorded."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.secrets import FakeDpapi

from pixelcat.app.chat_ui import ChatUI
from pixelcat.app.state import ChatState
from pixelcat.chats import ChatStore
from pixelcat.llm import models
from pixelcat.persistence import SavedState
from pixelcat.ui.chat import GREETING


class FakeWindow:
    """The parts of the chat window the saved chats use: its messages, the history list and its layout."""

    def __init__(self) -> None:
        self.msgs = [SimpleNamespace(role="cat", text=GREETING)]
        self.lists: list[tuple[list[Any], str | None]] = []
        self.size_u, self.is_full = (31.234, 24.567), True

    def say(self, role: str, text: str) -> None:
        self.msgs.append(SimpleNamespace(role=role, text=text))

    add = say

    def set_history(self, items: list[Any], cid: str | None) -> None:
        self.lists.append((items, cid))


def make(tmp_path: Path, qapp: Any = None, display: Any = None, sprites: Any = None) -> tuple[ChatUI, SimpleNamespace]:
    f = SimpleNamespace(calls=[], saves=0, dpapi=FakeDpapi(), saved=SavedState(), chat_state=ChatState())
    f.model_choice = SimpleNamespace(effective_model=lambda: "qwen3.5:9b", open_model_menu=lambda: f.calls.append("menu"),
                                     update_model_chip=lambda: f.calls.append("chip"))

    def rec(name: str) -> Any:
        return lambda *a: f.calls.append((name, *a))
    ui = ChatUI(f.chat_state, ChatStore(tmp_path / "chats", secrets=f.dpapi), f.saved, sprites, SimpleNamespace(),  # type: ignore[arg-type]
                SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
                SimpleNamespace(active=lambda: [SimpleNamespace(id=7, short=lambda: "the kettle")]),
                f.model_choice, SimpleNamespace(), display,
                on_user_message=rec("send"), cancel_chat=rec("stop"), new_chat=rec("new_chat"),
                set_auto_mode=rec("auto"), cancel_watch=rec("cancel_watch"), open_saved_chat=rec("open"),
                save=lambda: setattr(f, "saves", f.saves + 1), show_model_banner=rec("banner"), selftest=lambda: 0)
    return ui, f


def test_the_open_chat_is_written_only_once_the_user_asked_and_then_only_when_it_changed(tmp_path: Path) -> None:
    ui, f = make(tmp_path)
    ui.store_chat()                                   # (no window yet)
    ui.chat = FakeWindow()  # type: ignore[assignment]
    f.chat_state.history.append({"role": "user", "content": "[tool result] ok", "_tool": True})
    ui.store_chat()
    assert ui.chats.index() == [] and ui.chat_id is None and f.dpapi.protected == 0

    ui.chat.say("user", "plan a trip to Paris")  # type: ignore[union-attr]
    f.chat_state.history.append({"role": "user", "content": "plan a trip to Paris"})
    ui.store_chat()
    written = f.dpapi.protected
    cid = ui.chat_id
    assert cid and [i.title for i in ui.chats.index()] == ["plan a trip to Paris"] and written > 0
    data = ui.chats.load(cid) or {}
    assert data["msgs"] == [["user", "plan a trip to Paris"]] and data["model"] == "qwen3.5:9b"   # (no greeting)

    ui.store_chat()                                   # unchanged: not written again
    assert f.dpapi.protected == written and ui.chat_sig == ui.chat_signature()

    f.chat_state.chat_effort = "high"                 # changed: written again, same chat
    ui.store_chat()
    assert f.dpapi.protected > written and ui.chat_id == cid and len(ui.chats.index()) == 1
    assert (ui.chats.load(cid) or {})["chat_effort"] == "high"


def test_a_chat_that_cant_be_written_stays_unsaved(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ui, f = make(tmp_path)
    ui.chat = FakeWindow()  # type: ignore[assignment]
    f.chat_state.history.append({"role": "user", "content": "hi"})
    f.dpapi.protect_error = OSError("disk full")
    with caplog.at_level(logging.ERROR, logger="pixelcat.app"):
        ui.store_chat()
    assert ui.chat_id is None and ui.chat_sig == ""
    assert any(r.name == "pixelcat.app" and r.getMessage() == "couldn't save the chat to the chat history"
               for r in caplog.records)


def test_a_chat_the_secret_store_cant_protect_says_chats_arent_saved(tmp_path: Path) -> None:
    from pixelcat.domain.secrets import SecretStoreUnavailable

    ui, f = make(tmp_path)
    ui.chat = FakeWindow()  # type: ignore[assignment]
    f.chat_state.history.append({"role": "user", "content": "hi"})
    f.dpapi.protect_error = SecretStoreUnavailable("the keychain is locked")
    ui.store_chat()                                   # (before the history was ever read)
    notes = [m.text for m in ui.chat.msgs if m.role == "error"]  # type: ignore[union-attr]
    assert ui.chat_id is None and len(notes) == 1 and "chats aren't saved" in notes[0]
    ui.store_chat()                                   # (once per run)
    assert [m.role for m in ui.chat.msgs].count("error") == 1  # type: ignore[union-attr]


def test_the_saved_chats_list_shows_model_names_and_deleting_the_open_one_starts_a_new_chat(tmp_path: Path) -> None:
    ui, f = make(tmp_path)
    ui.chat = FakeWindow()  # type: ignore[assignment]
    for q in ("first", "second"):
        f.chat_state.history[:] = [{"role": "user", "content": q}]
        ui.chat_id, ui.chat_sig = None, ""
        ui.store_chat()
    items = ui.history_items()
    assert [i.title for i in items] == ["second", "first"] and {i.model for i in items} == {models.label("qwen3.5:9b")} != {"qwen3.5:9b"}

    ui._delete_saved_chat(items[1].id)                # not the open one
    assert [i.id for i in ui.chats.index()] == [items[0].id] and f.calls == [] and f.chat_state.history
    ui._delete_saved_chat(items[0].id)                # the open one
    assert ui.chats.index() == [] and f.chat_state.history == [] and f.calls == [("new_chat",)]
    assert ui.chat.lists[-1] == ([], ui.chat_id)  # type: ignore[union-attr]


def test_a_resized_chat_opens_like_that_next_time(tmp_path: Path) -> None:
    ui, f = make(tmp_path)
    ui._save_chat_layout()                            # (no window: nothing to keep)
    assert f.saves == 0
    ui.chat = FakeWindow()  # type: ignore[assignment]
    ui._save_chat_layout()
    assert f.saved.chat_size == [31.23, 24.57] and f.saved.chat_full is True and f.saves == 1


def test_the_window_is_made_once_and_its_buttons_reach_the_controller(qapp, fake_platform, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.sprites.library import SpriteLibrary

    ui, f = make(tmp_path, qapp, fake_platform.display, SpriteLibrary("charcoal"))
    f.saved.auto_mode = True
    chat = ui.ensure_chat()
    try:
        assert ui.ensure_chat() is chat and f.calls == [("banner",), "chip"]
        chat.send_requested.emit("hello")
        chat.auto_toggled.emit()
        chat.watch_cancel_requested.emit(7)
        chat.history_open.emit("20260101-000001-0001")
        chat.model_menu_requested.emit()
        chat.stop_requested.emit()
        assert f.calls[2:] == [("send", "hello"), ("auto", False), ("cancel_watch", 7),
                               ("open", "20260101-000001-0001"), "menu", ("stop",)]
    finally:
        chat.close()
