"""savecrypt: the signed format of exports (seal/unseal) and Windows data protection (DPAPI)."""
from __future__ import annotations

import ctypes
import random
from pathlib import Path

import pytest

from pixelcat import savecrypt

DATA = Path(__file__).parent / "data"


def _rand(n: int, seed: int = 7) -> bytes:
    return random.Random(seed).randbytes(n)


# ---------------------------------------------------------------- seal / unseal
@pytest.mark.parametrize("plain", [b"", b"x", b"hello cat", "Grüße 🐾".encode(), _rand(100_000), b"\0" * 50_000],
                         ids=["empty", "one", "text", "unicode", "random-100k", "zeros-50k"])
def test_seal_round_trip(plain: bytes) -> None:
    assert savecrypt.unseal(savecrypt.seal(plain)) == plain


def test_seal_is_not_readable_and_differs_each_time() -> None:
    plain = b'{"xp":900,"skin":"ginger"}'
    a, b = savecrypt.seal(plain), savecrypt.seal(plain)
    assert a != b                                  # a fresh nonce every time
    assert b"ginger" not in a and b"xp" not in a


def test_seal_layout_is_nonce_ciphertext_tag() -> None:
    import zlib

    plain = b"pixel cat " * 20
    blob = savecrypt.seal(plain)
    assert len(blob) == 16 + len(zlib.compress(plain, 9)) + 32


@pytest.mark.parametrize("where", [0, 15, 16, -33, -1], ids=["nonce-first", "nonce-last", "ct-first", "ct-last", "tag"])
def test_unseal_refuses_any_changed_byte(where: int) -> None:
    blob = bytearray(savecrypt.seal(b"the Cat Pass is earned by playing"))
    blob[where] ^= 0x01
    with pytest.raises(savecrypt.Unreadable, match="changed"):
        savecrypt.unseal(bytes(blob))


@pytest.mark.parametrize("blob", [b"", b"x" * 47], ids=["empty", "47-bytes"])
def test_unseal_refuses_too_short(blob: bytes) -> None:
    with pytest.raises(savecrypt.Unreadable, match="too short"):
        savecrypt.unseal(blob)


def test_unseal_refuses_truncated_or_extended() -> None:
    blob = savecrypt.seal(b"some save data" * 10)
    for bad in (blob[:-1], blob + b"\0", blob[1:]):
        with pytest.raises(savecrypt.Unreadable):
            savecrypt.unseal(bad)


def test_unseal_refuses_more_than_max_plain() -> None:
    blob = savecrypt.seal(b"\0" * (savecrypt.MAX_PLAIN + 1))
    with pytest.raises(savecrypt.Unreadable, match="too big"):
        savecrypt.unseal(blob)


def test_unseal_accepts_exactly_max_plain() -> None:
    assert len(savecrypt.unseal(savecrypt.seal(b"\0" * savecrypt.MAX_PLAIN))) == savecrypt.MAX_PLAIN


def test_unreadable_is_an_exception_not_an_oserror() -> None:
    # callers catch (OSError, Unreadable) separately; DPAPI failures on protect are OSError
    assert not issubclass(savecrypt.Unreadable, OSError)


def test_golden_sealed_blob_still_opens() -> None:
    """Sealed with the code of PR 0: the app key and format must never change, or every
    export users have made becomes unreadable."""
    assert savecrypt.unseal((DATA / "sealed_v1.bin").read_bytes()) == b"Pixel Cat golden vector \x00\x01\x02 :3"


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
    monkeypatch.setattr(savecrypt, "_Blob", _Blob)
    monkeypatch.setattr(savecrypt, "_api", (fake.crypt, fake.kernel))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 13, raising=False)
    return fake


def test_protect_and_unprotect_call_windows_without_prompts(crypt32: FakeCrypt32) -> None:
    blob = savecrypt.protect(b"hello cat")
    assert savecrypt.unprotect(blob) == b"hello cat"
    prot, unprot = crypt32.calls
    assert (prot["kind"], prot["desc"], prot["data"]) == ("protect", "Pixel Cat save", b"hello cat")
    assert (unprot["kind"], unprot["desc"], unprot["data"]) == ("unprotect", None, blob)
    assert prot["flags"] == unprot["flags"] == 0x1           # CRYPTPROTECT_UI_FORBIDDEN
    assert prot["entropy"] == unprot["entropy"] and len(prot["entropy"]) == 32


def test_protect_frees_what_windows_allocated(crypt32: FakeCrypt32) -> None:
    savecrypt.protect(b"abc")
    savecrypt.unprotect(savecrypt.protect(b"def"))
    assert crypt32.freed() == crypt32.addresses()


def test_protect_failure_is_an_oserror(crypt32: FakeCrypt32) -> None:
    crypt32.ok = False
    with pytest.raises(OSError) as e:
        savecrypt.protect(b"x")
    assert e.value.errno == 13
    assert crypt32.freed() == []


def test_unprotect_failure_is_unreadable(crypt32: FakeCrypt32) -> None:
    crypt32.ok = False
    with pytest.raises(savecrypt.Unreadable, match="error 13"):
        savecrypt.unprotect(b"\x01\x02\x03")


# ---------------------------------------------------------------- real DPAPI
@pytest.mark.windows
def test_real_dpapi_round_trip_and_not_plain() -> None:
    blob = savecrypt.protect(b"hello cat, xp 900")
    assert b"hello cat" not in blob
    assert savecrypt.unprotect(blob) == b"hello cat, xp 900"


@pytest.mark.windows
def test_real_dpapi_refuses_a_changed_byte() -> None:
    blob = bytearray(savecrypt.protect(b"hello cat"))
    blob[len(blob) // 2] ^= 1
    with pytest.raises(savecrypt.Unreadable):
        savecrypt.unprotect(bytes(blob))


@pytest.mark.windows
def test_real_dpapi_refuses_other_entropy(monkeypatch: pytest.MonkeyPatch) -> None:
    real = savecrypt._ENTROPY
    monkeypatch.setattr(savecrypt, "_ENTROPY", b"another account or PC")
    blob = savecrypt.protect(b"hello cat")
    monkeypatch.setattr(savecrypt, "_ENTROPY", real)
    with pytest.raises(savecrypt.Unreadable):
        savecrypt.unprotect(blob)


@pytest.mark.windows
def test_real_dpapi_large_payload() -> None:
    plain = _rand(500_000, seed=3)
    assert savecrypt.unprotect(savecrypt.protect(plain)) == plain
