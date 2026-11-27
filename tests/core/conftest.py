"""Fixtures for the top-level pixelcat modules (save, paths, installer, settings ...).

The core gets its storage ports passed in; the fakes for them are here:

* ``dpapi``         - a :class:`~pixelcat.ports.SecretStore`: reversible, tamper-checking and
  bound to an "account" (pass it as ``secrets=``).
* ``save_marks``    - a :class:`~pixelcat.ports.SaveMarks` in memory (pass it as ``marks=``).

and for the Windows-only installer, which still calls the platform itself:

* ``fake_winreg``   - an in-memory registry installed as ``sys.modules["winreg"]``
  (the modules do ``import winreg`` inside their functions, so this also replaces
  the real one on Windows).
* ``win_dpapi``     - ``dpapi`` behind ``pixelcat.adapters.windows.dpapi``.
* ``known_folders`` / ``no_windll`` (in tests/conftest.py) - a fake ``ctypes.windll``.
"""
from __future__ import annotations

import hashlib
import hmac
import sys
import types

import pytest

from pixelcat import savecrypt

# ---------------------------------------------------------------- registry
class _Key:
    def __init__(self, reg: "FakeRegistry", path: tuple[int, str]):
        self.reg, self.path, self.closed = reg, path, False

    def __enter__(self) -> "_Key":
        return self

    def __exit__(self, *exc) -> None:
        self.closed = True

    def Close(self) -> None:
        self.closed = True


class FakeRegistry(types.ModuleType):
    """A tiny case-insensitive ``winreg``: keys hold ``{name: (value, type)}``."""

    HKEY_CLASSES_ROOT = 0x80000000
    HKEY_CURRENT_USER = 0x80000001
    HKEY_LOCAL_MACHINE = 0x80000002
    KEY_QUERY_VALUE, KEY_SET_VALUE, KEY_READ, KEY_WRITE = 0x0001, 0x0002, 0x20019, 0x20006
    KEY_ALL_ACCESS, KEY_WOW64_64KEY, KEY_WOW64_32KEY = 0xF003F, 0x0100, 0x0200
    REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD, REG_MULTI_SZ, REG_QWORD = 1, 2, 3, 4, 7, 11
    HKEYType = _Key

    def __init__(self) -> None:
        super().__init__("winreg")
        self.keys: dict[tuple[int, str], dict[str, tuple[str, object, int]]] = {}
        self.fail: type[OSError] | None = None   # make every call raise this

    @staticmethod
    def _norm(path: str) -> str:
        return path.strip("\\").lower()

    def _full(self, key, sub: str) -> tuple[int, str]:  # type: ignore[no-untyped-def]
        if self.fail is not None:
            raise self.fail(5, "Access is denied")
        if isinstance(key, _Key):
            hive, base = key.path
            return hive, self._norm(base + "\\" + sub if sub else base)
        return key, self._norm(sub)

    # ---- for the tests
    def values(self, path: str, hive: int = HKEY_CURRENT_USER) -> dict[str, object]:
        """Values of a key by their real names ({} if the key is missing)."""
        return {n: v for n, v, _t in self.keys.get((hive, self._norm(path)), {}).values()}

    def types_of(self, path: str, hive: int = HKEY_CURRENT_USER) -> dict[str, int]:
        return {n: t for n, _v, t in self.keys.get((hive, self._norm(path)), {}).values()}

    def has_key(self, path: str, hive: int = HKEY_CURRENT_USER) -> bool:
        return (hive, self._norm(path)) in self.keys

    def put(self, path: str, name: str, value, typ: int = REG_SZ,  # type: ignore[no-untyped-def]
            hive: int = HKEY_CURRENT_USER) -> None:
        with self.CreateKeyEx(hive, path) as k:
            self.SetValueEx(k, name, 0, typ, value)

    # ---- the winreg API
    def OpenKey(self, key, sub_key, reserved=0, access=KEY_READ) -> _Key:  # type: ignore[no-untyped-def]
        full = self._full(key, sub_key)
        if full not in self.keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return _Key(self, full)

    OpenKeyEx = OpenKey

    def CreateKeyEx(self, key, sub_key, reserved=0, access=KEY_WRITE) -> _Key:  # type: ignore[no-untyped-def]
        hive, path = self._full(key, sub_key)
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault((hive, "\\".join(parts[:i])), {})
        return _Key(self, (hive, path))

    def CreateKey(self, key, sub_key) -> _Key:  # type: ignore[no-untyped-def]
        return self.CreateKeyEx(key, sub_key)

    def QueryValueEx(self, key: _Key, name: str):  # type: ignore[no-untyped-def]
        self._full(key, "")
        try:
            _n, value, typ = self.keys[key.path][name.lower()]
        except KeyError:
            raise FileNotFoundError(2, "The system cannot find the file specified") from None
        return value, typ

    def SetValueEx(self, key: _Key, name: str, reserved, typ: int, value) -> None:  # type: ignore[no-untyped-def]
        self._full(key, "")
        self.keys[key.path][name.lower()] = (name, value, typ)

    def DeleteValue(self, key: _Key, name: str) -> None:
        self._full(key, "")
        if self.keys[key.path].pop(name.lower(), None) is None:
            raise FileNotFoundError(2, "The system cannot find the file specified")

    def DeleteKey(self, key, sub_key) -> None:  # type: ignore[no-untyped-def]
        full = self._full(key, sub_key)
        if full not in self.keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        if any(h == full[0] and p.startswith(full[1] + "\\") for h, p in self.keys):
            raise PermissionError(5, "Access is denied")       # as Windows: the key has subkeys
        del self.keys[full]

    def EnumKey(self, key: _Key, index: int) -> str:
        hive, base = self._full(key, "")
        subs = sorted({p[len(base) + 1:].split("\\")[0] for h, p in self.keys
                       if h == hive and p.startswith(base + "\\")})
        if index >= len(subs):
            raise OSError(259, "No more data is available")
        return subs[index]

    def CloseKey(self, key) -> None:  # type: ignore[no-untyped-def]
        if isinstance(key, _Key):
            key.Close()


