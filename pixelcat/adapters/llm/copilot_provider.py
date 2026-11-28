"""GitHub Copilot: "Sign in with GitHub" or a GitHub token; requests through GitHub's official Copilot SDK and its
runtime (``pixelcat.adapters.llm.copilot_client``, imported when needed - it starts nothing until it is used)."""
from __future__ import annotations

import sys
from typing import Any, Callable

from ...llm import external
from ...ports.llm import ChatEvents, ChatJob, ChatRequest, Conversation, DeviceSignIn, ProviderInfo
from .base import ProviderBase
from .signin import GitHubSignIn

CLIENT = "pixelcat.adapters.llm.copilot_client"


class CopilotProvider(ProviderBase):
    info: ProviderInfo = external.COPILOT
    own_errors = ((CLIENT, "CopilotError"), ("pixelcat.adapters.llm.github_signin", "SignInError"))

    def auth(self, creds: Any, want: str) -> str:
        s = creds.get("copilot_signin")
        if want == "signin" and isinstance(s, dict) and s.get("token"):
            return "signin"
        return "key" if creds.get("copilot_key") else ""

    def list_models(self, creds: Any, auth: str) -> list[dict[str, Any]]:
        from . import copilot_client

        return copilot_client.list_models(creds, auth)

    def check_key(self, key: str) -> tuple[str, list[dict[str, Any]]]:
        from . import copilot_client

        return copilot_client.check_token(key)

    def effort_levels(self, model_info: dict[str, Any]) -> list[str] | None:
        if isinstance(model_info.get("efforts"), list):
            return [e for e in external.EFFORTS if e in model_info["efforts"]] or None
        return list(external.EFFORTS)

    def quota(self, creds: Any, auth: str, start: bool = False) -> dict[str, Any] | None:
        from . import copilot_client

        return copilot_client.quota(creds, auth, start=start)

    def runtime_ready(self) -> bool:
        from . import copilot_client

        return isinstance(copilot_client.runtime_path(), str)

    def install_runtime(self, progress: Callable[[int, int], None] | None = None,
                        cancel: Any = None) -> None:
        from . import copilot_client

        copilot_client.install(progress, cancel)

    def new_chat(self, req: ChatRequest, events: ChatEvents) -> ChatJob:
        from . import copilot_client

        return copilot_client.CopilotJob(req, events)

    def new_conversation(self) -> Conversation | None:
        from . import copilot_client

        return copilot_client.CopilotChat()

    def signin(self) -> DeviceSignIn:
        from . import github_signin  # noqa: F401 - (loaded now, on the caller's thread)

        return GitHubSignIn()

    def key_problem(self, exc: BaseException) -> str:
        kind = getattr(exc, "kind", "")
        if kind == "auth":
            return "GitHub didn't accept that token - check that you copied all of it (and that it hasn't expired)."
        if kind == "denied":
            return ("That can't use Copilot. A token needs the Copilot Requests permission (Account permissions), and "
                    "your Copilot plan or organization has to allow it.")
        if kind == "ext_offline":
            return "I couldn't reach GitHub - is the internet on?"
        return str(exc)[:200] if kind == "install" else f"That didn't work: {str(exc)[:160]}"

    @staticmethod
    def _client_shutdown() -> Callable[[float], None] | None:
        """The loaded client's shutdown - None while it isn't loaded, or is still being imported on another thread
        (it is in sys.modules before its names are)."""
        return getattr(sys.modules.get(CLIENT), "shutdown", None)

    def active(self) -> bool:
        return self._client_shutdown() is not None   # (never used: nothing runs, and the SDK isn't loaded for it)

    def shutdown(self, timeout: float) -> None:
        stop = self._client_shutdown()
        if stop is not None:
            stop(timeout)

    def self_check(self) -> str:
        from copilot._cli_version import CLI_VERSION

        from . import copilot_client, github_signin  # noqa: F401
        copilot_client.new_client("selftest", copilot_client.runtime_name())     # (made, never started)
        return f"runtime {CLI_VERSION}, {'here' if copilot_client.runtime_path() else 'downloaded on first use'}"
