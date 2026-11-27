"""logsetup: the log file (with fallbacks when it is busy), crash hooks, rate-limited logging."""
from __future__ import annotations

import io
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from pixelcat import logsetup, paths


@pytest.fixture
def root(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """The root logger and the crash hooks are put back afterwards."""
    log = logging.getLogger()
    before, level = list(log.handlers), log.level
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    yield log
    for h in list(log.handlers):
        if h not in before:
            log.removeHandler(h)
            h.close()
    log.setLevel(level)


def added(log: logging.Logger, before: list) -> list[logging.Handler]:  # type: ignore[type-arg]
    return [h for h in log.handlers if h not in before]


def file_handlers(log: logging.Logger) -> list[RotatingFileHandler]:
    return [h for h in log.handlers if isinstance(h, RotatingFileHandler)
            and Path(h.baseFilename).parent == paths.data_dir()]


def flush(log: logging.Logger) -> None:
    for h in log.handlers:
        h.flush()


def test_logs_to_log_txt_and_stderr(root: logging.Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    logsetup.setup_logging()
    (fh,) = file_handlers(root)
    assert Path(fh.baseFilename) == paths.log_file()
    assert (fh.maxBytes, fh.backupCount, fh.encoding) == (1_000_000, 3, "utf-8")
    assert root.level == logging.INFO
    logging.getLogger("pixelcat.test").info("hello 🐾")
    logging.getLogger("pixelcat.test").debug("not shown")
    flush(root)
    text = paths.log_file().read_text("utf-8")
    assert "INFO    pixelcat.test: hello 🐾" in text and "not shown" not in text
    assert "pixelcat.test: hello" in sys.stderr.getvalue()


def test_debug_level(root: logging.Logger) -> None:
    logsetup.setup_logging(debug=True)
    assert root.level == logging.DEBUG


def test_no_stderr_no_stream_handler(root: logging.Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", None)
    before = list(root.handlers)
    logsetup.setup_logging()
    assert [type(h) for h in added(root, before)] == [RotatingFileHandler]


def test_uncaught_exceptions_are_logged(root: logging.Logger) -> None:
    logsetup.setup_logging()
    try:
        raise ValueError("boom")
    except ValueError as e:
        sys.excepthook(type(e), e, e.__traceback__)

    def crash() -> None:
        raise RuntimeError("thread boom")

    t = threading.Thread(target=crash, name="worker-x")
    t.start()
    t.join()
    flush(root)
    text = paths.log_file().read_text("utf-8")
    assert "CRITICAL crash: uncaught exception" in text and "ValueError: boom" in text
    assert "uncaught exception in thread" in text and "worker-x" in text and "RuntimeError: thread boom" in text


@pytest.fixture
def busy(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """log.txt can't be opened; returns the sleeps and a switch for the fallback file."""
    state = {"sleeps": [], "fallback_ok": True}

    def handler(filename, *a, **k):  # type: ignore[no-untyped-def]
        if Path(filename) == paths.log_file() or not state["fallback_ok"]:
            raise PermissionError(13, "The process cannot access the file")
        return RotatingFileHandler(filename, *a, **k)

    monkeypatch.setattr(logsetup, "RotatingFileHandler", handler)
    monkeypatch.setattr(time, "sleep", lambda s: state["sleeps"].append(s))
    return state


def test_busy_log_txt_falls_back_to_a_log_of_its_own(root: logging.Logger, busy) -> None:  # type: ignore[no-untyped-def]
    logsetup.setup_logging()
    assert busy["sleeps"] == [0.25, 0.5, 0.75, 1.0]
    (fh,) = file_handlers(root)
    assert Path(fh.baseFilename) == paths.data_dir() / f"log-{os.getpid()}.txt"
    assert fh.backupCount == 0
    flush(root)
    assert "log.txt couldn't be opened" in Path(fh.baseFilename).read_text("utf-8")


def test_no_log_file_at_all_still_logs_to_stderr(root: logging.Logger, busy) -> None:  # type: ignore[no-untyped-def]
    busy["fallback_ok"] = False
    before = list(root.handlers)
    logsetup.setup_logging()
    assert file_handlers(root) == []
    assert [type(h) for h in added(root, before)] == [logging.StreamHandler]


# ---------------------------------------------------------------- RateLimitedLogger
class _Recorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def exception(self, msg: str) -> None:
        self.messages.append(msg)


def test_rate_limited_logger_logs_once_per_interval_per_key() -> None:
    rec = _Recorder()
    rl = logsetup.RateLimitedLogger(rec, interval=3600)  # type: ignore[arg-type]
    for _ in range(5):
        rl.exception("tick", "tick failed")
    rl.exception("other", "other failed")
    assert rec.messages == ["tick failed", "other failed"]


def test_rate_limited_logger_logs_again_after_the_interval() -> None:
    rec = _Recorder()
    rl = logsetup.RateLimitedLogger(rec, interval=0.0)  # type: ignore[arg-type]
    rl.exception("k", "a")
    rl.exception("k", "b")
    assert rec.messages == ["a", "b"]
