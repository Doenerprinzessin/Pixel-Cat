"""The macOS adapter's only door to pyobjc, to subprocesses and to the GUI thread.

* ``objc``, ``AK`` (AppKit), ``FN`` (Foundation), ``Q`` (Quartz: CoreGraphics), ``AX`` (ApplicationServices:
  HIServices / Accessibility), ``SEC`` (Security), ``SCK`` (ScreenCaptureKit), ``CF`` (CoreFoundation) are
  lazy stand-ins for the pyobjc modules: the module is imported on the first attribute use.  So every module
  of this adapter imports without pyobjc (boundary and conformance tests run on every OS), and tests swap a
  whole proxy (``monkeypatch.setattr(_objc, "Q", FakeQuartz())``).  Always look the proxy up through this
  module (``_objc.Q.CGEventCreate(...)``), never ``from ._objc import Q``, or a test's swap can't reach you.
* :func:`run` / :func:`spawn` start programs (osascript, open, pmset, ps ...); tests replace them too.
* :func:`on_main` runs a function on the GUI thread (AppKit and Qt widgets must only be used there).
"""
from __future__ import annotations

import importlib
import logging
import subprocess
import threading
from types import ModuleType
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

OWN_ID_MIN = 1 << 32    # the cat's own window ids are NSView pointers (>= 4 GiB: __PAGEZERO); CGWindowIDs are 32 bit


class _Lazy:
    """Stands for a pyobjc module and imports it when one of its names is used."""

    def __init__(self, module: str) -> None:
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_loaded", None)

    def _load(self) -> ModuleType:
        mod = self._loaded
        if mod is None:
            mod = importlib.import_module(self._module)
            object.__setattr__(self, "_loaded", mod)
        return mod

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._load(), attr)

    def __repr__(self) -> str:
        return f"<lazy pyobjc module {self._module}>"


objc = _Lazy("objc")
AK = _Lazy("AppKit")
FN = _Lazy("Foundation")
Q = _Lazy("Quartz")
AX = _Lazy("ApplicationServices")
SEC = _Lazy("Security")
SCK = _Lazy("ScreenCaptureKit")
CF = _Lazy("CoreFoundation")

# the proxies by name (the tests swap every one of them)
PROXIES = ("objc", "AK", "FN", "Q", "AX", "SEC", "SCK", "CF")


def available() -> bool:
    """True if pyobjc is installed."""
    import importlib.util

    return importlib.util.find_spec("objc") is not None


# ------------------------------------------------------------------ subprocesses
def run(argv: list[str], timeout: float, input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a program and wait for it (text in and out, never a shell); raises OSError / TimeoutExpired."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input, check=False,
                          close_fds=True)


def spawn(argv: list[str], cwd: str | None = None) -> subprocess.Popen[bytes]:
    """Start a program in the background, detached from the cat (its own session, no streams)."""
    return subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)


# ------------------------------------------------------------------ the cat's own windows
def is_own(win: int) -> bool:
    """True for one of the cat's own window ids (a Qt ``winId()``: an NSView pointer), not a CGWindowID.
    Never dereference it from here: resolve it with ``QWidget.find`` on the GUI thread first (it may be gone)."""
    try:
        return int(win) >= OWN_ID_MIN
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ the GUI thread
def _app() -> Any:
    from PySide6.QtCore import QCoreApplication

    return QCoreApplication.instance()


def is_main_thread() -> bool:
    """True on the GUI thread (the thread that runs Qt's event loop; the main thread before Qt starts)."""
    app = _app()
    if app is None:
        return threading.current_thread() is threading.main_thread()
    from PySide6.QtCore import QThread

    return QThread.currentThread() is app.thread()


_bridge: Any = None
_bridge_lock = threading.Lock()


def _get_bridge(app: Any) -> Any:
    """A QObject living on the GUI thread whose signal runs the jobs posted to it there."""
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            from PySide6.QtCore import QObject, Qt, Signal

            class _Bridge(QObject):
                post = Signal(object)

                def __init__(self) -> None:
                    super().__init__()
                    self.post.connect(self._run, Qt.ConnectionType.QueuedConnection)

                def _run(self, job: Callable[[], None]) -> None:
                    job()

            bridge = _Bridge()
            if bridge.thread() is not app.thread():
                bridge.moveToThread(app.thread())    # (pushing it from this thread to the GUI thread is allowed)
            _bridge = bridge
        return _bridge


def on_main(fn: Callable[[], T], timeout: float, default: T) -> T:
    """``fn()`` on the GUI thread: right away if this is it, else posted there and waited for at most
    ``timeout`` seconds.  Returns ``default`` if it didn't run in time (then it never runs: the GUI thread may
    itself be waiting for this worker, e.g. when the cat quits) or if there is no Qt application to run it.
    Exceptions of ``fn`` are raised here."""
    if is_main_thread():
        return fn()
    app = _app()
    if app is None:
        return default
    lock = threading.Lock()
    state = {"started": False, "cancelled": False}
    done = threading.Event()
    box: dict[str, Any] = {}

    def job() -> None:
        with lock:
            if state["cancelled"]:
                return
            state["started"] = True
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting thread
            box["error"] = exc
        finally:
            done.set()

    _get_bridge(app).post.emit(job)
    if not done.wait(timeout):
        with lock:
            if not state["started"]:
                state["cancelled"] = True
        if state["cancelled"] or not done.wait(max(timeout, 1.0)):   # started: let it finish, but not forever
            log.debug("on_main: %r didn't run within %.1f s", fn, timeout)
            return default
    if "error" in box:
        raise box["error"]
    return box["value"]
