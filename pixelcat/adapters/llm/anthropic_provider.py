"""Anthropic (Claude): an API key or "Sign in with Anthropic" (the ``ant`` CLI); requests through the official SDK
(``pixelcat.adapters.llm.anthropic_client``, imported when needed)."""
from __future__ import annotations

from typing import Any

from ...llm import external
from ...ports.llm import ChatEvents, ChatJob, ChatRequest, CliSignIn, ProviderInfo
from .base import ProviderBase
from .signin import AntSignIn


class AnthropicProvider(ProviderBase):
    info: ProviderInfo = external.ANTHROPIC
    own_errors = (("pixelcat.adapters.llm.ant_tool", "AntError"),)

    def auth(self, creds: Any, want: str) -> str:
        if want == "signin":
            return "signin"
        return "key" if creds.get("anthropic_key") else ""

    def list_models(self, creds: Any, auth: str) -> list[dict[str, Any]]:
        from . import anthropic_client

        return anthropic_client.list_models(creds, auth)

    def check_key(self, key: str) -> tuple[str, list[dict[str, Any]]]:
        from . import anthropic_client

        return "", anthropic_client.list_models(external.MemoryCredentials({"anthropic_key": key}), "key")

    def effort_levels(self, model_info: dict[str, Any]) -> list[str] | None:
        if isinstance(model_info.get("effort"), list):
            return [e for e in external.EFFORTS if e in model_info["effort"]] or None
        return list(external.EFFORTS)

    def max_input(self, model: str, model_info: dict[str, Any]) -> int | None:
        return external.max_input("anthropic", model, model_info)

    def refresh_models_for_context(self, model_info: dict[str, Any]) -> bool:
        return bool(model_info) and "context" not in model_info   # (a list saved before windows were kept)

    def new_chat(self, req: ChatRequest, events: ChatEvents) -> ChatJob:
        from . import anthropic_client

        return anthropic_client.AnthropicChat(req, events)

    def signin(self) -> CliSignIn:
        from . import ant_tool  # noqa: F401 - (loaded now, on the caller's thread)

        return AntSignIn()

    def self_check(self) -> str:
        import anthropic

        from . import ant_tool, anthropic_client  # noqa: F401
        anthropic.Anthropic(api_key="selftest")            # (made, never used)
        return f"anthropic {anthropic.__version__}"
