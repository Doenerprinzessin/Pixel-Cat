"""PixelCatApp: whole sign-in flows (popups, browser, results), the cat's mouse handlers, external
model failures (limits, too-long requests), Copilot's page-read permission and woken-up tasks."""
from __future__ import annotations

import json
import time

import pytest

from pixelcat.engine.states import S
from pixelcat.llm import external
from pixelcat.adapters.llm.chat_job import ExternalChat
from pixelcat.llm.watch import Watch

pytestmark = pytest.mark.qt
MODELS = [{"id": "m1", "name": "Model One"}]


@pytest.fixture
def bg(pet, sync_background):  # type: ignore[no-untyped-def]
    sync_background(pet)
    return pet


def _titles(pet) -> list[str]:  # type: ignore[no-untyped-def]
    out = []
    for p in pet.popups.open:
        try:
            out.append(p.title)
        except RuntimeError:
            pass
    return out


# ---------------------------------------------------------------- sign-in flows
@pytest.mark.parametrize("have_helper", [True, False])
def test_anthropic_sign_in(bg, app_env, monkeypatch, have_helper: bool) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import ant_tool, anthropic_client

    steps: list[str] = []
    monkeypatch.setattr(ant_tool, "find", lambda: "ant.exe" if have_helper else None)
    monkeypatch.setattr(ant_tool, "install", lambda progress, cancel: (steps.append("install"), progress(5, 10),
                                                                       "ant.exe")[2])

    def login(exe, open_url, cancel):  # type: ignore[no-untyped-def]
        steps.append(f"login {exe}")
        open_url("https://platform.claude.com/oauth")

    monkeypatch.setattr(ant_tool, "login", login)
    monkeypatch.setattr(anthropic_client, "list_models", lambda creds, auth="key": MODELS)
    bg._on_menu("ext:signin:anthropic")
    ask = bg.popups.open[-1]
    assert ask.title == "Sign in with Anthropic" and ("15 MB" in ask.text.text()) is (not have_helper)
    ask.ok.click()
    assert steps == ([] if have_helper else ["install"]) + ["login ant.exe"]
    assert app_env.opened_urls == ["https://platform.claude.com/oauth"]
    assert bg.accounts.auth("anthropic") == "signin" and bg.accounts.models("anthropic") == MODELS
    assert _titles(bg)[-1] == "Claude is connected"


