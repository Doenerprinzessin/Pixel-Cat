"""The composition root: the only place that picks the adapters.

``main`` builds the :class:`~pixelcat.ports.Platform` and the AI model providers here and hands them to the app,
which passes the ports on to its parts.  Nothing else in the core imports ``pixelcat.adapters``.
"""
from __future__ import annotations

import importlib.util
import logging
import sys

from .ports import ModelProvider, Platform

log = logging.getLogger(__name__)


def build_platform(platform: str = sys.platform) -> Platform:
    """The ports for ``platform`` (a ``sys.platform`` value): Windows, macOS (with pyobjc), or the null adapter
    elsewhere."""
    if platform == "win32":
        from .adapters.windows import platform as windows

        return windows.build()
    if platform == "darwin":
        if importlib.util.find_spec("objc") is not None:
            from .adapters.macos import platform as macos

            return macos.build()
        log.warning("pyobjc is not installed (pip install -r requirements.txt): the cat can't see or use the "
                    "desktop on this Mac")
    from .adapters import null

    return null.build()


def build_providers() -> dict[str, ModelProvider]:
    """The AI model providers by key: Anthropic, OpenAI, GitHub Copilot, then the local Ollama."""
    from .adapters import llm

    return llm.build_providers()
