"""Protecting the save file, so the Cat Pass can't be changed by editing a file.

* The save (``save.dat``): encrypted with Windows' own data protection (DPAPI,
  ``CryptProtectData``) for the signed-in Windows account.  Windows refuses to decrypt a
  file that was changed, and a save copied to another account or PC can't be read.  No
  admin rights, nothing to install.  That part is the Windows adapter's SecretStore
  (``pixelcat.adapters.windows.dpapi``); the app's entropy for it, ``_ENTROPY``, is kept here.
* Exports (``.pixelcat`` files): encrypted and signed with the app's own key (encrypt, then
  HMAC-SHA256), so they load on any PC but an edited file is refused.  The same scheme with
  other keys (``seal_keyed``/``unseal_keyed``) serves secret stores that keep a per-user key
  themselves (macOS: a key in the Keychain).

This stops editing a file by hand.  Pixel Cat is open source and runs on the user's own PC,
so someone who reads the code can still forge a save on purpose - nothing local can stop that.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import zlib

from .domain.secrets import Unreadable  # noqa: F401 - re-exported

_APP_KEY = hashlib.sha256(b"Pixel Cat :3 - the Cat Pass is earned by playing, not by editing").digest()
_ENC_KEY = hmac.new(_APP_KEY, b"encrypt", hashlib.sha256).digest()
_MAC_KEY = hmac.new(_APP_KEY, b"sign", hashlib.sha256).digest()
_ENTROPY = hmac.new(_APP_KEY, b"windows data protection", hashlib.sha256).digest()
MAX_PLAIN = 8_000_000                     # bytes: no save is anywhere near this
KEYED_MAX_PLAIN = 256_000_000             # bytes: the per-user stores (chats can be big)


# ------------------------------------------------------------------ encrypt, then sign
def _stream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hmac.new(enc_key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def _xor(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big") if a else b""


def seal_keyed(enc_key: bytes, mac_key: bytes, data: bytes, header: bytes = b"") -> bytes:
    """Compress, encrypt with ``enc_key`` and sign with ``mac_key``: nonce(16) + ciphertext + tag(32).

    ``header`` (e.g. a format marker the caller puts in front) is signed too but not part of the result."""
    nonce = os.urandom(16)
    z = zlib.compress(data, 9)
    ct = _xor(z, _stream(enc_key, nonce, len(z)))
    return nonce + ct + hmac.new(mac_key, header + nonce + ct, hashlib.sha256).digest()


def unseal_keyed(enc_key: bytes, mac_key: bytes, blob: bytes, header: bytes = b"",
                 max_plain: int = KEYED_MAX_PLAIN) -> bytes:
    """Undo :func:`seal_keyed` (same keys and header); raises Unreadable if anything was changed."""
    if len(blob) < 16 + 32:
        raise Unreadable("too short")
    nonce, ct, tag = blob[:16], blob[16:-32], blob[-32:]
    if not hmac.compare_digest(tag, hmac.new(mac_key, header + nonce + ct, hashlib.sha256).digest()):
        raise Unreadable("the signature doesn't match: the file was changed")
    try:
        d = zlib.decompressobj()
        data = d.decompress(_xor(ct, _stream(enc_key, nonce, len(ct))), max_plain)
    except zlib.error as e:
        raise Unreadable(f"damaged ({e})") from e
    if d.unconsumed_tail:
        raise Unreadable("too big")
    return data


# ------------------------------------------------------------------ signed files (exports)
def seal(data: bytes) -> bytes:
    """Compress, encrypt and sign: works on any PC, an edited copy is refused."""
    return seal_keyed(_ENC_KEY, _MAC_KEY, data)


def unseal(blob: bytes) -> bytes:
    return unseal_keyed(_ENC_KEY, _MAC_KEY, blob, max_plain=MAX_PLAIN)
