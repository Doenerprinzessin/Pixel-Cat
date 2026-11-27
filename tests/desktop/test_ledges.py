"""pixelcat.desktop.ledges: surfaces the cat can stand on inside windows (synthetic screens).

The screens are drawn with plain rectangles (no fonts), so the results are exact on any OS.
"""
from __future__ import annotations

import ctypes
import random
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from desktop.fakes import FakeDLL, raising
from pixelcat.desktop import ledges
from pixelcat.desktop.ledges import LedgeFinder, find_tops
from pixelcat.desktop.windows import WinInfo

pytestmark = pytest.mark.qt

AREA = (0, 0, 800, 600)
WIN = (100, 100, 700, 500)
DARK = QColor(32, 32, 40)


def screen(win=WIN, size=(800, 600), lines: bool = True, cover=None) -> QImage:  # type: ignore[no-untyped-def]
    """A dark desktop with one light window: a toolbar (line at +30), a card (+100, +140), three
    words with small gaps (+200), a thin vertical border.  ``cover``: a dark box (the cat)."""
    l, t, r, b = win
    img = QImage(*size, QImage.Format.Format_RGB32)
    img.fill(DARK)
    p = QPainter(img)
    p.fillRect(l, t, r - l, b - t, QColor(255, 255, 255))
    if lines:
        p.fillRect(l, t, r - l, 30, QColor(240, 240, 240))
        p.fillRect(l + 50, t + 100, 200, 40, QColor(0, 120, 212))
        for k in range(3):
            p.fillRect(l + 50 + k * 30, t + 200, 20, 10, QColor(20, 20, 20))
        p.fillRect(l + 400, t + 200, 2, 150, QColor(200, 200, 200))
    if cover is not None:
        p.fillRect(cover[0], cover[1], cover[2] - cover[0], cover[3] - cover[1], QColor(60, 60, 70))
    p.end()
    return img


def win_info(rect=WIN, hwnd: int = 5, z: int = 0) -> WinInfo:  # type: ignore[no-untyped-def]
    return WinInfo(hwnd, rect, z, "Doc", "X", False, False)


def ys(finder: LedgeFinder, win: WinInfo) -> list[int]:
    return sorted({y for *_x, y in finder.current([win]).get(win.hwnd, [])})


# ---------------------------------------------------------------- find_tops
def test_find_tops_window_edges_toolbar_card_and_a_line_of_words(qapp):
    assert find_tops(screen(), 48) == [(96, 702, 100), (104, 694, 130), (145, 355, 200), (153, 347, 240),
                                       (145, 234, 300), (104, 694, 500)]


def test_find_tops_min_len_lets_short_tops_through(qapp):
    tops = find_tops(screen(), 10)
    assert (492, 508, 300) in tops                 # the top of the thin vertical border
    assert (185, 225, 310) in tops                 # under the words, between them
    assert len(tops) == 8


