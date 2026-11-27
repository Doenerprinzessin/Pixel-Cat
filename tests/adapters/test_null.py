"""pixelcat.adapters.null: the ports on platforms the cat doesn't support yet - safe answers, no OS calls."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pixelcat.adapters import null
from pixelcat.domain import Hardware, Snapshot, Unreadable, UserState

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def plat():  # type: ignore[no-untyped-def]
    return null.build()


def test_loads_and_works_without_ctypes_winreg_qt_or_the_windows_adapter():
    probe = ("import sys, pixelcat.adapters.null as n\n"
             "p = n.build()\n"
             "assert p.name == 'null' and p.system.acquire_single_instance() and p.display.monitors() == []\n"
             "with p.input.holding(['ctrl']), p.capture.excluding(1):\n"
             "    p.windows.own_click_through(1, 2)\n"
             "print('\\n'.join(sys.modules))\n")
    res = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert res.returncode == 0, res.stderr
    loaded = set(res.stdout.split())
    assert "pixelcat.adapters.null" in loaded
    bad = {m for m in loaded if m.split(".")[0] in ("ctypes", "_ctypes", "winreg", "PySide6")
           or m.startswith("pixelcat.adapters.windows")}
    assert not bad


def test_answers_nothing_there(plat):  # type: ignore[no-untyped-def]
    assert plat.display.monitors() == [] and plat.display.virtual_screen() == (0, 0, 0, 0)
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
