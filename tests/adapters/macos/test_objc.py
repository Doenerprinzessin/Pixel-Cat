"""pixelcat.adapters.macos._objc: lazy pyobjc proxies, the subprocess helpers, own window ids, the GUI thread."""
from __future__ import annotations

import sys
import threading
import time

import pytest

from pixelcat.adapters.macos import _objc

REAL_RUN, REAL_SPAWN = _objc.run, _objc.spawn      # (taken at import, before the fixture swaps them)


def test_a_proxy_imports_its_module_on_first_use(monkeypatch):  # type: ignore[no-untyped-def]
    lazy = _objc._Lazy("json")
    monkeypatch.delitem(sys.modules, "json", raising=False)
    assert "json" not in sys.modules
    assert lazy.dumps([1]) == "[1]" and "json" in sys.modules


def test_a_missing_module_raises_on_use_not_on_import():
    lazy = _objc._Lazy("no_such_pyobjc_module")
    with pytest.raises(ImportError):
        lazy.anything  # noqa: B018


def test_every_proxy_is_swapped_in_unit_tests():
    for name in _objc.PROXIES:
        with pytest.raises(AssertionError, match="real pyobjc"):
            getattr(_objc, name).anything  # noqa: B018
    with pytest.raises(AssertionError, match="real pyobjc"):
        _objc.run(["/usr/bin/true"], timeout=1)


def test_run_and_spawn_never_use_a_shell(monkeypatch):  # type: ignore[no-untyped-def]
    import subprocess

    seen: list = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.append((argv, kw)) or "done")
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: seen.append((argv, kw)) or "proc")
    assert REAL_RUN(["ps", "-ax"], 5, input="x") == "done"
    assert REAL_SPAWN(["ollama", "serve"], cwd="/opt") == "proc"
    (argv, kw), (argv2, kw2) = seen
    assert argv == ["ps", "-ax"] and kw["timeout"] == 5 and kw["input"] == "x" and not kw.get("shell")
    assert argv2 == ["ollama", "serve"] and kw2["start_new_session"] and kw2["cwd"] == "/opt" and not kw2.get("shell")


@pytest.mark.parametrize(("win", "own"), [(0, False), (123, False), ((1 << 32) - 1, False), (1 << 32, True),
                                          (0x9C58D5500, True), (None, False)])
def test_is_own(win, own):  # type: ignore[no-untyped-def]
    assert _objc.is_own(win) is own


def test_on_main_runs_inline_on_the_gui_thread(qapp):  # type: ignore[no-untyped-def]
    assert _objc.is_main_thread()
    assert _objc.on_main(lambda: threading.current_thread(), 0.1, None) is threading.current_thread()


def test_on_main_from_a_worker_runs_on_the_gui_thread(qapp, qtbot):  # type: ignore[no-untyped-def]
    out: dict = {}

    def worker() -> None:
        out["main"] = _objc.is_main_thread()
        out["value"] = _objc.on_main(lambda: (threading.current_thread(), _objc.is_main_thread()), 5.0, None)

    t = threading.Thread(target=worker)
    t.start()
    qtbot.waitUntil(lambda: "value" in out, timeout=5000)
    t.join()
    assert out["main"] is False and out["value"] == (threading.main_thread(), True)


def test_on_main_raises_what_the_function_raised(qapp, qtbot):  # type: ignore[no-untyped-def]
    out: dict = {}

    def boom() -> None:
        raise ValueError("nope")

    def worker() -> None:
        try:
            _objc.on_main(boom, 5.0, None)
        except ValueError as exc:
            out["error"] = str(exc)

    t = threading.Thread(target=worker)
    t.start()
    qtbot.waitUntil(lambda: "error" in out, timeout=5000)
    t.join()
    assert out["error"] == "nope"


def test_on_main_times_out_with_the_default_and_never_runs_late(qapp, qtbot):  # type: ignore[no-untyped-def]
    ran: list = []
    out: dict = {}

    def worker() -> None:
        out["value"] = _objc.on_main(lambda: ran.append(1) or "late", 0.05, [])

    t = threading.Thread(target=worker)
    t.start()
    t.join()                    # the GUI thread is busy (blocked here): the call gives up
    assert out["value"] == []
    qtbot.wait(50)              # the posted job comes up now and is skipped
    assert ran == []


def test_on_main_without_a_qt_application_gives_the_default(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_objc, "_app", lambda: None)
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("value", _objc.on_main(lambda: 1, 0.1, 0)))
    t.start()
    t.join()
    assert out["value"] == 0
    started = time.monotonic()
    assert _objc.on_main(lambda: 7, 0.1, 0) == 7 and time.monotonic() - started < 1      # the main thread: inline
