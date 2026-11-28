"""The AI turn engine without the app: a fake local model (``FakeJob``s the test drives), no tools, a recording
chat window, and the controller's callbacks recorded."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.chat import FakeJob

from pixelcat.app.session import AgentSession
from pixelcat.app.state import ChatState
from pixelcat.persistence import SavedState
from pixelcat.settings import Config

pytestmark = pytest.mark.qt


class FakeChat:
    """The chat window's calls, recorded: ``calls`` [(name, *args)]."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __getattr__(self, name: str) -> Any:
        return lambda *a: self.calls.append((name, *a))


@pytest.fixture
def made(qapp):  # type: ignore[no-untyped-def]
    """``(session, jobs, f)``: the session, the model requests it made, the recorded fakes."""
    jobs: list[FakeJob] = []

    def new_chat(req: Any, events: Any) -> FakeJob:
        jobs.append(FakeJob(req, events))
        return jobs[-1]
    f = SimpleNamespace(chat=FakeChat(), saves=0, chat_state=ChatState(), commands=[])
    accounts = SimpleNamespace(local=SimpleNamespace(info=SimpleNamespace(key="ollama"), new_chat=new_chat))
    x = SimpleNamespace()
    session = AgentSession(
        f.chat_state, Config(), SavedState(), {}, accounts, x, x, x, x, x, x, x, x, x, x, x, x,  # type: ignore[arg-type]
        chat_ui=lambda: SimpleNamespace(chat=f.chat),  # type: ignore[arg-type, return-value]
        model_choice=lambda: SimpleNamespace(effective_model=lambda: "qwen3.5:9b"),  # type: ignore[arg-type, return-value]
        save=lambda: setattr(f, "saves", f.saves + 1), sync_tray=lambda: None, ext_gate=lambda p: True,
        credit_gate=lambda: True, copilot_credits=lambda n: None, copilot_quota=lambda: None,
        limit_popup=lambda p, info: None, show_model_banner=lambda: None,
        local_command=lambda text: (f.commands.append(text), text.startswith("/"))[1])
    session.toolbox = SimpleNamespace(names=lambda: [], schemas=lambda: [])  # type: ignore[assignment]
    session.agent = SimpleNamespace(cancel=threading.Event())  # type: ignore[assignment]
    session.esc_watch = SimpleNamespace(stop=lambda: None, start=lambda: None)  # type: ignore[assignment]
    yield session, jobs, f
    for job in jobs:
        job.events._w.wait(2000)  # type: ignore[attr-defined]   # (the worker threads: FakeJob.run returns at once)


def ask(session: AgentSession, f: SimpleNamespace, text: str) -> None:
    f.chat_state.history.append({"role": "user", "content": text})
    session.reset_task_counters()
    session.begin_task()
    session.request()


def test_an_answer_ends_the_task_and_goes_into_the_history(made) -> None:  # type: ignore[no-untyped-def]
    session, jobs, f = made
    ask(session, f, "hi")
    assert session.task_active and session.worker is not None and len(jobs) == 1
    assert jobs[0].messages[0]["role"] == "system" and jobs[0].messages[1:] == [{"role": "user", "content": "hi"}]
    jobs[0].events.done("Meow.", [])
    assert f.chat_state.history[-1] == {"role": "assistant", "content": "Meow."}
    assert not session.task_active and session.worker is None and f.saves == 1
    assert ("set_cat_text", "Meow.") in f.chat.calls and ("set_busy", False) in f.chat.calls


def test_a_failed_request_is_reported_and_ends_the_task(made) -> None:  # type: ignore[no-untyped-def]
    session, jobs, f = made
    ask(session, f, "hi")
    jobs[0].events.failed("connection", "refused")
    errors = [c for c in f.chat.calls if c[0] == "add" and c[1] == "error"]
    assert len(errors) == 1 and ("end_cat",) in f.chat.calls
    assert not session.task_active and session.worker is None
    assert [m["role"] for m in f.chat_state.history] == ["user"]


def test_an_answer_from_a_request_that_was_replaced_is_ignored(made) -> None:  # type: ignore[no-untyped-def]
    session, jobs, f = made
    ask(session, f, "hi")
    session.request()                                 # (e.g. the same request again, shortened)
    jobs[0].events.done("old answer", [])
    jobs[0].events.failed("connection", "refused")
    assert session.task_active and session.worker is jobs[1].events._w  # type: ignore[attr-defined]
    assert [m["role"] for m in f.chat_state.history] == ["user"] and f.saves == 0
    jobs[1].events.done("new answer", [])
    assert f.chat_state.history[-1]["content"] == "new answer" and not session.task_active


def test_the_users_turn_skips_the_chats_own_commands_and_a_new_chat_forgets_the_running_one(made) -> None:  # type: ignore[no-untyped-def]
    session, jobs, f = made
    session.on_user_message("/new")
    assert f.commands == ["/new"] and jobs == [] and f.chat_state.history == [] and not session.task_active
    session.on_user_message("plan a trip")
    session.on_user_message("ok, continue with it")
    assert len(jobs) == 2 and session.task_active and f.chat_state.history[-1]["_continue"] is True
    assert "_continue" not in f.chat_state.history[0] and ("drop_choice",) in f.chat.calls

    session._raw, session._tool_round, session._had_results = "half an answer", 3, True
    session.new_chat()
    assert session.worker is None and not session.task_active and f.chat_state.history == []
    assert (session._raw, session._tool_round, session._had_results, session._sent) == ("", 0, False, None)
    assert ("reset",) in f.chat.calls and f.saves >= 1
