"""Surfaces inside windows the cat can stand on: the top of a line of text, a word, a
heading, a picture, a button, a card, a toolbar, a panel - anything that has empty space
above it.

Every few seconds the screen near the cat is looked at (a snapshot kept in memory only -
never saved; only the positions of the surfaces are kept).  A surface is where empty space
(no change from row to row, one colour along the row) ends and *something* begins, along a
stretch at least a paw wide.  Letters of different heights on one line (capitals, x-height)
and the gaps between words are joined into one surface on top of the line.  Inside
pictures and videos nothing counts (there is no empty space above).  The heavy lifting is
done by Qt in C++ (difference images, averaging) and byte tables, so a whole screen takes
a few dozen ms in a background thread.

A surface belongs to its window and is kept relative to the window's corner, so it moves
with the window between searches.  It must be seen in two searches in a row before the cat
may use it (things that flash by don't count) and is forgotten after two misses - except
where one of our own windows (the cat itself, the chat...) hid it, because the cat sitting
on a line covers that line.  The line right under the cat is checked every second, so when
the page scrolls the cat drops off.  A window that changes size starts over.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from PySide6.QtGui import QImage, QPainter

if TYPE_CHECKING:
    from ..ports import CapturePort

log = logging.getLogger(__name__)

BIN = 8                 # px per averaging bin along a line
T_START = 6             # something starts: the average change into the next row (or along it) - subtle cards too
T_FLAT = 3              # empty space above: (almost) no change from row to row...
T_EMPTY = 4             # ...and (almost) one colour along the row
ROWS_ABOVE = 3          # empty rows needed above a surface
TOL = 6                 # px: tops of letters on one line (capitals / x-height) are one surface
GAP_BINS = 2            # bins: a word gap (or an icon gap) doesn't end a surface
MAX_PER_WINDOW = 120
Rect = tuple[int, int, int, int]
STRIP = 240             # rows per piece: each Qt step on a piece is short (the GUI thread keeps running)


def find_tops(img: QImage, min_len: int) -> list[tuple[int, int, int]]:
    """Surfaces in an RGB32 image: [(x1, x2, y)] in image px, y = the first row of the thing.
    Big images are done in horizontal strips (overlapping by the rows the test looks at)."""
    if img.format() != QImage.Format.Format_RGB32:
        img = img.convertToFormat(QImage.Format.Format_RGB32)
    raw: list[tuple[int, int, int]] = []
    if img.height() <= STRIP + 8:
        raw = _candidates(img)
    else:
        for y0 in range(0, img.height(), STRIP):
            top = max(0, y0 - ROWS_ABOVE - 2)
            piece = img.copy(0, top, img.width(), min(img.height(), y0 + STRIP + 3) - top)
            for x1, x2, y in _candidates(piece):
                gy = y + top
                if y0 <= gy - 1 < y0 + STRIP:
                    raw.append((x1, x2, gy))
    return _join(raw, min_len)


find_lines = find_tops        # (older name)


def _join(raw: list[tuple[int, int, int]], min_len: int) -> list[tuple[int, int, int]]:
    """Pieces at (almost) the same height that touch or have a small gap -> one surface on top."""
    raw.sort(key=lambda c: (c[2], c[0]))
    gap = GAP_BINS * BIN + 2
    active: list[list[int]] = []          # [x1, x2, ymin, ylast]
    done: list[list[int]] = []
    for x1, x2, y in raw:
        still = []
        for c in active:
            (still if y - c[2] <= TOL else done).append(c)
        active = still
        hit = [c for c in active if x1 <= c[1] + gap and c[0] - gap <= x2]
        if not hit:
            active.append([x1, x2, y, y])
            continue
        c = hit[0]
        c[0], c[1], c[3] = min(c[0], x1), max(c[1], x2), y
        for o in hit[1:]:                 # this piece joins two surfaces
            c[0], c[1], c[2] = min(c[0], o[0]), max(c[1], o[1]), min(c[2], o[2])
            active.remove(o)
    done += active
    return sorted(((c[0], c[1], c[2]) for c in done if c[1] - c[0] >= min_len), key=lambda c: (c[2], c[0]))


def _candidates(img: QImage) -> list[tuple[int, int, int]]:
    """Row by row: bins where empty space ends and something begins (pieces of surfaces)."""
    w, h = img.width(), img.height()
    if w < BIN * 3 or h < ROWS_ABOVE + 4:
        return []
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
    if nb < 2:
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

    out: list[tuple[int, int, int]] = []
    for y in range(ROWS_ABOVE, h - 2):
        # something begins in row y+1: it differs from row y, or has structure along it
        if max(vrow[y]) < T_START and max(hrow[y + 1]) < T_START:
            continue
        m = (mask(vrow, y, _GE_START, 0) | mask(hrow, y + 1, _GE_START, 3)) & mask(hrow, y, _LE_EMPTY, 2)
        if not m:
            continue
        for k in range(1, ROWS_ABOVE + 1):    # empty space above (no change, one colour)
            m &= mask(vrow, y - k, _LE_FLAT, 1)
            if k < ROWS_ABOVE:
                m &= mask(hrow, y - k, _LE_EMPTY, 2)
            if not m:
                break
        if not m:
            continue
        bits = m.to_bytes(nb, "big")
        for mt in _RUN.finditer(bits):
            out.append((int(mt.start() * step), int(mt.end() * step), y + 1))
    return out


# per-bin tests as byte tables (0x01 = passes); a run may have short gaps (between words)
_GE_START = bytes(1 if i >= T_START else 0 for i in range(256))
_LE_FLAT = bytes(1 if i <= T_FLAT else 0 for i in range(256))
_LE_EMPTY = bytes(1 if i <= T_EMPTY else 0 for i in range(256))
_RUN = re.compile(rb"\x01(?:\x01|\x00(?=\x01)|\x00\x00(?=\x01))*")


def _qt_consts() -> tuple[object, object]:
    from PySide6.QtCore import Qt

    return Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation


_IGNORE, _SMOOTH = _qt_consts()


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
    screen coordinates for the surface map (``current``).  The screen snapshots come from
    ``capture`` (:meth:`~pixelcat.ports.CapturePort.snapshot`)."""

    def __init__(self, capture: CapturePort) -> None:
        self.capture = capture
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
                img = self.capture.snapshot(area)
                found = find_tops(img, min_len) if img is not None else None
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

    def check_line(self, x1: int, x2: int, y: int, avoid: Rect) -> bool | None:
        """Is the surface (x1..x2 at y) still there?  Looks at a thin strip left and right of
        ``avoid`` (the cat sits on it and covers it).  None: can't tell."""
        parts = [(x1, min(x2, avoid[0])), (max(x1, avoid[2]), x2)]
        parts = [(a, b) for a, b in parts if b - a >= 3 * BIN]
        if not parts:
            return None
        top = y - ROWS_ABOVE - 3
        for a, b in parts:
            img = self.capture.snapshot((a, top, b, y + TOL + 3))
            if img is None:
                return None
            if any(abs(yy + top - y) <= TOL for _a, _b, yy in find_tops(img, 2 * BIN)):
                return True
        return False

    def forget(self, hwnd: int, rel_y: int) -> None:
        """A surface that is gone (scrolled away): forget it now."""
        st = self.wins.get(hwnd)
        if st is not None:
            st.ledges = [lg for lg in st.ledges if abs(lg.y - rel_y) > 2]

    def scan(self, windows: Iterable, area: Rect, own: list[Rect], min_len: int, scale: int,
             image: QImage | None = None) -> None:
        """The same, right away (tests).  ``image``: the screen picture to use."""
        img = image if image is not None else self.capture.snapshot(area)
        if img is not None:
            self._apply(find_tops(img, min_len), list(windows), area, list(own), min_len, scale)

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
                            if abs(lg.y - ry) <= 2 and lg.x1 < rx2 + 2 * BIN and rx1 - 2 * BIN < lg.x2), None)
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