def test_find_tops_nothing_inside_noise_or_a_soft_shadow(qapp):
    rng = random.Random(3)
    img = QImage(300, 200, QImage.Format.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    for y in range(50, 150):
        for x in range(50, 250, 4):
            c = rng.randint(30, 225)
            p.fillRect(x, y, 4, 1, QColor(c, (c * 3) % 256, (c * 7) % 256))
    for k in range(40):
        c = 255 - min(k, 39 - k) // 2
        p.fillRect(20, 160 + k, 260, 1, QColor(c, c, c))
    p.end()
    assert find_tops(img, 48) == [(48, 250, 50)]


def test_find_tops_big_images_in_strips_find_each_surface_once(qapp):
    img = QImage(400, 700, QImage.Format.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    for y in (100, 238, 240, 245, 480, 600):        # around the strip borders (240, 480)
        p.fillRect(50, y, 200, 6, QColor(0, 0, 0))
    p.end()
    tops = find_tops(img, 48)
    assert tops == [(48, 252, 100), (48, 252, 238), (57, 244, 251), (48, 252, 480), (48, 252, 600)]
    assert tops == ledges._join(ledges._candidates(img), 48)


def test_find_tops_converts_other_formats(qapp):
    img = screen().convertToFormat(QImage.Format.Format_ARGB32)
    assert find_tops(img, 48) == find_tops(screen(), 48)


@pytest.mark.parametrize("size", [(23, 100), (100, 6), (1, 1)])
def test_find_tops_tiny_images(qapp, size):
    img = QImage(*size, QImage.Format.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    assert find_tops(img, 1) == []


@pytest.mark.parametrize("raw, min_len, joined", [
    ([(0, 50, 100), (60, 120, 103)], 40, [(0, 120, 100)]),                  # small gap, a bit lower
    ([(0, 50, 100), (80, 150, 100)], 40, [(0, 50, 100), (80, 150, 100)]),   # gap too wide
    ([(0, 50, 100), (0, 50, 107)], 40, [(0, 50, 100), (0, 50, 107)]),       # too far below
    ([(0, 50, 100), (80, 150, 101), (45, 90, 102)], 40, [(0, 150, 100)]),   # one piece bridges two
    ([(0, 10, 5), (100, 160, 5)], 20, [(100, 160, 5)]),
])
def test_join(raw, min_len, joined):
    assert ledges._join(list(raw), min_len) == joined


# ---------------------------------------------------------------- remembering
@pytest.fixture
def seen(qapp):  # type: ignore[no-untyped-def]
    """A finder that has seen ``screen()`` twice."""
    f = LedgeFinder()
    f.scan([win_info()], AREA, [], 48, 1, image=screen())
    f.scan([win_info()], AREA, [], 48, 1, image=screen())
    return f


def test_a_surface_counts_after_two_sightings(qapp):
    f = LedgeFinder()
    f.scan([win_info()], AREA, [], 48, 1, image=screen())
    assert f.current([win_info()]) == {} and f.unconfirmed() and f.searches == 1
    f.scan([win_info()], AREA, [], 48, 1, image=screen())
    assert not f.unconfirmed()
    assert sorted(f.current([win_info()])[5]) == [(104, 694, 130), (145, 234, 300), (145, 355, 200),
                                                  (153, 347, 240)]


def test_the_windows_own_top_and_bottom_edges_are_not_surfaces(seen):
    assert 100 not in ys(seen, win_info()) and 500 not in ys(seen, win_info())


def test_surfaces_move_with_their_window(seen):
    moved = win_info((150, 50, 750, 450))
    assert ys(seen, moved) == [80, 150, 190, 250]
    assert min(x1 for x1, *_ in seen.current([moved])[5]) == 154


def test_a_resized_window_starts_over(seen):
    bigger = win_info((100, 100, 720, 500))
    assert seen.current([bigger]) == {}
    seen.scan([bigger], AREA, [], 48, 1, image=screen(bigger.rect))
    assert seen.current([bigger]) == {} and seen.unconfirmed()


def test_gone_surfaces_are_forgotten_after_two_misses(seen):
    plain = screen(lines=False)
    seen.scan([win_info()], AREA, [], 48, 1, image=plain)
    assert ys(seen, win_info()) == [130, 200, 240, 300]
    seen.scan([win_info()], AREA, [], 48, 1, image=plain)
    assert ys(seen, win_info()) == []


def test_a_surface_the_cat_hides_is_kept_at_full_length(seen):
    cat = (200, 170, 260, 260)                   # sits on the card's top, covering its middle
    for _ in range(3):
        seen.scan([win_info()], AREA, [cat], 48, 1, image=screen(cover=cat))
    card = [c for c in seen.current([win_info()])[5] if c[2] == 200]
    assert card == [(145, 355, 200)]


def test_a_surface_completely_covered_by_our_window_is_not_missed(seen):
    chat = (120, 280, 260, 330)                  # covers the line of words
    plain_words = screen(cover=chat)
    for _ in range(3):
        seen.scan([win_info()], AREA, [chat], 48, 1, image=plain_words)
    assert 300 in ys(seen, win_info())


def test_a_surface_outside_the_searched_area_is_not_missed(seen):
    small_area = (0, 0, 800, 260)                # the words (y 300) are below it
    for _ in range(3):
        seen.scan([win_info()], small_area, [], 48, 1, image=screen().copy(*small_area))
    assert ys(seen, win_info()) == [130, 200, 240, 300]


def test_closed_windows_are_dropped_and_far_away_windows_kept(seen):
    far = win_info((2000, 0, 2600, 400), hwnd=9)
    seen.wins[9] = ledges._WinLedges((600, 400), [ledges._Ledge(10, 100, 50, hits=2)])
    seen.scan([win_info(), far], AREA, [], 48, 1, image=screen())
    assert seen.current([far]) == {9: [(2010, 2100, 50)]}
    seen.scan([far], AREA, [], 48, 1, image=screen())
    assert 5 not in seen.wins and 9 in seen.wins


def test_lines_belong_to_the_topmost_window_there(qapp):
    front = win_info((80, 80, 400, 520), hwnd=1, z=0)
    back = win_info(WIN, hwnd=2, z=1)
    img = screen()
    p = QPainter(img)
    p.fillRect(80, 80, 320, 440, QColor(250, 250, 250))
    p.fillRect(150, 300, 200, 20, QColor(0, 0, 0))
    p.end()
    f = LedgeFinder()
    for _ in range(2):
        f.scan([back, front], AREA, [], 48, 1, image=img)
    # the black bar (and its bottom) is the front window's; only the toolbar beside it is the back one's
    assert f.current([front, back]) == {1: [(145, 355, 300), (153, 347, 320)], 2: [(395, 694, 130)]}


def test_scale_widens_the_ignored_frame_edges(qapp):
    f = LedgeFinder()
    for _ in range(2):
        f.scan([win_info()], AREA, [], 48, 3, image=screen())   # 36 px under the top: the toolbar line goes
    assert ys(f, win_info()) == [200, 240, 300]


def test_forget(seen):
    seen.forget(5, 102)
    seen.forget(77, 30)
    assert ys(seen, win_info()) == [130, 240, 300]


def test_clear(seen):
    seen.clear()
    assert seen.current([win_info()]) == {}


def test_check_line_looks_beside_the_cat(seen, monkeypatch):
    shown = [screen()]
    grabs = []

    def fake_grab(r):  # type: ignore[no-untyped-def]
        grabs.append(r)
        return shown[0].copy(r[0], r[1], r[2] - r[0], r[3] - r[1])

    monkeypatch.setattr(ledges, "grab", fake_grab)
    cat = (200, 170, 260, 200)
    assert seen.check_line(145, 355, 200, cat) is True
    assert grabs == [(145, 200 - ledges.ROWS_ABOVE - 3, 200, 200 + ledges.TOL + 3)]
    shown[0] = screen(lines=False)
    grabs.clear()
    assert seen.check_line(145, 355, 200, cat) is False
    assert [g[0] for g in grabs] == [145, 260]


def test_check_line_cannot_tell(seen, monkeypatch):
    monkeypatch.setattr(ledges, "grab", lambda r: None)
    assert seen.check_line(145, 355, 200, (100, 0, 400, 300)) is None      # all covered
    assert seen.check_line(145, 355, 200, (0, 0, 10, 10)) is None           # no picture


def test_background_search(qapp, monkeypatch):
    gate = threading.Event()

    def slow_grab(area):  # type: ignore[no-untyped-def]
        gate.wait(5)
        return screen()

    monkeypatch.setattr(ledges, "grab", slow_grab)
    f = LedgeFinder()
    assert not f.poll()
    assert f.start([win_info()], AREA, [], 48, 1) is True
    assert f.busy() and f.start([win_info()], AREA, [], 48, 1) is False and not f.poll()
    gate.set()
    deadline = time.monotonic() + 5
    while not f.poll():
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert not f.busy() and f.searches == 1 and f.unconfirmed() and f.last_ms >= 0


@pytest.mark.parametrize("grab", [lambda area: None, raising(RuntimeError("boom"))])
def test_background_search_without_a_picture_counts_as_empty(qapp, monkeypatch, grab):
    monkeypatch.setattr(ledges, "grab", grab)
    f = LedgeFinder()
    f.start([win_info()], AREA, [], 48, 1)
    deadline = time.monotonic() + 5
    while not f.poll():
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert f.searches == 1 and f.wins[5].ledges == []


def test_scan_without_a_picture_changes_nothing(qapp, monkeypatch):
    monkeypatch.setattr(ledges, "grab", lambda area: None)
    f = LedgeFinder()
    f.scan([win_info()], AREA, [], 48, 1)
    assert f.searches == 0


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
