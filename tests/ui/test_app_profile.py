"""The app's parts get the platform's words from the controller (Platform.profile): Windows' exactly as before, a
Mac's on macOS."""
from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtWidgets import QLabel

from pixelcat.adapters.macos.profile import MAC
from pixelcat.domain import WINDOWS

pytestmark = pytest.mark.usefixtures("qapp")


def _texts(pet) -> dict[str, str]:  # type: ignore[no-untyped-def]
    shown: list[str] = []
    pet.popups.popup = lambda title, text, *a, **k: shown.append(text)
    pet.progress_io.tell_unreadable_save()
    pet.model_status.state = "no_ollama"
    pet._ask_autostart()
    pet.provider_settings._key_dialog_for("openai")
    chat = pet.chat_ui.ensure_chat()
    return {"save": shown[-1], "ollama": pet.model_status.banner() or "", "ask": pet._dialog.label.text(),
            "key": " ".join(lb.text() for lb in pet.provider_settings.key_dialog.findChildren(QLabel)),
            "chat": chat.input.placeholderText(), "menu": _autostart_label(pet.menus.menu_items())}


def _autostart_label(items) -> str:  # type: ignore[no-untyped-def]
    for it in items:
        if it.key == "autostart":
            return it.label
        if it.children and (got := _autostart_label(it.children)):
            return got
    return ""


def test_windows_words_as_before(pet) -> None:  # type: ignore[no-untyped-def]
    assert pet.platform.profile is WINDOWS and pet.session.profile is WINDOWS and pet.commands.profile is WINDOWS
    t = _texts(pet)
    assert "or it's from another Windows account or PC." in t["save"]
    assert t["ollama"] == ("Ollama (the AI engine) isn't installed, so I can't chat yet. Run PixelCat-Setup.exe again "
                           "and tick “Set up the AI chat”, or install it from ollama.com.")
    assert "Start with Windows?" in t["ask"] and t["menu"] == "Start with Windows"
    assert "I keep it encrypted for your Windows account and" in t["key"]
    assert t["chat"].endswith("(Ctrl+N: new chat)")


def test_mac_words(make_app, fake_platform) -> None:  # type: ignore[no-untyped-def]
    pet = make_app(platform=dataclasses.replace(fake_platform, profile=MAC))
    assert pet.session.profile is MAC and pet.commands.profile is MAC
    assert pet.session.toolbox.profile is MAC and pet.session.agent._profile is MAC
    t = _texts(pet)
    assert "or it's from another Mac user account or Mac." in t["save"]
    assert t["ollama"].endswith("Install it from ollama.com (the Mac app; open it once), then try again.")
    assert "Open at Login?" in t["ask"] and t["menu"] == "Open at Login"
    assert "I keep it encrypted for your Mac user account and" in t["key"] and "Windows" not in t["key"]
    assert t["chat"].endswith("(Cmd+N: new chat)")


@pytest.mark.parametrize(("profile", "words", "fence"), [(WINDOWS, "Windows desktop", "```powershell\nollama serve"),
                                                          (MAC, "Mac desktop", "```zsh\nollama serve")])
def test_the_session_speaks_the_platforms_words(make_app, fake_platform, workers, profile, words, fence) -> None:  # type: ignore[no-untyped-def]
    pet = make_app(platform=dataclasses.replace(fake_platform, profile=profile))
    pet.chat_ui.open_chat()
    pet.chat_ui.chat.input.setText("hello")
    pet.chat_ui.chat._send()
    system = workers[-1].messages[0]["content"]
    assert f"lives on the user's {words}." in system and profile.prompt_note in system
    workers[-1].events.failed("offline", "no connection")
    assert fence in pet.chat_ui.chat.msgs[-1].text
