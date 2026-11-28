"""OpenAI (ChatGPT): an API key or "Sign in with ChatGPT"; requests to the Responses API
(``pixelcat.llm.openai_client``, imported when needed)."""
from __future__ import annotations

from typing import Any

from ...llm import external
from ...ports.llm import BrowserSignIn, ChatEvents, ChatJob, ChatRequest, ProviderInfo
from .base import ProviderBase
from .signin import ChatGPTSignIn


class OpenAIProvider(ProviderBase):
    info: ProviderInfo = external.OPENAI
    own_errors = (("pixelcat.llm.chatgpt_signin", "SignInError"),)

    def auth(self, creds: Any, want: str) -> str:
        from ...llm import chatgpt_signin

        if want == "chatgpt" and chatgpt_signin.signed_in(creds):
            return "chatgpt"
        return "key" if creds.get("openai_key") else ""

    def list_models(self, creds: Any, auth: str) -> list[dict[str, Any]]:
        from ...llm import openai_client

        return openai_client.list_models(creds, auth)

    def check_key(self, key: str) -> tuple[str, list[dict[str, Any]]]:
        from ...llm import openai_client

        return "", openai_client.list_models(external.MemoryCredentials({"openai_key": key}), "key")

    def max_input(self, model: str, model_info: dict[str, Any]) -> int | None:
        return external.max_input("openai", model, model_info)

    def new_chat(self, req: ChatRequest, events: ChatEvents) -> ChatJob:
        from ...llm import openai_client

        return openai_client.OpenAIChat(req, events)

    def signin(self) -> BrowserSignIn:
        from ...llm import chatgpt_signin  # noqa: F401 - (loaded now, on the caller's thread)

        return ChatGPTSignIn()

    def self_check(self) -> str:
        from ...llm import chatgpt_signin, openai_client  # noqa: F401
        return ""
