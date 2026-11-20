"""Logging to %APPDATA%/PixelCat/log.txt with rotation, plus crash hooks."""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import paths


def setup_logging(debug: bool = False) -> None:
    """Configure root logging. Safe to call once at startup."""
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    import os
    import time

    fh = None
    problem = None
    for attempt in range(4):                     # (the file can be busy for a moment: a cat quitting, a scanner)
        try:
            fh = RotatingFileHandler(paths.log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            break
        except OSError as e:
            problem = e
            time.sleep(0.25 * (attempt + 1))
    if fh is None:
        try:                                     # still not: a log of this run's own, next to it
            fh = RotatingFileHandler(paths.log_file().with_name(f"log-{os.getpid()}.txt"), maxBytes=1_000_000,
                                     backupCount=0, encoding="utf-8")
        except OSError:
            fh = None
    if fh is not None:
        fh.setFormatter(fmt)
        root.addHandler(fh)
        if problem is not None:
            root.warning("log.txt couldn't be opened (%s) - logging to %s", problem, fh.baseFilename)
    if sys.stderr is not None:  # pythonw / windowed exe has no stderr
        try:
            sys.stderr.reconfigure(errors="backslashreplace")  # emoji on cp1252 consoles
        except (AttributeError, ValueError):
            pass
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    def _excepthook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
        logging.getLogger("crash").critical("uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("crash").error(
            "uncaught exception in thread %s", args.thread, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook


class RateLimitedLogger:
    """Logs repeated errors from hot loops at most once every ``interval`` seconds."""

    def __init__(self, logger: logging.Logger, interval: float = 10.0) -> None:
        self._log = logger
        self._interval = interval
        self._last: dict[str, float] = {}

    def exception(self, key: str, msg: str) -> None:
        import time

        now = time.monotonic()
        if now - self._last.get(key, -1e9) >= self._interval:
            self._last[key] = now
            self._log.exception(msg)