def test_anthropic_helper_download_fails(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import ant_tool

    def fail(progress, cancel):  # type: ignore[no-untyped-def]
        raise ant_tool.AntError("The download was damaged.")

    monkeypatch.setattr(ant_tool, "find", lambda: None)
    monkeypatch.setattr(ant_tool, "install", fail)
    bg._on_menu("ext:signin:anthropic")
    bg.popups.open[-1].ok.click()
    pop = bg.popups.open[-1]
    assert "The download was damaged." in pop.text.text() and pop.ok.text() == "API KEY"
    assert bg.accounts.auth("anthropic") == ""


def test_github_sign_in(bg, app_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import copilot_client, github_signin

    def sign_in(creds, show_code, cancel):  # type: ignore[no-untyped-def]
        show_code("ABCD-1234", "https://github.com/login/device")
        creds.set("copilot_signin", {"token": "gho_x"})
        return {"login": "octocat"}

    monkeypatch.setattr(copilot_client, "runtime_path", lambda: "rt")
    monkeypatch.setattr(github_signin, "sign_in", sign_in)
    monkeypatch.setattr(copilot_client, "list_models", lambda creds, auth: MODELS)
    monkeypatch.setattr(copilot_client, "quota", lambda creds, auth, start=False: None)
    bg.provider_settings._device_signin("copilot")
    assert bg.popups.open[-1].title == "Sign in with GitHub"
    bg.popups.open[-1].ok.click()
    assert app_env.clipboard.text() == "ABCD-1234" and app_env.opened_urls == ["https://github.com/login/device"]
    assert bg.accounts.auth("copilot") == "signin" and bg.accounts.ext_p("copilot")["login"] == "octocat"
    assert _titles(bg)[-1] == "Copilot is connected"


def test_chatgpt_sign_in(bg, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import chatgpt_signin, openai_client

    def sign_in(creds, cancel=None, again=None):  # type: ignore[no-untyped-def]
        again()
        creds.set("chatgpt", {"access_token": "a", "refresh_token": "r"})
        return {"plan": True}

    monkeypatch.setattr(chatgpt_signin, "sign_in", sign_in)
    monkeypatch.setattr(openai_client, "list_models", lambda creds, auth: MODELS)
    bg._on_menu("ext:signin:openai")
    assert bg.popups.open[-1].title == "Sign in with ChatGPT"
    bg.popups.open[-1].ok.click()
    assert bg.accounts.auth("openai") == "chatgpt" and _titles(bg)[-1] == "Using your ChatGPT plan"


# ---------------------------------------------------------------- the cat under the mouse
def test_click_double_click_drag_and_drop(pet) -> None:  # type: ignore[no-untyped-def]
    pet.start()
    b = pet.brain
    pet.overlay.clicked.emit()
    pet.overlay.double_clicked.emit()
    pet.overlay.drag_started.emit()
    assert b.state == S.HELD
    pet.overlay.dragged.emit(700, 500)
    assert b.x == 700 and b.y > 500             # (held by the scruff: the feet hang below the pointer)
    pet.overlay.dropped.emit(100.0, -50.0)
    assert b.state != S.HELD and (b.vx, b.vy) == (60.0, -30.0)
    pet.overlay.petting.emit(0.5)
    pet.overlay.menu_requested.emit()
    assert pet.menu.isVisible()


# ---------------------------------------------------------------- external models
class FakeExternal(ExternalChat):
    def run(self) -> None:
        pass


@pytest.fixture
def claude(pet, monkeypatch):  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import anthropic_client

    made: list = []  # type: ignore[type-arg]

    def make(req, events):  # type: ignore[no-untyped-def]
        w = FakeExternal("anthropic", req, events)
        made.append(w)
        return w

    monkeypatch.setattr(anthropic_client, "AnthropicChat", make)
    pet.creds.set("anthropic_key", "k")
    pet.accounts.ext_p("anthropic")["models"] = [{"id": "m1", "name": "Claude One", "context": 200_000}]
    pet.cfg.model = "anthropic:m1"
    pet.open_chat()
    return made


def _say(pet, text: str) -> None:  # type: ignore[no-untyped-def]
    pet.chat.input.setText(text)
    pet.chat._send()


def test_an_external_answer_counts_its_tokens(pet, claude) -> None:  # type: ignore[no-untyped-def]
    _say(pet, "hi claude")
    w = claude[-1]
    assert w.messages[-1]["content"] == "hi claude" and w.web is True
    w.events.status(external.SEARCHING)
    assert pet.chat.msgs[-1].text == external.SEARCHING
    w.events.used("anthropic", 4321)
    w.raw, w.note = [{"type": "text", "text": "Hello!"}], "This model always thinks."
    w.events.done("Hello!", [])
    assert pet.accounts.budget("anthropic").used() == 4321
    assert pet.chat_state.history[-1]["raw"] == [{"type": "text", "text": "Hello!"}]
    assert any(m.text == "This model always thinks." for m in pet.chat.msgs)


def test_a_too_long_request_is_shortened_and_sent_again(pet, claude) -> None:  # type: ignore[no-untyped-def]
    _say(pet, "a long story")
    claude[-1].events.failed("too_long", json.dumps({"limit": 100_000, "actual": 150_000}))
    assert len(claude) == 2 and pet.accounts.ext_p("anthropic")["ctx_learned"]["m1"] >= external.MIN_BUDGET
    assert "shortened the older parts" in pet.chat.msgs[-2].text
    learned = pet.accounts.ext_p("anthropic")["ctx_learned"]["m1"]
    assert pet._ctx_budget() == learned
    claude[-1].events.failed("too_long", "{}")
    claude[-1].events.failed("too_long", "{}")
    assert len(claude) == 3 and pet.chat.msgs[-1].role == "error" and not pet._task_active


def test_a_provider_limit_pauses_the_task(pet, claude) -> None:  # type: ignore[no-untyped-def]
    _say(pet, "hi")
    claude[-1].limit("rate", None)
    assert pet.popups.open[-1].title == "Rate limit reached" and pet._task_active
    pet.popups.open[-1].no.click()
    assert not pet._task_active


@pytest.mark.parametrize("kind", ["auth", "ext_offline", "refused"])
def test_other_external_failures_end_the_task(pet, claude, kind: str) -> None:  # type: ignore[no-untyped-def]
    _say(pet, "hi")
    claude[-1].events.failed(kind, "detail")
    assert pet.chat.msgs[-1].role == "error" and not pet._task_active


@pytest.mark.parametrize(("p", "mid", "info", "budget"), [
    ("anthropic", "m1", {"context": 200_000}, external.max_input("anthropic", "m1", {"context": 200_000})),
    ("openai", "gpt-x", {}, external.max_input("openai", "gpt-x", {})), ("copilot", "m1", {}, 65536)])
def test_context_budget_per_provider(pet, p: str, mid: str, info: dict, budget: int) -> None:  # type: ignore[no-untyped-def,type-arg]
    pet.accounts.ext_p(p)["models"] = [{"id": mid, "name": "M", **info}]
    pet.cfg.model = f"{p}:{mid}"
    assert pet._ctx_budget() == budget


# ---------------------------------------------------------------- Copilot reads a web page
@pytest.mark.parametrize(("auto_mode", "approved", "asks"), [(False, [], True), (True, [], False),
                                                             (False, ["read_web_page"], False)])
def test_copilot_page_read_permission(pet, auto_mode: bool, approved: list, asks: bool) -> None:  # type: ignore[no-untyped-def,type-arg]
    pet._ensure_chat()
    pet._begin_task()
    pet.saved.auto_mode = auto_mode
    pet.saved.auto_approved_tools[:] = approved
    replies: list[bool] = []
    pet._copilot_permission("https://example.com", replies.append)
    if asks:
        assert replies == [] and "let Copilot read the web page https://example.com?" in pet.chat.confirm_label.text()
        pet.chat.always_btn.click()
        assert pet.saved.auto_approved_tools == [pet.toolbox.specs["read_web_page"].group]
    assert replies == [True] and pet.chat.msgs[-1].text.startswith("🌐 Copilot reads https://example.com")


def test_copilot_page_read_without_a_task_is_refused(pet) -> None:  # type: ignore[no-untyped-def]
    replies: list[bool] = []
    pet._copilot_permission("https://example.com", replies.append)
    assert replies == [False]


# ---------------------------------------------------------------- woken-up tasks
def _watch(i: int = 1) -> Watch:
    return Watch(i, "time", f"thing {i}", then="tell the user", request="remind me", started=time.time())


def test_a_wake_waits_while_the_cat_is_busy(pet, workers) -> None:  # type: ignore[no-untyped-def]
    pet._ensure_chat()
    pet._begin_task()
    pet._on_watch_fired(_watch(), "It happened", False)
    assert workers == [] and len(pet._wakes) == 1
    pet._end_task()
    pet._drain_wakes()
    assert pet._wakes == [] and len(workers) == 1 and pet._task_wake
    workers[-1].events.done("Done: it happened.", [])
    assert pet.brain.bubble.kind == "bubble_exclaim"


def test_a_wake_waits_while_the_user_is_typing(pet, workers) -> None:  # type: ignore[no-untyped-def]
    chat = pet._ensure_chat()
    chat.show()
    chat.input.setText("half a sentence")
    pet._on_watch_fired(_watch(), "x", True)
    assert workers == [] and pet._wake_timer.isActive()
    pet._wake_timer.stop()
    chat.input.setText("")
    pet._drain_wakes()
    assert workers and "Tell the user briefly, and offer to keep waiting." in pet.chat_state.history[-1]["content"]


def test_watch_bar_and_cancel(pet) -> None:  # type: ignore[no-untyped-def]
    chat = pet._ensure_chat()
    with pet.watches._lock:
        pet.watches._watches.append(_watch(5))
    pet._on_watches_changed()
    assert "thing 5" in chat.watch_bar.text()
    assert "watching 1 thing" in pet.tray.toolTip()
    chat.watch_cancel_requested.emit(5)
    assert chat.msgs[-1].text == "Okay, I stopped watching for thing 5."
