"""Fixtures for the top-level pixelcat modules (save, paths, installer, settings ...).

The Windows seams used by these modules are faked here, in one place:

* ``fake_winreg``   - an in-memory registry installed as ``sys.modules["winreg"]``
  (the modules do ``import winreg`` inside their functions, so this also replaces
  the real one on Windows).
* ``dpapi``         - replaces ``savecrypt.protect`` / ``savecrypt.unprotect`` with a
  reversible, tamper-checking fake bound to an "account".
* ``known_folders`` - a fake ``ctypes.windll`` whose ``SHGetKnownFolderPath`` answers
  from a GUID -> path table (``no_windll``: every Windows API call fails).

When the refactor moves these behind ports, only this file should need updating.
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import sys
import types
import uuid

import pytest

from pixelcat import paths, savecrypt

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
def dpapi(monkeypatch: pytest.MonkeyPatch) -> FakeDpapi:
    fake = FakeDpapi()
    monkeypatch.setattr(savecrypt, "protect", fake.protect)
    monkeypatch.setattr(savecrypt, "unprotect", fake.unprotect)
    return fake


# ---------------------------------------------------------------- Known Folders
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
    monkeypatch.setattr(paths, "_known_cache", {})
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(shell32=fake.shell32, ole32=fake.ole32),
                        raising=False)
    return fake


@pytest.fixture
def no_windll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows APIs unavailable: any ``ctypes.windll`` use raises."""
    class _Broken:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            raise OSError(126, "The specified module could not be found")

    monkeypatch.setattr(paths, "_known_cache", {})
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
