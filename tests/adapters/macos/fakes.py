"""Stand-ins for pyobjc modules and subprocesses in the macOS adapter tests."""
from __future__ import annotations

import subprocess
from typing import Any, Callable


class Explodes:
    """A pyobjc proxy that fails the test on any use (see conftest.py)."""

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"real pyobjc/subprocess used in a unit test: _objc.{self._name}.{attr} (set a fake)")


def explode(name: str) -> Callable[..., Any]:
    def fn(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"real pyobjc/subprocess used in a unit test: {name}{args!r} (set a fake)")
    return fn


class FakeModule:
    """A pyobjc module with just the names a test gives it; calls to function names are recorded in ``calls``.

    ``FakeModule(AXIsProcessTrusted=lambda: True, kAXTrustedCheckOptionPrompt="prompt")``; any other name fails.
    """

    def __init__(self, **names: Any) -> None:
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "_names", dict(names))

    def __getattr__(self, attr: str) -> Any:
        names = object.__getattribute__(self, "_names")
        if attr not in names:
            raise AssertionError(f"the fake pyobjc module has no {attr!r} (add it to the test's FakeModule)")
        value = names[attr]
        if not callable(value):
            return value

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((attr, args))
            return value(*args, **kwargs)
        return call

    def __setattr__(self, attr: str, value: Any) -> None:
        object.__getattribute__(self, "_names")[attr] = value

    def called(self, attr: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == attr]


class FakeRun:
    """``_objc.run``: answers by the longest matching argv prefix (``answer``); records every argv in ``calls``."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._answers: list[tuple[tuple[str, ...], Any]] = []

    def answer(self, prefix: list[str], stdout: str = "", returncode: int = 0, stderr: str = "",
               raises: BaseException | None = None) -> None:
        self._answers.append((tuple(prefix), raises or (returncode, stdout, stderr)))

    def __call__(self, argv: list[str], timeout: float, input: str | None = None) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        best = max((a for a in self._answers if tuple(argv[:len(a[0])]) == a[0]), key=lambda a: len(a[0]),
                   default=None)
        if best is None:
            raise AssertionError(f"unexpected subprocess {argv!r} (answer it with fake_run.answer)")
        if isinstance(best[1], BaseException):
            raise best[1]
        code, out, err = best[1]
        return subprocess.CompletedProcess(list(argv), code, out, err)
