"""Shared pytest setup for Pixel Cat.

* Off Windows, :mod:`winshim` fakes the Windows-only parts of ``ctypes`` and
  ``winreg`` so every ``pixelcat`` module imports (see that module).
* Tests marked ``@pytest.mark.windows`` run only on Windows: they exercise the
  real Win32 / UI Automation / DPAPI / registry code.
* Tests marked ``@pytest.mark.macos`` run only on macOS (read-only pyobjc calls that
  never prompt); ``@pytest.mark.macos_live`` ones only on macOS with
  ``PIXELCAT_MACOS_LIVE=1`` (they move the mouse, press keys, change and restore the
  volume, use a throwaway Keychain service, write and remove a LaunchAgent).
* Nothing may ask the user for a macOS permission or touch the real Keychain items:
  every test runs with ``PIXELCAT_NO_PROMPTS=1`` and
  ``PIXELCAT_KEYCHAIN_SERVICE=PixelCat-tests-never``.
* Every test gets its own ``%APPDATA%``, ``%LOCALAPPDATA%``, home folder, Known
  Folders (Desktop, Documents ... answered from ``known_folders._known_cache``) and installer
  root (``PIXELCAT_TEST_ROOT``), so nothing touches the developer's real settings,
  saves, notes, Start menu or registry.
* On Windows the shell resolves Known Folders (Desktop, Documents, Start menu ...)
  once per process from the environment and keeps the answer.  So they are resolved
  with the real profile before the environment is changed, and `windows` tests that
  use the shell (shortcuts, Known Folders) ask for the ``real_profile`` fixture; those
  tests only read the real profile.
* Outgoing network connections are refused (localhost is allowed); mock HTTP.
* Qt runs headless (``QT_QPA_PLATFORM=offscreen``) off Windows.
"""
from __future__ import annotations

import ctypes
import locale
import os
import shutil
import socket
import sys
import tempfile
import types
import uuid
from pathlib import Path

import pytest

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
MACOS_LIVE = IS_MACOS and os.environ.get("PIXELCAT_MACOS_LIVE") == "1"

# No macOS permission prompts or settings panes, and never the real Keychain items (also for subprocesses).
TEST_ENV = {"PIXELCAT_NO_PROMPTS": "1", "PIXELCAT_KEYCHAIN_SERVICE": "PixelCat-tests-never"}
os.environ.update(TEST_ENV)

# Same as main.py: the app does its own DPI scaling.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")
if not IS_WINDOWS:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PROFILE_VARS = ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA")
REAL_PROFILE_ENV = {var: os.environ[var] for var in _PROFILE_VARS if var in os.environ}

# Windows Known Folders that are defined relative to the profile (see the module docstring).
_PROFILE_KNOWN_FOLDERS = (
    "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",  # Desktop
    "FDD39AD0-238F-46AF-ADB4-6C85480369C7",  # Documents
    "374DE290-123F-4565-9164-39C4925E467B",  # Downloads
    "33E28130-4E1E-4676-835A-98395C3BC3BB",  # Pictures
    "4BD8D571-6D19-48D3-BE97-422220080E43",  # Music
    "18989B1D-99B5-455B-841C-AB7C74E4DDFC",  # Videos
    "5E6C858F-0E22-4760-9AFE-EA3317B67173",  # Profile
    "3EB685DB-65F9-4CF6-A03A-E3EF65729F3D",  # RoamingAppData
    "F1B32785-6FBA-4FCF-9D55-7B8E7F157091",  # LocalAppData
    "5CD7AEE2-2219-4A67-B85D-6C9CE15660CB",  # UserProgramFiles
    "625B53C3-AB48-4EC1-BA1F-A1EF4146FC19",  # StartMenu
    "A77F5D77-2E2B-44C3-A6A2-ABA601054A51",  # Programs (Start menu)
    "B97D20BB-F46A-4C97-BA10-5E3608430854",  # Startup
)