@pytest.fixture
def fake_winreg(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    reg = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    return reg


# ---------------------------------------------------------------- DPAPI
class FakeDpapi:
    """Stands in for Windows data protection: readable only by the same ``account``,
    a changed byte is refused, and the result isn't plain text."""

    def __init__(self) -> None:
        self.account = b"this account"
        self.protect_error: BaseException | None = None
        self.unprotect_error: BaseException | None = None
        self.protected = 0

    def protect(self, data: bytes) -> bytes:
        if self.protect_error is not None:
            raise self.protect_error
        self.protected += 1
        mac = hmac.new(self.account, data, hashlib.sha256).digest()
        return b"FAKEDPAPI" + mac + bytes(b ^ 0xA5 for b in data)

    def unprotect(self, blob: bytes) -> bytes:
        if self.unprotect_error is not None:
            raise self.unprotect_error
        if not blob.startswith(b"FAKEDPAPI") or len(blob) < 9 + 32:
            raise savecrypt.Unreadable("not protected")
        mac, body = blob[9:41], blob[41:]
        data = bytes(b ^ 0xA5 for b in body)
        if not hmac.compare_digest(mac, hmac.new(self.account, data, hashlib.sha256).digest()):
            raise savecrypt.Unreadable("Windows refused to open it (error 13)")
        return data


@pytest.fixture
def dpapi() -> FakeDpapi:
    return FakeDpapi()


@pytest.fixture
def win_dpapi(monkeypatch: pytest.MonkeyPatch, dpapi: FakeDpapi) -> FakeDpapi:
    """``dpapi`` behind the Windows adapter's own functions (for the installer, which uses that adapter)."""
    from pixelcat.adapters.windows import dpapi as windows_dpapi

    monkeypatch.setattr(windows_dpapi, "protect", dpapi.protect)
    monkeypatch.setattr(windows_dpapi, "unprotect", dpapi.unprotect)
    return dpapi


# ---------------------------------------------------------------- save marks
class FakeSaveMarks:
    """The protected-save marks in memory: ``marked`` maps folder ids to 1 (as the registry
    values); with ``fail`` set every call raises it (like winreg without access)."""

    def __init__(self) -> None:
        self.marked: dict[str, int] = {}
        self.fail: type[OSError] | None = None

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail(5, "Access is denied")

    def is_marked(self, folder_id: str) -> bool:
        self._check()
        if folder_id not in self.marked:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return bool(self.marked[folder_id])

    def mark(self, folder_id: str) -> None:
        self._check()
        self.marked[folder_id] = 1

    def forget(self, folder_id: str) -> None:
        self._check()
        if self.marked.pop(folder_id, None) is None:
            raise FileNotFoundError(2, "The system cannot find the file specified")


@pytest.fixture
def save_marks() -> FakeSaveMarks:
    return FakeSaveMarks()
