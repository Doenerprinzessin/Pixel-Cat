"""PixelCatApp: the external AI providers - settings from the menu, token and credit limits,
API keys, sign-in results, signing out, and the default / per-chat model and effort.

The provider SDKs and sign-in helpers are replaced by fakes; background work runs at once
(``sync_background``).  These pin what the ModelProvider refactor has to keep."""
from __future__ import annotations

from datetime import datetime

import pytest

from pixelcat.llm import external
from pixelcat.ui.dialogs import AmountDialog, KeyDialog

pytestmark = pytest.mark.qt
MODELS = [{"id": "m1", "name": "Model One"}, {"id": "m2", "name": "Model Two"}]


class Kind(Exception):
    def __init__(self, kind: str, msg: str = "nope") -> None:
        super().__init__(msg)
        self.kind = kind


@pytest.fixture
def bg(pet, sync_background):  # type: ignore[no-untyped-def]
    sync_background(pet)
    return pet


@pytest.fixture
def dialogs(pet, monkeypatch) -> list:  # type: ignore[no-untyped-def,type-arg]
    shown: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(pet, "_show_amount_dialog", shown.append)
    return shown


# ---------------------------------------------------------------- settings from the menu
def test_thinking_web_search_and_effort(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    st = pet.saved.ext_settings
    pet._on_menu("ext:thinking")
    pet._on_menu("ext:websearch")
    pet._on_menu("ext:effort:high")
    pet._on_menu("ext:effort:bogus")
    assert (st["thinking"], st["web_search"], st["effort"]) == (False, False, "high")
    assert app_env.saved_states
    ext = pet._external_menu()
    assert ext.children[3].checked is False and ext.children[5].checked is False
    assert [c.checked for c in ext.children[4].children] == [False, False, True, False, False]


def test_token_limit_period_and_maximum(pet) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("ext:limit:anthropic:50000")
    pet._on_menu("ext:period:anthropic:week")
    pet._on_menu("ext:max:anthropic:3")
    pet._on_menu("ext:period:anthropic:year")
    s = pet._ext_p("anthropic")
    assert (s["limit"], s["period"], s["maximum"]) == (50000, "week", 3.0)
    b = pet._budget("anthropic")
    assert (b.limit, b.period, b.maximum, b.allowed()) == (50000, "week", 150000, 50000)
    start = external.period_start("week", datetime.now()).timestamp()
    assert pet.saved.ext_usage["anthropic"]["start"] == start
    kids = pet._provider_menu("anthropic").children
    limit = next(k for k in kids if k.key == "ext_limit_anthropic")
    assert limit.note == "50k/week" and [c.checked for c in limit.children].count(True) == 1


def test_a_new_limit_drops_the_raises(pet) -> None:  # type: ignore[no-untyped-def]
    b = pet._budget("openai")
    b.add(1_000_000)
    b.raise_limit()
    assert b.allowed() == 1_500_000
    pet._on_menu("ext:limit:openai:2000000")
    assert pet._budget("openai").allowed() == 2_000_000


def test_credit_limit(pet) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("ext:climit:copilot:2000")
    assert pet._ext_p("copilot")["credit_limit"] == 2000.0 and pet._credit_budget().allowed() == 2000.0


def test_the_open_menu_shows_the_new_setting(pet) -> None:  # type: ignore[no-untyped-def]
    pet.start()
    pet._open_menu()
    pet._on_menu("ext:limit:copilot:0")
    assert pet.menu.isVisible()
    ext = next(c for c in pet.menu.items[4].children if c.key == "set_model").children[-1]
    cop = next(c for c in ext.children if c.key == "ext_copilot")
    assert next(c for c in cop.children if c.key == "ext_limit_copilot").note == "NONE"


@pytest.mark.parametrize(("text", "ok", "limit"), [
    ("3.5M", True, 3_500_000), ("750k", True, 750_000), ("2000000", True, 2_000_000),
    ("lots", False, None), ("999", False, None), ("1k", True, 1000), ("100M", True, 100_000_000),
    ("101M", False, None)])
def test_custom_token_limit(pet, dialogs, text: str, ok: bool, limit: int | None) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("ext:custom:openai")
    dlg = dialogs[-1]
    assert isinstance(dlg, AmountDialog) and dlg.field.text() == "1M" and dlg.save.text() == "SET"
    dlg.field.setText(text)
    dlg._save()
    if ok:
        assert pet._budget("openai").limit == limit and pet._budget("openai").allowed() == limit
    else:
        assert dlg.status.text() and pet._budget("openai").limit == 1_000_000 and dlg.field.isEnabled()


@pytest.mark.parametrize(("quota", "text", "ok", "limit"), [
    (None, "7.5k", True, 7500.0), (None, "0.01", False, None), (None, "x", False, None),
    ({"total": 1500}, "1500", True, 1500.0), ({"total": 1500}, "2000", False, None),
    ({"unlimited": True}, "50000", True, 50000.0)])
def test_custom_credit_limit(pet, dialogs, quota, text: str, ok: bool, limit) -> None:  # type: ignore[no-untyped-def]
    if quota is not None:
        pet._ext_p("copilot")["quota"] = quota
    pet._on_menu("ext:ccustom:copilot")
    dlg = dialogs[-1]
    assert dlg.field.text() == "5000"
    dlg.field.setText(text)
    dlg._save()
    if ok:
        assert pet._credit_budget().limit == limit
    else:
        assert dlg.status.text() and pet._credit_budget().limit == external.CREDIT_DEFAULT


def test_custom_credit_limit_asks_github_for_the_plan_first(bg, dialogs, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import copilot_client

    asked: list[str] = []
    monkeypatch.setattr(copilot_client, "quota", lambda creds, auth, start=False: (asked.append(auth),
                                                                                   {"total": 300.0, "used": 1})[1])
    bg.creds.set("copilot_key", "ghp_x")
    bg._on_menu("ext:ccustom:copilot")
    dialogs[-1].field.setText("1000")
    dialogs[-1]._save()
    assert asked == ["key"] and bg._ext_p("copilot")["quota"]["total"] == 300.0
    assert bg._credit_budget().limit == external.CREDIT_DEFAULT and "300" in dialogs[-1].status.text()


# ---------------------------------------------------------------- connection state
# (each provider's rules: tests/adapters/llm/test_model_providers.py; here: the app asks with its creds and choice)
@pytest.mark.parametrize(("p", "creds", "settings", "auth"), [
    ("anthropic", {}, {}, ""), ("anthropic", {"anthropic_key": "k"}, {"auth": "signin"}, "signin"),
    ("openai", {"chatgpt": {"access_token": "a", "refresh_token": "r"}}, {"auth": "chatgpt"}, "chatgpt"),
    ("copilot", {"copilot_key": "k"}, {}, "key"),
    ("copilot", {"copilot_signin": {"token": "t"}}, {"auth": "signin"}, "signin"),
])
def test_ext_auth(pet, p: str, creds: dict, settings: dict, auth: str) -> None:  # type: ignore[no-untyped-def,type-arg]
    for k, v in creds.items():
        pet.creds.set(k, v)
    pet._ext_p(p).update(settings)
    assert pet._ext_auth(p) == auth


@pytest.mark.parametrize(("exc", "text"), [
    (Kind("auth"), "That key wasn't accepted"), (type("AuthenticationError", (Exception,), {})(), "wasn't accepted"),
    (Kind("ext_offline"), "couldn't reach the provider"), (type("APITimeoutError", (Exception,), {})(), "couldn't reach"),
    (type("PermissionDeniedError", (Exception,), {})(), "isn't allowed"), (ValueError("boom"), "That didn't work: boom")])
def test_key_problems(exc: Exception, text: str) -> None:
    from pixelcat.app import PixelCatApp

    assert text in PixelCatApp._key_problem(exc)


@pytest.mark.parametrize(("exc", "text"), [
    (Kind("auth"), "GitHub didn't accept that token"), (Kind("denied"), "Copilot Requests permission"),
    (Kind("ext_offline"), "couldn't reach GitHub"), (Kind("install", "x" * 300), "x" * 200),
    (Kind("other", "weird"), "That didn't work: weird")])
def test_copilot_problems(exc: Exception, text: str) -> None:
    from pixelcat.app import PixelCatApp

    got = PixelCatApp._copilot_problem(exc)
    assert text in got and len(got) <= 200 + 30


@pytest.mark.parametrize(("q", "note"), [({"reset": "garbage"}, ""), ({}, "")])
def test_quota_reset_note_without_a_date(q, note) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.app import PixelCatApp

    assert PixelCatApp._quota_reset_note(q) == note


def test_credit_cap(pet) -> None:  # type: ignore[no-untyped-def]
    s = pet._ext_p("copilot")
    assert pet._credit_cap() is None
    s["quota"] = {"total": 1500}
    assert pet._credit_cap() == 1500.0
    s["quota"] = {"total": 1500, "unlimited": True}
    assert pet._credit_cap() is None
    s["quota"] = {"total": 0}
    assert pet._credit_cap() is None


# ---------------------------------------------------------------- API keys and sign-ins
@pytest.mark.parametrize("p", ["anthropic", "openai"])
def test_menu_key_entry_opens_the_key_dialog(pet, p: str) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu(f"ext:key:{p}")
    dlg = pet._key_dialog
    assert isinstance(dlg, KeyDialog) and dlg.isVisible() and not dlg.remove.isVisibleTo(dlg)
    assert dlg.field.placeholderText().endswith("...")


@pytest.mark.parametrize(("p", "module"), [("anthropic", "anthropic_client"), ("openai", "openai_client")])
def test_a_good_key_connects_the_provider(bg, monkeypatch, p: str, module: str) -> None:  # type: ignore[no-untyped-def]
    import importlib

    mod = importlib.import_module(f"pixelcat.llm.{module}")
    seen: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(mod, "list_models", lambda creds, auth="key": (seen.append((creds.get(f"{p}_key"), auth)),
                                                                        MODELS)[1])
    bg._ask_key(p)
    dlg = bg._key_dialog
    dlg.field.setText("sk-good")
    dlg._save()
    assert seen == [("sk-good", "key")]
    assert bg.creds.get(f"{p}_key") == "sk-good" and bg._ext_auth(p) == "key"
    assert bg._ext_models(p) == MODELS and external.NAMES[f"{p}:m1"] == "Model One"
    assert not dlg.isVisible()
    pop = bg._popups[-1]
    assert pop.title.endswith("is connected") and "2 " in pop.text.text()


def test_a_bad_key_is_refused_with_the_reason(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import anthropic_client

    def refuse(creds, auth="key"):  # type: ignore[no-untyped-def]
        raise Kind("auth")

    monkeypatch.setattr(anthropic_client, "list_models", refuse)
    bg._ask_key("anthropic")
    bg._key_dialog.field.setText("sk-bad")
    bg._key_dialog._save()
    assert bg.creds.get("anthropic_key") is None and bg._ext_auth("anthropic") == ""
    assert bg._key_dialog.status.text() == "That key wasn't accepted - check that you copied all of it."


def test_a_good_github_token_connects_copilot(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import copilot_client

    monkeypatch.setattr(copilot_client, "runtime_path", lambda: "copilot-runtime")
    monkeypatch.setattr(copilot_client, "check_token", lambda key: ("octocat", MODELS))
    monkeypatch.setattr(copilot_client, "quota", lambda creds, auth, start=False: {"total": 300.0, "used": 3})
    bg._on_menu("ext:key:copilot")
    dlg = bg._key_dialog
    assert dlg.get.text() == "GET A TOKEN"
    dlg.field.setText("github_pat_x")
    dlg._save()
    assert bg.creds.get("copilot_key") == "github_pat_x" and bg._ext_auth("copilot") == "key"
    assert bg._ext_p("copilot")["login"] == "octocat" and bg._ext_p("copilot")["quota"]["total"] == 300.0


def test_copilot_without_its_runtime_offers_to_get_it(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import copilot_client

    installs: list[bool] = []
    monkeypatch.setattr(copilot_client, "runtime_path", lambda: None)
    monkeypatch.setattr(copilot_client, "install", lambda progress, cancel: (installs.append(True), "rt")[1])
    bg._on_menu("ext:key:copilot")
    pop = bg._popups[-1]
    assert pop.title == "GitHub Copilot" and "100 MB" in pop.text.text()
    pop.ok.click()
    assert installs == [True] and isinstance(bg._key_dialog, KeyDialog)


@pytest.mark.parametrize(("p", "method"), [("anthropic", "_anthropic_signin"), ("openai", "_chatgpt_signin"),
                                           ("copilot", "_github_signin")])
def test_menu_sign_in_entry_starts_that_providers_sign_in(pet, monkeypatch, p: str, method: str) -> None:  # type: ignore[no-untyped-def]
    started: list[str] = []
    monkeypatch.setattr(pet, method, lambda: started.append(p))
    pet._on_menu(f"ext:signin:{p}")
    assert started == [p]


def test_chatgpt_sign_in_result(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import openai_client

    monkeypatch.setattr(openai_client, "list_models", lambda creds, auth: MODELS)
    bg.creds.set("chatgpt", {"access_token": "a", "refresh_token": "r", "email": "cat@example.com"})
    bg._chatgpt_done({"plan": True})
    assert bg._ext_auth("openai") == "chatgpt" and bg._ext_p("openai")["welcomed"]
    assert bg._popups[-1].title == "Using your ChatGPT plan"
    bg._chatgpt_done({"plan": True})
    assert bg._popups[-1].title == "ChatGPT is connected"


def test_chatgpt_sign_in_without_plan_usage(bg) -> None:  # type: ignore[no-untyped-def]
    bg._chatgpt_done({"plan": False})
    assert bg._popups[-1].title == "Signed in - but no plan usage" and bg._ext_auth("openai") == ""


def test_a_failed_or_cancelled_sign_in(bg) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import chatgpt_signin

    bg._chatgpt_done(chatgpt_signin.SignInError("cancelled"))
    assert bg._popups == []
    bg._chatgpt_done(chatgpt_signin.SignInError("The browser closed"))
    assert bg._popups[-1].title == "Sign in with ChatGPT" and "browser closed" in bg._popups[-1].text.text()


def test_github_sign_in_result(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import threading

    from pixelcat.llm import copilot_client

    monkeypatch.setattr(copilot_client, "list_models", lambda creds, auth: MODELS)
    monkeypatch.setattr(copilot_client, "quota", lambda creds, auth, start=False: {"unlimited": True})
    ev = threading.Event()
    bg._signin_cancel = ev
    bg.creds.set("copilot_signin", {"token": "t"})
    bg._github_done({"login": "octocat"}, threading.Event())         # an attempt that was started over
    assert bg._ext_auth("copilot") == ""
    bg._github_done({"login": "octocat"}, ev)
    assert bg._ext_auth("copilot") == "signin" and bg._ext_p("copilot")["login"] == "octocat"
    assert bg._ext_models("copilot") == MODELS and bg._ext_p("copilot")["quota"] == {"unlimited": True}


def test_anthropic_sign_in_result(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import threading

    from pixelcat.llm import anthropic_client

    monkeypatch.setattr(anthropic_client, "list_models", lambda creds, auth="key": MODELS)
    bg._anthropic_signed_in("ok", threading.Event())
    assert bg._ext_auth("anthropic") == "signin" and bg._ext_models("anthropic") == MODELS


def test_reload_the_model_list(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import openai_client

    monkeypatch.setattr(openai_client, "list_models", lambda creds, auth: MODELS[:1])
    bg._on_menu("ext:refresh:openai")
    assert bg._ext_models("openai") == []                           # (not connected: nothing to load)
    bg.creds.set("openai_key", "k")
    bg._on_menu("ext:refresh:openai")
    assert bg._ext_models("openai") == MODELS[:1]


def test_a_failed_reload_says_why(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import openai_client

    def fail(creds, auth):  # type: ignore[no-untyped-def]
        raise Kind("ext_offline")

    monkeypatch.setattr(openai_client, "list_models", fail)
    bg.creds.set("openai_key", "k")
    bg._on_menu("ext:refresh:openai")
    assert bg._popups[-1].title == "Models" and "internet" in bg._popups[-1].text.text()


# ---------------------------------------------------------------- signing out
def test_removing_a_key_goes_back_to_the_default_model(bg, app_env) -> None:  # type: ignore[no-untyped-def]
    bg.creds.set("anthropic_key", "k")
    bg._ext_p("anthropic")["models"] = MODELS
    bg._choose_default_model("anthropic:m1")
    assert bg.cfg.model == "anthropic:m1"
    bg._on_menu("ext:out:anthropic")
    assert bg.creds.get("anthropic_key") is None and bg._ext_auth("anthropic") == ""
    assert bg.cfg.model == "qwen3.5:35b-a3b" and ("model", "qwen3.5:35b-a3b") in app_env.user_settings
    assert bg._popups[-1].text.text().endswith("The Anthropic API key is removed.</p>")


def test_signing_out_of_anthropic(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import ant_tool

    calls: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(ant_tool, "find", lambda: "ant.exe")
    monkeypatch.setattr(ant_tool, "logout", lambda exe: calls.append(exe))
    bg._ext_p("anthropic")["auth"] = "signin"
    bg._on_menu("ext:out:anthropic")
    assert bg._ext_auth("anthropic") == "" and calls == ["ant.exe"]
    assert "Signed out of Anthropic." in bg._popups[-1].text.text()


def test_signing_out_of_chatgpt(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import chatgpt_signin

    monkeypatch.setattr(chatgpt_signin, "sign_out", lambda creds: False)
    bg.creds.set("chatgpt", {"access_token": "a", "refresh_token": "r"})
    bg._ext_p("openai")["auth"] = "chatgpt"
    bg._on_menu("ext:out:openai")
    assert bg._ext_p("openai")["auth"] == ""
    assert [p.title for p in bg._popups] == ["Signed out", "ChatGPT"]          # (OpenAI couldn't confirm it)


def test_signing_out_of_github_and_removing_the_token(bg) -> None:  # type: ignore[no-untyped-def]
    s = bg._ext_p("copilot")
    bg.creds.set("copilot_signin", {"token": "t"})
    bg.creds.set("copilot_key", "k")
    s.update(auth="signin", login="octocat", quota={"total": 5})
    bg.chat_model = "copilot:m1"
    bg._on_menu("ext:out:copilot")
    assert bg.creds.get("copilot_signin") is None and bg._ext_auth("copilot") == "key"
    assert "quota" not in s and s["login"] == "octocat" and bg.chat_model == "copilot:m1"
    bg._ext_disconnect("copilot", only_key=True)
    assert bg._ext_auth("copilot") == "" and "login" not in s and bg.chat_model is None
    assert bg._popups[-1].text.text().endswith("The GitHub token is removed.</p>")


# ---------------------------------------------------------------- limits while chatting
def _task(pet) -> None:  # type: ignore[no-untyped-def]
    pet._ensure_chat()
    pet._begin_task()


@pytest.mark.parametrize(("p", "used", "status"), [("anthropic", 0, "ok"), ("anthropic", 1_000_000, "soft"),
                                                   ("openai", 2_000_000, "hard")])
def test_token_limit_gate(pet, p: str, used: int, status: str) -> None:  # type: ignore[no-untyped-def]
    _task(pet)
    pet._budget(p).add(used)
    assert pet._budget(p).status() == status
    assert pet._ext_gate(p) is (status == "ok")
    if status == "soft":
        assert [b.text() for b in pet.chat._choice_btns] == ["SWITCH MODEL", "RAISE TO 1.5M", "STOP"]
        assert "Token limit reached." in pet.chat.choice_label.text()
    elif status == "hard":
        assert pet._popups[-1].title == "All tokens used" and pet.chat.msgs[-1].role == "error"


def test_raising_the_token_limit_goes_on(pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    requests: list[bool] = []
    monkeypatch.setattr(pet, "_request", lambda: requests.append(True))
    _task(pet)
    pet._budget("anthropic").add(1_000_000)
    pet._ext_gate("anthropic")
    pet.chat._choice_btns[1].click()
    assert pet._budget("anthropic").allowed() == 1_500_000 and requests == [True]
    assert "the Claude limit is 1.5M" in pet.chat.msgs[-1].text


def test_switching_or_stopping_at_the_token_limit(pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    opened: list[bool] = []
    monkeypatch.setattr(pet, "_open_model_menu", lambda: opened.append(True))
    _task(pet)
    pet._limit_answer("openai", "switch")
    assert pet._resume_after_switch and opened == [True]
    pet._limit_answer("openai", "stop")
    assert not pet._task_active and "I stopped here" in pet.chat.msgs[-1].text


def test_copilot_credit_gate(pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    requests: list[bool] = []
    monkeypatch.setattr(pet, "_request", lambda: requests.append(True))
    _task(pet)
    pet._on_copilot_credits(5000)
    assert pet._credit_budget().used() == 5000 and not pet._credit_gate()
    assert [b.text() for b in pet.chat._choice_btns] == ["SWITCH MODEL", "RAISE TO 7,500", "STOP"]
    pet.chat._choice_btns[1].click()
    assert pet._credit_budget().allowed() == 7500 and requests == [True]


@pytest.mark.parametrize(("why", "title", "url"), [
    ("rate", "Rate limit reached", None), ("plan", "ChatGPT plan limit reached", external.OPENAI.usage_url),
    ("copilot", "Copilot allowance used up", external.COPILOT.usage_url),
    ("credits", "Credits used up", external.BILLING["anthropic"]), ("spend", "Spend limit reached", None)])
def test_provider_limit_popups(pet, app_env, why: str, title: str, url) -> None:  # type: ignore[no-untyped-def]
    _task(pet)
    p = {"plan": "openai", "copilot": "copilot"}.get(why, "anthropic")
    pet._limit_popup(p, {"why": why})
    pop = pet._popups[-1]
    assert pop.title == title and pet.chat.msgs[-1].role == "error"
    pop.ok.click()
    if url:
        assert app_env.opened_urls == [url]


def test_used_tokens_are_counted(pet) -> None:  # type: ignore[no-untyped-def]
    pet._on_ext_used("anthropic", 1200)
    pet._on_ext_used("anthropic", 800)
    assert pet._budget("anthropic").used() == 2000


# ---------------------------------------------------------------- default and per-chat model, effort
def test_choosing_an_external_default_model_needs_a_connection(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("model:openai:m1")
    assert pet.cfg.model == "qwen3.5:35b-a3b"
    pet.creds.set("openai_key", "k")
    pet._on_menu("model:openai:m1")
    assert pet.cfg.model == "openai:m1" and app_env.user_settings[-1] == ("model", "openai:m1")


def test_choosing_a_local_model_that_isnt_downloaded_asks_first(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("model:qwen3.5:9b")
    assert pet.chat is not None and pet.chat.isVisible() and pet.cfg.model == "qwen3.5:35b-a3b"
    assert "download Medium (9B) (6.6 GB) and make it the default model" in pet.chat.confirm_label.text()
    pet.chat.yes_btn.click()
    assert pet.cfg.model == "qwen3.5:9b" and "qwen3.5:9b" in pet._download_ok


def test_a_local_default_model_switches_back_to_local_ai(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet.cfg.ai_mode = "online"
    pet._set_default_model("qwen3.5:4b")
    assert app_env.user_settings == [("model", "qwen3.5:4b"), ("ai_mode", "local")] and app_env.ensures == [True]


# (each provider's levels: tests/adapters/llm/test_model_providers.py; here: the chat's model and its list entry)
@pytest.mark.parametrize(("model", "info", "levels"), [
    ("qwen3.5:9b", {}, ["low", "medium", "high"]),
    ("anthropic:m1", {"effort": ["low", "high"]}, ["low", "high"]),
    ("copilot:m1", {"efforts": ["medium", "xhigh"]}, ["medium", "xhigh"]),
    ("openai:m1", {}, ["low", "medium", "high", "xhigh", "max"])])
def test_effort_levels(pet, model: str, info: dict, levels) -> None:  # type: ignore[no-untyped-def,type-arg]
    ext = external.parse(model)
    if ext:
        pet._ext_p(ext[0])["models"] = [{"id": ext[1], "name": "M", **info}]
    pet.cfg.model = model
    assert pet._effort_levels() == levels


@pytest.mark.parametrize(("think", "default"), [("auto", "medium"), (True, "high"), (False, "low"), ("on", "high"),
                                                ("off", "low")])
def test_default_effort_of_local_models(pet, think, default: str) -> None:  # type: ignore[no-untyped-def]
    pet.cfg.think = think
    assert pet._default_effort() == default


def test_default_effort_of_online_models_comes_from_settings(pet) -> None:  # type: ignore[no-untyped-def]
    pet.cfg.model = "openai:m1"
    pet.saved.ext_settings["effort"] = "xhigh"
    assert pet._default_effort() == "xhigh"
    assert pet._ext_think_effort() == (True, "xhigh")
    pet.saved.ext_settings["thinking"] = False
    assert pet._ext_think_effort() == (False, "xhigh")
    pet.chat_effort = "low"
    assert pet._ext_think_effort() == (True, "low")


def test_this_chats_model_and_effort(pet) -> None:  # type: ignore[no-untyped-def]
    chat = pet._ensure_chat()
    assert chat.model_btn.text() == "35B"
    pet._on_model_menu("chateffort:high")
    assert pet.chat_effort == "high" and chat.model_btn.text() == "35B · HIGH"
    assert "This chat now uses High effort - I always think first." in chat.msgs[-1].text
    pet._on_model_menu("chateffort:default")
    assert pet.chat_effort is None and chat.model_btn.text() == "35B"
    pet._on_model_menu("chatmodel:qwen3.5:9b")
    assert "download Medium (9B)" in chat.confirm_label.text()
    chat.no_btn.click()
    assert pet.chat_model is None and chat.msgs[-1].text == "Okay, no download."


def test_a_connected_external_model_for_this_chat(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    chat = pet._ensure_chat()
    pet._on_model_menu("chatmodel:anthropic:m1")
    assert pet.chat_model is None and "aren't connected" in chat.msgs[-1].text
    pet.creds.set("anthropic_key", "k")
    pet._ext_p("anthropic")["models"] = [{"id": "m1", "name": "Claude One"}]
    external.NAMES["anthropic:m1"] = "Claude One"
    pet._on_model_menu("chatmodel:anthropic:m1")
    assert pet.chat_model == "anthropic:m1" and pet.model_status.override == "anthropic:m1"
    assert chat.model_btn.styleSheet() != "" and app_env.ensures[-1] is True
    pet.new_chat()
    assert pet.chat_model is None and pet.model_status.override is None


def test_the_model_banner_for_a_chatgpt_plan(pet) -> None:  # type: ignore[no-untyped-def]
    chat = pet._ensure_chat()
    pet.creds.set("chatgpt", {"access_token": "a", "refresh_token": "r"})
    pet._ext_p("openai")["auth"] = "chatgpt"
    pet.cfg.model = "openai:m1"
    pet._show_model_banner()
    assert chat.model_bar.isVisibleTo(chat) and "Using ChatGPT plan" in chat.model_bar.text()
    assert external.OPENAI.usage_url in chat.model_bar.text()
