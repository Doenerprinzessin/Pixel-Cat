"""Fixtures for the ``pixelcat.desktop`` tests.

Mocked tests reach Win32 only through the fake DLLs installed here, so they are safe on
a real Windows desktop too.  When the desktop modules move (adapters/windows), only the
monkeypatch targets in this file (and ``fakes.py``) need to follow them.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from desktop.fakes import FakeClock, FakeDLL, FakeWinreg
from pixelcat.desktop import win32


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def w32(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Fresh fake DLLs behind every handle of :mod:`pixelcat.desktop.win32`."""
    ns = SimpleNamespace(**{name: FakeDLL(name) for name in ("user32", "kernel32", "dwmapi", "shell32", "shcore")})
    for name, dll in vars(ns).items():
        monkeypatch.setattr(win32, name, dll)
    return ns


@pytest.fixture
def fake_winreg(monkeypatch: pytest.MonkeyPatch) -> FakeWinreg:
    """A fresh in-memory registry for code that does ``import winreg`` when called."""
    reg = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    return reg
