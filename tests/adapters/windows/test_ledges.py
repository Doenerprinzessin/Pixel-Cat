"""pixelcat.adapters.windows.ledges: the screen snapshot for the ledge finder (fake GDI)."""
from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage

from adapters.windows.fakes import FakeDLL, raising
from pixelcat.adapters.windows import ledges

pytestmark = pytest.mark.qt


# ---------------------------------------------------------------- grab (fake GDI)
@pytest.fixture
def gdi(monkeypatch, qapp):  # type: ignore[no-untyped-def]
    u, g = FakeDLL("user32"), FakeDLL("gdi32")
    st = SimpleNamespace(u=u, g=g, keep=[])
    u.GetDC.result = 0x10
    g.CreateCompatibleDC.result = 0x20
    g.SelectObject.result = lambda dc, obj: 0x40 if obj == 0x30 else 0x30
    g.BitBlt.result = 1

    def dib(dc, bmi, usage, bits, section, offset):  # type: ignore[no-untyped-def]
        h = bmi._obj
        w, rows = h.biWidth, -h.biHeight
        buf = ctypes.create_string_buffer(bytes(((x * 3) % 256, 0, 200, 255)[i] for x in range(w * rows)
                                                for i in range(4)), w * rows * 4)
        st.keep.append(buf)
        bits._obj.value = ctypes.addressof(buf)
        return 0x30

    g.CreateDIBSection.result = dib
    monkeypatch.setattr(ledges, "_GDI", (u, g))
    return st


def released_all(st) -> bool:  # type: ignore[no-untyped-def]
    return st.u.ReleaseDC.calls == [(None, 0x10)] and st.g.DeleteDC.calls == [(0x20,)]


def test_grab_reads_the_screen_into_an_image(gdi):
    img = ledges.grab((10, 20, 30, 25))
    assert (img.width(), img.height(), img.format()) == (20, 5, QImage.Format.Format_RGB32)
    assert img.pixelColor(1, 0).getRgb()[:3] == (200, 0, 3)
    assert gdi.g.BitBlt.calls == [(0x20, 0, 0, 20, 5, 0x10, 10, 20, 0x00CC0020)]
    assert released_all(gdi) and gdi.g.DeleteObject.calls == [(0x30,)]


@pytest.mark.parametrize("rect", [(0, 0, 0, 10), (5, 5, 4, 10)])
def test_grab_empty_rect(gdi, rect):
    assert ledges.grab(rect) is None and gdi.u.GetDC.calls == []


def test_grab_failures_clean_up(gdi):
    gdi.g.BitBlt.result = 0
    assert ledges.grab((0, 0, 4, 4)) is None and released_all(gdi)
    gdi.u.GetDC.result = 0
    assert ledges.grab((0, 0, 4, 4)) is None
    gdi.u.GetDC.result = 0x10
    gdi.u.ReleaseDC.calls.clear()
    gdi.g.DeleteDC.calls.clear()
    gdi.g.CreateDIBSection.result = 0
    assert ledges.grab((0, 0, 4, 4)) is None and released_all(gdi)
    gdi.g.CreateCompatibleDC.result = raising(OSError("x"))
    assert ledges.grab((0, 0, 4, 4)) is None


def test_gdi_handles_are_made_once(monkeypatch):
    monkeypatch.setattr(ledges, "_GDI", None)
    monkeypatch.setattr(ctypes, "WinDLL", FakeDLL, raising=False)
    first = ledges._gdi()
    assert ledges._gdi() is first and [d._name for d in first] == ["user32", "gdi32"]
