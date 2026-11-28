"""pixelcat.adapters.null: the ports on platforms the cat doesn't support yet - safe answers, no OS calls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pixelcat.adapters import null
from pixelcat.domain import FALLBACK_MONITOR, Hardware, Snapshot, Unreadable, UserState

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def plat():  # type: ignore[no-untyped-def]
    return null.build()


# Watched modules, and for each import of one the module whose code asked for it.  On Windows the interpreter
# itself loads winreg at startup (importlib._bootstrap_external: ``import winreg``), and the standard library
# may load ctypes or winreg on its own; only a request from outside the standard library (pixelcat) counts.
_PROBE = """
import json, sys
WATCHED = ("ctypes", "_ctypes", "winreg", "PySide6", "pixelcat.adapters.windows")
preloaded = sorted(sys.modules)
events = []

def importer():
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_globals.get("__name__", "?")
        if not (name == "importlib" or name.startswith(("importlib.", "_frozen_importlib"))):
            return name
        frame = frame.f_back
    return "?"

class Recorder:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if any(name == w or name.startswith(w + ".") for w in WATCHED):
            events.append((name, importer()))
        return None

sys.meta_path.insert(0, Recorder)
import pixelcat.adapters.null as n
p = n.build()
assert p.name == 'null' and p.system.acquire_single_instance() and p.display.monitors() == [n.FALLBACK_MONITOR]
with p.input.holding(['ctrl']), p.capture.excluding(1):
    p.windows.own_click_through(1, 2)
print("@@probe" + json.dumps({"preloaded": preloaded, "loaded": sorted(sys.modules), "events": events,
                              "stdlib": sorted(sys.stdlib_module_names)}))
"""


def test_loads_and_works_without_ctypes_winreg_qt_or_the_windows_adapter():
    res = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert res.returncode == 0, res.stderr
    out = json.loads([line for line in res.stdout.splitlines() if line.startswith("@@probe")][-1][len("@@probe"):])
    loaded, stdlib = set(out["loaded"]), set(out["stdlib"])
    assert "pixelcat.adapters.null" in loaded
    assert not {m for m in loaded if m.split(".")[0] == "PySide6" or m.startswith("pixelcat.adapters.windows")}
    asked = {(m, by) for m, by in out["events"] if by.split(".")[0] not in stdlib}
    assert not asked, f"loaded by non-stdlib code: {sorted(asked)}"
    # whatever else got loaded was already there at startup, or the standard library asked for it
    win32 = {m for m in loaded if m.split(".")[0] in ("ctypes", "_ctypes", "winreg")}
    assert win32 <= set(out["preloaded"]) | {m for m, _ in out["events"]}, win32


def test_answers_nothing_there(plat):  # type: ignore[no-untyped-def]
    # never no monitor (the core needs a primary one): the same fallback the Windows adapter answers
    assert plat.display.monitors() == [FALLBACK_MONITOR] and FALLBACK_MONITOR.primary
    assert plat.display.virtual_screen() == FALLBACK_MONITOR.rect == (0, 0, 1920, 1080)
    assert plat.display.window_dpi(7) == 0 and plat.display.user_state() is UserState.NORMAL
    assert plat.display.set_dpi_awareness() is False and plat.display.session_locked() is False
    assert plat.windows.top_level(1) == [] and plat.windows.app_windows(1) == [] and plat.windows.foreground() == 0
    assert plat.windows.frame_bounds(7) is None and plat.windows.title(7) == "" and plat.windows.exists(7) is False
    assert plat.windows.own_click_through(1, 2) == []
    assert plat.input.cursor_pos() == (0, 0) and plat.input.idle_seconds() == 0.0
    assert plat.clipboard.backup() is None and plat.clipboard.text() is None and plat.clipboard.has("files") is False
    assert plat.accessibility.snapshot(7) == Snapshot(hwnd=7, window_rect=(0, 0, 0, 0))
    assert plat.accessibility.focused_element() is None and plat.accessibility.document_text(7) == ""
    assert plat.capture.grab_window(7, (0, 0, 1, 1)) is None and plat.capture.snapshot((0, 0, 1, 1)) is None
    assert plat.notifications.open_feed().available() is False and plat.notifications.open_feed().since(0) == []
    assert plat.folders.known_folder("documents") is None
    assert plat.system.hardware() == Hardware() and plat.system.instance == ""


def test_grab_gives_an_empty_image(plat, qapp):  # type: ignore[no-untyped-def]
    assert plat.capture.grab((0, 0, 10, 10)).isNull()
    assert plat.capture.grab_without((0, 0, 10, 10), 1).isNull()


def test_a_second_cat_is_not_stopped(plat):  # type: ignore[no-untyped-def]
    assert plat.system.acquire_single_instance() is True


def test_secrets_cant_be_kept(plat):  # type: ignore[no-untyped-def]
    with pytest.raises(OSError):
        plat.secrets.protect(b"data")
    with pytest.raises(Unreadable):
        plat.secrets.unprotect(b"blob")


def test_actions_fail_the_way_their_callers_expect(plat):  # type: ignore[no-untyped-def]
    for start in (lambda: plat.apps.open("x"), lambda: plat.apps.open_elevated("x", []),
                  lambda: plat.apps.open_settings("display")):
        with pytest.raises(OSError):
            start()
    with pytest.raises(ValueError):
        plat.processes.plan_force_end("chrome", 1)
    assert plat.apps.run_as_admin("x") == (False, null.UNSUPPORTED)
    assert plat.processes.force_end_elevated([1]) == (False, null.UNSUPPORTED)
    assert plat.input.pinch(1, 2, 10.0, 90.0) == "failed"
    assert plat.input.drag([(0, 0), (1, 1)]) == "" and plat.input.glide(1, 2) == ""
    assert plat.autostart.set(True) is False and plat.installer.run_window("install") == 1


def test_contexts_work(plat):  # type: ignore[no-untyped-def]
    with plat.input.holding(["ctrl", "shift"]), plat.capture.excluding(1):
        pass
    watcher = plat.input.escape_watcher(lambda: None)
    watcher.start()
    assert watcher.active is False
    watcher.stop()


def test_passthrough_flag(plat):  # type: ignore[no-untyped-def]
    assert plat.input.passthrough_active() is False
    plat.input.set_passthrough_until(time.monotonic() + 60)
    assert plat.input.passthrough_active() is True
    plat.input.set_passthrough_until(0.0)
    assert plat.input.passthrough_active() is False


def test_parse_combo_gives_the_key_names(plat):  # type: ignore[no-untyped-def]
    assert plat.input.parse_combo(" Ctrl + Shift+S ") == ["ctrl", "shift", "s"]
    assert plat.input.parse_combo("+") == ["plus"]
    with pytest.raises(ValueError):
        plat.input.parse_combo("  ")
