"""What most providers answer the same way (each provider overrides what it has)."""
from __future__ import annotations

import sys
from typing import Any, Callable

from ...llm import external
from ...ports.llm import Conversation


class ProviderBase:
    """No plan quota, nothing to install, no conversation of its own, nothing to stop."""

    # (module, exception class): failures whose message is meant for the user.  Looked up in sys.modules only:
    # an exception of a module that was never loaded can't be one of them.
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

    def shutdown(self, timeout: float) -> None:
        return None

    def self_check(self) -> None:
        return None

    def error_text(self, exc: BaseException, fallback: str) -> str:
        for module, name in self.own_errors:
            mod = sys.modules.get(module)
            if mod is not None and isinstance(exc, getattr(mod, name)):
                return str(exc)
        return fallback
