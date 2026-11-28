"""The local models (Ollama): no account, no key, no budget; requests to Ollama's /api/chat
(``pixelcat.llm.ollama_client``, imported when needed).  Starting Ollama and getting the model is
``pixelcat.llm.model_status``'s job."""
from __future__ import annotations

from typing import Any

from ...ports.llm import ChatEvents, ChatJob, ChatRequest, ProviderInfo
from .base import ProviderBase

OLLAMA = ProviderInfo("ollama", "Ollama", "Local", "", "", "", "", local=True)


class OllamaProvider(ProviderBase):
    info: ProviderInfo = OLLAMA

    def auth(self, creds: Any, want: str) -> str:
        return "local"                           # (always there: it needs no account)

    def list_models(self, creds: Any, auth: str) -> list[dict[str, Any]]:
        return []                                # (the local model is the settings' one)

    def check_key(self, key: str) -> tuple[str, list[dict[str, Any]]]:
        raise ValueError("the local models take no key")

    def effort_levels(self, model_info: dict[str, Any]) -> list[str] | None:
        return ["low", "medium", "high"]         # (how much it thinks: never, when planning, always)

    def new_chat(self, req: ChatRequest, events: ChatEvents) -> ChatJob:
        from ...llm import ollama_client

        return ollama_client.OllamaChat(req, events)
