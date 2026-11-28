"""What most providers answer the same way (each provider overrides what it has)."""
from __future__ import annotations

import sys
from typing import Any, Callable

from ...llm import external
from ...ports.llm import BrowserSignIn, CliSignIn, Conversation, DeviceSignIn


class ProviderBase:
    """No plan quota, nothing to install, no conversation of its own, no sign-in, nothing to stop."""

    # (module, exception class): failures whose message is meant for the user.  Looked up in sys.modules only:
    # an exception of a module that was never loaded (or is still being loaded) can't be one of them.
    own_errors: tuple[tuple[str, str], ...] = ()

    def effort_levels(self, model_info: dict[str, Any]) -> list[str] | None:
        return list(external.EFFORTS)

    def max_input(self, model: str, model_info: dict[str, Any]) -> int | None:
        return None

    def refresh_models_for_context(self, model_info: dict[str, Any]) -> bool:
        return False

    def quota(self, creds: Any, auth: str, start: bool = False) -> dict[str, Any] | None:
        return None

    def runtime_ready(self) -> bool:
        return True

    def install_runtime(self, progress: Callable[[int, int], None] | None = None,
                        cancel: Any = None) -> None:
        return None

    def new_conversation(self) -> Conversation | None:
        return None

    def signin(self) -> CliSignIn | BrowserSignIn | DeviceSignIn | None:
        return None

    def active(self) -> bool:
        return False

    def shutdown(self, timeout: float) -> None:
        return None

    def self_check(self) -> str:
        return ""

    def error_text(self, exc: BaseException, fallback: str) -> str:
        for module, name in self.own_errors:
            cls = getattr(sys.modules.get(module), name, None)   # (None too while it is still being imported)
            if isinstance(cls, type) and isinstance(exc, cls):
                return str(exc)
        return fallback

    def key_problem(self, exc: BaseException) -> str:
        name, kind = type(exc).__name__, getattr(exc, "kind", "")      # (the SDKs' errors, and the clients' kinds)
        if "Authentication" in name or kind == "auth":
            return "That key wasn't accepted - check that you copied all of it."
        if "Connection" in name or "Timeout" in name or kind == "ext_offline":
            return "I couldn't reach the provider - is the internet on?"
        if "PermissionDenied" in name:
            return "That key isn't allowed to use the models."
        return f"That didn't work: {str(exc)[:160]}"
