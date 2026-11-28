"""The Qt bridge for the chat jobs: a :class:`QtChatWorker` runs one job in a QThread and turns the job's
:class:`~pixelcat.ports.llm.ChatEvents` calls into Qt signals; a :class:`QtNotifier` does the same for a
plain ``notify()`` callback (``ModelStatus``).

The signals are emitted from whatever thread the job reports on (the QThread's ``run()``, or Copilot's
asyncio thread); receivers on the GUI thread get them queued (AutoConnection), as before the bridge.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..ports.llm import ChatEvents, ChatJob


class _QtEvents:
    """The :class:`ChatEvents` sink of one :class:`QtChatWorker`: each call emits the worker's signal."""

    def __init__(self, worker: QtChatWorker) -> None:
        self._w = worker

    def token(self, text: str) -> None:
        self._w.token.emit(text)

    def thinking(self, chars: int) -> None:
        self._w.thinking.emit(chars)

    def done(self, text: str, tool_calls: list) -> None:  # type: ignore[type-arg]
        self._w.done.emit(text, tool_calls)

    def failed(self, kind: str, detail: str) -> None:
        self._w.failed.emit(kind, detail)

    def used(self, provider: str, tokens: int) -> None:
        self._w.used.emit(provider, tokens)

    def status(self, text: str) -> None:
        self._w.status.emit(text)

    def credits(self, n: float) -> None:
        self._w.credits.emit(n)

    def permission(self, url: str, reply: Callable[[bool], None]) -> None:
        self._w.permission.emit(url, reply)


class QtChatWorker(QThread):
    """One chat request in a QThread.  ``make_job(events)`` builds the job (right away) with this
    worker's sink; ``run()`` runs it.  ``provider``/``model``: what was asked; ``local``: Ollama."""

    token = Signal(str)
    thinking = Signal(int)            # characters of hidden reasoning so far (for a progress hint)
    done = Signal(str, list)          # full content, native tool calls
    failed = Signal(str, str)
    used = Signal(str, int)
    status = Signal(str)
    credits = Signal(float)
    permission = Signal(str, object)  # (url, reply(ok))

    def __init__(self, make_job: Callable[[ChatEvents], ChatJob], *, provider: str, model: str,
                 local: bool) -> None:
        super().__init__()
        self.provider, self.model, self.local = provider, model, local
        self.job: ChatJob = make_job(_QtEvents(self))

    @property
    def raw(self) -> Any:
        return getattr(self.job, "raw", None)

    @property
    def note(self) -> str:
        return getattr(self.job, "note", "")

    def run(self) -> None:
        self.job.run()

    def cancel(self) -> None:
        self.job.cancel()


class QtNotifier(QObject):
    """``changed`` for a plain ``notify`` callback: pass ``changed.emit``."""

    changed = Signal()
