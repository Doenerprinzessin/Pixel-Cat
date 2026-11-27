"""Windows data protection (DPAPI, ``CryptProtectData``): encrypts for the signed-in Windows account.

Windows refuses to decrypt data that was changed, and data copied to another account or PC can't be read.
No admin rights, nothing to install.  The entropy is the app's own (``savecrypt._ENTROPY``, read when
called).
"""
from __future__ import annotations

import ctypes
import sys

from ... import savecrypt
from ...domain.secrets import Unreadable

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
    ent, _k2 = _blob(savecrypt._ENTROPY)
    out = _Blob()
    if not crypt.CryptProtectData(ctypes.byref(src), "Pixel Cat save", ctypes.byref(ent), None, None,
                                  _UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "Windows couldn't protect the save")
    return _take(out)


def unprotect(blob: bytes) -> bytes:
    """Decrypt a :func:`protect` result.  Unreadable: changed, or another account's."""
    crypt, _kernel = _dpapi()
    src, _k1 = _blob(blob)
    ent, _k2 = _blob(savecrypt._ENTROPY)
    out = _Blob()
    if not crypt.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None,
                                    _UI_FORBIDDEN, ctypes.byref(out)):
        raise Unreadable(f"Windows refused to open it (error {ctypes.get_last_error()})")
    return _take(out)
