"""The AI model providers' accounts (no Qt): each one's settings in the save, how it is connected, its models
and its token budget."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from ..llm import external

if TYPE_CHECKING:
    from ..persistence import SavedState
    from ..ports import ModelProvider, ProviderInfo


class Accounts:
    """The providers (by key, in the settings' order), their settings in ``saved.ext_settings`` and the keys and
    sign-ins (``creds``: the only reference - the other parts read it here, so swapping it here reaches them all).
    ``save()`` is the app's: asked at call time."""

    def __init__(self, providers: Mapping[str, ModelProvider], saved: SavedState, creds: external.Credentials, *,
                 save: Callable[[], None]) -> None:
        self.providers = providers
        self.saved = saved
        self.creds = creds
        self._save = save

    def ext_p(self, p: str) -> dict[str, Any]:
        return self.saved.ext_settings.setdefault(p, {})

    @property
    def local(self) -> ModelProvider:
        """The local models' provider (Ollama)."""
        return next(prov for prov in self.providers.values() if prov.info.local)

    @property
    def external(self) -> dict[str, ProviderInfo]:
        """The external (online) providers' descriptions by key, in the settings' order."""
        return {p: prov.info for p, prov in self.providers.items() if not prov.info.local}

    @property
    def credit_p(self) -> str:
        """The provider whose plan counts AI credits (Copilot): the credit limit and the plan's quota are its."""
        return next(p for p, prov in self.providers.items() if prov.info.credits)

    def budget(self, p: str) -> external.Budget:
        return external.Budget(self.ext_p(p), self.saved.ext_usage.setdefault(p, {}))

    def auth(self, p: str) -> str:
        """How a provider is connected: "key", "signin" (Anthropic, GitHub), "chatgpt", or "" (not)."""
        return self.providers[p].auth(self.creds, self.ext_p(p).get("auth", ""))

    def models(self, p: str) -> list[dict[str, Any]]:
        ms = self.ext_p(p).get("models")
        return [m for m in ms if isinstance(m, dict) and m.get("id")] if isinstance(ms, list) else []

    def info(self, p: str, mid: str) -> dict[str, Any]:
        return next((m for m in self.models(p) if m["id"] == mid), {})

    def set_models(self, p: str, ms: list[dict[str, Any]]) -> None:
        self.ext_p(p)["models"] = ms
        for m in ms:
            external.NAMES[f"{p}:{m['id']}"] = m["name"]
        self._save()
