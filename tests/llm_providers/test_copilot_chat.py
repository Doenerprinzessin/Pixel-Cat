"""copilot_client.CopilotChat / CopilotJob: one chat with the Copilot runtime, played by a
scripted fake SDK session (no runtime, no GitHub).  The job runs in its own thread (like the app's
worker thread); its events come from the SDK's asyncio thread."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import pytest

from fakes.chat import RecordingEvents
from llm_providers.fakes import PNG, Recorder
from pixelcat.llm import copilot_client as CC
from pixelcat.llm import webpage
from pixelcat.llm.external import MemoryCredentials
from pixelcat.ports.llm import ChatRequest

pytestmark = pytest.mark.qt

TOOLS = [{"type": "function", "function": {"name": "get_time", "description": "Get the time.",
                                           "parameters": {"type": "object", "properties": {}}}},
         {"type": "function", "function": {"name": "look_at_screen", "description": "Screenshot."}}]
INFO = {"efforts": ["low", "medium", "high"]}


class D(SimpleNamespace):
    """Event data: fields that aren't given are None (like the SDK's optional fields)."""

    def __getattr__(self, name: str) -> Any:
        return None


Turn = Callable[["FakeSession"], Awaitable[None]]


class FakeSession:
    def __init__(self, client: "FakeClient", kw: dict[str, Any]) -> None:
        self.client, self.kw = client, kw
        self.session_id = f"s{len(client.sessions) + 1}"
        self.cb: Any = None
        self.sent: list[str] = []
        self.results: dict[str, Any] = {}
        self.decisions: list[Any] = []
        self.calls: list[str] = []
        self.models: list[tuple] = []
        self.idle_after_abort = 0.0

    def on(self, cb: Any) -> None:
        self.cb = cb

    def emit(self, kind: str, agent_id: str | None = None, **data: Any) -> None:
        self.cb(SimpleNamespace(type=SimpleNamespace(value=kind), data=D(**data), agent_id=agent_id))

    async def send(self, prompt: str) -> None:
        self.sent.append(prompt)
        turn = self.client.script.pop(0)
        asyncio.get_running_loop().create_task(turn(self))

    async def abort(self) -> None:
        self.calls.append("abort")
        if self.idle_after_abort:
            asyncio.get_running_loop().call_later(self.idle_after_abort, self.emit, "session.idle")
        else:
            self.emit("session.idle")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    async def set_model(self, model: str, reasoning_effort: str | None = None) -> None:
        self.models.append((model, reasoning_effort))

    async def call(self, name: str, cid: str, args: Any = None) -> Any:
        tool = next(t for t in self.kw["tools"]["definitions"] if t.name == name)
        try:
            self.results[cid] = await tool.handler(SimpleNamespace(tool_call_id=cid, arguments=args or {}))
        except asyncio.CancelledError:
            self.results[cid] = "cancelled"
        return self.results[cid]

    async def ask(self, url: str, kind: str = "url") -> Any:
        decision = await self.kw["permissions"]["on_request"](SimpleNamespace(kind=kind, url=url), None)
        self.decisions.append(decision)
        return decision


class FakeClient:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []
        self.deleted: list[str] = []
        self.script: list[Turn] = []

    async def create_session(self, **kw: Any) -> FakeSession:
        s = FakeSession(self, kw)
        self.sessions.append(s)
        return s

    async def delete_session(self, sid: str) -> None:
        self.deleted.append(sid)


class FakeRT:
    def __init__(self) -> None:
        self.client: Any = FakeClient()
        self.token: str | None = None

    async def get(self, token: str) -> Any:
        self.token = token
        return self.client


@pytest.fixture
def rt(monkeypatch):
    fake = FakeRT()
    monkeypatch.setattr(CC, "_RT", fake)
    return fake


@pytest.fixture
def chat():
    c = CC.CopilotChat()
    yield c
    c.close(3)


@pytest.fixture
def run_worker(qtbot):
    started: list[threading.Thread] = []

    def run(chat: CC.CopilotChat, history: list, model: str = "fake-model", think: bool = True,
            effort: str = "medium", info: dict | None = INFO, web: bool = False, creds: Any = None,
            on_perm: Any = None, cancel_when: Callable[[Recorder], bool] | None = None) -> Recorder:
        req = ChatRequest(model, [], TOOLS, think, effort, creds or MemoryCredentials({"copilot_key": "github_pat_1"}),
                          "key", info or {}, web=web, history=history, system="You are Pixel, a test cat.",
                          conversation=chat)
        w = CC.CopilotJob(req, RecordingEvents(on={"permission": on_perm} if on_perm is not None else None))
        rec = Recorder(w)
        th = threading.Thread(target=w.run, daemon=True)   # (like the app's worker thread)
        started.append(th)
        th.start()
        if cancel_when is not None:
            qtbot.waitUntil(lambda: cancel_when(rec), timeout=5000)
            w.cancel()
        else:
            qtbot.waitUntil(lambda: rec.done is not None or rec.failed is not None, timeout=5000)
        th.join(5)
        assert not th.is_alive()
        qtbot.wait(20)
        rec.worker = w
        return rec
    yield run
    for th in started:
        th.join(5)


# ------------------------------------------------------------------ turns
def say(*pieces: str, usage: tuple | None = (120, 8, 5e8)) -> Turn:
    async def turn(s: FakeSession) -> None:
        for p in pieces:
            s.emit("assistant.message_delta", delta_content=p)
        if usage:
            s.emit("assistant.usage", input_tokens=usage[0], output_tokens=usage[1],
                   copilot_usage=D(total_nano_aiu=usage[2]))
        s.emit("assistant.message", content="".join(pieces), tool_requests=[])
        s.emit("session.idle")
    return turn


def tools_then(text: str, calls: list[tuple[str, str]], after: str) -> Turn:
    """The model asks for tools; once the app answered all of them it says ``after``."""
    async def turn(s: FakeSession) -> None:
        reqs = [SimpleNamespace(name=n, tool_call_id=c) for c, n in calls]
        s.emit("assistant.message", content=text, tool_requests=reqs)
        results = await asyncio.gather(*(s.call(n, c) for c, n in calls))
        if any(r == "cancelled" for r in results):
            return
        s.emit("assistant.message_delta", delta_content=after)
        s.emit("assistant.message", content=after, tool_requests=[])
        s.emit("session.idle")
    return turn


def error(**data: Any) -> Turn:
    async def turn(s: FakeSession) -> None:
        s.emit("session.error", **data)
        s.emit("session.idle")
    return turn


def silent() -> Turn:
    async def turn(s: FakeSession) -> None:
        return None
    return turn


# ------------------------------------------------------------------ tests
def test_text_reply(rt, chat, run_worker):
    rt.client.script = [say("Meow! ", "Hello.")]
    rec = run_worker(chat, [{"role": "user", "content": "hi there"}])
    assert rec.events == [("token", "Meow! "), ("token", "Hello."), ("used", "copilot", 128), ("credits", 0.5),
                          ("done", "Meow! Hello.", [])]
    s = rt.client.sessions[0]
    assert s.sent == ["hi there"] and rt.token == "github_pat_1"
    assert (chat.consumed, chat.expect_reply, chat.turn, chat.model, chat.effort) == (1, True, False, "fake-model",
                                                                                    "medium")


def test_session_settings(rt, chat, run_worker):
    rt.client.script = [say("ok")]
    run_worker(chat, [{"role": "user", "content": "hi"}], web=True, effort="max")
    kw = rt.client.sessions[0].kw
    assert kw["model"] == "fake-model" and kw["client_name"] == "Pixel Cat"
    assert kw["system_message"] == {"mode": "replace", "content": "You are Pixel, a test cat."}
    assert [(t.name, t.description, t.parameters, t.skip_permission) for t in kw["tools"]["definitions"]] == [
        ("get_time", "Get the time.", {"type": "object", "properties": {}}, True),
        ("look_at_screen", "Screenshot.", {"type": "object", "properties": {}}, True)]
    assert kw["tools"]["available_tools"] == ["get_time", "look_at_screen", "web_fetch"]
    assert kw["inference"] == {"reasoning_effort": "high"}
    assert kw["github"] == {"telemetry_enabled": False}
    assert kw["agents"] == {"included_builtin_agents": []} and kw["skills"] == {"enabled": False}
    assert kw["instructions"] == {"skip_custom_instructions": True}
    assert kw["state"] == {"enable_cross_session_store": False} and kw["enable_config_discovery"] is False
    assert kw["working_directory"] == str(CC._work_dir()) and kw["events"] == {"streaming": True}


def test_no_web_fetch_and_no_effort(rt, chat, run_worker):
    rt.client.script = [say("ok")]
    run_worker(chat, [{"role": "user", "content": "hi"}], info={"efforts": []})
    kw = rt.client.sessions[0].kw
    assert kw["tools"]["available_tools"] == ["get_time", "look_at_screen"] and "inference" not in kw


def test_tool_calls_go_to_the_app_and_results_come_back(rt, chat, run_worker):
    hist: list[dict] = [{"role": "user", "content": "what time is it, and show me"}]
    rt.client.script = [tools_then("Let me look.", [("call_1", "get_time"), ("call_2", "look_at_screen")],
                                   "It is noon.")]
    rec = run_worker(chat, hist)
    assert rec.done == ("Let me look.", [{"id": "call_1", "name": "get_time", "arguments": {}},
                                         {"id": "call_2", "name": "look_at_screen", "arguments": {}}])
    assert chat.order == ["call_1", "call_2"] and set(chat.pending) == {"call_1", "call_2"}
    hist += [{"role": "assistant", "content": "Let me look.", "tool_calls": [
                 {"id": "call_1", "function": {"name": "get_time"}}, {"id": "call_2", "function": {"name": "look"}}]},
             {"role": "tool", "tool_name": "get_time", "content": "12:00"},
             {"role": "tool", "tool_name": "look_at_screen", "content": "", "images": [PNG]}]
    rec = run_worker(chat, hist)
    s = rt.client.sessions[0]
    assert rec.events == [("token", "It is noon."), ("done", "It is noon.", [])]
    assert len(s.sent) == 1                                       # (the same turn went on)
    r1, r2 = s.results["call_1"], s.results["call_2"]
    assert (r1.text_result_for_llm, r1.binary_results_for_llm) == ("12:00", None)
    assert r2.text_result_for_llm == "(done)"
    pic = r2.binary_results_for_llm[0]
    assert (pic.data, pic.mime_type, pic.type, pic.description) == (PNG, "image/png", "image",
                                                                    "the picture from look_at_screen")
    assert chat.pending == {} and chat.consumed == 4


def test_missing_results_are_answered_not_run(rt, chat, run_worker):
    hist: list[dict] = [{"role": "user", "content": "go"}]
    rt.client.script = [tools_then("", [("c1", "get_time"), ("c2", "get_time")], "ok")]
    run_worker(chat, hist)
    hist += [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}, {"id": "c2"}]},
             {"role": "user", "_tool": True, "content": "[tool result for get_time] 12:00"}]
    rec = run_worker(chat, hist)
    s = rt.client.sessions[0]
    assert s.results["c1"].text_result_for_llm == "12:00"
    assert s.results["c2"].text_result_for_llm == "(not run - the user stopped it)"
    assert rec.done == ("ok", [])


def test_a_new_message_while_tools_wait_drops_the_old_turn(rt, chat, run_worker):
    hist: list[dict] = [{"role": "user", "content": "what time is it?"}]
    rt.client.script = [tools_then("", [("call_3", "get_time")], "never said"), say("A joke!")]
    run_worker(chat, hist)
    hist += [{"role": "assistant", "content": "", "tool_calls": [{"id": "call_3", "function": {"name": "get_time"}}]},
             {"role": "user", "content": "never mind, tell a joke"}]
    rec = run_worker(chat, hist)
    s = rt.client.sessions[0]
    assert s.sent == ["what time is it?", "never mind, tell a joke"]
    assert "abort" in s.calls and s.results["call_3"] == "cancelled"
    assert rec.done == ("A joke!", []) and chat.pending == {}


def test_abort_then_a_new_message(rt, chat, run_worker):
    hist: list[dict] = [{"role": "user", "content": "time?"}]
    rt.client.script = [tools_then("", [("c", "get_time")], "x"), say("Sure.")]
    run_worker(chat, hist)
    CC._LOOP.run(chat.abort(), 5)
    assert chat.pending == {} and chat.order == [] and not chat.turn
    hist += [{"role": "assistant", "content": "", "tool_calls": [{"id": "c"}]}, {"role": "user", "content": "other"}]
    rec = run_worker(chat, hist)
    assert rt.client.sessions[0].sent[-1] == "other" and rec.done == ("Sure.", [])


def test_a_chat_that_began_elsewhere_tells_the_runtime_the_earlier_part(rt, chat, run_worker):
    rt.client.script = [say("Anna!")]
    hist = [{"role": "user", "content": "my name is Anna"}, {"role": "assistant", "content": "Hi Anna!"},
            {"role": "user", "content": "what's my name?"}]
    run_worker(chat, hist)
    assert rt.client.sessions[0].sent == [
        "(Earlier in this chat - for context, already done:)\nUser: my name is Anna\nYou: Hi Anna!\n\n(Now:)\n"
        "what's my name?"]


def test_follow_up_sends_only_what_is_new(rt, chat, run_worker):
    hist: list[dict] = [{"role": "user", "content": "one"}]
    rt.client.script = [say("1"), say("2")]
    run_worker(chat, hist)
    hist += [{"role": "assistant", "content": "1"}, {"role": "user", "content": "two"}]
    run_worker(chat, hist)
    assert rt.client.sessions[0].sent == ["one", "two"] and len(rt.client.sessions) == 1


def test_another_model_mid_chat_keeps_the_session(rt, chat, run_worker):
    hist: list[dict] = [{"role": "user", "content": "hi"}]
    rt.client.script = [say("a"), say("4")]
    run_worker(chat, hist)
    hist += [{"role": "assistant", "content": "a"}, {"role": "user", "content": "2+2?"}]
    run_worker(chat, hist, model="think-model", effort="high")
    s = rt.client.sessions[0]
    assert s.models == [("think-model", "high")] and (chat.model, chat.effort) == ("think-model", "high")
    assert len(rt.client.sessions) == 1


def test_a_shorter_history_starts_a_new_session(rt, chat, run_worker):
    rt.client.script = [say("a"), say("b")]
    run_worker(chat, [{"role": "user", "content": "1"}, {"role": "assistant", "content": "x"},
                      {"role": "user", "content": "2"}])
    run_worker(chat, [{"role": "user", "content": "fresh"}])
    first, second = rt.client.sessions
    assert first.calls == ["abort", "disconnect"] and rt.client.deleted == [first.session_id]
    assert second.sent == ["fresh"]


def test_another_runtime_starts_a_new_session(rt, chat, run_worker):
    rt.client.script = [say("a")]
    run_worker(chat, [{"role": "user", "content": "1"}])
    old = rt.client
    rt.client = FakeClient()
    rt.client.script = [say("b")]
    run_worker(chat, [{"role": "user", "content": "1"}, {"role": "assistant", "content": "a"},
                      {"role": "user", "content": "2"}])
    assert old.sessions[0].calls == ["abort", "disconnect"]
    assert rt.client.sessions[0].sent[0].endswith("(Now:)\n2")


def test_reasoning_web_fetch_status_and_sub_agents(rt, chat, run_worker):
    async def turn(s: FakeSession) -> None:
        s.emit("assistant.reasoning_delta", delta_content="hmm")
        s.emit("assistant.reasoning_delta", delta_content="")
        s.emit("tool.execution_start", tool_name="web_fetch")
        s.emit("tool.execution_start", tool_name="other")
        s.emit("assistant.message_delta", agent_id="sub", delta_content="hidden")
        s.emit("assistant.message", content="First.", tool_requests=[SimpleNamespace(name="web_fetch",
                                                                                     tool_call_id="w")])
        s.emit("assistant.message_delta", delta_content="Sec")
        s.emit("assistant.message_delta", delta_content="ond.")
        s.emit("assistant.message", content=None, tool_requests=None)
        s.emit("assistant.usage", input_tokens=0, output_tokens=0, copilot_usage=None)
        s.emit("session.idle")
    rt.client.script = [turn]
    rec = run_worker(chat, [{"role": "user", "content": "hi"}])
    assert rec.events == [("thinking", 3), ("thinking", 4), ("status", "🌐 reading a web page…"),
                          ("token", "Sec"), ("token", "ond."), ("done", "First.\n\nSecond.", [])]


def test_idle_without_a_message_sends_the_deltas(rt, chat, run_worker):
    async def turn(s: FakeSession) -> None:
        s.emit("assistant.message_delta", delta_content="half")
        s.emit("session.idle")
    rt.client.script = [turn]
    assert run_worker(chat, [{"role": "user", "content": "hi"}]).done == ("half", [])


@pytest.mark.parametrize("data, kind, why", [
    ({"status_code": 401, "message": "Bad credentials"}, "auth", None),
    ({"error_type": "authentication", "message": "x"}, "auth", None),
    ({"status_code": 402, "message": "no allowance"}, "limit", "copilot"),
    ({"error_code": "quota_exceeded", "message": "x"}, "limit", "copilot"),
    ({"message": "Your monthly budget is used up"}, "limit", "copilot"),
    ({"status_code": 429, "message": "slow"}, "limit", "rate"),
    ({"error_type": "rate_limited", "message": "x"}, "limit", "rate"),
    ({"status_code": 403, "message": "forbidden"}, "denied", None),
    ({"message": "blocked by organization policy"}, "denied", None),
    ({"status_code": 404, "message": "no such model"}, "model_gone", None),
    ({"error_code": "model_unavailable", "message": "model is not available"}, "model_gone", None),
    ({"status_code": 503, "message": "down"}, "busy", None),
    ({"message": "Model overloaded"}, "busy", None),
    ({"message": "fetch failed"}, "ext_offline", None),
    ({"message": "getaddrinfo ENOTFOUND"}, "ext_offline", None),
    ({"message": "odd"}, "error", None),
    ({}, "error", None),
])
def test_session_errors_are_mapped(rt, chat, run_worker, data, kind, why):
    rt.client.script = [error(**data)]
    rec = run_worker(chat, [{"role": "user", "content": "hi"}])
    assert rec.failed[0] == kind
    if why:
        lim = json.loads(rec.failed[1])
        assert (lim["provider"], lim["why"], lim["reset"]) == ("copilot", why, None)
    elif not data:
        assert rec.failed[1] == "Copilot didn't answer."
    assert chat.order == []


def test_left_over_events_are_counted_before_the_next_turn(rt, chat, run_worker, qtbot):
    async def turn(s: FakeSession) -> None:
        s.emit("assistant.message", content="done", tool_requests=[])
        s.emit("session.idle")
        await asyncio.sleep(0.01)
        s.emit("assistant.usage", input_tokens=5, output_tokens=5, copilot_usage=D(total_nano_aiu=0))
    rt.client.script = [turn, say("next", usage=None)]
    hist: list[dict] = [{"role": "user", "content": "a"}]
    rec = run_worker(chat, hist)
    qtbot.waitUntil(lambda: chat.queue is not None and not chat.queue.empty(), timeout=2000)
    assert rec.of("used") == []
    hist += [{"role": "assistant", "content": "done"}, {"role": "user", "content": "b"}]
    rec = run_worker(chat, hist)
    assert rec.events == [("used", "copilot", 10), ("token", "next"), ("done", "next", [])]


def test_idle_timeout(rt, chat, run_worker, monkeypatch):
    monkeypatch.setattr(CC, "IDLE_TIMEOUT", 0.3)
    rt.client.script = [silent()]
    rec = run_worker(chat, [{"role": "user", "content": "hi"}])
    assert rec.events == [("failed", "timeout", "")] and "abort" in rt.client.sessions[0].calls


def test_cancel_stops_waiting_quietly(rt, chat, run_worker):
    def started(_rec: Recorder) -> bool:
        if rt.client.sessions and rt.client.sessions[0].sent:
            rt.client.sessions[0].idle_after_abort = 0.6
            return True
        return False
    rt.client.script = [silent()]
    rec = run_worker(chat, [{"role": "user", "content": "hi"}], cancel_when=started)
    assert rec.done is None and rec.failed is None
    assert rt.client.sessions[0].calls == ["abort"]


def test_no_token_fails_at_once(rt, chat, run_worker):
    rec = run_worker(chat, [{"role": "user", "content": "hi"}], creds=MemoryCredentials({}))
    assert rec.events == [("failed", "auth", "No GitHub sign-in or token saved.")]
    assert rt.client.sessions == []


@pytest.mark.parametrize("exc, kind", [(RuntimeError("401 bad credentials"), "auth"),
                                       (CC.CopilotError("install", "GitHub's Copilot runtime isn't set up yet."),
                                        "install"),
                                       (RuntimeError("weird"), "error")])
def test_runtime_failures_are_mapped(chat, run_worker, monkeypatch, exc, kind):
    class BrokenRT:
        client = None

        async def get(self, token: str) -> Any:
            raise exc
    monkeypatch.setattr(CC, "_RT", BrokenRT())
    rec = run_worker(chat, [{"role": "user", "content": "hi"}])
    assert rec.events == [("failed", kind, str(exc))]


# ------------------------------------------------------------------ web_fetch: the app asks first
def fetch_turn(url: str, after: str) -> Turn:
    async def turn(s: FakeSession) -> None:
        await s.ask(url)
        s.emit("assistant.message", content=after, tool_requests=[])
        s.emit("session.idle")
    return turn


def test_web_fetch_asks_the_app(rt, chat, run_worker, monkeypatch):
    monkeypatch.setattr(webpage, "check_address", lambda url: None)
    from copilot._generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

    asked: list[str] = []
    rt.client.script = [fetch_turn("https://cats.example/page", "It says cats nap."),
                        fetch_turn("https://cats.example/again", "Okay, I won't.")]
    hist: list[dict] = [{"role": "user", "content": "read it"}]
    rec = run_worker(chat, hist, web=True, on_perm=lambda url, reply: (asked.append(url), reply(True)))
    assert rec.of("permission")[0][0] == "https://cats.example/page"
    assert rec.done == ("It says cats nap.", [])
    hist += [{"role": "assistant", "content": "It says cats nap."}, {"role": "user", "content": "again"}]
    rec = run_worker(chat, hist, web=True, on_perm=lambda url, reply: (asked.append(url), reply(False)))
    s = rt.client.sessions[0]
    assert asked == ["https://cats.example/page", "https://cats.example/again"]
    assert isinstance(s.decisions[0], PermissionDecisionApproveOnce)
    assert isinstance(s.decisions[1], PermissionDecisionReject)
    assert s.decisions[1].feedback == "The user didn't allow reading this page."


def test_web_fetch_refused_without_asking(rt, chat, run_worker):
    from copilot._generated.rpc import PermissionDecisionReject

    rt.client.script = [fetch_turn("http://127.0.0.1/admin", "Can't."), fetch_turn("https://x.example/", "No web.")]
    hist: list[dict] = [{"role": "user", "content": "local"}]
    rec = run_worker(chat, hist, web=True)
    assert rec.of("permission") == [] and rec.done == ("Can't.", [])
    hist += [{"role": "assistant", "content": "Can't."}, {"role": "user", "content": "web off now"}]
    run_worker(chat, hist, web=False)                    # (switched off in Settings: refused too)
    d1, d2 = rt.client.sessions[0].decisions
    assert isinstance(d1, PermissionDecisionReject) and "local network" in d1.feedback
    assert isinstance(d2, PermissionDecisionReject) and d2.feedback == "Only the app's own tools are available."


def test_other_permission_kinds_are_refused(rt, chat, run_worker):
    async def turn(s: FakeSession) -> None:
        await s.ask("", kind="shell")
        s.emit("session.idle")
    rt.client.script = [turn]
    run_worker(chat, [{"role": "user", "content": "x"}], web=True)
    assert rt.client.sessions[0].decisions[0].feedback == "Only the app's own tools are available."


# ------------------------------------------------------------------ closing
def test_close_deletes_the_session_of_the_runtime(rt, run_worker):
    chat = CC.CopilotChat()
    rt.client.script = [tools_then("", [("c", "get_time")], "x")]
    run_worker(chat, [{"role": "user", "content": "hi"}])
    s = rt.client.sessions[0]
    chat.close(3)
    assert s.calls == ["abort", "disconnect"] and rt.client.deleted == [s.session_id]
    assert (chat.session, chat.session_id, chat.pending, chat.consumed) == (None, "", {}, 0)
    assert s.results["c"] == "cancelled"
    chat.close(3)                                            # (nothing left: a no-op)


def test_close_soon_and_abort_soon(rt, run_worker, qtbot):
    chat = CC.CopilotChat()
    rt.client.script = [tools_then("", [("c", "get_time")], "x")]
    run_worker(chat, [{"role": "user", "content": "hi"}])
    chat.abort_soon()
    qtbot.waitUntil(lambda: chat.pending == {}, timeout=3000)
    chat.close_soon()
    qtbot.waitUntil(lambda: rt.client.deleted != [], timeout=3000)
    assert chat.session is None


def test_bring_your_own_key_session(rt, chat, run_worker, monkeypatch):
    prov = {"name": "fake", "type": "openai", "base_url": "http://127.0.0.1:9/v1", "api_key": "x"}
    monkeypatch.setattr(CC, "BYOK", {"provider": prov, "models": [{"id": "fake-model", "vision": True}]})
    rt.client.script = [say("ok")]
    run_worker(chat, [{"role": "user", "content": "hi"}], creds=MemoryCredentials({}))
    kw = rt.client.sessions[0].kw
    assert kw["model"] == "fake/fake-model" and rt.token == ""
    inf = kw["inference"]
    assert inf["reasoning_effort"] == "medium" and inf["providers"] == [prov] and inf["default_provider"] == "fake"
    assert [m["id"] for m in inf["models"]] == ["fake-model"]
    assert inf["models"][0]["capabilities"].supports.vision is True
    assert kw["github"]["capi"] == {"enabled": False}
