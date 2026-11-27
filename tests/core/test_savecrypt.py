"""savecrypt: the signed format of exports (seal/unseal); Windows data protection is in
tests/adapters/windows/test_dpapi.py."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from pixelcat import domain, savecrypt

DATA = Path(__file__).parent / "data"


def _rand(n: int, seed: int = 7) -> bytes:
    return random.Random(seed).randbytes(n)


def test_unreadable_is_the_domain_error() -> None:
    assert savecrypt.Unreadable is domain.Unreadable


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
