"""Protecting the save file, so the Cat Pass can't be changed by editing a file.

* The save (``save.dat``): encrypted with Windows' own data protection (DPAPI,
  ``CryptProtectData``) for the signed-in Windows account.  Windows refuses to decrypt a
  file that was changed, and a save copied to another account or PC can't be read.  No
  admin rights, nothing to install.
* Exports (``.pixelcat`` files): encrypted and signed with the app's own key (encrypt, then
  HMAC-SHA256), so they load on any PC but an edited file is refused.

This stops editing a file by hand.  Pixel Cat is open source and runs on the user's own PC,
so someone who reads the code can still forge a save on purpose - nothing local can stop that.
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import sys
import zlib

from .domain.secrets import Unreadable  # noqa: F401 - re-exported

_APP_KEY = hashlib.sha256(b"Pixel Cat :3 - the Cat Pass is earned by playing, not by editing").digest()
_ENC_KEY = hmac.new(_APP_KEY, b"encrypt", hashlib.sha256).digest()
_MAC_KEY = hmac.new(_APP_KEY, b"sign", hashlib.sha256).digest()
_ENTROPY = hmac.new(_APP_KEY, b"windows data protection", hashlib.sha256).digest()
MAX_PLAIN = 8_000_000                     # bytes: no save is anywhere near this


# ------------------------------------------------------------------ Windows data protection
if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
else:                                     # (Pixel Cat is a Windows app; this keeps imports working)
    _Blob = None  # type: ignore[assignment,misc]

_api: tuple | None = None


def _dpapi() -> tuple:
    global _api
    if _api is None:
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        pb = ctypes.POINTER(_Blob)
        crypt.CryptProtectData.argtypes = [pb, wintypes.LPCWSTR, pb, ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, pb]
        crypt.CryptProtectData.restype = wintypes.BOOL
        crypt.CryptUnprotectData.argtypes = [pb, ctypes.c_void_p, pb, ctypes.c_void_p, ctypes.c_void_p,
                                             wintypes.DWORD, pb]
        crypt.CryptUnprotectData.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        _api = (crypt, kernel)
    return _api


def _blob(data: bytes) -> tuple:
    buf = (ctypes.c_ubyte * max(1, len(data))).from_buffer_copy(data or b"\0")
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def _take(out) -> bytes:
    _crypt, kernel = _dpapi()
    addr = ctypes.cast(out.pbData, ctypes.c_void_p).value
    try:
        return ctypes.string_at(addr, out.cbData) if addr else b""
    finally:
        if addr:
            kernel.LocalFree(addr)


_UI_FORBIDDEN = 0x1                       # never show a Windows prompt


def protect(data: bytes) -> bytes:
    """Encrypt for this Windows account (raises OSError if Windows can't)."""
    crypt, _kernel = _dpapi()
    src, _k1 = _blob(data)
    ent, _k2 = _blob(_ENTROPY)
    out = _Blob()
    if not crypt.CryptProtectData(ctypes.byref(src), "Pixel Cat save", ctypes.byref(ent), None, None,
                                  _UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "Windows couldn't protect the save")
    return _take(out)


def unprotect(blob: bytes) -> bytes:
    """Decrypt a :func:`protect` result.  Unreadable: changed, or another account's."""
    crypt, _kernel = _dpapi()
    src, _k1 = _blob(blob)
    ent, _k2 = _blob(_ENTROPY)
    out = _Blob()
    if not crypt.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None,
                                    _UI_FORBIDDEN, ctypes.byref(out)):
        raise Unreadable(f"Windows refused to open it (error {ctypes.get_last_error()})")
    return _take(out)


# ------------------------------------------------------------------ signed files (exports)
def _stream(nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hmac.new(_ENC_KEY, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def _xor(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big") if a else b""


def seal(data: bytes) -> bytes:
    """Compress, encrypt and sign: works on any PC, an edited copy is refused."""
    nonce = os.urandom(16)
    z = zlib.compress(data, 9)
    ct = _xor(z, _stream(nonce, len(z)))
    return nonce + ct + hmac.new(_MAC_KEY, nonce + ct, hashlib.sha256).digest()


def unseal(blob: bytes) -> bytes:
    if len(blob) < 16 + 32:
        raise Unreadable("too short")
    nonce, ct, tag = blob[:16], blob[16:-32], blob[-32:]
    if not hmac.compare_digest(tag, hmac.new(_MAC_KEY, nonce + ct, hashlib.sha256).digest()):
        raise Unreadable("the signature doesn't match: the file was changed")
    try:
        d = zlib.decompressobj()
        data = d.decompress(_xor(ct, _stream(nonce, len(ct))), MAX_PLAIN)
    except zlib.error as e:
        raise Unreadable(f"damaged ({e})") from e
    if d.unconsumed_tail:
        raise Unreadable("too big")
    return data
