"""PixelCatApp's agent loop (in app.py, not llm/agent.py): a user message starts a task, the model
answers with tool calls, each call is checked / asked / run, its result goes back to the model,
until a final answer - with the step limit, the stuck / failing guards, approvals (ask, ALWAYS,
auto mode), cancelling and the history the model sees.

The model is a scripted fake worker (``workers``) and the tools a fake ToolBox, so only the loop
itself is under test."""
from __future__ import annotations

import threading
from typing import Any, Callable

import pytest

from pixelcat.llm.agent import ToolOutput
from pixelcat.llm.tools import ToolError, ToolSpec

pytestmark = pytest.mark.qt


class FakeToolBox:
    """What the loop uses of llm.tools.ToolBox: specs, names, schemas, prepare, describe, run."""

    def __init__(self) -> None:
        self.specs: dict[str, ToolSpec] = {}
        self.extra: dict[str, dict[str, Any]] = {}
        self.ran: list[tuple[str, dict[str, Any]]] = []

    def add(self, name: str, run: Callable[[dict[str, Any]], Any] | None = None, confirm: str = "none",
            group: str = "", background: bool = False, extra: dict[str, Any] | None = None) -> None:
        def default(a: dict[str, Any]) -> str:
            return f"{name} done {a.get('n', '')}".strip()

        self.specs[name] = ToolSpec(name, f"the {name} tool", {"n": {"type": "integer"}}, [], confirm,
                                    run or default, lambda a, n=name: f"use {n}", True, background, group)
        self.extra[name] = extra or {}

    def names(self) -> list[str]:
        return list(self.specs)

    def schemas(self) -> list[dict[str, Any]]:
        return [s.schema() for s in self.specs.values()]

    def prepare(self, name: str, args: dict[str, Any], internal: bool = False) -> tuple[ToolSpec, dict[str, Any]]:
        if name not in self.specs:
            raise ToolError(f"'{name}' is not an allowed tool")
        return self.specs[name], {**args, **self.extra[name]}

    def describe(self, spec: ToolSpec, args: dict[str, Any]) -> str:
        return spec.describe(args)

    def run(self, spec: ToolSpec, args: dict[str, Any]) -> Any:
        self.ran.append((spec.name, {k: v for k, v in args.items() if not k.startswith("_")}))
        return spec.run(args)


@pytest.fixture
def loop(pet, workers):  # type: ignore[no-untyped-def]
    tb = FakeToolBox()
    pet.session.toolbox = tb
    pet.chat_ui.open_chat()
    pet.tb = tb
    return pet


def ask(pet, text: str) -> None:  # type: ignore[no-untyped-def]
    pet.chat_ui.chat.input.setText(text)
    pet.chat_ui.chat._send()


def call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "arguments": args}


def roles(pet) -> list[str]:  # type: ignore[no-untyped-def]
    return [m["role"] for m in pet.chat_state.history]


