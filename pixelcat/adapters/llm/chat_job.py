"""The base of the external models' chat jobs (Anthropic, OpenAI, Copilot): one request, reported through a
:class:`~pixelcat.ports.llm.ChatEvents` sink (the app's Qt bridge turns the calls into signals)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...ports.llm import ChatEvents, ChatRequest


class ExternalChat:
    """Like ollama_client.OllamaChat (same events, same messages in), for an external model.
    ``failed`` kinds: auth, ext_offline, timeout, limit (detail: JSON with why/reset/url),
    refused, truncated, model_gone, denied, busy, too_long, error.  ``used``: tokens this request
    used.  ``status``: what the model is doing meanwhile ("searching the web").
    ``raw``: the model's reply as the provider's own blocks (replayed unchanged next time).
    ``web``: the provider's own web search may be used (Settings: Web search)."""

    def __init__(self, provider: str, req: ChatRequest, events: ChatEvents) -> None:
        self.events = events
        self.provider, self.model = provider, req.model
        self.messages, self.tools = req.messages, req.tools
        self.think, self.effort = req.think, req.effort
        self.creds = req.creds
        self.web = req.web
        self.raw: list[dict[str, Any]] | None = None
        self.note = ""                       # something to tell the user once (e.g. "this model always thinks")
        self._cancel = False

    def run(self) -> None:
        raise NotImplementedError

    def cancel(self) -> None:
        self._cancel = True

    def limit(self, why: str, reset: datetime | None = None, detail: str = "") -> None:
        self.events.failed("limit", json.dumps({"provider": self.provider, "why": why, "detail": detail[:300],
                                                "reset": reset.timestamp() if reset else None}))
