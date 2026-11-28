"""llm/qt_chat with the real Copilot job (copilot_client.CopilotJob, played by the fake SDK of
tests/adapters/llm/test_copilot_chat.py): the job runs in the QtChatWorker's thread, but reports from the
SDK's asyncio thread - the events still arrive queued on the GUI thread, in order, from the worker; and a
web_fetch permission is answered on the GUI thread while the job waits in its own thread (no deadlock)."""
from __future__ import annotations

import functools
import threading
from typing import Any

import pytest
from PySide6.QtCore import QObject, Qt, Slot

from adapters.llm.test_copilot_chat import INFO, TOOLS, FakeRT, fetch_turn, say
from pixelcat.adapters.llm import copilot_client as CC
from pixelcat.adapters.llm.copilot_provider import CopilotProvider
from pixelcat.llm import webpage
from pixelcat.llm.external import MemoryCredentials
from pixelcat.llm.qt_chat import QtChatWorker
from pixelcat.ports.llm import ChatRequest

pytestmark = pytest.mark.qt

SIGNALS = ("token", "thinking", "done", "failed", "used", "status", "credits", "permission")


@pytest.fixture
def rt(monkeypatch):
    fake = FakeRT()
    monkeypatch.setattr(CC, "_RT", fake)
    return fake


@pytest.fixture
def provider():
    return CopilotProvider()


@pytest.fixture
def chat(provider):
    c = provider.new_conversation()
    yield c
    c.close(3)


@pytest.fixture
def workers():
    made: list[QtChatWorker] = []
    yield made
    for w in made:
        if w.isRunning():
            w.cancel()
        assert w.wait(5000)


class Receiver(QObject):
    """The app's side: slots on the GUI thread recording what came, on which thread and from whom.
    ``answer``: what the permission slot replies (on the GUI thread)."""

    def __init__(self, answer: bool = True) -> None:
        super().__init__()
        self.answer = answer
        self.calls: list[tuple] = []
        self.worker_running_when_asked: bool | None = None

    def _add(self, *call: Any) -> None:
        self.calls.append((call, threading.get_ident(), self.sender()))

    @Slot(str)
    def on_token(self, text: str) -> None:
        self._add("token", text)

    @Slot(str, list)
    def on_done(self, text: str, tool_calls: list) -> None:
        self._add("done", text, tool_calls)

    @Slot(str, str)
    def on_failed(self, kind: str, detail: str) -> None:
        self._add("failed", kind, detail)

    @Slot(str, int)
    def on_used(self, provider: str, tokens: int) -> None:
        self._add("used", provider, tokens)

    @Slot(float)
    def on_credits(self, n: float) -> None:
        self._add("credits", n)

    @Slot(str, object)
    def on_permission(self, url: str, reply: Any) -> None:
        self._add("permission", url)
        sender = self.sender()
        self.worker_running_when_asked = sender.isRunning() and not sender.isFinished()
        reply(self.answer)

    def listen(self, w: QtChatWorker) -> None:
        for name in ("token", "done", "failed", "used", "credits", "permission"):
            getattr(w, name).connect(getattr(self, f"on_{name}"))

    @property
    def names(self) -> list[str]:
        return [c[0][0] for c in self.calls]

    @property
    def finished(self) -> bool:
        return any(n in ("done", "failed") for n in self.names)


def start(provider: CopilotProvider, chat: Any, workers: list, rcv: Receiver, history: list, web: bool = False
          ) -> tuple[QtChatWorker, dict[str, Any]]:
    """Start a Copilot turn through the bridge; ``seen``: the threads the worker's signals were emitted
    from (a direct connection runs in the emitting thread) and the worker's own thread."""
    req = ChatRequest("fake-model", [], TOOLS, True, "medium", MemoryCredentials({"copilot_key": "github_pat_1"}),
                      "key", INFO, web=web, history=history, system="You are Pixel, a test cat.",
                      conversation=chat)
    w = QtChatWorker(functools.partial(provider.new_chat, req), provider="copilot", model="fake-model", local=False)
    workers.append(w)
    seen: dict[str, Any] = {"emitted": set()}
    lock = threading.Lock()

    def emitted(*_a: Any) -> None:
        with lock:
            seen["emitted"].add(threading.get_ident())
    for name in SIGNALS:
        getattr(w, name).connect(emitted, Qt.ConnectionType.DirectConnection)
    w.started.connect(lambda: seen.setdefault("worker", threading.get_ident()), Qt.ConnectionType.DirectConnection)
    rcv.listen(w)
    w.start()
    return w, seen


def sdk_thread() -> int:
    async def ident() -> int:
        return threading.get_ident()
    return CC._LOOP.run(ident(), 5)


def test_a_copilot_turn_arrives_in_order_on_the_gui_thread(rt, provider, chat, workers, qtbot) -> None:  # type: ignore[no-untyped-def]
    rt.client.script = [say("Meow! ", "Hello.")]
    rcv = Receiver()
    w, seen = start(provider, chat, workers, rcv, [{"role": "user", "content": "hi there"}])
    qtbot.waitUntil(lambda: rcv.finished, timeout=5000)
    assert w.wait(5000)
    qtbot.wait(20)                                             # (anything left over would arrive now)
    main = threading.main_thread().ident
    assert [c[0] for c in rcv.calls] == [("token", "Meow! "), ("token", "Hello."), ("used", "copilot", 128),
                                         ("credits", 0.5), ("done", "Meow! Hello.", [])]
    assert all(c[1] == main and c[2] is w for c in rcv.calls)
    assert seen["emitted"] == {sdk_thread()}                   # (the job reported from the SDK's thread...)
    assert seen["worker"] not in seen["emitted"] and main not in seen["emitted"]   # (...not the worker's or ours)
    assert rt.client.sessions[0].sent == ["hi there"]


@pytest.mark.parametrize("ok", [True, False])
def test_a_permission_is_answered_on_the_gui_thread_and_the_turn_goes_on(rt, provider, chat, workers, qtbot,
                                                                         monkeypatch, ok) -> None:  # type: ignore[no-untyped-def]
    from copilot._generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

    monkeypatch.setattr(webpage, "check_address", lambda url: None)
    rt.client.script = [fetch_turn("https://cats.example/page", "It says cats nap." if ok else "Okay, I won't.")]
    rcv = Receiver(answer=ok)
    w, seen = start(provider, chat, workers, rcv, [{"role": "user", "content": "read it"}], web=True)
    qtbot.waitUntil(lambda: rcv.finished, timeout=5000)
    assert w.wait(5000)
    qtbot.wait(20)
    main = threading.main_thread().ident
    assert [c[0] for c in rcv.calls] == [("permission", "https://cats.example/page"),
                                         ("done", "It says cats nap." if ok else "Okay, I won't.", [])]
    assert all(c[1] == main and c[2] is w for c in rcv.calls)
    assert rcv.worker_running_when_asked                       # (the job waited for the answer in its thread)
    assert seen["emitted"] == {sdk_thread()}
    decision = rt.client.sessions[0].decisions[0]
    assert isinstance(decision, PermissionDecisionApproveOnce if ok else PermissionDecisionReject)
