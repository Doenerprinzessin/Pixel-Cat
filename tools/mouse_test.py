"""Mouse and screen-watching test (developer tool) - REAL input on a test window of its own.

Opens tools/mouse_test_app.py ("PixelCat Mouse Test", it records every press, move, wheel,
touch, hover and key) and drives it through the chat's tools without a model: click (any
button, triple, held keys, long press), drag (path, held keys, stops when the user moves the
mouse), zoom (ctrl + wheel, touch pinch), scroll at a spot, hover, holding keys, and
watch_screen (a change, one that already happened, small changes ignored, an area, a text
appearing / going away, stillness, Esc).  The pointer moves and the test window comes to
the front for about a minute - don't touch the mouse meanwhile.  The pointer goes back at
the end.  No screenshot is saved.
Usage:  python tools/mouse_test.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

results: list[tuple[str, bool, str]] = []
T = "PixelCat Mouse Test"


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def near(p: dict, xy: tuple[int, int], tol: int = 4) -> bool:
    return abs(p["x"] - xy[0]) <= tol and abs(p["y"] - xy[1]) <= tol


def main() -> int:  # noqa: C901 - a linear test script
    from PySide6.QtWidgets import QApplication

    from pixelcat.adapters.windows import inputs, system, win32
    from pixelcat.logsetup import setup_logging

    if system.session_locked():
        print("The PC is locked - unlock it first (real mouse input only works on an unlocked desktop).", flush=True)
        return 2
    setup_logging(False)
    win32.set_dpi_awareness()
    app = QApplication(sys.argv[:1])          # screenshots and their marks need it
    from pixelcat.llm.tools import AppHooks, ToolBox, ToolError
    from pixelcat.settings import load_config

    helper = subprocess.Popen([sys.executable, str(ROOT / "tools" / "mouse_test_app.py")], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    assert helper.stdin is not None and helper.stdout is not None
    info = json.loads(helper.stdout.readline())

    def cmd(c: str) -> None:
        helper.stdin.write(c + "\n")
        helper.stdin.flush()

    def report() -> dict:
        time.sleep(0.25)
        app.processEvents()
        cmd("report")
        return json.loads(helper.stdout.readline())

    tb = ToolBox(load_config(), AppHooks(lambda a: "", lambda s, label: "", lambda: "", lambda t: "", os.getpid()))
    ag = tb.agent
    start_cursor = win32.cursor_pos()

    def run(name: str, **args):  # type: ignore[no-untyped-def]
        spec, a = tb.prepare(name, args)
        return a, tb.run(spec, a)

    def img(gx: float, gy: float) -> tuple[float, float]:
        s = ag.shot
        assert s is not None
        return round((gx - s.origin[0]) * s.scale, 1), round((gy - s.origin[1]) * s.scale, 1)

    def timed(name: str, **args):  # type: ignore[no-untyped-def]
        t0 = time.time()
        _a, out = run(name, **args)
        return out, time.time() - t0

    pad, hover = info["pad"], info["hover"]
    mid = (pad[0] + pad[2] // 2, pad[1] + pad[3] // 2)
    try:
        run("look_at_screen", target=T)
        check("look_at_screen sees the test window", ag.shot is not None and ag.shot.hwnd != 0)

        # ---- clicks
        cmd("reset")
        run("click", **dict(zip("xy", img(*mid))), button="right", clicks=3, keys="ctrl")
        r = report()
        rp = [p for p in r["presses"] if p["b"] == "right"]
        check("click: right button, three times, ctrl held, on the spot", len(rp) == 3 and all(
            "ctrl" in p["mods"] and near(p, mid) for p in rp), str(r["presses"]))
        cmd("reset")
        run("click", **dict(zip("xy", img(*mid))), button="back")
        r = report()
        check("click: the mouse's back button", [p["b"] for p in r["presses"]] == ["back"], str(r["presses"]))
        cmd("reset")
        run("click", **dict(zip("xy", img(*mid))), hold=0.8)
        r = report()
        held = r["releases"][0]["t"] - r["presses"][0]["t"] if r["presses"] and r["releases"] else 0
        check("click hold=0.8: a long press", len(r["presses"]) == 1 and 0.7 <= held <= 1.6, f"{held:.2f} s")

        # ---- drag
        a, m, b = (pad[0] + 60, pad[1] + 60), (pad[0] + 200, pad[1] + 330), (pad[0] + 410, pad[1] + 90)
        cmd("reset")
        fa, fm, fb = img(*a), img(*m), img(*b)
        _a, out = run("drag", from_x=fa[0], from_y=fa[1], to_x=fb[0], to_y=fb[1], path=[list(fm)], keys="shift")
        r = report()
        check("drag: pressed at the start, moved along the path, let go at the end",
              len(r["presses"]) == 1 and near(r["presses"][0], a) and len(r["releases"]) == 1
              and near(r["releases"][0], b) and r["moves"] >= 20, f"{r['presses']} -> {r['releases']}, {r['moves']} moves")
        check("drag: shift held all the way; a screenshot comes back", r["move_mods"] and all(
            "shift" in mm for mm in r["move_mods"]) and bool(out.images), out.text[:90])
        cmd("reset")

        def jiggle() -> None:
            time.sleep(0.6)
            inputs.move_cursor(pad[0] + 30, pad[1] + pad[3] - 30)

        th = threading.Thread(target=jiggle)
        th.start()
        fa, fb = img(*a), img(*b)
        _a, out = run("drag", from_x=fa[0], from_y=fa[1], to_x=fb[0], to_y=fb[1], seconds=2.5)
        th.join()
        r = report()
        check("drag: stops and lets go when you move the mouse", "moved the mouse" in out.text
              and len(r["presses"]) == len(r["releases"]) == 1 and not win32.mouse_buttons_down(), out.text[:90])

        # ---- zoom, scroll, hover, keys
        cmd("reset")
        run("zoom", direction="in", **dict(zip("xy", img(*mid))), steps=3)
        r = report()
        check("zoom in: ctrl + mouse wheel up, 3 steps", len(r["wheels"]) == 3 and all(
            w["dy"] > 0 and "ctrl" in w["mods"] for w in r["wheels"]), str(r["wheels"]))
        cmd("reset")
        _a, out = run("zoom", direction="in", **dict(zip("xy", img(*mid))), steps=3, how="pinch")
        r = report()
        t = r["touch"]
        if "didn't take a touch pinch" in out.text:
            check("zoom pinch: Windows refused touch here - fell back to ctrl + wheel", len(r["wheels"]) == 3, out.text)
        else:
            check("zoom pinch: two fingers that move apart", t["points"] >= 2 and t["d1"] > t["d0"] + 100,
                  f"{t}")
        cmd("reset")
        spot = img(pad[0] + 100, pad[1] + 100)
        run("scroll", direction="down", amount=2, x=spot[0], y=spot[1], keys="shift")
        r = report()
        check("scroll at a spot with shift held", len(r["wheels"]) == 2 and all(
            "shift" in w["mods"] and near(w, (pad[0] + 100, pad[1] + 100)) for w in r["wheels"]), str(r["wheels"]))
        cmd("reset")
        hc = (hover[0] + hover[2] // 2, hover[1] + hover[3] // 2)
        _a, out = run("move_mouse", **dict(zip("xy", img(*hc))), seconds=0.5)
        r = report()
        cx, cy = win32.cursor_pos()
        check("move_mouse: hovers and stays there; a picture of it comes back", r["hovered"] and abs(cx - hc[0]) <= 4
              and abs(cy - hc[1]) <= 4 and bool(out.images), out.text[:80])
        cmd("reset")
        run("press_keys", keys="space", hold=0.6, target=T)
        r = report()
        k = [x for x in r["keys"] if x["k"] == "space"]
        check("press_keys hold=0.6: the key stays down", len(k) == 1 and 0.5 <= k[0]["up"] - k[0]["down"] <= 1.4,
              str(k))

        # ---- watch_screen
        run("look_at_screen", target=T)
        cmd("flash_in 1.2")
        out, dt = timed("watch_screen", target=T, seconds=10)
        check("watch_screen: wakes up when the screen changes, with a fresh screenshot",
              "changed after" in out.text and 1.0 <= dt <= 6 and bool(out.images), f"{dt:.1f} s: {out.text[:60]}")
        cmd("flash")
        time.sleep(0.4)
        out, dt = timed("watch_screen", target=T, seconds=10)
        check("...a change that happened since the latest screenshot counts at once (the bot already moved)",
              "already changed" in out.text and dt < 4, f"{dt:.1f} s: {out.text[:60]}")
        cmd("ticker on")
        out, dt = timed("watch_screen", target=T, seconds=2)
        check("a ticking clock doesn't wake it (normal sensitivity)", "Nothing changed" in out.text and not out.images,
              f"{dt:.1f} s: {out.text[:60]}")
        cmd("ticker off")
        x0, y0 = img(pad[0], pad[1])
        s = ag.shot.scale if ag.shot else 1.0
        cmd("flash_in 0.6")
        out, dt = timed("watch_screen", target=T, area=[x0, y0, pad[2] * s, pad[3] * s], seconds=2.5)
        check("area: a change outside the watched part doesn't count", "Nothing changed" in out.text,
              f"{dt:.1f} s: {out.text[:60]}")
        out, dt = timed("watch_screen", target=T, seconds=5)
        check("...but it does for the whole window", "already changed" in out.text, f"{dt:.1f} s: {out.text[:60]}")
        cmd("text_in 1 Question 7!")
        out, dt = timed("watch_screen", target=T, until="text", text="Question 7", seconds=10)
        check("until=text: wakes up when the text appears", "is there" in out.text and 0.8 <= dt <= 8,
              f"{dt:.1f} s: {out.text[:60]}")
        cmd("clear_in 1")
        out, dt = timed("watch_screen", target=T, until="gone", text="Question 7", seconds=10)
        check("until=gone: ...and when it goes away", "is gone" in out.text and 0.8 <= dt <= 8,
              f"{dt:.1f} s: {out.text[:60]}")
        cmd("animate 1.5")
        time.sleep(0.2)
        out, dt = timed("watch_screen", target=T, until="still", seconds=10)
        check("until=still: waits until the animation is over", "stopped changing" in out.text and dt >= 1.3,
              f"{dt:.1f} s: {out.text[:60]}")

        def esc() -> None:
            time.sleep(0.6)
            ag.cancel.set()

        th = threading.Thread(target=esc)
        th.start()
        out, dt = timed("watch_screen", target=T, seconds=30)
        th.join()
        ag.cancel.clear()
        check("Esc / Stop ends the watching at once", "Stopped watching" in out.text and dt < 3, f"{dt:.1f} s")

        # ---- what is refused
        def refused(name: str, **args) -> str:  # type: ignore[no-untyped-def]
            try:
                tb.prepare(name, args)
            except ToolError as exc:
                return str(exc)
            return ""

        p = img(*mid)
        bad = {"click holding a letter": refused("click", x=p[0], y=p[1], keys="a"),
               "drag without a target": refused("drag", from_x=p[0], from_y=p[1]),
               "a path point outside the screenshot": refused("drag", from_x=p[0], from_y=p[1], path=[[99999, 5]]),
               "until=text without a text": refused("watch_screen", target=T, until="text"),
               "an area of another picture": refused("watch_screen", target="screen", area=[0, 0, 10, 10]),
               "zoom sideways": refused("zoom", direction="sideways", target=T)}
        check("wrong requests are refused with a reason", all(bad.values()), str(bad))
        _s, a = tb.prepare("drag", {"from_x": p[0], "from_y": p[1], "to_x": p[0] + 50, "to_y": p[1], "keys": "ctrl"})
        check("the confirmation says what will happen", a["_desc"].startswith("drag from ")
              and "holding ctrl" in a["_desc"] and _s.group == "desktop", a["_desc"])
    finally:
        try:
            cmd("quit")
            helper.wait(5)
        except Exception:  # noqa: BLE001
            helper.kill()
        inputs.move_cursor(*start_cursor)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    rc = main()
    os._exit(rc)
