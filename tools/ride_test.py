"""Riding a real window (developer tool): the cat sits on a window of another process; the
window is dragged around (moved every 16 ms, like a mouse drag), then closed - and another
time snapped to a different size.  Nothing takes the keyboard focus.

Checks: during the drag the cat stays at the same spot on the window every frame (no flying
through the air), it holds on, the frame rate goes up; when the window closes the cat falls
from where it was (not from the top of the screen) and lands; when the window snaps, it is
dropped in place.
Usage:  python tools/ride_test.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

HELPER = r'''
import ctypes, math, sys, threading, queue
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel
app = QApplication([])
u = ctypes.windll.user32
u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
w = QLabel("Pixel Cat ride test")
w.setWindowTitle("Pixel Cat ride test")
w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
x0, y0 = int(sys.argv[1]), int(sys.argv[2])
w.setGeometry(x0, y0, 900, 500)
w.show()
hwnd = int(w.winId())
NOACT, NOSIZE, NOZ = 0x10, 0x1, 0x4
u.SetWindowPos(hwnd, None, 0, 0, 0, 0, NOACT | NOSIZE | 0x2)        # to the top, no focus
print("ready", hwnd, flush=True)
cmds = queue.Queue()
threading.Thread(target=lambda: [cmds.put(l.strip()) for l in sys.stdin], daemon=True).start()
state = {"t": -1}
def frame():
    while not cmds.empty():
        c = cmds.get()
        if c == "drag":
            state["t"] = 0
        elif c == "close":
            w.close(); print("closed", flush=True)
        elif c.startswith("jump"):
            _, x, y, ww, hh = c.split()
            u.SetWindowPos(hwnd, None, int(x), int(y), int(ww), int(hh), NOACT | NOZ)
            print("jumped", flush=True)
        elif c == "quit":
            app.quit(); return
    if state["t"] >= 0:
        k = state["t"]
        dx, dy = int(260 * math.sin(k / 30)), int(-120 * math.sin(k / 45))
        u.SetWindowPos(hwnd, None, x0 + dx, y0 + dy, 0, 0, NOACT | NOSIZE | NOZ)
        state["t"] += 1
        if state["t"] > 150:
            state["t"] = -1
            print("dragged", flush=True)
    QTimer.singleShot(16, frame)
QTimer.singleShot(16, frame)
app.exec()
'''

results: list[tuple[str, bool, str]] = []
HELPERS: list[subprocess.Popen] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def main() -> int:  # noqa: C901 - a linear test script
    from PySide6.QtWidgets import QApplication

    from pixelcat.adapters.windows import win32
    from pixelcat.logsetup import setup_logging

    setup_logging(False)
    win32.set_dpi_awareness()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    from pixelcat.app import PixelCatApp
    from pixelcat.bootstrap import build_platform
    from pixelcat.domain import primary
    from pixelcat.engine.states import S
    from pixelcat.persistence import SavedState
    from pixelcat.settings import load_config

    pet = PixelCatApp(app, load_config(), SavedState(autostart_asked=True), selftest=0, platform=build_platform())
    pet.save = lambda: None
    pet.start()
    b = pet.brain
    mon = primary(pet.monitors)
    wx, wy = (mon.work[0] + mon.work[2]) // 2 - 450, (mon.work[1] + mon.work[3]) // 2 - 150
    samples: list[tuple[float, float, str, float]] = []
    hwnd_box = [0]
    real_tick = pet._tick

    def timed_tick() -> None:
        real_tick()
        h = hwnd_box[0]
        if h:
            r = win32.frame_bounds(h) if win32.user32.IsWindow(h) else None
            if r is not None:
                samples.append((b.x - r[0], b.y - r[1], b.mode, time.perf_counter()))
    pet.tick_timer.timeout.disconnect()
    pet.tick_timer.timeout.connect(timed_tick)

    def pump(sec: float, until=None) -> bool:  # type: ignore[no-untyped-def]
        end = time.time() + sec
        while time.time() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.003)
        return until is None

    def start_helper() -> tuple[subprocess.Popen, int]:
        h = subprocess.Popen([sys.executable, "-c", HELPER, str(wx), str(wy)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        HELPERS.append(h)
        line = h.stdout.readline().split()
        hwnd = int(line[1])
        last, stable = None, 0.0                   # wait until Windows has finished placing it
        end = time.time() + 5
        while time.time() < end and stable < 0.5:
            r = win32.frame_bounds(hwnd)
            stable = stable + 0.05 if r == last else 0.0
            last = r
            pump(0.05)
        return h, hwnd

    def sit_on(hwnd: int) -> bool:
        pet._refresh_windows()
        top = next((t for t in b.smap.tops if t.hwnd == hwnd), None)
        if top is None:
            return False
        b.set_state(S.SITTING, travel=False)
        b.explore_legs = 0
        b.place((top.x1 + top.x2) / 2, top.y - 40)
        ok = pump(4, lambda: b.mode == "ground" and b.surf is not None and b.surf.hwnd == hwnd)
        if not ok:
            print(f"   (not seated: placed above y={top.y} x={(top.x1 + top.x2) / 2:.0f}; now mode={b.mode} "
                  f"surf={b.surf} y={b.y:.0f} state={b.state} top now={[t for t in b.smap.tops if t.hwnd == hwnd]})",
                  flush=True)
        b.set_state(S.LOAFING, travel=False)
        b.state_dur = 1e6
        return ok

    def say(h: subprocess.Popen, cmd: str, wait_for: str, sec: float = 8) -> bool:
        assert h.stdin is not None and h.stdout is not None
        try:
            h.stdin.write(cmd + "\n")
            h.stdin.flush()
        except OSError:                       # (the helper quit with its last window)
            return False
        got: list[str] = []
        import threading

        threading.Thread(target=lambda: got.append(h.stdout.readline().strip()), daemon=True).start()
        return pump(sec, lambda: bool(got) and got[0].startswith(wait_for))

    pump(1.5)
    # ---------------------------------------------------------------- drag, then close
    h, hwnd = start_helper()
    check("the cat sits on the test window", sit_on(hwnd))
    samples.clear()
    hwnd_box[0] = hwnd
    t0 = time.perf_counter()
    dragged = say(h, "drag", "dragged")
    secs = time.perf_counter() - t0
    hwnd_box[0] = 0
    moving = samples[5:]
    offs_x = [s[0] for s in moving]
    offs_y = [s[1] for s in moving]
    air = sum(1 for s in moving if s[2] != "ground")
    spread = (max(offs_x) - min(offs_x), max(offs_y) - min(offs_y)) if moving else (999, 999)
    check("the window is dragged around for 2.5 s: the cat stays at its spot on the window, never in the air",
          dragged and air == 0 and spread[0] <= 30 and spread[1] <= 30,
          f"offset spread {spread[0]:.0f}x{spread[1]:.0f} px over {len(moving)} frames, {air} in the air")
    fps = len(moving) / max(0.1, secs)
    check("...updated about every screen frame while riding", fps >= 40, f"{fps:.0f} frames/s")
    pump(0.6)
    y_close = b.y
    lowest_y = [b.y]
    hwnd_box[0] = 0
    say(h, "close", "closed")

    def watch() -> bool:
        lowest_y.append(b.y)
        return b.mode == "ground" and b.surf is not None and b.surf.hwnd != hwnd
    landed = pump(6, watch)
    check("closing the window: the cat falls from where it sat (not from the top of the screen) and lands",
          landed and min(lowest_y) >= y_close - 60, f"sat at y={y_close:.0f}, highest {min(lowest_y):.0f}, "
                                                    f"landed at {b.y:.0f}")
    say(h, "quit", "")
    # ---------------------------------------------------------------- a window that snaps
    h, hwnd = start_helper()
    check("the cat sits on a second test window", sit_on(hwnd))
    y_jump = b.y
    lowest_y = [b.y]
    say(h, f"jump {mon.work[0]} {mon.work[1] + 40} {(mon.work[2] - mon.work[0]) // 2} {mon.work[3] - mon.work[1] - 40}",
        "jumped")
    pump(6, watch)
    check("the window snaps to half the screen: the cat is dropped where it was", min(lowest_y) >= y_jump - 60
          and b.mode == "ground", f"sat at y={y_jump:.0f}, highest {min(lowest_y):.0f}")
    say(h, "quit", "")
    # ---------------------------------------------------------------- our own chat window
    pump(1.0)
    pet._show_chat_quietly()                  # (shown without taking the keyboard)
    chat = pet.chat
    assert chat is not None
    chat_hwnd = int(chat.winId())
    win32.move_window(chat_hwnd, wx, wy, chat.width(), chat.height(), topmost=False)
    pump(0.5)
    check("the chat window is something to sit on", sit_on(chat_hwnd))
    g0 = win32.frame_bounds(chat_hwnd)
    for i in range(40):                       # the user drags the chat by its title bar
        win32.move_window(chat_hwnd, wx + i * 6, wy - i * 3, chat.width(), chat.height(), topmost=False)
        pump(0.016)
    g1 = win32.frame_bounds(chat_hwnd)
    check("dragging the chat carries the cat along", b.mode == "ground" and b.surf is not None
          and b.surf.hwnd == chat_hwnd and g0 is not None and g1 is not None
          and abs((b.x - g1[0]) - (b.x - g1[0])) < 1 and abs(b.y - g1[1]) <= 2, f"cat y {b.y:.0f}, chat top {g1[1] if g1 else None}")
    y_hide = b.y
    lowest_y = [b.y]
    chat.hide()

    def watch2() -> bool:
        lowest_y.append(b.y)
        return b.mode == "ground" and b.surf is not None and b.surf.hwnd != chat_hwnd
    pump(6, watch2)
    check("closing the chat: the cat falls from there", min(lowest_y) >= y_hide - 60 and b.mode == "ground",
          f"sat at y={y_hide:.0f}, highest {min(lowest_y):.0f}")
    pet.quit()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        for hp in HELPERS:
            if hp.poll() is None:
                hp.kill()
    os._exit(rc)
