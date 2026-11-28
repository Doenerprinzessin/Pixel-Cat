"""The screen snapshot the ledge finder (:mod:`pixelcat.ui.ledges`) looks at: GDI, in memory only - never saved."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]


# ---------------------------------------------------------------- screen snapshot (memory only)
class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


_GDI: tuple | None = None


def _gdi() -> tuple:
    """user32/gdi32 with argument types (64-bit handles must not be cut to 32 bits)."""
    global _GDI
    if _GDI is None:
        user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
        user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
        user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = [wintypes.HWND, wintypes.HDC], ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ
        gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        gdi32.BitBlt.restype = wintypes.BOOL
        gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = [wintypes.HGDIOBJ], wintypes.BOOL
        gdi32.DeleteDC.argtypes, gdi32.DeleteDC.restype = [wintypes.HDC], wintypes.BOOL
        _GDI = (user32, gdi32)
    return _GDI


def grab(rect: Rect) -> QImage | None:
    """The screen in ``rect`` (physical px) as an RGB32 image - in memory only."""
    l, t, r, b = rect
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    try:
        user32, gdi32 = _gdi()
        screen = user32.GetDC(None)
        if not screen:
            return None
        mem = gdi32.CreateCompatibleDC(screen)
        bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not mem or not dib or not bits.value:
            if dib:
                gdi32.DeleteObject(dib)
            if mem:
                gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)
            return None
        old = gdi32.SelectObject(mem, dib)
        try:
            if not gdi32.BitBlt(mem, 0, 0, w, h, screen, l, t, 0x00CC0020):   # SRCCOPY
                return None
            data = ctypes.string_at(bits.value, w * h * 4)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(dib)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)
        return QImage(data, w, h, w * 4, QImage.Format.Format_RGB32).copy()
    except Exception:  # noqa: BLE001
        log.debug("screen grab failed", exc_info=True)
        return None
