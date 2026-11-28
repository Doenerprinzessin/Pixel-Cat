"""Fixtures for the ``pixelcat.adapters.macos`` tests.

Every test here runs with each pyobjc proxy of ``_objc`` (and its ``run`` / ``spawn``) swapped for a stub that
fails the test when used: a forgotten fake can never reach a real API (CGEventPost, the Keychain, an AX
prompt).  A test that needs one sets its own fake (``monkeypatch.setattr(_objc, "Q", FakeModule(...))``, or the
``fake_run`` fixture).  Tests marked ``macos`` / ``macos_live`` use the real pyobjc instead.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from adapters.macos.fakes import Explodes, FakeRun, explode
from pixelcat.adapters.macos import _objc, permissions


@pytest.fixture(autouse=True)
def no_real_macos(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    permissions.reset()
    if request.node.get_closest_marker("macos") or request.node.get_closest_marker("macos_live"):
        yield
        permissions.reset()
        return
    for name in _objc.PROXIES:
        monkeypatch.setattr(_objc, name, Explodes(name))
    monkeypatch.setattr(_objc, "run", explode("_objc.run"))
    monkeypatch.setattr(_objc, "spawn", explode("_objc.spawn"))
    yield
    permissions.reset()


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    """``_objc.run`` answered from a table of argv prefixes; every call recorded."""
    fake = FakeRun()
    monkeypatch.setattr(_objc, "run", fake)
    return fake