def _resolve_known_folders_with_the_real_profile() -> None:
    import ctypes
    import uuid
    from ctypes import wintypes

    shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
    fn = shell32.SHGetKnownFolderPath
    fn.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    fn.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    for guid in _PROFILE_KNOWN_FOLDERS:
        g = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        out = ctypes.c_wchar_p()
        fn(ctypes.byref(g), 0, None, ctypes.byref(out))
        ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))


if IS_WINDOWS:
    try:
        _resolve_known_folders_with_the_real_profile()
    except Exception:  # noqa: BLE001 - only an optimisation for test isolation
        pass

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


def pytest_unconfigure(config: pytest.Config) -> None:
    shutil.rmtree(_SESSION_ROOT, ignore_errors=True)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "windows: needs real Windows (Win32, UIA, DPAPI, registry)")
    config.addinivalue_line("markers", "macos: needs real macOS (read-only pyobjc calls, never prompts)")
    config.addinivalue_line("markers", "macos_live: drives the real Mac; only with PIXELCAT_MACOS_LIVE=1")
    config.addinivalue_line("markers", "qt: needs a QApplication (pytest-qt qapp/qtbot)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_windows = pytest.mark.skip(reason="needs real Windows")
    skip_macos = pytest.mark.skip(reason="needs real macOS")
    skip_live = pytest.mark.skip(reason="drives the real Mac: needs macOS and PIXELCAT_MACOS_LIVE=1")
    for item in items:
        # the marker itself: item.keywords also holds folder names, and tests/adapters/windows is one
        if item.get_closest_marker("windows") and not IS_WINDOWS:
            item.add_marker(skip_windows)
        if item.get_closest_marker("macos") and not IS_MACOS:
            item.add_marker(skip_macos)
        if item.get_closest_marker("macos_live") and not MACOS_LIVE:
            item.add_marker(skip_live)


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
    for var, value in TEST_ENV.items():
        monkeypatch.setenv(var, value)
    # On Windows the shell would still answer with the developer's real Desktop, Documents,
    # Pictures ..., so the Known Folders are answered from the cache, inside tmp_path.
    from pixelcat.adapters.windows import known_folders
    known = {}
    for name in known_folders._KNOWN_FOLDER_IDS:
        known[name] = home / name.capitalize()
        known[name].mkdir()
    monkeypatch.setattr(known_folders, "_known_cache", known)
    # ... and the installer's layout() moves everything (Start menu, program folder,
    # registry keys) under a test root instead of asking the shell.
    monkeypatch.setenv("PIXELCAT_TEST_ROOT", str(tmp_path / "install-root"))
    return tmp_path


@pytest.fixture(autouse=True)
def qt_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with physical-pixel coordinates (``ui.geometry.set_qt_logical`` doesn't leak)."""
    from pixelcat.ui import geometry
    monkeypatch.setattr(geometry, "_QT_LOGICAL", False)


@pytest.fixture(autouse=True)
def c_locale() -> None:
    """On macOS/Linux creating a QApplication calls setlocale(LC_ALL, ""), which
    changes strftime's day/month names for every later test; Windows doesn't."""
    locale.setlocale(locale.LC_ALL, "C")


_real_connect = socket.socket.connect


_real_connect_ex = socket.socket.connect_ex


def _refuse_outside(address) -> None:  # type: ignore[no-untyped-def]
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
        raise OSError(f"tests must not use the network (tried to reach {address!r})")


def _guarded_connect(self: socket.socket, address, *args, **kwargs):  # type: ignore[no-untyped-def]
    _refuse_outside(address)
    return _real_connect(self, address, *args, **kwargs)


def _guarded_connect_ex(self: socket.socket, address, *args, **kwargs):  # type: ignore[no-untyped-def]
    _refuse_outside(address)
    return _real_connect_ex(self, address, *args, **kwargs)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)


@pytest.fixture
def real_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real profile variables again (USERPROFILE, HOME, APPDATA, LOCALAPPDATA), for
    `windows` tests that use the shell: shortcuts and Known Folders resolve against the
    environment.  Such tests must only read the profile, or write to their own temp
    folders - never into these real ones."""
    for var in _PROFILE_VARS:
        if var in REAL_PROFILE_ENV:
            monkeypatch.setenv(var, REAL_PROFILE_ENV[var])
        else:
            monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("PIXELCAT_TEST_ROOT", raising=False)
    from pixelcat.adapters.windows import known_folders
    monkeypatch.setattr(known_folders, "_known_cache", {})


@pytest.fixture
def fake_platform():  # type: ignore[no-untyped-def]
    """A :class:`fakes.platform.FakePlatform`: every port does nothing and records its calls."""
    from fakes.platform import FakePlatform

    return FakePlatform()


# ---------------------------------------------------------------- Known Folders (the Windows adapter's ctypes calls)
class FakeKnownFolders:
    """``ctypes.windll`` with ``shell32.SHGetKnownFolderPath`` / ``ole32.CoTaskMemFree``."""

    def __init__(self, fake_dll) -> None:  # type: ignore[no-untyped-def]
        self.folders: dict[str, str] = {}      # GUID (upper case) -> path
        self.hresult = 0
        self.asked: list[str] = []
        self.shell32 = fake_dll("shell32")
        self.ole32 = fake_dll("ole32")
        self.shell32.SHGetKnownFolderPath.result = self._get

    def _get(self, guid_ref, flags, token, out_ref) -> int:  # type: ignore[no-untyped-def]
        guid = str(uuid.UUID(bytes_le=bytes(guid_ref._obj))).upper()
        self.asked.append(guid)
        if self.hresult != 0:
            return self.hresult
        if guid not in self.folders:
            return -2147024894                 # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
        out_ref._obj.value = self.folders[guid]
        return 0

    @property
    def freed(self) -> int:
        return len(self.ole32.CoTaskMemFree.calls)


@pytest.fixture
def known_folders(monkeypatch: pytest.MonkeyPatch, fake_dll) -> FakeKnownFolders:  # type: ignore[no-untyped-def]
    fake = FakeKnownFolders(fake_dll)
    from pixelcat.adapters.windows import known_folders as windows_known_folders
    monkeypatch.setattr(windows_known_folders, "_known_cache", {})
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(shell32=fake.shell32, ole32=fake.ole32),
                        raising=False)
    return fake


@pytest.fixture
def no_windll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows APIs unavailable: any ``ctypes.windll`` use raises."""
    class _Broken:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            raise OSError(126, "The specified module could not be found")

    from pixelcat.adapters.windows import known_folders
    monkeypatch.setattr(known_folders, "_known_cache", {})
    monkeypatch.setattr(ctypes, "windll", _Broken(), raising=False)


# ---------------------------------------------------------------- real registry (Windows only)
@pytest.fixture
def test_reg_key():  # type: ignore[no-untyped-def]
    """A unique ``HKCU\\Software\\PixelCatTests\\<uuid>`` path, deleted with its subkeys afterwards."""
    import winreg

    base = r"Software\PixelCatTests"
    key = base + "\\" + uuid.uuid4().hex

    def delete_tree(path: str) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        sub = winreg.EnumKey(k, 0)
                    except OSError:
                        break
                    delete_tree(path + "\\" + sub)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass

    yield key
    delete_tree(key)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base)    # only succeeds when empty
    except OSError:
        pass


@pytest.fixture
def real_reg_read():  # type: ignore[no-untyped-def]
    import winreg

    def read(path: str, name: str):  # type: ignore[no-untyped-def]
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                return winreg.QueryValueEx(k, name)[0]
        except FileNotFoundError:
            return None

    return read


@pytest.fixture
def fake_dll():
    """Factory for a fake ctypes DLL: ``dll = fake_dll(); dll.Foo.result = 1``.

    Works the same on Windows, so mocked tests can run everywhere."""
    return winshim.FakeDLL
