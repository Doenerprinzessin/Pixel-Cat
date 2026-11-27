"""Every pixelcat module imports (on macOS/Linux with the Windows shim from conftest)."""
from __future__ import annotations

import importlib
import pkgutil

import pytest

import pixelcat

MODULES = sorted(m.name for m in pkgutil.walk_packages(pixelcat.__path__, "pixelcat."))


def test_found_the_packages():
    for pkg in ("desktop", "engine", "games", "llm", "sprites", "ui"):
        assert f"pixelcat.{pkg}" in MODULES
    assert "pixelcat.app" in MODULES


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)
