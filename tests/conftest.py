"""Shared pytest setup for Pixel Cat.

* Off Windows, :mod:`winshim` fakes the Windows-only parts of ``ctypes`` and
  ``winreg`` so every ``pixelcat`` module imports (see that module).
* Tests marked ``@pytest.mark.windows`` run only on Windows: they exercise the
  real Win32 / UI Automation / DPAPI / registry code.
* Every test gets its own ``%APPDATA%``, ``%LOCALAPPDATA%`` and home folder, so
  nothing touches the developer's real settings, saves or notes.
* Outgoing network connections are refused (localhost is allowed); mock HTTP.
* Qt runs headless (``QT_QPA_PLATFORM=offscreen``) off Windows.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

IS_WINDOWS = sys.platform == "win32"

# Same as main.py: the app does its own DPI scaling.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")
if not IS_WINDOWS:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Point the data folders somewhere harmless before any pixelcat module is imported.
_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="pixelcat-tests-"))
for _var in ("APPDATA", "LOCALAPPDATA"):
    os.environ[_var] = str(_SESSION_ROOT / _var)

# Import the heavy third-party packages before the shim adds fake Windows
# attributes to ctypes, so they never take a Windows code path by mistake.
import PySide6.QtWidgets  # noqa: E402,F401

for _mod in ("requests", "anthropic", "copilot"):
    try:
        __import__(_mod)
    except ImportError:  # pragma: no cover - optional in a minimal env
        pass

import winshim  # noqa: E402

winshim.install()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "windows: needs real Windows (Win32, UIA, DPAPI, registry)")
    config.addinivalue_line("markers", "qt: needs a QApplication (pytest-qt qapp/qtbot)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if IS_WINDOWS:
        return
    skip = pytest.mark.skip(reason="needs real Windows")
    for item in items:
        if "windows" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh ``%APPDATA%``, ``%LOCALAPPDATA%`` and home for each test."""
    home = tmp_path / "home"
    for var, path in (("APPDATA", tmp_path / "AppData" / "Roaming"),
                      ("LOCALAPPDATA", tmp_path / "AppData" / "Local"),
                      ("HOME", home), ("USERPROFILE", home)):
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(path))
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    from pixelcat import paths
    monkeypatch.setattr(paths, "_known_cache", {})
    return tmp_path


_real_connect = socket.socket.connect


def _guarded_connect(self: socket.socket, address, *args, **kwargs):  # type: ignore[no-untyped-def]
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
        raise OSError(f"tests must not use the network (tried to reach {address!r})")
    return _real_connect(self, address, *args, **kwargs)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


@pytest.fixture
def fake_dll():
    """Factory for a fake ctypes DLL: ``dll = fake_dll(); dll.Foo.result = 1``.

    Works the same on Windows, so mocked tests can run everywhere."""
    return winshim.FakeDLL
