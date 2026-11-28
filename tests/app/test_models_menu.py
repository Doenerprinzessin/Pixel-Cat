"""The chat's model and effort choice without the app: fake providers, a fake chat and a fake model menu."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pixelcat.adapters.null import NullSystem
from pixelcat.app.accounts import Accounts
from pixelcat.app.models_menu import ModelChoice
from pixelcat.app.state import ChatState
from pixelcat.llm import models
from pixelcat.persistence import SavedState
from pixelcat.ports import ProviderInfo
from pixelcat.settings import Config


class FakeProvider:
    """Local: three effort levels; online: the levels its model lists (None: it lists none)."""

    def __init__(self, key: str, local: bool = False) -> None:
        self.info = ProviderInfo(key=key, name=key.title(), brand=key.upper(), signin="", key_url="", key_hint="",
                                 usage_url="", local=local)

    def auth(self, creds: Any, want: str) -> str:
        return "key" if creds.get(f"{self.info.key}_key") else ""

    def effort_levels(self, model_info: dict[str, Any]) -> list[str] | None:
        return ["low", "medium", "high"] if self.info.local else model_info.get("effort")


class FakeChat:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []
        self.chip: tuple[str, bool, str] | None = None

    def add(self, role: str, text: str) -> None:
        self.said.append((role, text))

    def set_model_chip(self, short: str, temp: bool, tip: str) -> None:
        self.chip = (short, temp, tip)


class FakeMenu:
    def __init__(self) -> None:
        self.visible, self.refreshed = True, 0

    def isVisible(self) -> bool:  # noqa: N802 - the Qt name
        return self.visible

    def refresh(self, items: Any) -> None:
        self.refreshed += 1

    def close(self) -> None:
        self.visible = False


def make(qapp: Any, chat: FakeChat | None = None) -> tuple[ModelChoice, SimpleNamespace]:
    cfg, saved = Config(), SavedState()
    cfg.model = "qwen3.5:9b"
    providers = {"ollama": FakeProvider("ollama", local=True), "anthropic": FakeProvider("anthropic")}
    acc = Accounts(providers, saved, {"anthropic_key": "sk-1"}, save=lambda: None)  # type: ignore[arg-type]
    acc.ext_p("anthropic")["models"] = [{"id": "m1", "name": "One", "effort": ["low", "high"]},
                                        {"id": "m2", "name": "Two"}]
    f = SimpleNamespace(chat=chat, menu=FakeMenu(), status=SimpleNamespace(override=None, ensured=0),
                        switched=0, banners=0)
    f.status.ensure = lambda force=False: setattr(f.status, "ensured", f.status.ensured + 1)

    def nope(*_a: Any) -> Any:
        raise AssertionError("not expected here")
    mc = ModelChoice(ChatState(), cfg, saved, providers, acc, f.status, f.menu, SimpleNamespace(),  # type: ignore[arg-type]
                     SimpleNamespace(), SimpleNamespace(), NullSystem(), chat=lambda: f.chat, open_chat=nope,
                     show_model_banner=lambda: setattr(f, "banners", f.banners + 1),
                     switched=lambda: setattr(f, "switched", f.switched + 1))
    return mc, f


def test_the_effort_levels_and_the_default_effort_follow_the_chats_model(qapp) -> None:  # type: ignore[no-untyped-def]
    mc, _ = make(qapp)
    assert mc.effort_levels() == ["low", "medium", "high"]
    for think, default in (("auto", "medium"), ("on", "high"), ("off", "low"), (True, "high"), (False, "low")):
        mc.cfg.think = think
        assert mc.default_effort() == default
    mc.saved.ext_settings["effort"] = "xhigh"
    mc.chat_state.chat_model = "anthropic:m1"
    assert mc.effort_levels() == ["low", "high"] and mc.default_effort() == "xhigh"
    assert [k.key for k in mc.model_menu_items()[-1].children] == ["chateffort:default", "chateffort:low",
                                                                   "chateffort:high"]
    mc.chat_state.chat_model = "anthropic:m2"
    assert mc.effort_levels() is None
    assert [k.key for k in mc.model_menu_items()[-1].children] == ["chateffort:none"]


def test_the_model_menu_shows_the_connected_models_and_this_chats_pick(qapp) -> None:  # type: ignore[no-untyped-def]
    mc, _ = make(qapp)
    mc.chat_state.chat_model = "anthropic:m2"
    items = mc.model_menu_items()
    assert items[0].key == "chatmodel:default" and not items[0].checked
    assert [i.key for i in items[1:-2]] == [f"chatmodel:{m.tag}" for m in models.MODELS]
    ext = items[-2]
    assert ext.key == "chatmodels_anthropic" and ext.label == "ANTHROPIC models"
    assert [(k.key, k.checked) for k in ext.children] == [("chatmodel:anthropic:m1", False),
                                                          ("chatmodel:anthropic:m2", True)]
    mc.accounts.creds.pop("anthropic_key")                       # (not connected: its models aren't offered)
    assert mc.ext_chat_items() == []


def test_an_effort_and_a_model_for_this_chat_only(qapp) -> None:  # type: ignore[no-untyped-def]
    chat = FakeChat()
    mc, f = make(qapp, chat)
    mc.on_model_menu("chateffort:high")
    assert mc.chat_state.chat_effort == "high" and chat.chip is not None and chat.chip[:2] == ("9B · HIGH", True)
    assert "High effort - I always think first" in chat.said[-1][1] and f.menu.refreshed == 1
    mc.set_chat_effort("bogus")                                  # (no such level: nothing changes)
    assert mc.chat_state.chat_effort == "high" and len(chat.said) == 1
    assert mc.ext_think_effort() == (True, "high")

    mc.on_model_menu("chatmodel:anthropic:m1")
    assert not f.menu.visible and mc.chat_state.chat_model == "anthropic:m1"
    assert f.status.override == "anthropic:m1" and f.status.ensured == 1 and f.banners == 1 and f.switched == 1
    assert mc.effective_model() == "anthropic:m1" and mc.cfg.model == "qwen3.5:9b"

    mc.on_model_menu("chatmodel:default")
    assert mc.chat_state.chat_model is None and f.status.override is None and f.switched == 2
    assert chat.said[-1][1].startswith("This chat uses the default model again")
