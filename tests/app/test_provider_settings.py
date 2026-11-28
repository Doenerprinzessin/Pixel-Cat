"""A provider's settings menu and the limit dialogs' checks, without the app (fake providers, popups and menu)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from pixelcat.app.accounts import Accounts
from pixelcat.app.provider_settings import ProviderSettings
from pixelcat.app.state import ChatState
from pixelcat.llm import external
from pixelcat.persistence import SavedState
from pixelcat.ports import ProviderInfo
from pixelcat.settings import Config
from pixelcat.ui.menu import MenuItem


class FakeProvider:
    """Connected with a key when the keys have "<key>_key"; "signin" when the user chose it."""

    def __init__(self, key: str, *, signin_kind: str = "browser", can_sign_in: bool = True,
                 credits: bool = False, quota: dict[str, Any] | None = None) -> None:
        self.info = ProviderInfo(key=key, name=key.title(), brand=key.upper(), signin=f"Sign in with {key.title()}",
                                 key_url="", key_hint="", usage_url="", signin_kind=signin_kind,
                                 signed_in=f"Signed in with {key.title()}", credits=credits)
        self._signin = SimpleNamespace(available=lambda: can_sign_in)
        self._quota = quota
        self.quota_asked: list[tuple[str, bool]] = []

    def auth(self, creds: Any, want: str) -> str:
        if want == "signin":
            return "signin"
        return "key" if creds.get(f"{self.info.key}_key") else ""

    def signin(self) -> Any:
        return self._signin

    def quota(self, creds: Any, auth: str, start: bool = False) -> Any:
        self.quota_asked.append((auth, start))
        return self._quota


class FakePopups:
    def in_background(self, fn: Any, cb: Any) -> None:
        cb(fn())


class FakeDialog:
    def __init__(self, closed: bool = False) -> None:
        self.closed = closed
        self.answers: list[tuple[bool, str]] = []

    def checked(self, ok: bool, msg: str) -> None:
        if self.closed:
            raise RuntimeError("Internal C++ object already deleted.")
        self.answers.append((ok, msg))


def make(providers: dict[str, FakeProvider], creds: dict[str, Any] | None = None,
         model: str = "") -> tuple[ProviderSettings, list[str]]:
    saved, cfg, saves = SavedState(), Config(), []
    cfg.model = model
    acc = Accounts(providers, saved, {} if creds is None else creds, save=lambda: saves.append("save"))  # type: ignore[arg-type]

    def nope(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("not expected here")
    ps = ProviderSettings(
        acc, providers, acc.creds, saved, cfg, FakePopups(), SimpleNamespace(), SimpleNamespace(), ChatState(),  # type: ignore[arg-type]
        save=lambda: saves.append("save"), ui_unit=nope, cat_monitor=nope, menu_items=nope, open_menu_at=nope,
        advice=nope, credit_budget=lambda: external.CreditBudget(acc.ext_p(acc.credit_p),
                                                                 saved.ext_usage.setdefault("copilot_credits", {})),
        model_choice=nope, model_status=nope, drop_copilot_chat=nope, stop_copilot=nope, copilot_quota=nope,
        show_model_banner=nope)
    return ps, saves


def entries(item: MenuItem) -> dict[str, str]:
    return {k.key: k.label for k in item.children}


def test_a_providers_menu_follows_how_it_is_connected(qapp) -> None:  # type: ignore[no-untyped-def]
    creds: dict[str, Any] = {}
    providers = {"acme": FakeProvider("acme"), "gh": FakeProvider("gh", signin_kind="device", can_sign_in=False)}
    ps, _ = make(providers, creds, model="acme:m2")
    item = ps.provider_menu("acme")
    kids = entries(item)
    assert item.note == "" and kids["ext:state:acme"] == "Not connected"
    assert kids["ext:signin:acme"] == "Sign in with Acme" and kids["ext:key:acme"] == "Use an API key..."
    assert "ext_models_acme" not in kids and "ext:out:acme" not in kids
    assert "ext:signin:gh" not in entries(ps.provider_menu("gh"))            # (this build can't sign in there)

    creds["acme_key"] = "sk-1"
    ps.accounts.ext_p("acme")["models"] = [{"id": "m1", "name": "One"}, {"id": "m2", "name": "Two"}]
    item = ps.provider_menu("acme")
    kids = entries(item)
    assert item.note == "ON" and kids["ext:state:acme"] == "Connected with an API key"
    assert kids["ext:key:acme"] == "Change API key..." and kids["ext:out:acme"] == "Remove API key"
    models = next(k for k in item.children if k.key == "ext_models_acme").children
    assert [(m.key, m.checked) for m in models] == [("model:acme:m1", False), ("model:acme:m2", True),
                                                    ("ext:refresh:acme", None)]

    ps.accounts.ext_p("acme")["auth"] = "signin"
    kids = entries(ps.provider_menu("acme"))
    assert kids["ext:state:acme"] == "Signed in with Acme" and kids["ext:out:acme"] == "Sign out"
    assert "ext:signin:acme" not in kids


@pytest.mark.parametrize(("text", "msg"), [
    ("lots", "That isn't an amount I understand - try e.g. 3.5M, 750k or 2000000."),
    ("500", "That's too little to chat with - at least 1k tokens."),
    ("200M", "The highest limit is 100M tokens."),
])
def test_a_custom_token_limit_out_of_range_is_refused(qapp, text: str, msg: str) -> None:  # type: ignore[no-untyped-def]
    ps, saves = make({"acme": FakeProvider("acme")})
    dlg = FakeDialog()
    ps._check_limit("acme", text, dlg)
    assert dlg.answers == [(False, msg)] and "limit" not in ps.accounts.ext_p("acme") and not saves


def test_a_custom_token_limit_is_set_unless_the_dialog_was_closed(qapp) -> None:  # type: ignore[no-untyped-def]
    ps, saves = make({"acme": FakeProvider("acme")})
    ps._check_limit("acme", "3.5M", FakeDialog(closed=True))
    assert "limit" not in ps.accounts.ext_p("acme") and not saves
    dlg = FakeDialog()
    ps._check_limit("acme", "3.5M", dlg)
    assert dlg.answers == [(True, "")] and saves == ["save"]
    assert ps.accounts.ext_p("acme")["limit"] == 3_500_000 and ps.accounts.budget("acme").allowed() == 3_500_000


def test_a_custom_credit_limit_asks_for_the_plan_then_stays_within_it(qapp) -> None:  # type: ignore[no-untyped-def]
    plan = FakeProvider("plan", credits=True, quota={"total": 300, "used": 10})
    ps, saves = make({"plan": plan}, {"plan_key": "ghp"})
    assert ps.credit_cap() is None
    dlg = FakeDialog()
    ps._check_credit_limit("400", dlg)
    assert plan.quota_asked == [("key", True)] and ps.credit_cap() == 300.0
    assert dlg.answers == [(False, "Your Copilot plan has 300 credits a month - that's the most I can be given.")]
    ps._check_credit_limit("250.04", dlg)
    assert plan.quota_asked == [("key", True)]                              # (the plan is known now)
    assert dlg.answers[-1] == (True, "") and ps.accounts.ext_p("plan")["credit_limit"] == 250.0 and saves == ["save"]
