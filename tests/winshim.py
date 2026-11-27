"""Test-only stand-ins for the Windows parts of ``ctypes`` and ``winreg``.

Pixel Cat's ``adapters/windows/*`` modules call ``ctypes.WINFUNCTYPE`` / ``ctypes.windll`` /
``ctypes.WinDLL`` at import time, so on macOS and Linux they (and everything that
imports them: engine, games, ui, app) can't even be imported.  :func:`install`
adds harmless fakes so the modules import; every fake DLL function returns ``0``
unless a test replaces it.  Production code is untouched, and nothing here runs
on Windows - there the real APIs are used.
"""
from __future__ import annotations

import ctypes
import sys
import types

INSTALLED = False


class FakeFunc:
    """A DLL export: accepts ``argtypes``/``restype`` and returns ``result`` when called."""

    def __init__(self, name: str, result=0):
        self.__name__ = name
        self.argtypes = None
        self.restype = None
        self.errcheck = None
        self.result = result
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result(*args) if callable(self.result) else self.result


class FakeDLL:
    """Any attribute is a :class:`FakeFunc` (cached, so argtypes stick)."""

    def __init__(self, name: str = "", *args, **kwargs):
        self._name = name

    def __getattr__(self, name: str) -> FakeFunc:
        if name.startswith("__"):
            raise AttributeError(name)
        fn = FakeFunc(name)
        setattr(self, name, fn)
        return fn

    def __getitem__(self, name: str) -> FakeFunc:
        return getattr(self, name)


class FakeLoader:
    """``ctypes.windll`` / ``ctypes.oledll``: ``loader.user32`` -> a shared FakeDLL."""

    def __init__(self):
        self._dlls: dict[str, FakeDLL] = {}

    def __getattr__(self, name: str) -> FakeDLL:
        if name.startswith("__") or name == "_dlls":
            raise AttributeError(name)
        return self._dlls.setdefault(name, FakeDLL(name))

    def LoadLibrary(self, name: str) -> FakeDLL:
        return getattr(self, name)


def _winerror(code=None, descr=None):
    code = 0 if code is None else code
    return OSError(None, descr or f"[WinError {code}]", None, code)


def _make_winreg() -> types.ModuleType:
    """A ``winreg`` whose every key is missing (reads raise ``FileNotFoundError``)."""
    m = types.ModuleType("winreg")
    m.__dict__.update(
        HKEY_CURRENT_USER=0x80000001, HKEY_LOCAL_MACHINE=0x80000002, HKEY_CLASSES_ROOT=0x80000000,
        KEY_READ=0x20019, KEY_WRITE=0x20006, KEY_ALL_ACCESS=0xF003F, KEY_SET_VALUE=0x0002,
        KEY_QUERY_VALUE=0x0001, KEY_WOW64_64KEY=0x0100, KEY_WOW64_32KEY=0x0200,
        REG_SZ=1, REG_EXPAND_SZ=2, REG_BINARY=3, REG_DWORD=4, REG_MULTI_SZ=7, REG_QWORD=11,
    )

    def _missing(*a, **k):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    for name in ("OpenKey", "OpenKeyEx", "QueryValueEx", "QueryValue", "EnumKey", "EnumValue",
                 "DeleteKey", "DeleteKeyEx", "DeleteValue", "QueryInfoKey",
                 "CreateKey", "CreateKeyEx", "ConnectRegistry"):
        setattr(m, name, _missing)
    m.SetValueEx = lambda *a, **k: None
    m.SetValue = lambda *a, **k: None
    m.CloseKey = lambda *a, **k: None
    m.ExpandEnvironmentStrings = lambda s: s
    m.HKEYType = object
    return m


def install() -> None:
    """Install the fakes (idempotent; a no-op on Windows)."""
    global INSTALLED
    if INSTALLED or sys.platform == "win32":
        return
    INSTALLED = True
    if not hasattr(ctypes, "WINFUNCTYPE"):
        ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    if not hasattr(ctypes, "HRESULT"):
        ctypes.HRESULT = ctypes.c_long
    for name, value in (("WinDLL", FakeDLL), ("OleDLL", FakeDLL), ("windll", FakeLoader()),
                        ("oledll", FakeLoader()), ("WinError", _winerror),
                        ("FormatError", lambda code=0: f"error {code}"),
                        ("GetLastError", lambda: 0), ("get_last_error", lambda: 0),
                        ("set_last_error", lambda v: 0)):
        if not hasattr(ctypes, name):
            setattr(ctypes, name, value)
    sys.modules.setdefault("winreg", _make_winreg())
    # win32.py sets its DLL handles to None off Windows, but sibling modules
    # declare argtypes on them at import time - give them fake DLLs instead.
    from pixelcat.adapters.windows import win32
    for name in ("user32", "kernel32", "dwmapi", "shell32", "shcore"):
        if getattr(win32, name) is None:
            setattr(win32, name, ctypes.windll.LoadLibrary(name))
