"""Ledge finder test (developer tool, offscreen; synthetic screens - the real screen is only
read with --live, in memory, and only counts are printed).

* Lines found: the border under a toolbar, the top of a status bar, the bottom of a card.
  Not found: text, a soft gradient, a vertical border, the window's own outer edges.
* Remembering: a line counts after 2 sightings; it moves with its window; it survives being
  covered by the cat (it sits on it); it goes after 2 misses; a resized window starts over.
* Speed: a whole screen full of text in the background thread's budget.
Usage:  python tools/ledges_test.py [--live]
"""
from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if "--live" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv[:1])
from pixelcat.desktop.ledges import LedgeFinder, find_lines  # noqa: E402
from pixelcat.desktop.windows import WinInfo  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def screen(win: tuple[int, int, int, int], W: int = 1600, H: int = 1000) -> QImage:
    """A dark desktop with one light window: toolbar border, side panel, text, a card, a
    gradient and a status bar."""
    l, t, r, b = win
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(32, 32, 40))
    p = QPainter(img)
    p.fillRect(l, t, r - l, b - t, QColor(255, 255, 255))
    p.fillRect(l, t, r - l, 40, QColor(243, 243, 243))            # title/toolbar
    p.fillRect(l, t + 40, r - l, 1, QColor(224, 224, 224))         # subtle border under it  -> ledge
    p.fillRect(l, t + 41, 260, b - t - 71, QColor(249, 249, 249))  # side panel
    p.fillRect(l + 260, t + 41, 1, b - t - 71, QColor(229, 229, 229))   # its vertical border: no ledge
    p.fillRect(l, b - 30, r - l, 30, QColor(240, 240, 240))        # status bar               -> ledge
    p.setPen(QColor(20, 20, 20))
    p.setFont(QFont("Segoe UI", 11))
    rng = random.Random(1)
    for i in range(14):                                            # text: no ledges
        words = " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 9)))
                         for _ in range(7))
        p.drawText(l + 290, t + 90 + i * 30, words)
    for k in range(60):                                            # a soft shadow: no ledge
        c = 255 - min(k, 59 - k)
        p.fillRect(l + 290, t + 520 + k, 300, 1, QColor(c, c, c))
    p.fillRect(r - 420, t + 200, 380, 80, QColor(0, 120, 212))     # a card: its bottom edge -> ledge
    p.end()
    return img


def main() -> int:
    win = (100, 100, 1300, 800)
    img = screen(win)
    t0 = time.perf_counter()
    lines = find_lines(img, 120)
    ms = (time.perf_counter() - t0) * 1000
    ys = sorted({y for _a, _b, y in lines if win[0] < (_a + _b) / 2 < win[2]})
    expect = {140, 770, 380}
    check("finds the toolbar border, the status bar and a card's edge", expect <= set(ys), str(ys))
    inner = [y for y in ys if win[1] + 12 < y < win[3] - 6]
    check("...and nothing else inside the window (no text, gradient or vertical border)",
          expect <= set(inner) <= expect | {300}, str(inner))      # (300: the card's top - open with narrow fonts)
    check("a window-sized screen takes a few ms", ms < 60, f"{ms:.0f} ms")

    # remembering ---------------------------------------------------------------
    f = LedgeFinder()
    area = (0, 0, 1600, 1000)
    w1 = WinInfo(5, win, 0, "W", "X", False, False)
    f.scan([w1], area, [], 120, 1, image=img)
    check("a line seen once doesn't count yet", not f.current([w1]))
    f.scan([w1], area, [], 120, 1, image=img)
    cur = f.current([w1]).get(5, [])
    check("seen twice: the window's ledges are there", {140, 380, 770} <= {y for *_x, y in cur} <= {140, 300, 380, 770},
          str(sorted(y for *_x, y in cur)))
    moved = (300, 150, 1500, 850)
    w2 = WinInfo(5, moved, 0, "W", "X", False, False)
    cur = f.current([w2]).get(5, [])
    check("they move with their window (between scans)", {190, 430, 820} <= {y for *_x, y in cur} <= {190, 350, 430, 820}
          and min(x for x, *_r in cur) >= 300, str(sorted(y for *_x, y in cur)))
    img2 = screen(moved)
    cat = (600, 120, 800, 300)                                     # the cat sitting on the toolbar line
    p = QPainter(img2)
    p.fillRect(cat[0], cat[1], cat[2] - cat[0], cat[3] - cat[1], QColor(60, 60, 70))
    p.end()
    for _ in range(3):
        f.scan([w2], area, [cat], 120, 1, image=img2)
    cur = f.current([w2]).get(5, [])
    top = [c for c in cur if c[2] == 190]
    check("the line the cat sits on (covering it) is kept, full length", len(top) == 1 and top[0][0] < 400
          and top[0][1] > 1300, str(top))
    plain = QImage(1600, 1000, QImage.Format.Format_RGB32)
    plain.fill(QColor(32, 32, 40))
    p = QPainter(plain)
    p.fillRect(moved[0], moved[1], moved[2] - moved[0], moved[3] - moved[1], QColor(255, 255, 255))
    p.end()
    f.scan([w2], area, [], 120, 1, image=plain)
    still = bool(f.current([w2]).get(5))
    f.scan([w2], area, [], 120, 1, image=plain)
    check("lines that are gone are forgotten after two misses", still and not f.current([w2]).get(5))
    f.scan([w2], area, [], 120, 1, image=img2)
    f.scan([w2], area, [], 120, 1, image=img2)
    w3 = WinInfo(5, (300, 150, 1400, 850), 0, "W", "X", False, False)
    check("a resized window starts over", bool(f.current([w2]).get(5)) and not f.current([w3]).get(5))

    # speed on a big screen full of text --------------------------------------------
    big = QImage(2560, 1528, QImage.Format.Format_RGB32)
    big.fill(QColor(255, 255, 255))
    p = QPainter(big)
    p.setPen(QColor(20, 20, 20))
    p.setFont(QFont("Segoe UI", 10))
    rng = random.Random(2)
    for i in range(70):
        p.drawText(20, 30 + i * 21, " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz")
                                                     for _ in range(rng.randint(2, 9))) for _ in range(40)))
    p.end()
    t0 = time.perf_counter()
    found = find_lines(big, 120)
    ms = (time.perf_counter() - t0) * 1000
    check("a 2560x1528 screen full of text: no ledges, done in the background in well under a second",
          not found and ms < 400, f"{ms:.0f} ms, {len(found)} lines")

    if "--live" in sys.argv:                  # the real screen, in memory; prints counts only
        from pixelcat.desktop import win32
        from pixelcat.desktop.monitors import enumerate_monitors, primary
        from pixelcat.desktop.windows import enumerate_windows

        win32.set_dpi_awareness()
        mon = primary(enumerate_monitors())
        wins = enumerate_windows(os.getpid())
        lf = LedgeFinder()
        for _ in range(2):
            lf.start(wins, mon.work, [], 125, 3)
            while not lf.poll():
                time.sleep(0.02)
        cur = lf.current(wins)
        print(f"live: {len(wins)} windows, {sum(len(v) for v in cur.values())} ledges in {len(cur)} of them, "
              f"search {lf.last_ms:.0f} ms (background)")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
