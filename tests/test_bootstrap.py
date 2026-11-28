"""pixelcat.bootstrap: the composition root picks the adapters for the platform and the AI model providers."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pixelcat import bootstrap
from pixelcat.adapters import null
from pixelcat.ports import Platform

ROOT = Path(__file__).resolve().parents[1]


def test_windows_gets_the_windows_adapter():
    from pixelcat.adapters.windows import platform as windows

    plat = bootstrap.build_platform("win32")
    assert isinstance(plat, Platform) and plat.name == "windows"
    assert isinstance(plat.display, windows.WindowsDisplay) and isinstance(plat.secrets, windows.WindowsSecretStore)


@pytest.mark.parametrize("name", ["darwin", "linux"])
def test_other_platforms_get_the_null_adapter(name):  # type: ignore[no-untyped-def]
    plat = bootstrap.build_platform(name)
    assert isinstance(plat, Platform) and plat.name == "null"
    assert isinstance(plat.display, null.NullDisplay) and isinstance(plat.installer, null.NullInstaller)


def test_the_ai_model_providers_one_per_provider_in_the_settings_order():
    from pixelcat.adapters import llm

    providers = bootstrap.build_providers()
    assert list(providers) == ["anthropic", "openai", "copilot", "ollama"]
    assert [type(p) for p in providers.values()] == [llm.AnthropicProvider, llm.OpenAIProvider, llm.CopilotProvider,
                                                     llm.OllamaProvider]
    assert providers["ollama"].info.local and not providers["copilot"].info.local


def test_importing_it_loads_no_adapter():
    probe = "import sys, pixelcat.bootstrap\nprint('\\n'.join(sys.modules))\n"
    res = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert res.returncode == 0, res.stderr
    loaded = set(res.stdout.split())
    assert "pixelcat.ports" in loaded
    assert not {m for m in loaded if m.startswith("pixelcat.adapters")}
