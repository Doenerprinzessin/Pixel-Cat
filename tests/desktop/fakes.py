"""Fakes shared by the ``pixelcat.desktop`` tests (imported as ``desktop.fakes``)."""
from __future__ import annotations

import ctypes
import types
from typing import Any, Callable

import winshim

FakeDLL = winshim.FakeDLL


class FakeClock:
    """Stand-in for a module's ``time``: ``sleep`` advances a virtual clock instantly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[float], None] | None = None

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += max(0.0, s)
        if self.on_sleep is not None:
            self.on_sleep(s)

    def perf_counter(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now


def enum_windows(user32: FakeDLL, hwnds: list[int]) -> None:
    """Make ``user32.EnumWindows(proc, lparam)`` call ``proc`` for each of ``hwnds``."""

    def run(proc, lparam):  # type: ignore[no-untyped-def]
        for h in hwnds:
            if not proc(h, lparam):
                break
        return 1

    user32.EnumWindows.result = run


def by_hwnd(table: dict[int, Any], default: Any = 0) -> Callable[..., Any]:
    """FakeFunc result: look the first argument (an hwnd) up in ``table``."""
    return lambda h, *_a: table.get(int(h or 0), default)


def raising(exc: BaseException) -> Callable[..., Any]:
    """FakeFunc result that raises ``exc`` whatever the arguments."""
    def fn(*_a: Any) -> Any:
        raise exc
    return fn


def out(arg: Any) -> Any:
    """The ctypes object behind ``ctypes.byref(x)`` passed to a fake function."""
    return arg._obj


def fill_text(buf: Any, text: str) -> int:
    """Write ``text`` into a ``create_unicode_buffer`` passed to a fake (like GetWindowTextW)."""
    buf.value = text
    return len(text)


# ---------------------------------------------------------------- fake COM objects
class FakeCom:
    """A COM object in plain memory: a pointer to a vtable whose slots call Python.

    ``methods`` maps a vtable index to ``(argtypes, fn)``; ``fn`` gets the arguments
    after ``this`` and returns the HRESULT (``None`` = S_OK).  Slot 2 (Release) is
    counted in ``released``.  The callbacks use the x64/cdecl convention, which is
    what the code's WINFUNCTYPE calls use on 64-bit Windows and on macOS/Linux.
    """

    SLOTS = 80

    def __init__(self, methods: dict[int, tuple[tuple, Callable[..., Any]]] | None = None) -> None:
        self.released = 0
        self.calls: list[tuple[int, tuple]] = []
        self._keep: list[Any] = []
        self.vtbl = (ctypes.c_void_p * self.SLOTS)()
        methods = dict(methods or {})
        methods.setdefault(2, ((), self._release))
        for index, (argtypes, fn) in methods.items():
            restype = ctypes.c_ulong if index == 2 else ctypes.c_long
            cb = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(self._wrap(index, fn))
            self._keep.append(cb)
            self.vtbl[index] = ctypes.cast(cb, ctypes.c_void_p).value
        self.obj = (ctypes.c_void_p * 1)(ctypes.addressof(self.vtbl))
        self.ptr: int = ctypes.addressof(self.obj)

    def _wrap(self, index: int, fn: Callable[..., Any]) -> Callable[..., int]:
        def call(_this, *args):  # type: ignore[no-untyped-def]
            self.calls.append((index, args))
            return fn(*args) or 0
        return call

    def _release(self) -> int:
        self.released += 1
        return 0

    def called(self, index: int) -> list[tuple]:
        return [a for i, a in self.calls if i == index]


def write_ptr(address: int, value: int) -> None:
    """``*(void**)address = value`` (fill a COM out-parameter)."""
    ctypes.c_void_p.from_address(address).value = value


# ---------------------------------------------------------------- fake winreg
class FakeKey:
    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self) -> "FakeKey":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


class FakeWinreg(types.ModuleType):
    """In-memory ``winreg``: ``reg.add(r"HKCU\\Some\\Key", Name=value)``."""

    HKEY_CLASSES_ROOT = 0x80000000
    HKEY_CURRENT_USER = 0x80000001
    HKEY_LOCAL_MACHINE = 0x80000002
    KEY_READ, KEY_WRITE, KEY_SET_VALUE, KEY_QUERY_VALUE = 0x20019, 0x20006, 0x0002, 0x0001
    REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD, REG_QWORD = 1, 2, 3, 4, 11
    _ROOTS = {0x80000000: "hkcr", 0x80000001: "hkcu", 0x80000002: "hklm"}

    def __init__(self) -> None:
        super().__init__("winreg")
        self.keys: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple] = []

    def add(self, path: str, **values: Any) -> None:
        parts = path.lower().split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:i]), {})
        self.keys[path.lower()].update(values)

    def _path(self, root: Any, sub: str) -> str:
        base = root.path if isinstance(root, FakeKey) else self._ROOTS[root]
        return f"{base}\\{sub}".lower() if sub else base

    def OpenKey(self, root: Any, sub: str, reserved: int = 0, access: int = 0) -> FakeKey:
        path = self._path(root, sub)
        if path not in self.keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return FakeKey(path)

    def QueryValueEx(self, key: FakeKey, name: str) -> tuple[Any, int]:
        for k, v in self.keys[key.path].items():
            if k.lower() == name.lower():
                if isinstance(v, BaseException):
                    raise v
                return v, 1
        raise FileNotFoundError(2, "The system cannot find the file specified")

    def EnumKey(self, key: FakeKey, index: int) -> str:
        prefix = key.path + "\\"
        subs = sorted({p[len(prefix):].split("\\")[0] for p in self.keys if p.startswith(prefix)})
        if index >= len(subs):
            raise OSError(259, "No more data is available")
        return subs[index]

    def SetValueEx(self, key: FakeKey, name: str, reserved: int, kind: int, value: Any) -> None:
        self.writes.append(("set", key.path, name, kind, value))
        self.keys[key.path][name] = value

    def DeleteValue(self, key: FakeKey, name: str) -> None:
        values = self.keys[key.path]
        if name not in values:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        self.writes.append(("delete", key.path, name))
        del values[name]
