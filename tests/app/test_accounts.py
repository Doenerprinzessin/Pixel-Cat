"""The providers' accounts, without the app (fake providers and keys)."""
from __future__ import annotations

from typing import Any

import pytest

from pixelcat.app.accounts import Accounts
from pixelcat.llm import external
from pixelcat.persistence import SavedState
from pixelcat.ports import ProviderInfo


class FakeProvider:
    """Connected with a key when the keys have "<key>_key"; "signin" when the user chose it."""

    def __init__(self, key: str, *, local: bool = False, credits: bool = False) -> None:
        self.info = ProviderInfo(key=key, name=key.title(), brand=key.upper(), signin="", key_url="", key_hint="",
                                 usage_url="", local=local, credits=credits)
        self.asked: list[tuple[Any, str]] = []

    def auth(self, creds: Any, want: str) -> str:
        self.asked.append((creds, want))
        if want == "signin":
            return "signin"
        return "key" if creds.get(f"{self.info.key}_key") else ""


@pytest.fixture
def providers() -> dict[str, FakeProvider]:
    return {"acme": FakeProvider("acme"), "plan": FakeProvider("plan", credits=True),
            "here": FakeProvider("here", local=True)}


def make(providers: dict[str, FakeProvider], creds: dict[str, Any] | None = None) -> tuple[Accounts, list[str]]:
    saves: list[str] = []
    return Accounts(providers, SavedState(), creds or {}, save=lambda: saves.append("save")), saves  # type: ignore[arg-type]


def test_the_views_follow_the_providers(providers: dict[str, FakeProvider]) -> None:
    acc, _ = make(providers)
    assert list(acc.external) == ["acme", "plan"]
    assert acc.local is providers["here"] and acc.credit_p == "plan"
    providers["more"] = FakeProvider("more")                  # (computed each time, never kept)
    del providers["plan"]
    providers["more"].info = ProviderInfo(key="more", name="M", brand="M", signin="", key_url="", key_hint="",
                                          usage_url="", credits=True)
    assert list(acc.external) == ["acme", "more"] and acc.credit_p == "more"


def test_settings_live_in_the_save_and_auth_asks_the_provider(providers: dict[str, FakeProvider]) -> None:
    creds = {"acme_key": "sk-1"}
    acc, _ = make(providers, creds)
    assert acc.auth("acme") == "key" and acc.auth("plan") == ""
    assert providers["acme"].asked == [(creds, "")]
    acc.ext_p("plan")["auth"] = "signin"
    assert acc.saved.ext_settings["plan"] == {"auth": "signin"}
    assert acc.auth("plan") == "signin"


def test_models_skip_broken_entries_and_set_models_names_them(providers: dict[str, FakeProvider],
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(external, "NAMES", {})
    acc, saves = make(providers)
    acc.ext_p("acme")["models"] = "broken"
    assert acc.models("acme") == []
    acc.set_models("acme", [{"id": "a1", "name": "Alpha"}, {"id": "", "name": "?"}, {"id": "b2", "name": "Beta"}])
    acc.ext_p("acme")["models"].append("junk")
    assert [m["id"] for m in acc.models("acme")] == ["a1", "b2"]
    assert acc.info("acme", "b2") == {"id": "b2", "name": "Beta"} and acc.info("acme", "zz") == {}
    assert external.NAMES["acme:a1"] == "Alpha" and saves == ["save"]


def test_budget_reads_and_writes_the_save(providers: dict[str, FakeProvider]) -> None:
    acc, _ = make(providers)
    acc.ext_p("acme")["limit"] = 2_000_000
    b = acc.budget("acme")
    b.u["allowed"] = 5
    assert b.limit == 2_000_000 and acc.saved.ext_usage["acme"]["allowed"] == 5
