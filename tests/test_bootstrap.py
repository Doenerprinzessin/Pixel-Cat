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


def test_macos_gets_the_macos_adapter(monkeypatch):  # type: ignore[no-untyped-def]
    from pixelcat.adapters.macos import platform as macos
    from pixelcat.adapters.macos.profile import MAC

    real = bootstrap.importlib.util.find_spec      # (pyobjc there or not: the adapter loads without it)
    monkeypatch.setattr(bootstrap.importlib.util, "find_spec",
                        lambda name, *a: object() if name == "objc" else real(name, *a))
    plat = bootstrap.build_platform("darwin")
    assert isinstance(plat, Platform) and plat.name == "macos" and plat.profile is MAC
    assert isinstance(plat.display, macos.MacDisplay) and isinstance(plat.secrets, macos.MacSecretStore)


def test_macos_without_pyobjc_gets_the_null_adapter(monkeypatch, caplog):  # type: ignore[no-untyped-def]
    real = bootstrap.importlib.util.find_spec
    monkeypatch.setattr(bootstrap.importlib.util, "find_spec",
                        lambda name, *a: None if name == "objc" else real(name, *a))
    plat = bootstrap.build_platform("darwin")
    assert plat.name == "null" and isinstance(plat.display, null.NullDisplay)
    assert "pyobjc is not installed" in caplog.text


@pytest.mark.parametrize("name", ["linux", "freebsd14"])
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
