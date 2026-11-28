"""What the cat needs from an AI model provider: its models, one chat request, and how it reports back.

A :class:`ModelProvider` per provider is built by ``pixelcat.bootstrap`` and handed to the app.  A chat job
(one request to a model) runs on a worker thread and reports what happens through a :class:`ChatEvents` sink;
the app turns those calls into Qt signals (``pixelcat.llm.qt_chat``).

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


@dataclass(frozen=True)
class ProviderInfo:
    """What a model provider is called and where its pages are (the texts the settings show)."""

    key: str
    name: str            # the company
    brand: str           # the models
    signin: str          # the sign-in button
    key_url: str         # where an API key is made
    key_hint: str        # what a key starts with
    usage_url: str       # where the account's usage / credits are
    local: bool = False  # the models run on this PC (Ollama): no account, no key, no budget


class ModelProvider(Protocol):
    """One AI model provider (Anthropic, OpenAI, GitHub Copilot, the local Ollama).  ``creds``: the app's
    ``Credentials``; ``auth``: how the provider is connected (from :meth:`auth`)."""

    info: ProviderInfo

    def auth(self, creds: Any, want: str) -> str:
        """How it is connected ("key", "signin", "chatgpt"...; "" not), given the way the user chose (``want``)."""
        ...

    def list_models(self, creds: Any, auth: str) -> list[dict[str, Any]]:
        """The models the account may use (blocking; raises on failure)."""
        ...

    def check_key(self, key: str) -> tuple[str, list[dict[str, Any]]]:
        """Try a pasted API key / token: (the account's login or "", its models); raises if it isn't accepted."""
        ...

    def effort_levels(self, model_info: dict[str, Any]) -> list[str] | None:
        """The effort levels a model has (None: none to pick); ``model_info``: its model-list entry."""
        ...

    def max_input(self, model: str, model_info: dict[str, Any]) -> int | None:
        """The most one request may hold, in tokens (None: not tracked here - the context setting applies)."""
        ...

    def refresh_models_for_context(self, model_info: dict[str, Any]) -> bool:
        """True: the saved model list lacks the context window, load it again (once)."""
        ...

    def quota(self, creds: Any, auth: str, start: bool = False) -> dict[str, Any] | None:
        """The plan's own allowance (None: the provider has none to show); ``start``: may start a runtime for it."""
        ...

    def runtime_ready(self) -> bool:
        """Everything the provider needs on this PC is here (Copilot: GitHub's runtime)."""
        ...

    def install_runtime(self, progress: Callable[[int, int], None] | None = None,
                        cancel: Any = None) -> None:
        """Get what :meth:`runtime_ready` lacks (blocking; ``progress(done, total)``, ``cancel``: an Event)."""
        ...

    def new_chat(self, req: ChatRequest, events: ChatEvents) -> ChatJob:
        """The job for one chat request (run on a worker thread)."""
        ...

    def new_conversation(self) -> Conversation | None:
        """A chat the provider keeps itself (Copilot's session), or None (each request sends the whole chat)."""
        ...

    def shutdown(self, timeout: float) -> None:
        """Quitting / signing out: stop what the provider runs (nothing if it was never used)."""
        ...

    def self_check(self) -> None:
        """--selftest: build the provider's SDK client without using it, and log how that went."""
        ...

    def error_text(self, exc: BaseException, fallback: str) -> str:
        """What to show for a failure: the provider's own errors say it themselves, others get ``fallback``."""
        ...
