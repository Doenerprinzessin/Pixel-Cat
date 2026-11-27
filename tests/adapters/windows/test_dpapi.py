"""pixelcat.adapters.windows.dpapi: Windows data protection (CryptProtectData) for the save, chats and keys."""
from __future__ import annotations

import ctypes
import random

import pytest

from pixelcat import savecrypt
from pixelcat.adapters.windows import dpapi
from pixelcat.domain import Unreadable


def _rand(n: int, seed: int = 7) -> bytes:
    return random.Random(seed).randbytes(n)


# ---------------------------------------------------------------- DPAPI calls (mocked)
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class FakeCrypt32:
    """CryptProtectData / CryptUnprotectData that XOR with the entropy, plus LocalFree."""

    def __init__(self, fake_dll) -> None:  # type: ignore[no-untyped-def]
        self.crypt = fake_dll("crypt32")
        self.kernel = fake_dll("kernel32")
        self.ok = True
        self.calls: list[dict] = []
        self._keep: list = []
        self.crypt.CryptProtectData.result = lambda *a: self._call("protect", *a)
        self.crypt.CryptUnprotectData.result = lambda *a: self._call("unprotect", *a)

    def _call(self, kind, src_ref, desc, ent_ref, reserved, prompt, flags, out_ref):  # type: ignore[no-untyped-def]
        src, ent = src_ref._obj, ent_ref._obj
        data = ctypes.string_at(src.pbData, src.cbData)
        entropy = ctypes.string_at(ent.pbData, ent.cbData)
        self.calls.append({"kind": kind, "desc": desc, "entropy": entropy, "flags": flags, "data": data})
        if not self.ok:
            return 0
        result = bytes(b ^ entropy[i % len(entropy)] for i, b in enumerate(data))
        buf = ctypes.create_string_buffer(result, max(1, len(result)))
        self._keep.append(buf)
        out_ref._obj.cbData = len(result)
        out_ref._obj.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        return 1

    def freed(self) -> list:
        return [c[0] for c in self.kernel.LocalFree.calls]

    def addresses(self) -> list[int]:
        return [ctypes.addressof(b) for b in self._keep]


@pytest.fixture
def crypt32(monkeypatch: pytest.MonkeyPatch, fake_dll) -> FakeCrypt32:  # type: ignore[no-untyped-def]
    fake = FakeCrypt32(fake_dll)
    monkeypatch.setattr(dpapi, "_Blob", _Blob)
    monkeypatch.setattr(dpapi, "_api", (fake.crypt, fake.kernel))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 13, raising=False)
    return fake


def test_protect_and_unprotect_call_windows_without_prompts(crypt32: FakeCrypt32) -> None:
    blob = dpapi.protect(b"hello cat")
    assert dpapi.unprotect(blob) == b"hello cat"
    prot, unprot = crypt32.calls
    assert (prot["kind"], prot["desc"], prot["data"]) == ("protect", "Pixel Cat save", b"hello cat")
    assert (unprot["kind"], unprot["desc"], unprot["data"]) == ("unprotect", None, blob)
    assert prot["flags"] == unprot["flags"] == 0x1           # CRYPTPROTECT_UI_FORBIDDEN
    assert prot["entropy"] == unprot["entropy"] and len(prot["entropy"]) == 32
    assert prot["entropy"] == savecrypt._ENTROPY             # the app's own, or old saves can't be opened


def test_protect_frees_what_windows_allocated(crypt32: FakeCrypt32) -> None:
    dpapi.protect(b"abc")
    dpapi.unprotect(dpapi.protect(b"def"))
    assert crypt32.freed() == crypt32.addresses()


def test_protect_failure_is_an_oserror(crypt32: FakeCrypt32) -> None:
    crypt32.ok = False
    with pytest.raises(OSError) as e:
        dpapi.protect(b"x")
    assert e.value.errno == 13
    assert crypt32.freed() == []


def test_unprotect_failure_is_unreadable(crypt32: FakeCrypt32) -> None:
    crypt32.ok = False
    with pytest.raises(Unreadable, match="error 13"):
        dpapi.unprotect(b"\x01\x02\x03")


# ---------------------------------------------------------------- real DPAPI
@pytest.mark.windows
def test_real_dpapi_round_trip_and_not_plain() -> None:
    blob = dpapi.protect(b"hello cat, xp 900")
    assert b"hello cat" not in blob
    assert dpapi.unprotect(blob) == b"hello cat, xp 900"


@pytest.mark.windows
def test_real_dpapi_refuses_a_changed_byte() -> None:
    blob = bytearray(dpapi.protect(b"hello cat"))
    blob[len(blob) // 2] ^= 1
    with pytest.raises(Unreadable):
        dpapi.unprotect(bytes(blob))


@pytest.mark.windows
def test_real_dpapi_refuses_other_entropy(monkeypatch: pytest.MonkeyPatch) -> None:
    real = savecrypt._ENTROPY
    monkeypatch.setattr(savecrypt, "_ENTROPY", b"another account or PC")
    blob = dpapi.protect(b"hello cat")
    monkeypatch.setattr(savecrypt, "_ENTROPY", real)
    with pytest.raises(Unreadable):
        dpapi.unprotect(blob)


@pytest.mark.windows
def test_real_dpapi_large_payload() -> None:
    plain = _rand(500_000, seed=3)
    assert dpapi.unprotect(dpapi.protect(plain)) == plain
