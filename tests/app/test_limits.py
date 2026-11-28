"""The limits at request time without the app: real accounts and budgets, a recording chat window, popups and
session."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pixelcat.app.accounts import Accounts
from pixelcat.app.limits import Limits
from pixelcat.persistence import SavedState
from pixelcat.ports import ProviderInfo


class FakeChat:
    """The chat window's calls, recorded: ``calls`` [(name, *args)]."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __getattr__(self, name: str) -> Any:
        return lambda *a: self.calls.append((name, *a))


class FakePopup:
    def __init__(self, title: str, text: str, buttons: tuple[str, ...]) -> None:
        self.title, self.text, self.buttons = title, text, buttons
        self.answered = SimpleNamespace(connect=lambda fn: setattr(self, "answer", fn))


class FakePopups:
    def __init__(self) -> None:
        self.shown: list[FakePopup] = []

    def popup(self, title: str, text: str, *buttons: str) -> FakePopup:
        self.shown.append(FakePopup(title, text, buttons))
        return self.shown[-1]


def provider(key: str, credits: bool = False) -> Any:
    info = ProviderInfo(key=key, name=key.title(), brand=key.upper(), signin="", key_url="", key_hint="",
                        usage_url="", credits=credits)
    return SimpleNamespace(info=info, auth=lambda creds, want: "key")


def make() -> tuple[Limits, SimpleNamespace]:
    saved = SavedState()
    f = SimpleNamespace(calls=[], saves=0, chat=FakeChat(), popups=FakePopups())
    providers = {"acme": provider("acme"), "gh": provider("gh", credits=True)}
    accounts = Accounts(providers, saved, {}, save=lambda: None)  # type: ignore[arg-type]
    f.session = SimpleNamespace(task_active=True, resume_after_switch=False,
                                request=lambda: f.calls.append("request"), end_task=lambda: f.calls.append("end"))
    chat_ui = SimpleNamespace(chat=f.chat, open_chat=lambda: f.calls.append("open_chat"))
    model_choice = SimpleNamespace(effective_model=lambda: "acme:m1", open_model_menu=lambda: f.calls.append("menu"))
    limits = Limits(accounts, providers, saved, f.popups, SimpleNamespace(), SimpleNamespace(),  # type: ignore[arg-type]
                    f.session, chat_ui, model_choice, SimpleNamespace(), SimpleNamespace(),  # type: ignore[arg-type]
                    save=lambda: setattr(f, "saves", f.saves + 1))
    return limits, f


def test_all_tokens_used_shows_the_popup_and_no_request_and_switching_goes_on_later() -> None:
    limits, f = make()
    limits.accounts.budget("acme").add(2_000_000)
    assert limits.ext_gate("acme") is False
    assert [p.title for p in f.popups.shown] == ["All tokens used"] and f.popups.shown[0].buttons == ("SWITCH MODEL", "OK")
    assert ("set_busy", False) in f.chat.calls and [c[1] for c in f.chat.calls if c[0] == "add"] == ["error"]
    assert f.calls == []                              # (no request)
    f.popups.shown[0].answer(True)
    assert f.session.resume_after_switch and f.calls == ["open_chat", "menu"]


def test_the_token_limit_card_raises_the_limit_and_goes_on() -> None:
    limits, f = make()
    b = limits.accounts.budget("acme")
    b.add(1_000_000)
    assert limits.ext_gate("acme") is False and f.popups.shown == []
    text, choices, answer = next(c[1:] for c in f.chat.calls if c[0] == "ask_choice")
    assert "Token limit reached." in text and [k for k, _ in choices] == ["switch", "raise", "stop"]
    answer("raise")
    assert b.allowed() == 1_500_000 and f.saves == 1 and f.calls == ["request"]
    assert limits.ext_gate("acme") is True


def test_the_credit_limit_card_names_the_plans_own_counter_and_stop_ends_the_task() -> None:
    limits, f = make()
    limits.accounts.ext_p("gh")["quota"] = {"total": 300.0, "used": 120.0}
    limits.on_copilot_credits(6000.0)
    assert f.saves == 1 and limits.credit_budget().used() == 6000.0
    assert limits.credit_gate() is False
    text, _choices, answer = next(c[1:] for c in f.chat.calls if c[0] == "ask_choice")
    assert "Copilot credit limit reached." in text and "your whole plan: 120 of 300" in text
    answer("stop")
    assert f.calls == ["end"]
