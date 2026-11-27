"""pixelcat.desktop.capture: screen/window capture to QImage and PNG/base64 (mocked GDI)."""
from __future__ import annotations

import base64
import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor, QImage

from desktop.fakes import FakeDLL, by_hwnd, enum_windows, out, raising
from pixelcat.desktop import capture

pytestmark = pytest.mark.qt


def pattern(x: int, y: int) -> tuple[int, int, int]:
    return (x * 7) % 256, (y * 5) % 256, 99


@pytest.fixture
def gdi(monkeypatch, w32, clock, qapp):  # type: ignore[no-untyped-def]
    """Fake GDI whose 'screen' shows ``pattern`` (or one flat colour when ``flat`` is set)."""
    g = FakeDLL("gdi32")
    st = SimpleNamespace(g=g, u=w32.user32, clock=clock, flat=None, headers=[])
    w32.user32.GetDC.result = 0x10
    g.CreateCompatibleDC.result = 0x20
    g.CreateCompatibleBitmap.result = 0x30
    g.SelectObject.result = lambda dc, obj: 0x40 if obj == 0x30 else 0x30
    g.BitBlt.result = 1

    def dibits(dc, bmp, start, lines, buf, bih, usage):  # type: ignore[no-untyped-def]
        h = out(bih)
        st.headers.append((h.biWidth, h.biHeight, h.biBitCount, h.biPlanes, h.biSize))
        w, rows = h.biWidth, -h.biHeight
        data = bytearray(w * rows * 4)
        for y in range(rows):
            for x in range(w):
                r, gg, b = st.flat or pattern(x, y)
                data[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((b, gg, r, 255))
        ctypes.memmove(buf, bytes(data), len(data))
        return rows

    g.GetDIBits.result = dibits
    monkeypatch.setattr(capture, "gdi32", g)
    monkeypatch.setattr(capture, "time", clock)
    return st


def rgb(img: QImage, x: int, y: int) -> tuple[int, int, int]:
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def cleaned_up(st) -> bool:  # type: ignore[no-untyped-def]
    return (st.g.DeleteObject.calls == [(0x30,)] and st.g.DeleteDC.calls == [(0x20,)]
            and st.u.ReleaseDC.calls == [(None, 0x10)])


def test_grab_copies_the_screen_rect(gdi):
    img = capture.grab((-100, 50, -60, 80))
    assert (img.width(), img.height()) == (40, 30)
    assert rgb(img, 0, 0) == pattern(0, 0) and rgb(img, 39, 29) == pattern(39, 29)
    (bitblt,) = gdi.g.BitBlt.calls
    assert bitblt == (0x20, 0, 0, 40, 30, 0x10, -100, 50, capture.SRCCOPY)
    assert gdi.headers == [(40, -30, 32, 1, ctypes.sizeof(capture.BITMAPINFOHEADER))]
    assert cleaned_up(gdi)


def test_grab_of_an_empty_rect_is_one_pixel(gdi):
    img = capture.grab((10, 10, 10, 5))
    assert (img.width(), img.height()) == (1, 1)


def test_grab_cleans_up_when_reading_fails(gdi):
    gdi.g.GetDIBits.result = raising(OSError("device lost"))
    with pytest.raises(OSError):
        capture.grab((0, 0, 4, 4))
    assert cleaned_up(gdi)


def window_rect(gdi, rect):  # type: ignore[no-untyped-def]
    def fill(h, p):  # type: ignore[no-untyped-def]
        r = out(p)
        r.left, r.top, r.right, r.bottom = rect
        return 1

    gdi.u.GetWindowRect.result = fill


def test_grab_window_crops_to_the_visible_frame(gdi):
    window_rect(gdi, (100, 100, 420, 340))
    gdi.u.PrintWindow.result = 1
    img = capture.grab_window(7, (108, 100, 412, 332))
    assert (img.width(), img.height()) == (304, 232)
    assert rgb(img, 0, 0) == pattern(8, 0)
    assert gdi.u.PrintWindow.calls == [(7, 0x20, capture.PW_RENDERFULLCONTENT)]
    assert cleaned_up(gdi)


def test_grab_window_blank_picture_means_the_app_cannot_draw_itself(gdi):
    window_rect(gdi, (0, 0, 200, 100))
    gdi.u.PrintWindow.result = 1
    gdi.flat = (0, 0, 0)
    assert capture.grab_window(7, (0, 0, 200, 100)) is None


def test_grab_window_print_refused(gdi):
    window_rect(gdi, (0, 0, 200, 100))
    gdi.u.PrintWindow.result = 0
    assert capture.grab_window(7, (0, 0, 200, 100)) is None
    assert gdi.g.GetDIBits.calls == [] and cleaned_up(gdi)


@pytest.mark.parametrize("rect", [(0, 0, 1, 100), (0, 0, 100, 1), (0, 0, 0, 0)])
def test_grab_window_too_small(gdi, rect):
    window_rect(gdi, rect)
    assert capture.grab_window(7, rect) is None
    assert gdi.u.GetDC.calls == []


@pytest.fixture
def own(gdi):  # type: ignore[no-untyped-def]
    pids = {1: 10, 2: 20, 3: 10, 4: 10}
    enum_windows(gdi.u, [1, 2, 3, 4])
    gdi.u.IsWindowVisible.result = lambda h: h != 3

    def pid(h, p):  # type: ignore[no-untyped-def]
        out(p).value = pids[h]
        return 1

    gdi.u.GetWindowThreadProcessId.result = pid
    return gdi


def test_own_windows_are_the_visible_ones_of_the_process(own):
    assert capture.own_windows(10) == [1, 4]


def test_excluding_hides_own_windows_and_always_shows_them_again(own):
    own.u.SetWindowDisplayAffinity.result = lambda h, mode: h == 1 or mode == capture.WDA_NONE
    with pytest.raises(ZeroDivisionError):
        with capture.excluding(10):
            assert own.u.SetWindowDisplayAffinity.calls == [(1, capture.WDA_EXCLUDEFROMCAPTURE),
                                                            (4, capture.WDA_EXCLUDEFROMCAPTURE)]
            1 / 0
    assert own.u.SetWindowDisplayAffinity.calls[2:] == [(1, capture.WDA_NONE)]
    assert own.clock.sleeps == [0.12]


def test_excluding_nothing_hidden_does_not_wait(own):
    own.u.SetWindowDisplayAffinity.result = 0
    with capture.excluding(10):
        pass
    assert own.clock.sleeps == []


def test_grab_without_grabs_while_hidden(own):
    own.u.SetWindowDisplayAffinity.result = by_hwnd({1: 1, 4: 1})
    img = capture.grab_without((0, 0, 8, 6), 10)
    assert (img.width(), img.height()) == (8, 6)
    modes = [m for _h, m in own.u.SetWindowDisplayAffinity.calls]
    assert modes == [capture.WDA_EXCLUDEFROMCAPTURE] * 2 + [capture.WDA_NONE] * 2


@pytest.mark.parametrize("size, max_px, new, scale", [
    ((1000, 500), 400, (400, 200), 0.4), ((300, 900), 450, (150, 450), 0.5), ((640, 480), 640, (640, 480), 1.0),
])
def test_fit(qapp, size, max_px, new, scale):
    img = QImage(*size, QImage.Format.Format_RGB32)
    got, s = capture.fit(img, max_px)
    assert (got.width(), got.height()) == new and s == pytest.approx(scale)
    if scale == 1.0:
        assert got is img


def test_draw_marks_boxes_and_labels(qapp):
    img = QImage(200, 100, QImage.Format.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    capture.draw_marks(img, [(1, 20, 40, 120, 80), (23, 150, 0, 190, 30)])
    pink = (230, 40, 140)
    assert rgb(img, 20, 60) == pink and rgb(img, 120, 60) == pink and rgb(img, 70, 80) == pink
    assert rgb(img, 70, 60) == (255, 255, 255)
    above = [rgb(img, x, y) for x in range(20, 30) for y in range(25, 40)]
    assert pink in above                                  # the label sits above the box
    inside = [rgb(img, x, y) for x in range(150, 165) for y in range(0, 12)]
    assert pink in inside and (255, 255, 255) in inside   # at the top edge: the label goes inside


def test_to_base64_png_round_trips(qapp):
    img = QImage(3, 2, QImage.Format.Format_RGB32)
    img.fill(QColor(10, 20, 30))
    img.setPixelColor(2, 1, QColor(200, 100, 0))
    text = capture.to_base64_png(img)
    raw = base64.b64decode(text, validate=True)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    back = QImage.fromData(raw, "PNG")
    assert (back.width(), back.height()) == (3, 2)
    assert rgb(back, 2, 1) == (200, 100, 0) and rgb(back, 0, 0) == (10, 20, 30)
