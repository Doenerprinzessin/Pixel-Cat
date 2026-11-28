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


# ---------------------------------------------------------------- seal_keyed / unseal_keyed (per-user keys)
KEYS = (b"e" * 32, b"m" * 32)


def test_seal_is_seal_keyed_with_the_app_keys(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(savecrypt.os, "urandom", lambda n: b"\x07" * n)
    plain = b"pixel cat " * 20
    assert savecrypt.seal(plain) == savecrypt.seal_keyed(savecrypt._ENC_KEY, savecrypt._MAC_KEY, plain)


@pytest.mark.parametrize("header", [b"", b"PXK1" + b"\x01" * 8])
def test_seal_keyed_round_trip(header: bytes) -> None:
    plain = _rand(50_000)
    blob = savecrypt.seal_keyed(*KEYS, plain, header=header)
    assert savecrypt.unseal_keyed(*KEYS, blob, header=header) == plain
    with pytest.raises(savecrypt.Unreadable):
        savecrypt.unseal(blob)                         # not an export


@pytest.mark.parametrize("keys, header", [((b"x" * 32, KEYS[1]), b"H"), ((KEYS[0], b"x" * 32), b"H"), (KEYS, b"h")],
                         ids=["other-enc-key", "other-mac-key", "other-header"])
def test_unseal_keyed_refuses_other_keys_or_header(keys: tuple[bytes, bytes], header: bytes) -> None:
    blob = savecrypt.seal_keyed(*KEYS, b"a chat", header=b"H")
    with pytest.raises(savecrypt.Unreadable):          # (another encryption key: the tag matches, it's garbage)
        savecrypt.unseal_keyed(*keys, blob, header=header)


def test_unseal_keyed_refuses_a_changed_byte_and_short_blobs() -> None:
    blob = bytearray(savecrypt.seal_keyed(*KEYS, b"a chat"))
    blob[20] ^= 0x01
    with pytest.raises(savecrypt.Unreadable, match="changed"):
        savecrypt.unseal_keyed(*KEYS, bytes(blob))
    with pytest.raises(savecrypt.Unreadable, match="too short"):
        savecrypt.unseal_keyed(*KEYS, b"x" * 47)


def test_unseal_keyed_takes_more_than_an_export() -> None:
    plain = b"\0" * (savecrypt.MAX_PLAIN + 1)
    assert savecrypt.unseal_keyed(*KEYS, savecrypt.seal_keyed(*KEYS, plain)) == plain
    with pytest.raises(savecrypt.Unreadable, match="too big"):
        savecrypt.unseal_keyed(*KEYS, savecrypt.seal_keyed(*KEYS, plain), max_plain=savecrypt.MAX_PLAIN)
