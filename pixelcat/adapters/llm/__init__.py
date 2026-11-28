"""The AI model providers: one :class:`~pixelcat.ports.ModelProvider` each (Anthropic, OpenAI, GitHub Copilot,
the local Ollama).  Qt-free and light: each provider imports its client module (SDKs, requests...) only when a
method needs it."""
from __future__ import annotations

from ...ports.llm import ModelProvider
from .anthropic_provider import AnthropicProvider
from .copilot_provider import CopilotProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider


def build_providers() -> dict[str, ModelProvider]:
    """Every provider by its key, in the settings' order (the external ones first, then the local one)."""
    providers: list[ModelProvider] = [AnthropicProvider(), OpenAIProvider(), CopilotProvider(), OllamaProvider()]
    return {p.info.key: p for p in providers}


__all__ = ["AnthropicProvider", "CopilotProvider", "OllamaProvider", "OpenAIProvider", "build_providers"]