# ---------------------------------------------------------------- the happy path
def test_tool_call_then_result_then_final_answer(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("ping")
    ask(loop, "ping it")
    assert len(workers) == 1 and [t["function"]["name"] for t in workers[0].tools] == ["ping"]
    workers[0].events.done("Pinging!", [call("ping", n=1)])
    assert loop.tb.ran == [("ping", {"n": 1})]
    assert len(workers) == 2 and loop.session.task_active and loop.session._tool_round == 1
    second = workers[1].messages
    assert second[0]["role"] == "system" and [m["role"] for m in second[1:]] == ["user", "assistant", "tool"]
    assert second[-1] == {"role": "tool", "content": "ping done 1", "tool_name": "ping"}
    assert any(m.role == "info" and m.text == "ping done 1" for m in loop.chat_ui.chat.msgs)
    workers[1].events.done("All pinged.", [])
    assert not loop.session.task_active and loop.session.worker is None
    assert roles(loop) == ["user", "assistant", "tool", "assistant"]
    assert loop.chat_state.history[1]["tool_calls"] == [{"function": {"name": "ping", "arguments": {"n": 1}}}]
    assert loop.chat_ui.chat.msgs[-1].text == "All pinged." and loop.chat_ui.chat.send_btn.text() == "SEND"


def test_several_calls_in_one_reply_run_in_order_then_one_request(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("a")
    loop.tb.add("b")
    ask(loop, "both")
    workers[0].events.done("", [call("a", n=1), call("b", n=2), call("a", n=3)])
    assert [r[0] for r in loop.tb.ran] == ["a", "b", "a"]
    assert len(workers) == 2 and loop.session._tool_round == 1
    assert roles(loop) == ["user", "assistant", "tool", "tool", "tool"]


def test_a_reply_without_calls_ends_the_task(loop, workers) -> None:  # type: ignore[no-untyped-def]
    ask(loop, "hi")
    workers[0].events.done("Meow.", [])
    assert not loop.session.task_active and len(workers) == 1


def test_tool_output_with_a_picture_keeps_only_the_newest(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("look", run=lambda a: ToolOutput(f"saw {a['n']}", images=[f"img{a['n']}"], display="👀 Looked"))
    ask(loop, "look twice")
    workers[0].events.done("", [call("look", n=1)])
    workers[1].events.done("", [call("look", n=2)])
    tools = [m for m in loop.chat_state.history if m["role"] == "tool"]
    assert "images" not in tools[0] and tools[0]["content"] == "saw 1 (older screenshot removed)"
    assert tools[1]["images"] == ["img2"]
    assert any(m.text == "👀 Looked" for m in loop.chat_ui.chat.msgs)


def test_text_tool_protocol_puts_results_in_user_messages(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("ping")
    loop.session.native_tools = False
    ask(loop, "ping")
    assert workers[0].tools is None
    workers[0].events.done('<tool_call>{"name": "ping", "arguments": {"n": 4}}</tool_call>', [])
    assert loop.chat_state.history[-1] == {"role": "user", "content": "[tool result for ping] ping done 4", "_tool": True}
    assert "tool_calls" not in loop.chat_state.history[-2]


# ---------------------------------------------------------------- limits and guards
def test_the_step_limit_pauses_the_task_and_continue_goes_on(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.cfg.agent_max_steps = 3
    loop.tb.add("step")
    ask(loop, "do many steps")
    for i in range(3):
        workers[-1].events.done("", [call("step", n=i)])
    assert len(loop.tb.ran) == 3 and loop.session._tool_round == 3 and loop.session.task_active
    workers[-1].events.done("", [call("step", n=99)])
    assert len(loop.tb.ran) == 3 and not loop.session.task_active
    assert "That was 3 steps in a row" in loop.chat_ui.chat.msgs[-1].text and len(workers) == 4
    ask(loop, "continue")
    assert loop.chat_state.history[-1] == {"role": "user", "content": "continue", "_continue": True}
    assert loop.session._tool_round == 0 and loop.session.task_active and len(workers) == 5
    workers[-1].events.done("", [call("step", n=100)])
    assert len(loop.tb.ran) == 4


def test_waiting_rounds_do_not_count_as_steps(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.cfg.agent_max_steps = 1
    loop.tb.add("wait")
    loop.tb.add("step")
    ask(loop, "wait a bit")
    for i in range(3):
        workers[-1].events.done("", [call("wait", n=i)])
    assert loop.session._tool_round == 0 and len(loop.tb.ran) == 3
    workers[-1].events.done("", [call("step", n=1), call("wait", n=9)])
    assert loop.session._tool_round == 1


@pytest.mark.parametrize(("name", "repeats"), [("ping", 3), ("scroll", 6)])
def test_repeating_the_same_call_stops(loop, workers, name: str, repeats: int) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add(name)
    ask(loop, "go")
    for _ in range(repeats - 1):
        workers[-1].events.done("", [call(name, n=1)])
    assert loop.session.task_active and len(loop.tb.ran) == repeats - 1
    workers[-1].events.done("", [call(name, n=1)])
    assert not loop.session.task_active and len(loop.tb.ran) == repeats - 1
    assert "I keep doing the same thing" in loop.chat_ui.chat.msgs[-1].text


def test_a_tool_error_goes_back_to_the_model(loop, workers) -> None:  # type: ignore[no-untyped-def]
    def broken(a: dict[str, Any]) -> str:
        raise ToolError("the window is gone")

    loop.tb.add("poke", run=broken)
    ask(loop, "poke")
    workers[0].events.done("", [call("poke", n=1)])
    assert loop.chat_state.history[-1]["content"] == "error: the window is gone" and loop.session._fail_streak == 1
    assert loop.chat_ui.chat.msgs[-2].role == "error" and "poke didn't work: the window is gone" in loop.chat_ui.chat.msgs[-2].text
    assert len(workers) == 2 and loop.session.task_active and workers[1].think is True      # (it thinks after a failure)


def test_an_unexpected_exception_in_a_tool_is_reported_too(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("bad", run=lambda a: 1 / 0)
    ask(loop, "go")
    workers[0].events.done("", [call("bad")])
    assert loop.chat_state.history[-1]["content"] == "error: division by zero" and len(workers) == 2


def test_failing_again_and_again_stops(loop, workers) -> None:  # type: ignore[no-untyped-def]
    def broken(a: dict[str, Any]) -> str:
        raise ToolError("nope")

    loop.tb.add("poke", run=broken)
    ask(loop, "poke")
    for i in range(4):
        workers[-1].events.done("", [call("poke", n=i)])
    assert loop.session._fail_streak == 4 and loop.session.task_active
    workers[-1].events.done("", [call("poke", n=9)])
    assert not loop.session.task_active and "Several steps in a row didn't work" in loop.chat_ui.chat.msgs[-1].text


def test_a_success_resets_the_failures(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("poke", run=lambda a: (_ for _ in ()).throw(ToolError("x")))
    loop.tb.add("ping")
    ask(loop, "go")
    workers[-1].events.done("", [call("poke", n=1)])
    workers[-1].events.done("", [call("ping", n=1)])
    assert loop.session._fail_streak == 0


def test_an_unknown_tool_is_an_error_result_and_the_next_call_still_runs(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("ping")
    ask(loop, "go")
    workers[0].events.done("", [call("nope"), call("ping", n=2)])
    tool_msgs = [m for m in loop.chat_state.history if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == "error: 'nope' is not an allowed tool"
    assert tool_msgs[1]["content"] == "ping done 2" and len(workers) == 2


# ---------------------------------------------------------------- approvals
def test_an_action_is_asked_first_and_no_means_no(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("close", confirm="hard")
    ask(loop, "close it")
    workers[0].events.done("", [call("close", n=1)])
    assert loop.tb.ran == [] and "use close?" in loop.chat_ui.chat.confirm_label.text()
    assert not loop.chat_ui.chat.always_btn.isVisibleTo(loop.chat_ui.chat)               # (no ALWAYS for hard actions)
    loop.chat_ui.chat.no_btn.click()
    assert loop.tb.ran == [] and loop.chat_state.history[-1]["content"] == "The user declined this action."
    assert loop.chat_ui.chat.msgs[-2].text == "Okay, I won't use close." and len(workers) == 2


def test_always_approves_the_whole_group(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("vol_up", confirm="soft", group="volume")
    loop.tb.add("vol_down", confirm="soft", group="volume")
    ask(loop, "louder")
    workers[0].events.done("", [call("vol_up", n=1)])
    loop.chat_ui.chat.always_btn.click()
    assert loop.saved.auto_approved_tools == ["volume"] and loop.tb.ran == [("vol_up", {"n": 1})]
    workers[1].events.done("", [call("vol_down", n=2)])
    assert loop.chat_ui.chat._confirm_cb is None and loop.tb.ran[-1] == ("vol_down", {"n": 2})
    assert loop.chat_ui.chat.msgs[-2].text == "vol_down done 2 (auto-approved)"


def test_a_hidden_chat_comes_back_to_ask(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("close", confirm="hard")
    ask(loop, "close it")
    loop.chat_ui.chat.hide()
    workers[0].events.done("", [call("close")])
    assert loop.chat_ui.chat.isVisible() and loop.chat_ui.chat._confirm_cb is not None


def test_auto_mode_acts_up_to_its_limit_then_asks(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.cfg.auto_mode_max_actions = 2
    loop.tb.add("act", confirm="soft")
    loop.tb.add("click", confirm="soft", group="desktop")
    loop.session.set_auto_mode(True)
    ask(loop, "go")
    workers[-1].events.done("", [call("click", n=0), call("act", n=1), call("act", n=2), call("act", n=3)])
    assert [r[1]["n"] for r in loop.tb.ran] == [0, 1, 2] and loop.session._auto_actions == 2      # (clicks don't count)
    assert loop.chat_ui.chat._confirm_cb is not None
    loop.chat_ui.chat.yes_btn.click()
    assert [r[1]["n"] for r in loop.tb.ran] == [0, 1, 2, 3]


def test_auto_mode_also_does_hard_actions_but_never_forced_ones(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("close", confirm="hard")
    loop.tb.add("run_box", confirm="soft", extra={"_force_confirm": True})
    loop.session.set_auto_mode(True)
    ask(loop, "go")
    workers[-1].events.done("", [call("close", n=1), call("run_box", n=2)])
    assert loop.tb.ran == [("close", {"n": 1})]
    assert "(this can run commands, so I always ask)" in loop.chat_ui.chat.confirm_label.text()
    assert not loop.chat_ui.chat.always_btn.isVisibleTo(loop.chat_ui.chat)


# ---------------------------------------------------------------- cancelling
def test_stop_while_the_model_is_answering(loop, workers) -> None:  # type: ignore[no-untyped-def]
    ask(loop, "long answer")
    workers[0].events.token("Once upon")
    loop.chat_ui.chat.send_btn.click()                            # (it reads STOP)
    assert workers[0].cancelled and not loop.session.task_active and loop.session.agent.cancel.is_set()
    assert loop.chat_ui.chat.msgs[-1].text == "(stopped)"
    workers[0].events.done("Once upon a time", [call("anything")])
    assert loop.chat_state.history[-1]["role"] == "user" and len(workers) == 1


def test_stop_while_a_card_asks(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("close", confirm="hard")
    ask(loop, "close it")
    workers[0].events.done("", [call("close")])
    cb = loop.chat_ui.chat._confirm_cb
    loop.session.on_escape()
    assert not loop.session.task_active and loop.chat_ui.chat._confirm_cb is None and not loop.chat_ui.chat.confirm.isVisibleTo(loop.chat_ui.chat)
    cb("yes")                                             # (a late answer does nothing)
    assert loop.tb.ran == [] and len(workers) == 1


def test_escape_without_a_task_does_nothing(loop, workers) -> None:  # type: ignore[no-untyped-def]
    n = len(loop.chat_ui.chat.msgs)
    loop.session.on_escape()
    assert len(loop.chat_ui.chat.msgs) == n


def test_stop_while_a_background_tool_runs(loop, workers, qtbot) -> None:  # type: ignore[no-untyped-def]
    release = threading.Event()

    def slow(a: dict[str, Any]) -> str:
        release.wait(2)
        return "finally done"

    loop.tb.add("slow", run=slow, background=True, group="desktop")
    ask(loop, "do the slow thing")
    workers[0].events.done("", [call("slow")])
    assert loop.session._tool_thread is not None and loop.session.esc_watch.active
    loop.session.cancel_chat()
    assert not loop.session.task_active and not loop.session.esc_watch.active and loop.chat_ui.chat.msgs[-1].text == "(stopped)"
    release.set()
    qtbot.waitUntil(lambda: loop.session._tool_thread is None and not loop.session.live_tools, timeout=3000)
    assert loop.chat_state.history[-1] == {"role": "tool", "content": "finally done", "tool_name": "slow"}
    assert len(workers) == 1                              # (the result is kept, nothing is asked any more)
    assert not any(m.text == "finally done" for m in loop.chat_ui.chat.msgs)


def test_a_background_tool_result_continues_the_task(loop, workers, qtbot) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("slow", run=lambda a: "slow done", background=True)
    ask(loop, "go")
    workers[0].events.done("", [call("slow")])
    qtbot.waitUntil(lambda: len(workers) == 2, timeout=3000)
    assert loop.chat_state.history[-1]["content"] == "slow done" and loop.session._tool_thread is None


def test_a_new_chat_stops_the_task_and_forgets_it(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("close", confirm="hard")
    ask(loop, "close it")
    workers[0].events.done("", [call("close")])
    loop.session.new_chat()
    assert not loop.session.task_active and loop.chat_state.history == [] and loop.session._pending_calls == []
    assert loop.chat_ui.chat._confirm_cb is None and loop.session.agent.cancel.is_set()


class SimpleQApp:
    def __init__(self) -> None:
        self.quits = 0

    def quit(self) -> None:
        self.quits += 1


@pytest.fixture
def slow_mac_shot(loop, tmp_path):  # type: ignore[no-untyped-def]
    """take_screenshot as on macOS (the GUI runs while it waits for the capture), with a capture that answers only
    when ``release`` is set (or after 5 s): ``(release, during)``; ``during`` runs on the GUI 50 ms into the wait."""
    from types import SimpleNamespace

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QImage

    release, during = threading.Event(), []

    def grab(rect):  # type: ignore[no-untyped-def]
        release.wait(5)
        img = QImage(4, 4, QImage.Format.Format_RGB32)
        img.fill(0)
        return img

    def shot(a: dict[str, Any]) -> Any:
        QTimer.singleShot(50, lambda: during[0]())
        return loop.session.tool_screenshot()
    s = loop.session
    s.display = SimpleNamespace(qt_logical=lambda: True, virtual_screen=lambda: (0, 0, 4, 4),
                                session_locked=lambda: False)
    s.capture = SimpleNamespace(grab=grab)
    s.folders = SimpleNamespace(known_folder=lambda name: tmp_path)
    loop.tb.add("take_screenshot", run=shot)
    yield release, during
    release.set()


def test_quitting_while_a_mac_screenshot_waits_shows_and_starts_nothing(loop, workers, slow_mac_shot) -> None:  # type: ignore[no-untyped-def]
    import time

    release, during = slow_mac_shot
    loop.qapp = SimpleQApp()
    during.append(loop.quit)
    ask(loop, "take a screenshot")
    t0 = time.monotonic()
    workers[0].events.done("", [call("take_screenshot")])
    assert time.monotonic() - t0 < 2.0 and not release.is_set() and loop.qapp.quits == 1
    assert not loop.overlay.isVisible() and not loop.chat_ui.chat.isVisible()
    assert len(workers) == 1 and "Screenshot saved" not in str(loop.chat_state.history)
    assert not any("Screenshot saved" in m.text for m in loop.chat_ui.chat.msgs)


def test_a_new_chat_while_a_mac_screenshot_waits_gets_nothing_of_it(loop, workers, slow_mac_shot) -> None:  # type: ignore[no-untyped-def]
    release, during = slow_mac_shot
    during.append(lambda: (loop.session.new_chat(), release.set()))
    ask(loop, "take a screenshot")
    workers[0].events.done("", [call("take_screenshot")])
    assert loop.chat_state.history == [] and len(workers) == 1 and not loop.session.task_active
    assert not any("Screenshot saved" in m.text for m in loop.chat_ui.chat.msgs)


def test_a_new_message_after_a_stop_is_a_new_task(loop, workers) -> None:  # type: ignore[no-untyped-def]
    loop.tb.add("ping")
    ask(loop, "one")
    first = loop.session._task_id
    loop.session.cancel_chat()
    ask(loop, "two")
    assert loop.session._task_id > first + 1 and loop.session.task_active and not loop.session.agent.cancel.is_set()
    assert [m["content"] for m in workers[-1].messages if m["role"] == "user"] == ["one", "two"]
