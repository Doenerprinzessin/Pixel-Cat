"""Screens and windows test (developer tool, headless - no Qt, no real windows).

* Stacked monitors (one above the laptop): the cat stays on the screen it is on - also when
  dropped onto the taskbar, and through window refreshes (it used to land on the upper one).
* A window the cat sits on is dragged around: the cat rides along every frame, holding on,
  at the same spot on the window - no flying through the air.
* The window closes / minimises: the cat falls from where it is (never from the top of the
  screen).  A window that jumps (snapped, maximised) drops it in place too; resizing the
  window keeps it on.
* Ledges inside windows: the cat gets onto them (hops down in front of the window, jumps up
  from below), they move with their window, a ledge that disappears just drops the cat.
* A curious cat explores: hops from surface to surface, up and down.
Usage:  python tools/screens_test.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pixelcat.desktop.monitors import Monitor  # noqa: E402
from pixelcat.desktop.windows import WinInfo  # noqa: E402
from pixelcat.engine.brain import Brain, Ctx  # noqa: E402
from pixelcat.engine.needs import Needs  # noqa: E402
from pixelcat.engine.states import S  # noqa: E402
from pixelcat.engine.surfaces import SurfaceMap  # noqa: E402
from pixelcat.settings import Tuning  # noqa: E402

DT = 1 / 60
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


LAPTOP = Monitor(1, (0, 0, 2560, 1600), (0, 0, 2560, 1552), 144, True, "laptop")
ABOVE = Monitor(2, (300, -1080, 2220, 0), (300, -1080, 2220, 0), 96, False, "above")


class World:
    def __init__(self, mons: list[Monitor], wins: list[WinInfo], seed: int = 1,
                 ledges: dict[int, list[tuple[int, int, int]]] | None = None) -> None:
        self.t = Tuning()
        self.mons = mons
        self.wins = list(wins)
        self.ledge_rel = {h: [(a - self.rect(h)[0], b - self.rect(h)[0], y - self.rect(h)[1]) for a, b, y in v]
                          for h, v in (ledges or {}).items()}
        self.b = Brain(self.t, Needs(0.9, 0.2, 0.9))
        self.b.rng.seed(seed)
        self.b.scale = 3
        self.cursor = (5, 5)
        self.refresh()

    def rect(self, hwnd: int) -> tuple[int, int, int, int]:
        return next(w.rect for w in self.wins if w.hwnd == hwnd)

    def refresh(self) -> None:
        led = {h: [(a + self.rect(h)[0], b + self.rect(h)[0], y + self.rect(h)[1]) for a, b, y in v]
               for h, v in self.ledge_rel.items() if any(w.hwnd == h for w in self.wins)}
        smap = SurfaceMap.build(self.mons, self.wins, lambda m: 3, self.t.min_surface_len, led)
        self.b.set_world(smap, {w.hwnd: w.rect for w in self.wins})

    def move(self, hwnd: int, rect: tuple[int, int, int, int] | None) -> None:
        old = next(w for w in self.wins if w.hwnd == hwnd)
        self.wins.remove(old)
        if rect is not None:
            self.wins.append(WinInfo(hwnd, rect, old.z, old.title, old.cls, False, False))
        self.wins.sort(key=lambda w: w.z)

    def step(self, n: int = 1, state: S | None = None) -> None:
        for _ in range(n):
            self.b.update(DT, Ctx(self.cursor, False, 0, 14, False, 3, False))

    def settle(self, sec: float = 3.0) -> None:
        for _ in range(int(sec / DT)):
            self.step()
            if self.b.mode == "ground" and not self.b.plan and not self.b.oneshot:
                break


def stacked_monitors() -> None:
    w = World([LAPTOP, ABOVE], [])
    b = w.b
    b.place(1200, 1500)
    w.settle()
    ups = 0
    for i in range(120):                                  # many window refreshes
        w.refresh()
        w.step(15)
        if b.y < 0:
            ups += 1
    check("stacked monitors: a cat on the laptop stays there through window refreshes", ups == 0 and
          abs(b.y - 1552) < 2, f"y={b.y:.0f}")
    b.pick_up()
    b.drag_to(1300, 1560)                                 # let go over the laptop's taskbar
    b.drop(0, 0)
    for _ in range(240):
        w.step()
    check("dropped onto the laptop's taskbar: lands on the laptop, not a screen up", b.mode == "ground"
          and abs(b.y - 1552) < 2, f"y={b.y:.0f} mode={b.mode}")
    b.pick_up()
    b.drag_to(1000, -400)                                 # let go on the upper monitor
    b.drop(0, 0)
    for _ in range(240):
        w.step()
    check("dropped on the upper monitor: lands there", b.mode == "ground" and abs(b.y - 0) < 2, f"y={b.y:.0f}")
    for _ in range(40):
        w.refresh()
        w.step(15)
    check("...and stays there", abs(b.y) < 2, f"y={b.y:.0f}")


def on_window(seed: int = 1, ledges=None) -> World:  # type: ignore[no-untyped-def]
    win = WinInfo(7, (800, 700, 1700, 1300), 0, "Editor", "X", False, False)
    w = World([LAPTOP], [win], seed, ledges)
    b = w.b
    top = next(s for s in b.smap.tops if s.hwnd == 7)
    b.place(1200, 690)
    w.settle()
    b.set_state(S.SITTING, travel=False)
    assert b.surf is not None and b.surf.same(top), "cat not on the window"
    return w


def dragging() -> None:
    w = on_window()
    b = w.b
    off = b.x - w.rect(7)[0]
    worst, air, held = 0.0, 0, 0
    x, y = 800, 700
    for i in range(240):                                  # 4 s of dragging, refreshes in between
        x += 9 * math.cos(i / 25)
        y += 5 * math.sin(i / 17)
        rect = (int(x), int(y), int(x) + 900, int(y) + 600)
        w.move(7, rect)
        b.ride_window(7, rect)
        if i % 15 == 0:
            w.refresh()
        w.step()
        worst = max(worst, abs((b.x - rect[0]) - off), abs(b.y - rect[1]))
        air += b.mode != "ground"
        held += b.current_anim()[0] == "hold_on"
    check("dragging the window: the cat rides along every frame, at the same spot, never in the air",
          worst <= 1 and air == 0, f"max offset {worst:.1f}px, {air} frames in the air")
    check("...holding on while the window moves (and frame rate up)", held > 200 and b.fps_hint() >= 60,
          f"{held}/240 frames holding on")
    for _ in range(60):
        w.step()
    check("the window stops: the cat lets go again", b.current_anim()[0] != "hold_on" and b.mode == "ground")


def closing() -> None:
    w = on_window()
    b = w.b
    y0 = b.y
    w.move(7, None)
    b.ride_window(7, None)                                # closed (seen by the per-frame check)
    highest = b.y
    for i in range(300):
        if i == 3:
            w.refresh()
        w.step()
        highest = min(highest, b.y)
    check("closing the window: the cat falls from where it was (not from the top of the screen)",
          highest >= y0 - 80 and b.mode == "ground" and abs(b.y - 1552) < 2, f"from {y0:.0f}, highest {highest:.0f}")
    # a closing app that first jumps to the top-left corner and shrinks
    w = on_window()
    b = w.b
    y0 = b.y
    rect = (0, 0, 300, 200)
    w.move(7, rect)
    b.ride_window(7, rect)
    highest = b.y
    for i in range(300):
        if i % 15 == 0:
            w.refresh()
        w.step()
        highest = min(highest, b.y)
    check("a window that jumps (snap, maximise, closing) drops the cat in place", highest >= y0 - 80
          and b.mode == "ground", f"from {y0:.0f}, highest {highest:.0f}")
    # the same through a slow window refresh only (no per-frame check)
    w = on_window()
    b = w.b
    y0 = b.y
    w.move(7, (0, 0, 300, 200))
    w.refresh()
    highest = b.y
    for _ in range(300):
        w.step()
        highest = min(highest, b.y)
    check("...also when only the window refresh sees it", highest >= y0 - 80, f"highest {highest:.0f}")
    # resizing from the right: stays on
    w = on_window()
    b = w.b
    for i in range(60):
        rect = (800, 700, 1700 - i * 4, 1300)
        w.move(7, rect)
        b.ride_window(7, rect)
        if i % 15 == 0:
            w.refresh()
        w.step()
    check("resizing the window keeps the cat on it", b.mode == "ground" and b.surf is not None and b.surf.hwnd == 7)
    w.move(7, None)
    b.ride_window(7, None)
    w.step()
    check("minimising/hiding it (gone for the per-frame check) drops the cat", b.mode == "air")


def ledges() -> None:
    led = {7: [(820, 1680, 760), (820, 1680, 1150)]}       # under a toolbar, top of a status bar
    w = on_window(3, led)
    b = w.b
    lg = [s for s in b.smap.ledges]
    check("ledges inside the window become surfaces", len(lg) == 2 and all(s.hwnd == 7 for s in lg),
          str([(s.x1, s.x2, s.y) for s in lg]))
    low = max(lg, key=lambda s: s.y)
    ok = b.go_to(low, 1200, "walk")
    for _ in range(600):
        w.step()
        if not b.plan and b.mode == "ground":
            break
    check("from the window's top the cat hops down onto a ledge inside it", ok and b.surf is not None
          and b.surf.kind == "ledge" and abs(b.y - low.y) < 2, f"surf {b.surf.kind if b.surf else None} y={b.y:.0f}")
    hi = min(lg, key=lambda s: s.y)
    ok = b.go_to(hi, 1000, "walk")
    for _ in range(int(60 / DT)):                        # (off the end, across, up the side, down onto it)
        w.step()
        if not b.plan and b.mode == "ground":
            break
    check("...and gets up to a higher ledge", ok and b.surf is not None and b.surf.same(hi), f"y={b.y:.0f}")
    off = b.y - w.rect(7)[1]
    for i in range(60):
        rect = (800 + i * 5, 700 - i * 2, 1700 + i * 5, 1300 - i * 2)
        w.move(7, rect)
        b.ride_window(7, rect)
        if i % 15 == 0:
            w.refresh()
        w.step()
    check("a ledge moves with its window (the cat on it too)", abs((b.y - w.rect(7)[1]) - off) <= 1 and
          b.mode == "ground", f"offset {off:.0f} -> {b.y - w.rect(7)[1]:.0f}")
    w.ledge_rel = {}                                      # the line scrolled away
    w.refresh()
    st = b.after_land
    w.step()
    check("a ledge that disappears just drops the cat (no fright)", b.mode == "air" and st is None and
          b.after_land is None)
    # from the floor: up onto a low ledge in front of a window
    win = WinInfo(9, (300, 900, 1400, 1552), 0, "Low", "X", False, False)
    w2 = World([LAPTOP], [win], 4, {9: [(320, 1380, 1440)]})
    b2 = w2.b
    b2.place(800, 1540)
    w2.settle()
    target = b2.smap.ledges[0]
    ok = b2.go_to(target, 900, "walk")
    for _ in range(600):
        w2.step()
        if not b2.plan and b2.mode == "ground":
            break
    check("from the floor the cat jumps up onto a low ledge", ok and b2.surf is not None and b2.surf.kind == "ledge")


def exploring() -> None:
    wins = [WinInfo(7, (800, 700, 1700, 1300), 0, "A", "X", False, False),
            WinInfo(8, (1900, 900, 2500, 1552), 1, "B", "X", False, False)]
    w = World([LAPTOP], wins, 11, {7: [(820, 1680, 760), (820, 1680, 1150)], 8: [(1920, 2480, 1000)]})
    b = w.b
    b.place(1300, 1500)
    w.settle()
    kinds, ups, downs = set(), 0, 0
    last = None
    for leg in range(25):
        b.set_state(S.CURIOUS)
        if not b.plan:
            b.start_trip()
        for _ in range(int(25 / DT)):
            w.step()
            if b.mode == "ground" and b.surf is not None and (last is None or not b.surf.same(last)):
                if last is not None:
                    ups += b.surf.y < last.y - 5
                    downs += b.surf.y > last.y + 5
                last = b.surf
                kinds.add(b.surf.kind)
            if not b.plan and b.explore_legs == 0 and b.mode == "ground":
                break
    check("a curious cat explores: floor, window tops and ledges, up and down", {"floor", "top", "ledge"} <= kinds
          and ups >= 3 and downs >= 3, f"{sorted(kinds)}, {ups} up, {downs} down")


def main() -> int:
    stacked_monitors()
    dragging()
    closing()
    ledges()
    exploring()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
