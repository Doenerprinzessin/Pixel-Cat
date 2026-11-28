"""llm/qt_chat: the Qt bridge - a chat job's events become the QtChatWorker's signals, delivered queued on
the GUI thread (also from a plain thread like Copilot's asyncio thread); QtNotifier does the same for
ModelStatus."""
from __future__ import annotations

import threading
from typing import Any

import pytest
from PySide6.QtCore import QObject, QThread, Slot

from fakes.chat import FakeJob
from pixelcat.llm.qt_chat import QtChatWorker, QtNotifier
from pixelcat.ports.llm import ChatRequest

pytestmark = pytest.mark.qt

SIGNALS = ("token", "thinking", "done", "failed", "used", "status", "credits", "permission")


def make(job_cls: Any = FakeJob, **kw: Any) -> QtChatWorker:
    req = ChatRequest("m1", [{"role": "user", "content": "hi"}])
    return QtChatWorker(lambda events: job_cls(req, events), provider=kw.get("provider", "anthropic"),
                        model=kw.get("model", "m1"), local=kw.get("local", False))


def record(w: QtChatWorker) -> list[tuple]:
    got: list[tuple] = []
    for name in SIGNALS:
        getattr(w, name).connect(lambda *a, n=name: got.append((n, *a)))
    return got


def test_each_event_emits_its_signal(qapp) -> None:  # type: ignore[no-untyped-def]
    w = make()
    got = record(w)
    ev = w.job.events                   # type: ignore[attr-defined]

    def reply(ok: bool) -> None:
        pass
    ev.token("Mrr")
    ev.thinking(12)
    ev.done("Hi.", [{"name": "a", "arguments": {}}])
    ev.failed("auth", "no key")
    ev.used("anthropic", 42)
    ev.status("🔎 searching the web…")
    ev.credits(0.25)
    ev.permission("https://cats.example/", reply)
    assert got == [("token", "Mrr"), ("thinking", 12), ("done", "Hi.", [{"name": "a", "arguments": {}}]),
                   ("failed", "auth", "no key"), ("used", "anthropic", 42), ("status", "🔎 searching the web…"),
                   ("credits", 0.25), ("permission", "https://cats.example/", reply)]


def test_what_was_asked_and_the_jobs_raw_and_note(qapp) -> None:  # type: ignore[no-untyped-def]
    w = make(provider="ollama", model="qwen3.5:9b", local=True)
    assert (w.provider, w.model, w.local) == ("ollama", "qwen3.5:9b", True)
    assert (w.raw, w.note) == (None, "")
    w.job.raw, w.job.note = [{"type": "text", "text": "Hi"}], "This model always thinks."  # type: ignore[attr-defined]
    assert (w.raw, w.note) == ([{"type": "text", "text": "Hi"}], "This model always thinks.")
    w.cancel()
    assert w.job.cancelled                                     # type: ignore[attr-defined]


def test_run_runs_the_job_in_the_thread_and_finishes(qtbot) -> None:  # type: ignore[no-untyped-def]
    ran_in: list[Any] = []

    class Job(FakeJob):
        def run(self) -> None:
            ran_in.append(QThread.currentThread())
            self.events.done("Hi.", [])

    w = make(Job)
    got = record(w)
    with qtbot.waitSignal(w.finished, timeout=5000):
        w.start()
    assert ran_in == [w] and w.isFinished()
    qtbot.waitUntil(lambda: got == [("done", "Hi.", [])], timeout=2000)


class Receiver(QObject):
    """A slot on the GUI thread, like the app's: where and from whom each call came."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    @Slot(str)
    def on_token(self, text: str) -> None:
        self.calls.append((text, threading.current_thread(), self.sender()))

    @Slot()
    def on_changed(self) -> None:
        self.calls.append(("changed", threading.current_thread(), self.sender()))


def test_an_event_from_a_plain_thread_arrives_queued_on_the_gui_thread(qtbot) -> None:  # type: ignore[no-untyped-def]
    w = make()
    rcv = Receiver()
    w.token.connect(rcv.on_token)
    th = threading.Thread(target=lambda: w.job.events.token("from asyncio"))  # type: ignore[attr-defined]
    th.start()
    th.join(5)
    assert rcv.calls == []                                     # (not yet: it waits for the GUI thread's loop)
    qtbot.waitUntil(lambda: rcv.calls != [], timeout=2000)
    assert rcv.calls == [("from asyncio", threading.main_thread(), w)]


def test_the_notifier_delivers_a_background_notify_on_the_gui_thread(qtbot) -> None:  # type: ignore[no-untyped-def]
    n = QtNotifier()
    rcv = Receiver()
    n.changed.connect(rcv.on_changed)
    th = threading.Thread(target=n.changed.emit)
    th.start()
    th.join(5)
    assert rcv.calls == []
    qtbot.waitUntil(lambda: rcv.calls != [], timeout=2000)
    assert rcv.calls == [("changed", threading.main_thread(), n)]
