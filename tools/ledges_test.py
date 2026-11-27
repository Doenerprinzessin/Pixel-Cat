"""Surface finder test (developer tool, offscreen; synthetic screens - the real screen is only
read with --live, in memory, and only counts are printed).

* Found: the top of every line of text and of a heading, the line under a toolbar, the top of
  a status bar, a card's top and bottom, the top of a picture and of buttons.
  Not found: anything inside a picture (noise), a soft shadow, a vertical border.
* Remembering: a surface counts after 2 sightings; it moves with its window; it survives
  being covered by the cat (it sits on it); it goes after 2 misses; a resized window starts
  over.  The line under the cat: still there / scrolled away.
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
from pixelcat.desktop import ledges as L  # noqa: E402
from pixelcat.desktop.ledges import LedgeFinder, find_tops  # noqa: E402
from pixelcat.domain import WinInfo  # noqa: E402

results: list[tuple[str, bool, str]] = []
TEXT_Y = [190 + i * 30 for i in range(10)]     # (window-relative baselines of the paragraph)


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def screen(win: tuple[int, int, int, int], W: int = 1600, H: int = 1000) -> QImage:
    """A dark desktop with one light window: toolbar, side panel, a heading, a paragraph, a
    card, a soft shadow, a photo, buttons and a status bar."""
    l, t, r, b = win
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(32, 32, 40))
    p = QPainter(img)
    p.fillRect(l, t, r - l, b - t, QColor(255, 255, 255))
    p.fillRect(l, t, r - l, 40, QColor(243, 243, 243))            # title/toolbar
    p.fillRect(l, t + 40, r - l, 1, QColor(224, 224, 224))         # subtle border under it         -> 140
    p.fillRect(l, t + 41, 260, b - t - 71, QColor(249, 249, 249))  # side panel
    p.fillRect(l + 260, t + 41, 1, b - t - 71, QColor(229, 229, 229))   # its vertical border: nothing
    p.fillRect(l, b - 30, r - l, 30, QColor(240, 240, 240))        # status bar                     -> 770
    p.setPen(QColor(20, 20, 20))
    p.setFont(QFont("Segoe UI", 18))
    p.drawText(l + 290, t + 70, 300, 30, 0, "A heading here")         # heading
    p.setFont(QFont("Segoe UI", 11))
    rng = random.Random(1)
    for y in TEXT_Y:                                               # a paragraph: a surface per line
        words = " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 7)))
                         for _ in range(5))
        p.drawText(l + 290, t + y - 16, 330, 20, 0, words)
    for k in range(60):                                            # a soft shadow: nothing
        c = 255 - min(k, 59 - k) // 2
        p.fillRect(l + 290, t + 520 + k, 300, 1, QColor(c, c, c))
    p.fillRect(r - 420, t + 200, 380, 80, QColor(0, 120, 212))     # a card: top 300, bottom 380
    for y in range(t + 420, t + 600):                              # a photo (noise): its top only -> 520
        for x in range(r - 420, r - 120, 4):
            c = rng.randint(30, 225)
            p.fillRect(x, y, 4, 1, QColor(c, (c * 3) % 256, (c * 7) % 256))
    for k in range(2):                                             # buttons: their tops -> 730
        p.fillRect(l + 300 + k * 140, t + 630, 110, 32, QColor(0, 120, 212))
    p.end()
    return img


def main() -> int:  # noqa: C901 - a linear test script
    win = (100, 100, 1300, 800)
    img = screen(win)
    t0 = time.perf_counter()
    tops = find_tops(img, 48)
    ms = (time.perf_counter() - t0) * 1000
    inside = [(a, b, y) for a, b, y in tops if win[0] < (a + b) / 2 < win[2] and win[1] + 12 < y < win[3] - 6]
    ys = sorted({y for _a, _b, y in inside})
    check("finds the toolbar line, the status bar, the card (top and bottom), the photo's top, the buttons",
          {140, 770, 300, 380, 520, 730} <= set(ys), str(ys))
    text = [y for a, b, y in inside if 160 < y < 200 + TEXT_Y[-1] and a < win[0] + 700]
    check("finds the heading and every line of text (one surface per line)", len(text) >= len(TEXT_Y) + 1, str(text))
    photo = [y for a, _b, y in inside if 521 < y < 700 and a > win[2] - 430]
    shadow = [y for a, _b, y in inside if 621 < y < 680 and a < win[0] + 600]
    check("nothing inside the photo, nothing for a soft shadow", not photo and not shadow, f"{photo} {shadow}")
    check("a window-sized screen takes a few ms", ms < 80, f"{ms:.0f} ms")

    # remembering ---------------------------------------------------------------
    f = LedgeFinder()
    area = (0, 0, 1600, 1000)
    w1 = WinInfo(5, win, 0, "W", "X", False, False)
    f.scan([w1], area, [], 48, 1, image=img)
    check("a surface seen once doesn't count yet", not f.current([w1]))
    f.scan([w1], area, [], 48, 1, image=img)
    cur = f.current([w1]).get(5, [])
    got = {y for *_x, y in cur}
    check("seen twice: the window's surfaces are there (lots of them)", {140, 300, 770} <= got and len(cur) >= 15,
          f"{len(cur)} surfaces")
    moved = (300, 150, 1500, 850)
    w2 = WinInfo(5, moved, 0, "W", "X", False, False)
    cur = f.current([w2]).get(5, [])
    check("they move with their window (between searches)", {190, 350, 820} <= {y for *_x, y in cur}
          and min(x for x, *_r in cur) >= 300)
    img2 = screen(moved)
    cat = (600, 120, 800, 300)                                     # the cat sitting on the toolbar line
    p = QPainter(img2)
    p.fillRect(cat[0], cat[1], cat[2] - cat[0], cat[3] - cat[1], QColor(60, 60, 70))
    p.end()
    for _ in range(3):
        f.scan([w2], area, [cat], 48, 1, image=img2)
    cur = f.current([w2]).get(5, [])
    top = [c for c in cur if c[2] == 190]
    check("the line the cat sits on (covering it) is kept, full length", len(top) == 1 and top[0][0] < 400
          and top[0][1] > 1300, str(top))
    plain = QImage(1600, 1000, QImage.Format.Format_RGB32)
    plain.fill(QColor(32, 32, 40))
    p = QPainter(plain)
    p.fillRect(moved[0], moved[1], moved[2] - moved[0], moved[3] - moved[1], QColor(255, 255, 255))
    p.end()
    f.scan([w2], area, [], 48, 1, image=plain)
    still = bool(f.current([w2]).get(5))
    f.scan([w2], area, [], 48, 1, image=plain)
    check("surfaces that are gone are forgotten after two misses", still and not f.current([w2]).get(5))
    f.scan([w2], area, [], 48, 1, image=img2)
    f.scan([w2], area, [], 48, 1, image=img2)
    w3 = WinInfo(5, (300, 150, 1400, 850), 0, "W", "X", False, False)
    check("a resized window starts over", bool(f.current([w2]).get(5)) and not f.current([w3]).get(5))

    # the line under the cat (the screen is faked from a picture) ----------------------
    shown = [img2]
    real_grab = L.grab
    L.grab = lambda r: shown[0].copy(r[0], r[1], r[2] - r[0], r[3] - r[1])  # type: ignore[assignment]
    ok_there = f.check_line(305, 1495, 190, cat)
    shown[0] = plain
    ok_gone = f.check_line(305, 1495, 190, cat)
    f.forget(5, 40)
    L.grab = real_grab  # type: ignore[assignment]
    check("the line under the cat: still there -> yes; scrolled away -> no (and forgotten)",
          ok_there is True and ok_gone is False and not any(c[2] == 190 for c in f.current([w2]).get(5, [])),
          f"{ok_there} / {ok_gone}")

    # speed on a big screen full of text --------------------------------------------
    big = QImage(2560, 1528, QImage.Format.Format_RGB32)
    big.fill(QColor(255, 255, 255))
    p = QPainter(big)
    p.setPen(QColor(20, 20, 20))
    p.setFont(QFont("Segoe UI", 10))
    rng = random.Random(2)
    for i in range(70):
        p.drawText(20, 14 + i * 21, 2500, 20, 0, " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz")
                                                                   for _ in range(rng.randint(2, 9)))
                                                           for _ in range(40)))
    p.end()
    t0 = time.perf_counter()
    found = find_tops(big, 48)
    ms = (time.perf_counter() - t0) * 1000
    check("a 2560x1528 screen full of text: a surface on every line, in the background budget",
          60 <= len(found) <= 140 and ms < 600, f"{ms:.0f} ms, {len(found)} surfaces")

    if "--live" in sys.argv:                  # the real screen, in memory; prints counts only
        from pixelcat.desktop import win32
        from pixelcat.desktop.monitors import enumerate_monitors, primary
        from pixelcat.desktop.windows import enumerate_windows

        win32.set_dpi_awareness()
        mon = primary(enumerate_monitors())
        wins = enumerate_windows(os.getpid())
        lf = LedgeFinder()
        for _ in range(2):
            lf.start(wins, mon.work, [], 48, 3)
            while not lf.poll():
                time.sleep(0.02)
        cur = lf.current(wins)
        print(f"live: {len(wins)} windows, {sum(len(v) for v in cur.values())} surfaces in {len(cur)} of them, "
              f"search {lf.last_ms:.0f} ms (background)")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
