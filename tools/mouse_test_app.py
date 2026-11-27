"""A harmless test window for mouse_test.py (developer tool).

Window title "PixelCat Mouse Test": a pad that records every mouse press, release, move,
wheel and touch (with buttons, keys held and positions), a box that notices hovering, a
board that changes when told to (for watch_screen), a small ticking clock and a text label.
Records keys pressed anywhere in the window.  Commands on stdin (one per line):
report, reset, flash, flash_in S, ticker on|off, text_in S TEXT, clear_in S, animate S, quit.
Prints one JSON line per "report", and {"ready": ..., widget rects} at the start.
"""
from __future__ import annotations

import json
import math
import os
import queue
import random
import sys
import threading
import time

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout,  # noqa: E402
                               QWidget)

BUTTONS = {Qt.MouseButton.LeftButton: "left", Qt.MouseButton.RightButton: "right",
           Qt.MouseButton.MiddleButton: "middle", Qt.MouseButton.BackButton: "back",
           Qt.MouseButton.ForwardButton: "forward"}
state: dict = {}


def reset() -> None:
    state.update(presses=[], releases=[], moves=0, move_mods=[], wheels=[], touch={"points": 0, "d0": 0.0, "d1": 0.0,
                 "updates": 0}, hovered=False, keys=[])


def mods(m) -> list[str]:  # type: ignore[no-untyped-def]
    out = []
    for flag, name in ((Qt.KeyboardModifier.ControlModifier, "ctrl"), (Qt.KeyboardModifier.ShiftModifier, "shift"),
                       (Qt.KeyboardModifier.AltModifier, "alt")):
        if m & flag:
            out.append(name)
    return out


class Pad(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(480, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background:#dde6f0;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _rec(self, kind: str, e) -> None:  # type: ignore[no-untyped-def]
        g = e.globalPosition()
        state[kind].append({"b": BUTTONS.get(e.button(), "?"), "mods": mods(e.modifiers()), "x": round(g.x()),
                            "y": round(g.y()), "t": time.time()})

    def mousePressEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        self._rec("presses", e)

    def mouseDoubleClickEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        self._rec("presses", e)

    def mouseReleaseEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        self._rec("releases", e)

    def mouseMoveEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        if e.buttons():
            state["moves"] += 1
            state["move_mods"].append("+".join(mods(e.modifiers())))

    def wheelEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        g = e.globalPosition()
        state["wheels"].append({"dy": e.angleDelta().y(), "dx": e.angleDelta().x(), "mods": mods(e.modifiers()),
                                "x": round(g.x()), "y": round(g.y())})

    def event(self, e) -> bool:  # type: ignore[no-untyped-def]
        if e.type() in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd):
            pts = e.points()
            t = state["touch"]
            t["points"] = max(t["points"], len(pts))
            if len(pts) >= 2:
                a, b = pts[0].globalPosition(), pts[1].globalPosition()
                d = math.hypot(a.x() - b.x(), a.y() - b.y())
                if not t["d0"]:
                    t["d0"] = d
                t["d1"] = d
                t["updates"] += 1
            e.accept()
            return True
        return super().event(e)


class HoverBox(QLabel):
    def __init__(self) -> None:
        super().__init__("hover me")
        self.setFixedSize(220, 90)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#f3e1a8;")

    def enterEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        state["hovered"] = True
        self.setStyleSheet("background:#a8f3b4;")


class Keys(QObject):
    def eventFilter(self, obj, e) -> bool:  # type: ignore[no-untyped-def]
        if e.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and not e.isAutoRepeat() \
                and obj.isWidgetType() and obj.isWindow():
            name = "space" if e.key() == Qt.Key.Key_Space else str(e.key())
            if e.type() == QEvent.Type.KeyPress:
                state["keys"].append({"k": name, "down": time.time(), "up": 0.0})
            else:
                for k in reversed(state["keys"]):
                    if k["k"] == name and not k["up"]:
                        k["up"] = time.time()
                        break
        return False


def main() -> None:
    reset()
    app = QApplication(sys.argv[:1])
    win = QMainWindow()
    win.setWindowTitle("PixelCat Mouse Test")
    central = QWidget()
    row = QHBoxLayout(central)
    pad = Pad()
    row.addWidget(pad)
    col = QVBoxLayout()
    hover = HoverBox()
    col.addWidget(hover)
    board = QLabel("")
    board.setFixedSize(300, 220)
    board.setAlignment(Qt.AlignmentFlag.AlignCenter)
    board.setStyleSheet("background:#3050c0; color:white; font-size:22px;")
    col.addWidget(board)
    clock = QLabel("00")
    clock.setFixedSize(26, 14)
    clock.setStyleSheet("font-size:10px;")
    col.addWidget(clock)
    col.addStretch(1)
    row.addLayout(col)
    win.setCentralWidget(central)
    keys = Keys()
    app.installEventFilter(keys)
    geo = [int(v) for v in sys.argv[1:5]] if len(sys.argv) >= 5 else [160, 120, 900, 520]
    win.setGeometry(*geo)
    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    win.show()
    flash = {"on": False}

    def toggle() -> None:
        flash["on"] = not flash["on"]
        board.setStyleSheet(f"background:{'#e05030' if flash['on'] else '#3050c0'}; color:white; font-size:22px;")

    ticker = QTimer()
    ticker.setInterval(300)
    ticker.timeout.connect(lambda: clock.setText(f"{int(time.time() * 10) % 100:02d}"))
    anim = QTimer()
    anim.setInterval(100)
    anim.timeout.connect(lambda: board.setStyleSheet(
        f"background:#{random.randint(0, 0xFFFFFF):06x}; color:white; font-size:22px;"))

    def rect(w: QWidget) -> list[int]:
        p = w.mapToGlobal(QPoint(0, 0))
        return [p.x(), p.y(), w.width(), w.height()]

    def ready() -> None:
        pad.setFocus()
        print(json.dumps({"ready": True, "pad": rect(pad), "hover": rect(hover), "board": rect(board),
                          "clock": rect(clock)}), flush=True)

    QTimer.singleShot(400, ready)
    cmds: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [cmds.put(line.strip()) for line in sys.stdin], daemon=True).start()

    def poll() -> None:
        while not cmds.empty():
            c = cmds.get()
            parts = c.split(" ", 2)
            if c == "quit":
                app.quit()
                return
            if c == "report":
                print(json.dumps(state), flush=True)
            elif c == "reset":
                reset()
            elif c == "flash":
                toggle()
            elif parts[0] == "flash_in":
                QTimer.singleShot(int(float(parts[1]) * 1000), toggle)
            elif c == "ticker on":
                ticker.start()
            elif c == "ticker off":
                ticker.stop()
            elif parts[0] == "text_in":
                QTimer.singleShot(int(float(parts[1]) * 1000), lambda t=parts[2]: board.setText(t))
            elif parts[0] == "clear_in":
                QTimer.singleShot(int(float(parts[1]) * 1000), lambda: board.setText(""))
            elif parts[0] == "animate":
                anim.start()
                QTimer.singleShot(int(float(parts[1]) * 1000), anim.stop)
        QTimer.singleShot(30, poll)

    QTimer.singleShot(30, poll)
    app.exec()


if __name__ == "__main__":
    main()
