"""PixelCatApp: the chat - a request to a (fake) model worker, streaming, tool calls with and without
asking, failures, the chat commands, the context sent to the model, and the chat history."""
from __future__ import annotations

import pytest

from fakes.chat import FakeJob
from pixelcat.app import context
from pixelcat.ui.chat import GREETING

pytestmark = pytest.mark.qt


@pytest.fixture
def chat_pet(pet, workers):  # type: ignore[no-untyped-def]
    pet.chat_ui.open_chat()
    return pet


def _say(pet, text: str) -> None:  # type: ignore[no-untyped-def]
    pet.chat_ui.chat.input.setText(text)
    pet.chat_ui.chat._send()


def test_open_chat_shows_it_and_wakes_the_ai(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet.chat_ui.open_chat()
    assert pet.chat_ui.chat.isVisible() and app_env.ensures == [False]
    assert [m.text for m in pet.chat_ui.chat.msgs] == [GREETING]


def test_a_too_big_default_model_is_mentioned_once(pet) -> None:  # type: ignore[no-untyped-def]
    pet.cfg.model = "qwen3.5:122b-a10b"
    pet.chat_ui.open_chat()
    pet.chat_ui.open_chat()
    notes = [m.text for m in pet.chat_ui.chat.msgs if m.text.startswith("Heads up")]
    assert len(notes) == 1 and "Huge 122B is too big for this PC" in notes[0]


def test_a_message_goes_to_the_model_and_the_answer_streams_back(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    pet = chat_pet
    _say(pet, "hello there")
    w = workers[-1]
    assert w.started and w.model == "qwen3.5:35b-a3b" and w.think is True
    assert w.messages[0]["role"] == "system" and w.messages[-1] == {"role": "user", "content": "hello there"}
    assert w.tools and any(t["function"]["name"] == "get_time" for t in w.tools)
    assert pet.session.task_active and pet.chat_ui.chat.send_btn.text() == "STOP"
    w.events.thinking(50)
    assert pet.chat_ui.chat.msgs[-1].text == "💭 thinking… (10 words)"
    w.events.token("Mrr")
    w.events.token("ow!")
    assert pet.chat_ui.chat.msgs[-1].text == "Mrrow!"
    w.events.done("Mrrow! Hi.", [])
    assert pet.chat_ui.chat.msgs[-1].text == "Mrrow! Hi." and not pet.session.task_active and pet.session.worker is None
    assert pet.chat_state.history[-1] == {"role": "assistant", "content": "Mrrow! Hi."}
    assert pet.chat_ui.chat.send_btn.text() == "SEND"


def test_signals_from_an_old_worker_are_ignored(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "one")
    old = workers[-1]
    chat_pet.session.cancel_chat()
    assert old.cancelled and chat_pet.chat_ui.chat.msgs[-1].text == "(stopped)"
    old.events.token("late")
    old.events.done("late", [])
    assert all("late" not in m.text for m in chat_pet.chat_ui.chat.msgs)


def test_a_tool_call_runs_and_the_model_gets_the_result(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    pet = chat_pet
    _say(pet, "what time is it?")
    workers[-1].events.done("", [{"name": "get_time", "arguments": {}, "id": "c1"}])
    assert len(workers) == 2                                     # the result went back to the model
    assert pet.chat_state.history[-2]["tool_calls"] == [{"function": {"name": "get_time", "arguments": {}}, "id": "c1"}]
    assert pet.chat_state.history[-1]["role"] == "tool" and pet.chat_state.history[-1]["content"].startswith("It is ")
    assert any(m.role == "info" and m.text.startswith("It is ") for m in pet.chat_ui.chat.msgs)
    assert workers[-1].think is False                            # (routine steps don't think)
    workers[-1].events.done("It's teatime.", [])
    assert not pet.session.task_active and pet.session._tool_round == 1


def test_tool_calls_written_as_text_are_understood(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "time?")
    workers[-1].events.done('<tool_call>{"name": "get_time", "arguments": {}}</tool_call>', [])
    assert len(workers) == 2 and chat_pet.chat_state.history[-1]["content"].startswith("It is ")


@pytest.mark.parametrize(("answer", "copied", "approved"), [("yes", True, []), ("always", True, ["copy_to_clipboard"]),
                                                            ("no", False, [])])
def test_an_action_asks_first(chat_pet, workers, app_env, answer: str, copied: bool, approved: list) -> None:  # type: ignore[no-untyped-def,type-arg]
    pet = chat_pet
    _say(pet, "copy meow")
    workers[-1].events.done("", [{"name": "copy_to_clipboard", "arguments": {"text": "meow"}}])
    assert pet.chat_ui.chat._confirm_cb is not None and pet.chat_ui.chat.always_btn.isVisibleTo(pet.chat_ui.chat)
    assert "May I?" in pet.chat_ui.chat.confirm_label.text()
    {"yes": pet.chat_ui.chat.yes_btn, "always": pet.chat_ui.chat.always_btn, "no": pet.chat_ui.chat.no_btn}[answer].click()
    assert (app_env.clipboard.text() == "meow") is copied
    assert pet.saved.auto_approved_tools == approved
    assert pet.chat_state.history[-1]["role"] == "tool"
    if answer == "no":
        assert pet.chat_state.history[-1]["content"] == "The user declined this action."


def test_auto_mode_does_it_without_asking(chat_pet, workers, app_env) -> None:  # type: ignore[no-untyped-def]
    pet = chat_pet
    pet.set_auto_mode(True)
    _say(pet, "copy purr")
    workers[-1].events.done("", [{"name": "copy_to_clipboard", "arguments": {"text": "purr"}}])
    assert pet.chat_ui.chat._confirm_cb is None and app_env.clipboard.text() == "purr"
    assert pet.session._auto_actions == 1 and pet.chat_ui.chat.msgs[-2].text.endswith("(auto)")


def test_an_unknown_tool_is_reported_to_the_model(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "hack")
    workers[-1].events.done("", [{"name": "format_disk", "arguments": {}}])
    assert chat_pet.chat_state.history[-1]["content"].startswith("error: ") and len(workers) == 2
    assert any("I can't do that" in m.text for m in chat_pet.chat_ui.chat.msgs)


def test_repeating_the_same_call_stops_the_task(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "loop")
    for _ in range(3):
        workers[-1].events.done("", [{"name": "get_time", "arguments": {}}])
    assert not chat_pet.session.task_active
    assert "I keep doing the same thing (get time)" in chat_pet.chat_ui.chat.msgs[-1].text


def test_a_failed_request_shows_an_error(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "hi")
    workers[-1].events.failed("offline", "connection refused")
    assert chat_pet.chat_ui.chat.msgs[-1].role == "error" and not chat_pet.session.task_active


def test_a_model_without_tools_is_asked_again_with_text_tools(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "hi")
    workers[-1].events.failed("no_tools", "")
    assert not chat_pet.session.native_tools and len(workers) == 2 and workers[-1].tools is None


def test_a_promise_to_wait_later_is_questioned(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "tell me when the build is done")
    workers[-1].events.done("Sure, I'll let you know when it's done!", [])
    assert len(workers) == 2 and chat_pet.chat_state.history[-1]["_tool"] and "wait_for" in chat_pet.chat_state.history[-1]["content"]
    workers[-1].events.done("Sorry, I can't wait for that.", [])
    assert not chat_pet.session.task_active


@pytest.mark.parametrize(("text", "promise"), [
    ("I'll let you know when it arrives.", True), ("I will keep an eye on it.", True),
    ("Ich sage dir Bescheid.", True), ("I'll wait for your answer.", False), ("Done!", False)])
def test_broken_promise_detection(pet, text: str, promise: bool) -> None:  # type: ignore[no-untyped-def]
    assert pet.session._broken_promise(text) is promise


def test_online_only_pc_without_an_online_model(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    chat_pet.cfg.ai_mode = "online"
    _say(chat_pet, "hi")
    assert workers == [] and not chat_pet.session.task_active and chat_pet.chat_ui.chat.msgs[-1].role == "info"


def test_an_external_model_that_isnt_connected(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    chat_pet.cfg.model = "anthropic:m1"
    _say(chat_pet, "hi")
    assert workers == [] and "Claude models aren't connected yet" in chat_pet.chat_ui.chat.msgs[-1].text


def test_external_workers_are_built_per_provider(chat_pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import anthropic_client, openai_client

    made: list[tuple] = []

    class Fake(FakeJob):
        def __init__(self, *a):  # type: ignore[no-untyped-def]
            super().__init__(*a)
            made.append((type(self).__name__, a))

    monkeypatch.setattr(anthropic_client, "AnthropicChat", type("AnthropicChat", (Fake,), {}))
    monkeypatch.setattr(openai_client, "OpenAIChat", type("OpenAIChat", (Fake,), {}))
    chat_pet.creds.set("anthropic_key", "k")
    chat_pet.creds.set("openai_key", "k")
    chat_pet.session.begin_task()
    w1 = chat_pet.session._ext_worker("anthropic", "m1", [{"role": "system", "content": "s"}])
    w2 = chat_pet.session._ext_worker("openai", "m2", [])
    assert [m[0] for m in made] == ["AnthropicChat", "OpenAIChat"]
    req = made[0][1][0]
    assert req.model == "m1" and req.auth == "key" and w1.job.web is True and w2.job.web is True
    assert (w1.provider, w1.model, w1.local, w2.provider, w2.model, w2.local) == \
        ("anthropic", "m1", False, "openai", "m2", False)


def test_a_copilot_request_reports_credits_and_pages(chat_pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import copilot_client

    made: list[FakeJob] = []

    def make(req, events):  # type: ignore[no-untyped-def]
        made.append(FakeJob(req, events))
        return made[-1]

    monkeypatch.setattr(copilot_client, "CopilotJob", make)
    chat_pet.creds.set("copilot_key", "k")
    chat_pet.session.begin_task()
    w = chat_pet.session._ext_worker("copilot", "m3", [{"role": "system", "content": "be a cat"}])
    req = made[0].req
    assert req.conversation is chat_pet.session._copilot and req.history is chat_pet.chat_state.history and req.system == "be a cat"
    assert req.auth == "key" and req.web is True and (w.provider, w.model, w.local) == ("copilot", "m3", False)
    made[0].events.credits(0.5)
    assert chat_pet._credit_budget().used() == 0.5
    chat_pet.saved.auto_approved_tools.append("read_web_page")
    answers: list[bool] = []
    made[0].events.permission("https://cats.example/", answers.append)
    assert answers == [True] and "Copilot reads https://cats.example/" in chat_pet.chat_ui.chat.msgs[-1].text


# ---------------------------------------------------------------- chat commands
@pytest.mark.parametrize(("cmd", "reply"), [
    ("/help", "Commands: /new"), ("/permissions", "Actions I may do without asking: none"),
    ("/watches", "I'm not watching anything right now."), ("/unwatch 3", "Nothing to stop"),
    ("/model", "This chat uses Smart (35B)"), ("/model nonsense", "I don't know that model."),
    ("/effort", "This chat's effort: the default (Medium)"), ("/effort xhigh", "has these effort levels: low, medium, high"),
    ("/force", "Usage: /force end <app>"), ("/getmodel", "I'll download the AI model qwen3.5:35b-a3b now")])
def test_chat_commands(chat_pet, workers, cmd: str, reply: str) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, cmd)
    assert workers == [] and reply in chat_pet.chat_ui.chat.msgs[-1].text


def test_effort_command(chat_pet) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "/effort high")
    assert chat_pet.chat_state.chat_effort == "high"
    _say(chat_pet, "/effort default")
    assert chat_pet.chat_state.chat_effort is None


def test_auto_and_permission_commands(chat_pet) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "/auto")
    assert chat_pet.saved.auto_mode
    _say(chat_pet, "/auto off")
    assert not chat_pet.saved.auto_mode
    chat_pet.saved.auto_approved_tools.append("clipboard")
    _say(chat_pet, "/permissions reset")
    assert chat_pet.saved.auto_approved_tools == []


def test_new_chat_and_history_commands(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    _say(chat_pet, "remember this")
    workers[-1].events.done("ok", [])
    _say(chat_pet, "/new")
    assert chat_pet.chat_state.history == [] and [m.text for m in chat_pet.chat_ui.chat.msgs] == [GREETING]
    _say(chat_pet, "/history")
    assert chat_pet.chat_ui.chat.history_shown and "remember this" in chat_pet.chat_ui.chat.hist_view.toPlainText()


def test_force_end_asks_and_never_involves_the_model(chat_pet, workers, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ran: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(chat_pet.session.toolbox, "prepare", lambda name, args, internal=False: (
        chat_pet.session.toolbox.specs[name], dict(args)))
    monkeypatch.setattr(chat_pet.session.toolbox, "describe", lambda spec, args: f"end {args['target']}")
    monkeypatch.setattr(chat_pet.session.toolbox, "run", lambda spec, args: (ran.append(args), "Ended Teams.")[1])
    _say(chat_pet, "/force end the app teams")
    assert workers == [] and "end teams?" in chat_pet.chat_ui.chat.confirm_label.text()
    chat_pet.chat_ui.chat.yes_btn.click()
    assert ran == [{"target": "teams"}] and chat_pet.chat_ui.chat.msgs[-1].text == "Ended Teams."


# ---------------------------------------------------------------- the context for the model
def _hist() -> list[dict]:  # type: ignore[type-arg]
    return [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_time", "arguments": {}}}]},
        {"role": "tool", "content": "It is 12:00.\nmore", "tool_name": "get_time"},
        {"role": "assistant", "content": "Noon! (Steps I took: get_time)"},
        {"role": "user", "content": "second question"},
        {"role": "user", "content": "[tool result for x] y", "_tool": True},
        {"role": "assistant", "content": "answer two"},
        {"role": "user", "content": "continue", "_continue": True},
        {"role": "assistant", "content": "more of two"},
    ]


def test_turns_and_rounds(pet) -> None:  # type: ignore[no-untyped-def]
    pet.chat_state.history[:] = _hist()
    turns = context.turns(pet.chat_state.history)
    assert [len(t) for t in turns] == [4, 5]                     # ("continue" stays in its task)
    rounds = context.rounds(turns[1][1:])
    assert [[m["role"] for m in r] for r in rounds] == [["user"], ["assistant"], ["user"], ["assistant"]]
    assert pet.session.task_request() == "second question"


def test_summarize_keeps_the_request_the_steps_and_the_answer(pet) -> None:  # type: ignore[no-untyped-def]
    turn = _hist()[:4]
    short = context.summarize(turn)
    assert short[0] == {"role": "user", "content": "first question"}
    assert short[1]["tool_calls"][0]["function"]["name"] == "get_time"
    assert short[2] == {"role": "tool", "tool_name": "get_time", "content": "It is 12:00."}
    assert short[-1] == {"role": "assistant", "content": "Noon!"}         # (old "steps" notes are scrubbed)
    assert [m["role"] for m in context.summarize(turn, native=False)] == ["user", "assistant"]


def test_the_context_keeps_the_request_and_the_newest_steps(pet) -> None:  # type: ignore[no-untyped-def]
    pet.cfg.num_ctx = 12_000
    pet.chat_state.history[:] = [{"role": "user", "content": "do the long task"}]
    for i in range(60):
        pet.chat_state.history.append({"role": "assistant", "content": f"step {i}",
                            "tool_calls": [{"function": {"name": "get_time", "arguments": {}}}]})
        pet.chat_state.history.append({"role": "tool", "content": f"result {i} " + "z" * 600, "tool_name": "get_time"})
    ctx = pet.session._build_context()
    assert ctx[0]["content"].startswith("do the long task")
    assert ctx[-1]["content"].startswith("result 59 ")
    assert context.tokens(ctx) < 12_000
    assert len(ctx) < len(pet.chat_state.history)


def test_the_context_is_extended_during_a_task(pet) -> None:  # type: ignore[no-untyped-def]
    pet.chat_state.history[:] = [{"role": "user", "content": "go"}]
    first = pet.session._context()
    pet.chat_state.history.append({"role": "assistant", "content": "ok"})
    second = pet.session._context()
    assert second[:len(first)] == first and second[-1] == {"role": "assistant", "content": "ok"}


@pytest.mark.parametrize(("effort", "think", "last_user", "want"), [
    (None, "auto", True, True), (None, "auto", False, False), ("low", "auto", True, False),
    ("high", "auto", False, True), ("medium", True, False, False), (None, True, False, True), (None, "off", True, False)])
def test_when_the_model_thinks(pet, effort, think, last_user: bool, want: bool) -> None:  # type: ignore[no-untyped-def]
    pet.chat_state.chat_effort, pet.cfg.think = effort, think
    pet.chat_state.history[:] = [{"role": "user", "content": "x"}] if last_user else [{"role": "tool", "content": "y"}]
    assert pet.session._think_now() is want


# ---------------------------------------------------------------- the chat history
def test_a_chat_is_saved_and_can_be_reopened(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    pet = chat_pet
    _say(pet, "plan a trip to Paris")
    workers[-1].events.done("Pack a beret.", [])
    cid = pet.chat_ui.chat_id
    assert cid and [i.title for i in pet.chat_ui.chats.index()] == ["plan a trip to Paris"]
    pet.new_chat()
    assert pet.chat_state.history == [] and pet.chat_ui.chat_id is None
    pet.chat_ui.toggle_history()
    assert pet.chat_ui.chat.history_shown and "plan a trip to Paris" in pet.chat_ui.chat.hist_view.toPlainText()
    pet.open_saved_chat(cid)
    assert pet.chat_ui.chat_id == cid and pet.chat_state.history[-1]["content"] == "Pack a beret."
    assert [m.text for m in pet.chat_ui.chat.msgs][:2] == ["plan a trip to Paris", "Pack a beret."]
    assert pet.chat_ui.chat.msgs[-1].text.startswith("🕘 Our chat from ")


def test_a_chat_without_a_question_is_not_saved(chat_pet) -> None:  # type: ignore[no-untyped-def]
    chat_pet.save()
    assert chat_pet.chat_ui.chats.index() == []


def test_deleting_saved_chats(chat_pet, workers) -> None:  # type: ignore[no-untyped-def]
    pet = chat_pet
    for q in ("one", "two"):
        _say(pet, q)
        workers[-1].events.done("ok", [])
        pet.new_chat()
    ids = [i.id for i in pet.chat_ui.chats.index()]
    pet.chat_ui._delete_saved_chat(ids[0])
    assert [i.id for i in pet.chat_ui.chats.index()] == ids[1:]
    pet.chat_ui._delete_all_chats()
    assert pet.chat_ui.chats.index() == []
    pet.open_saved_chat("gone")
    assert "can't be opened any more" in pet.chat_ui.chat.msgs[-1].text


def test_the_chat_layout_is_remembered(chat_pet, app_env) -> None:  # type: ignore[no-untyped-def]
    chat_pet.chat_ui.chat.set_fullscreen(True)
    assert chat_pet.saved.chat_full is True and chat_pet.saved.chat_size == [175.0, 225.0]


# ---------------------------------------------------------------- watches, timers and other tool hooks
def test_a_timer_rings(pet) -> None:  # type: ignore[no-untyped-def]
    pet.chat_ui.ensure_chat()
    text = pet.session.tool_set_timer(90, "tea")
    assert text == "Timer 'tea' set for 1.5 min." and len(pet.session._user_timers) == 1
    assert pet.session.tool_set_timer(3 * 3600, "nap") == "Timer 'nap' set for 3.0 h."
    tm = pet.session._user_timers[0]
    pet.session._timer_fired("tea", tm)
    assert tm not in pet.session._user_timers and pet.chat_ui.chat.msgs[-1].text == "⏰ Time's up: tea"
    for t in pet.session._user_timers:
        t.stop()


def test_cat_actions_wait_for_minigames(pet) -> None:  # type: ignore[no-untyped-def]
    assert "minigame" not in pet.session.tool_cat_action("happy")
    pet.saved.tutorials_seen.append("ball")
    pet.start()
    pet.minigames.start_minigame("ball")
    assert "in the middle of a minigame" in pet.session.tool_cat_action("happy")


def test_clipboard_hook(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    assert pet.session.tool_clipboard("abc") == "Copied 3 characters to the clipboard."
    assert app_env.clipboard.text() == "abc"


def test_a_fired_watch_wakes_the_chat(chat_pet, workers, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import time

    from pixelcat.llm.watch import Watch

    w = Watch(7, "time", "the kettle", then="say hi", request="tea?", started=time.time())
    chat_pet.session.on_watch_fired(w, "The kettle is done", False)
    assert chat_pet.chat_ui.chat.msgs[-2].role == "event" and "🔔 The kettle is done" in chat_pet.chat_ui.chat.msgs[-2].text
    assert chat_pet.session.task_active and chat_pet.session._task_wake and workers
    assert "You were waiting for: the kettle" in chat_pet.chat_state.history[-1]["content"]
    workers[-1].events.done("Your tea is ready!", [])
    assert not chat_pet.session.task_active
