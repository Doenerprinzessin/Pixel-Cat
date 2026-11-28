"""Fakes for the chat jobs (``from fakes.chat import RecordingEvents, FakeJob``).

* :class:`RecordingEvents`: a ``ChatEvents`` sink that records every call (from any thread).
* :class:`FakeJob`: a chat job the test drives itself through its ``events`` (``job.events.done("hi", [])``).
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from pixelcat.ports.llm import ChatEvents, ChatRequest


class RecordingEvents:
    """``events``: (name, *args) in call order.  ``on``: name -> a callback run after recording
    (e.g. ``{"permission": lambda url, reply: reply(True)}``), in the job's thread."""

    def __init__(self, on: dict[str, Callable[..., Any]] | None = None) -> None:
        self.events: list[tuple] = []  # type: ignore[type-arg]
        self.on = dict(on or {})
        self._lock = threading.Lock()

    def _add(self, name: str, *args: Any) -> None:
        with self._lock:
            self.events.append((name, *args))
        cb = self.on.get(name)
        if cb is not None:
            cb(*args)

    def token(self, text: str) -> None:
        self._add("token", text)

    def thinking(self, chars: int) -> None:
        self._add("thinking", chars)

    def done(self, text: str, tool_calls: list) -> None:  # type: ignore[type-arg]
        self._add("done", text, tool_calls)

    def failed(self, kind: str, detail: str) -> None:
        self._add("failed", kind, detail)

    def used(self, provider: str, tokens: int) -> None:
        self._add("used", provider, tokens)

    def status(self, text: str) -> None:
        self._add("status", text)

    def credits(self, n: float) -> None:
        self._add("credits", n)

    def permission(self, url: str, reply: Callable[[bool], None]) -> None:
        self._add("permission", url, reply)


class FakeJob:
    """A chat job that sends nothing: it keeps the request, ``run()`` only notes that it started,
    and the test reports through ``events`` like the real job would."""

    def __init__(self, req: ChatRequest, events: ChatEvents) -> None:
        self.req, self.events = req, events
        self.raw: list[dict[str, Any]] | None = None
        self.note = ""
        self.started = self.cancelled = False

    @property
    def model(self) -> str:
        return self.req.model

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.req.messages

    @property
    def tools(self) -> list[dict[str, Any]] | None:
        return self.req.tools

    @property
    def think(self) -> bool | None:
        return self.req.think

    @property
    def web(self) -> bool:
        return self.req.web

    def run(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True
