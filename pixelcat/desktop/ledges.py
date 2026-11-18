"""Borders inside windows the cat can stand on: the line under a toolbar or a tab strip,
the top of a status bar, the edge of a panel...

Every few seconds the visible part of each window is looked at (a screen snapshot kept in
memory only - never saved; only line positions are kept): a *ledge* is a long, straight,
one-coloured horizontal line or colour step that has empty space above it.  Text, icons,
gradients and shadows don't count (they are not one colour along the line, or there is no
empty space above).  The heavy lifting is done by Qt in C++ (difference images, averaging),
so a scan of a whole screen takes a few ms of Python.

A ledge belongs to its window and is kept relative to the window's corner, so it moves with
the window between scans.  It must be seen in two scans in a row before the cat may use it
(content that scrolls or animates doesn't count) and is forgotten after two misses - except
where one of our own windows (the cat itself, the chat...) hid it, because the cat sitting
on a line covers that line.  A window that changes size starts over.
"""
from __future__ import annotations

import ctypes
import logging
import re
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Iterable

from PySide6.QtGui import QImage, QPainter

log = logging.getLogger(__name__)

BIN = 8                 # px per averaging bin along a line
T_EDGE = 12             # average brightness step across the line (0-255) - Windows 11 borders are subtle
T_FLAT = 3              # above the line: (almost) no change from row to row
T_SMOOTH = 5            # along the line and the row above it: one colour
MAX_PER_WINDOW = 6
Rect = tuple[int, int, int, int]


STRIP = 240             # rows per piece: each Qt step on a piece is short (the GUI thread keeps running)


def find_lines(img: QImage, min_len: int) -> list[tuple[int, int, int]]:
    """Ledge candidates in an RGB32 image: [(x1, x2, y)] in image px, y = the line's first row.
    Big images are done in horizontal strips (overlapping by the rows the test looks at)."""
    if img.format() != QImage.Format.Format_RGB32:
        img = img.convertToFormat(QImage.Format.Format_RGB32)
    if img.height() <= STRIP + 8:
        return _find_lines(img, min_len)
    out: list[tuple[int, int, int]] = []
    for y0 in range(0, img.height(), STRIP):
        top = max(0, y0 - 4)
        piece = img.copy(0, top, img.width(), min(img.height(), y0 + STRIP + 3) - top)
        for x1, x2, y in _find_lines(piece, min_len):
            gy = y + top
            if y0 <= gy - 1 < y0 + STRIP and not (out and out[-1][2] >= gy - 2 and out[-1][0] < x2 and x1 < out[-1][1]):
                out.append((x1, x2, gy))
    return out


def _find_lines(img: QImage, min_len: int) -> list[tuple[int, int, int]]:
    w, h = img.width(), img.height()
    if w < BIN * 4 or h < 8:
        return []
    if img.format() != QImage.Format.Format_RGB32:
        img = img.convertToFormat(QImage.Format.Format_RGB32)
    # |row y - row y+1| and |col x - col x+1| as images (in C++), then averaged over bins of BIN px
    v = img.copy(0, 0, w, h - 1)
    p = QPainter(v)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
    p.drawImage(0, 0, img, 0, 1, w, h - 1)
    p.end()
    hz = img.copy(0, 0, w - 1, h)
    p = QPainter(hz)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
    p.drawImage(0, 0, img, 1, 0, w - 1, h)
    p.end()
    nb = (w - 1) // BIN
    if nb < 4:
        return []
    step = (w - 1) / nb                       # px per bin (about BIN)
    gray = QImage.Format.Format_Grayscale8
    vs = v.scaled(nb, v.height(), aspectMode=_IGNORE, mode=_SMOOTH).convertToFormat(gray)
    hs = hz.scaled(nb, hz.height(), aspectMode=_IGNORE, mode=_SMOOTH).convertToFormat(gray)
    vb, vstride = bytes(vs.constBits()), vs.bytesPerLine()
    hb, hstride = bytes(hs.constBits()), hs.bytesPerLine()
    vrow = [vb[y * vstride:y * vstride + nb] for y in range(h - 1)]
    hrow = [hb[y * hstride:y * hstride + nb] for y in range(h)]
    cache: dict[tuple[int, int], int] = {}

    def mask(rows: list[bytes], y: int, table: bytes, kind: int) -> int:
        """Bins passing a test, as bits of a big int (the tests run in C via translate)."""
        key = (y, kind)
        got = cache.get(key)
        if got is None:
            got = cache[key] = int.from_bytes(rows[y].translate(table), "big")
        return got

    min_bins = max(4, int(min_len / step))
    out: list[tuple[int, int, int]] = []
    last_y = -10
    for y in range(4, h - 2):
        if max(vrow[y]) < T_EDGE:
            continue
        m = (mask(vrow, y, _GE_EDGE, 0) & mask(vrow, y - 1, _LE_FLAT, 1) & mask(vrow, y - 2, _LE_FLAT, 1)
             & mask(vrow, y - 3, _LE_FLAT, 1) & mask(hrow, y, _LE_SMOOTH, 2) & mask(hrow, y + 1, _LE_SMOOTH, 2))
        if not m:
            continue
        bits = m.to_bytes(nb, "big")
        runs = [(mt.start(), mt.end() - 1) for mt in _RUN.finditer(bits) if mt.end() - mt.start() >= min_bins]
        if runs:
            if y - last_y <= 2:                  # a 2-3 px border: only its top row
                continue
            last_y = y
            for i0, i1 in runs:
                out.append((int(i0 * step), int((i1 + 1) * step), y + 1))
    return out


