"""What the cat needs from an AI model provider: one chat request, and how it reports back.

A chat job (one request to a model) runs on a worker thread and reports what happens through a
:class:`ChatEvents` sink; the app turns those calls into Qt signals (``pixelcat.llm.qt_chat``).

Plain interfaces (``typing.Protocol``); see :mod:`pixelcat.ports.desktop`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class ChatEvents(Protocol):
    """Where a chat job reports.  May be called from any thread (Copilot: its asyncio thread)."""

    def token(self, text: str) -> None:
        """A piece of the answer."""
        ...

    def thinking(self, chars: int) -> None:
        """Characters of hidden reasoning so far (for a progress hint)."""
        ...

    def done(self, text: str, tool_calls: list) -> None:  # type: ignore[type-arg]
        """The whole answer and the native tool calls."""
        ...

    def failed(self, kind: str, detail: str) -> None:
        """The request failed (kinds: see the jobs)."""
        ...

    def used(self, provider: str, tokens: int) -> None:
        """Tokens this request used (external models)."""
        ...

    def status(self, text: str) -> None:
        """What the model is doing meanwhile ("searching the web")."""
        ...

    def credits(self, n: float) -> None:
        """AI credits a model call used (Copilot)."""
        ...

    def permission(self, url: str, reply: Callable[[bool], None]) -> None:
        """The model wants to read a web page (Copilot's web_fetch); ``reply(ok)`` answers."""
        ...


class Conversation(Protocol):
    """A chat the provider keeps itself (Copilot's session): only what is new is sent."""

    def close_soon(self) -> None: ...

    def close(self, timeout: float = 6.0) -> None: ...

    def abort_soon(self) -> None: ...


@dataclass
class ChatRequest:
    """One request to a model (the same type for every provider; each uses what it needs)."""

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    think: bool | None = None
    effort: str = ""
    creds: Any = None                    # the app's Credentials (external models)
    auth: str = ""                       # how the provider is connected: "key", "signin", "chatgpt"...
    info: dict[str, Any] = field(default_factory=dict)      # the model's capabilities (from the model list)
    web: bool = False                    # the provider's own web search may be used
    history: list[dict[str, Any]] | None = None             # Copilot: the whole history (it keeps the chat)
    system: str = ""                     # Copilot: the system prompt
    conversation: Conversation | None = None                # Copilot: this chat's session
    cfg: Any = None                      # Ollama: the Config (url, options)


class ChatJob(Protocol):
    """One chat request.  ``raw``: the reply as the provider's own blocks (replayed next time);
    ``note``: something to tell the user once."""

    raw: list[dict[str, Any]] | None
    note: str

    def run(self) -> None:
        """Send the request and report through the events (blocking; called on a worker thread)."""
        ...

    def cancel(self) -> None: ...
