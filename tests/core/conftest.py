"""Fixtures for the top-level pixelcat modules (save, paths, settings ...).

The core gets its storage ports passed in; the fakes for them are here:

* ``dpapi``         - a :class:`~pixelcat.ports.SecretStore`: reversible, tamper-checking and
  bound to an "account" (pass it as ``secrets=``).
* ``save_marks``    - a :class:`~pixelcat.ports.SaveMarks` in memory (pass it as ``marks=``).

(The Windows-only installer and its fakes live in tests/adapters/windows.)
"""
from __future__ import annotations

import pytest

from fakes.secrets import FakeDpapi


# ---------------------------------------------------------------- DPAPI
@pytest.fixture
def dpapi() -> FakeDpapi:
    return FakeDpapi()


# ---------------------------------------------------------------- save marks
class FakeSaveMarks:
    """The protected-save marks in memory: ``marked`` maps folder ids to 1 (as the registry
    values); with ``fail`` set every call raises it (like winreg without access)."""

    def __init__(self) -> None:
        self.marked: dict[str, int] = {}
        self.fail: type[OSError] | None = None

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail(5, "Access is denied")

    def is_marked(self, folder_id: str) -> bool:
        self._check()
        if folder_id not in self.marked:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return bool(self.marked[folder_id])

    def mark(self, folder_id: str) -> None:
        self._check()
        self.marked[folder_id] = 1

    def forget(self, folder_id: str) -> None:
        self._check()
        if self.marked.pop(folder_id, None) is None:
            raise FileNotFoundError(2, "The system cannot find the file specified")


@pytest.fixture
def save_marks() -> FakeSaveMarks:
    return FakeSaveMarks()