# per-bin tests as byte tables (0x01 = passes); a run may have single failing bins (an icon on the line)
_GE_EDGE = bytes(1 if i >= T_EDGE else 0 for i in range(256))
_LE_FLAT = bytes(1 if i <= T_FLAT else 0 for i in range(256))
_LE_SMOOTH = bytes(1 if i <= T_SMOOTH else 0 for i in range(256))
_RUN = re.compile(rb"\x01(?:\x01|\x00(?=\x01))*")


def _qt_consts() -> tuple[object, object]:
    from PySide6.QtCore import Qt

    return Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation


_IGNORE, _SMOOTH = _qt_consts()


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


# ---------------------------------------------------------------- remembering ledges per window
@dataclass
class _Ledge:
    x1: int            # relative to the window's left
    x2: int
    y: int             # relative to the window's top
    hits: int = 1
    misses: int = 0
    born: int = 0      # the search that found it


@dataclass
class _WinLedges:
    size: tuple[int, int]
    ledges: list[_Ledge] = field(default_factory=list)


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class LedgeFinder:
    """Keeps the ledges of the windows on screen up to date (``scan``) and hands them out in
    screen coordinates for the surface map (``current``)."""

    def __init__(self) -> None:
        self.wins: dict[int, _WinLedges] = {}
        self.last_ms = 0.0               # how long the last snapshot + line search took (background)
        self.fresh = 0                   # new lines (kept) in the last search
        self.searches = 0
        self._job: dict | None = None
        self._lock = threading.Lock()

    def clear(self) -> None:
        self.wins.clear()

    # the snapshot and the line search run in a background thread; the result is applied
    # on the GUI thread (``poll``) - the cat never stutters for it
    def start(self, windows: Iterable, area: Rect, own: list[Rect], min_len: int, scale: int) -> bool:
        with self._lock:
            if self._job is not None:
                return False
            job = {"wins": list(windows), "area": area, "own": list(own), "min_len": min_len, "scale": scale,
                   "found": None}
            self._job = job

        def work() -> None:
            t0 = time.perf_counter()
            found: list[tuple[int, int, int]] | None = None
            try:
                img = grab(area)
                found = find_lines(img, min_len) if img is not None else None
            except Exception:  # noqa: BLE001
                log.debug("ledge search failed", exc_info=True)
            job["ms"] = (time.perf_counter() - t0) * 1000
            job["found"] = found if found is not None else []
            job["done"] = True

        threading.Thread(target=work, name="ledges", daemon=True).start()
        return True

    def poll(self) -> bool:
        """Apply a finished background search; True if there was one."""
        with self._lock:
            job = self._job
            if job is None or not job.get("done"):
                return False
            self._job = None
        self.last_ms = job.get("ms", 0.0)
        self._apply(job["found"], job["wins"], job["area"], job["own"], job["min_len"], job["scale"])
        return True

    def busy(self) -> bool:
        return self._job is not None

    def unconfirmed(self) -> bool:
        """The last search found new lines: they want a second look (just one)."""
        return self.fresh > 0

    def scan(self, windows: Iterable, area: Rect, own: list[Rect], min_len: int, scale: int,
             image: QImage | None = None) -> None:
        """The same, right away (tests).  ``image``: the screen picture to use."""
        img = image if image is not None else grab(area)
        if img is not None:
            self._apply(find_lines(img, min_len), list(windows), area, list(own), min_len, scale)

    def _apply(self, found: list[tuple[int, int, int]], windows: list, area: Rect, own: list[Rect],
               min_len: int, scale: int) -> None:
        """``own``: rects of our own windows (the cat, the chat...) - lines there are not judged."""
        wins = sorted(windows, key=lambda w: w.z)
        ax, ay = area[0], area[1]
        self.searches += 1
        # which window each line belongs to: the topmost one at that spot, well inside its frame
        per: dict[int, list[tuple[int, int, int, bool]]] = {}
        for x1, x2, y in found:
            gx1, gx2, gy = x1 + ax, x2 + ax, y + ay
            mx = (gx1 + gx2) // 2
            owner = next((w for w in wins if w.left <= mx < w.right and w.top <= gy < w.bottom), None)
            if owner is None:
                continue
            l, t, r, b = owner.rect
            if gy < t + 12 * scale or gy > b - 6 * scale:
                continue                                  # its own frame edges: tops are surfaces anyway
            spans = [(max(gx1, l + 2), min(gx2, r - 2))]
            # cut off by the edge of the searched area: its real length is not known
            clean = spans[0][0] > area[0] + 3 * BIN and spans[0][1] < area[2] - 3 * BIN
            for o in own:                                 # our own windows (the cat...) cut it
                if not spans:
                    break
                if o[1] - 2 <= gy < o[3] + 2 and o[0] - 3 * BIN < spans[-1][1] and spans[0][0] < o[2] + 3 * BIN:
                    clean = False                         # (a piece that ends at the cat is cut by it)
                    spans = [(pp, qq) for p0, q0 in spans for pp, qq in ((p0, min(q0, o[0])), (max(p0, o[2]), q0))
                             if qq - pp > 0]
            for pp, qq in spans:
                if qq - pp >= (min_len if clean else min_len // 2):
                    per.setdefault(owner.hwnd, []).append((pp, qq, gy, clean))
        live = {w.hwnd for w in wins}
        for hwnd in [h for h in self.wins if h not in live]:
            del self.wins[hwnd]
        for w in wins:
            l, t, r, b = w.rect
            if not _overlaps(w.rect, area):
                continue
            size = (r - l, b - t)
            st = self.wins.get(w.hwnd)
            if st is None or st.size != size:             # new or resized: start over
                st = self.wins[w.hwnd] = _WinLedges(size)
            seen = per.get(w.hwnd, [])
            matched: set[int] = set()
            for gx1, gx2, gy, clean in seen:
                rx1, rx2, ry = gx1 - l, gx2 - l, gy - t
                hit = next((i for i, lg in enumerate(st.ledges)
                            if abs(lg.y - ry) <= 1 and lg.x1 < rx2 + 4 * BIN and rx1 - 4 * BIN < lg.x2), None)
                if hit is None:
                    st.ledges.append(_Ledge(rx1, rx2, ry, born=self.searches))
                    matched.add(len(st.ledges) - 1)
                else:
                    lg = st.ledges[hit]
                    if clean and hit not in matched:      # seen whole: take its length as it is now
                        lg.x1, lg.x2 = rx1, rx2
                    else:                                 # partly hidden by the cat: keep the known length
                        lg.x1, lg.x2 = min(lg.x1, rx1), max(lg.x2, rx2)
                    lg.y = ry
                    if hit not in matched:
                        lg.hits += 1
                    lg.misses = 0
                    matched.add(hit)
            keep = []
            for i, lg in enumerate(st.ledges):
                if i not in matched:
                    g = (lg.x1 + l, lg.y + t - 3, lg.x2 + l, lg.y + t + 3)
                    hidden = any(_overlaps(g, o) for o in own) or not _overlaps(g, area)
                    if not hidden:
                        lg.misses += 1
                if lg.misses < 2:
                    keep.append(lg)
            keep.sort(key=lambda lg: (lg.hits < 2, -(lg.x2 - lg.x1)))   # confirmed ones first, then long ones
            st.ledges = keep[:MAX_PER_WINDOW * 2]
        # new lines that made the cut want one more look (lines that didn't make it don't count)
        self.fresh = sum(1 for st in self.wins.values() for lg in st.ledges if lg.born == self.searches)

    def current(self, windows: Iterable) -> dict[int, list[tuple[int, int, int]]]:
        """Confirmed ledges (seen twice) in screen coordinates, for the windows where they are now."""
        out: dict[int, list[tuple[int, int, int]]] = {}
        for w in windows:
            st = self.wins.get(w.hwnd)
            if st is None or st.size != (w.right - w.left, w.bottom - w.top):
                continue
            good = [lg for lg in st.ledges if lg.hits >= 2][:MAX_PER_WINDOW]
            if good:
                out[w.hwnd] = [(lg.x1 + w.left, lg.x2 + w.left, lg.y + w.top) for lg in good]
        return out
