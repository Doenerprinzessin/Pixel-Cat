"""pixelcat.adapters.macos.capture: Screen Recording is only checked in the background, pictures come out in
points (as big as their rect), ScreenCaptureKit with the old CGWindowListCreateImage as the fallback."""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, capture
from pixelcat.domain import PermissionNeeded

OWN_PID = 99
DISPLAYS = {1: (0, 0, 100, 80), 2: (100, 0, 200, 80)}


class Recorder:
    """An ObjC object whose setters (``setWidth_``) are recorded in ``set``."""

    def __init__(self, **fields: Any) -> None:
        self.set: dict[str, Any] = {}
        self.__dict__.update(fields)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("set") and name.endswith("_"):
            key = name[3].lower() + name[4:-1]
            return lambda value: self.set.__setitem__(key, value)
        raise AttributeError(name)


class FakeSCK:
    """ScreenCaptureKit with two displays, two apps and two windows; every screenshot recorded in ``shots``."""

    def __init__(self) -> None:
        self.shots: list[tuple[Recorder, Recorder]] = []
        self.fail = False
        self.silent = False
        self.held: list[tuple[Any, Any]] = []         # (filter, completion handler) of silent screenshots
        self.window_ids = [42, 43]
        self.listed = 0
        apps = [SimpleNamespace(processID=lambda: OWN_PID), SimpleNamespace(processID=lambda: 7)]
        self.apps = apps
        content = SimpleNamespace(
            applications=lambda: apps,
            displays=lambda: [SimpleNamespace(displayID=lambda i=i: i) for i in DISPLAYS],
            windows=lambda: [SimpleNamespace(windowID=lambda i=i: i) for i in sck.window_ids])
        sck = self

        class Shareable:
            @staticmethod
            def getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(desk, on, done):  # type: ignore[no-untyped-def]
                sck.listed += 1
                ids = list(sck.window_ids)                 # (the windows when it was listed)
                done(SimpleNamespace(applications=content.applications, displays=content.displays,
                                     windows=lambda: [SimpleNamespace(windowID=lambda i=i: i) for i in ids]), None)

        class Filter:
            @staticmethod
            def alloc():  # type: ignore[no-untyped-def]
                return SimpleNamespace(
                    initWithDisplay_excludingApplications_exceptingWindows_=lambda d, a, w: Recorder(
                        display=d.displayID(), excluded=[x.processID() for x in a], window=None),
                    initWithDesktopIndependentWindow_=lambda w: Recorder(window=w.windowID()))

        class Config:
            @staticmethod
            def alloc():  # type: ignore[no-untyped-def]
                return SimpleNamespace(init=Recorder)

        class Manager:
            @staticmethod
            def captureImageWithFilter_configuration_completionHandler_(flt, cfg, done):  # type: ignore[no-untyped-def]
                sck.shots.append((flt, cfg))
                if sck.silent:
                    sck.held.append((flt, done))
                    return
                if sck.fail:
                    done(None, "SCStreamErrorDomain -3801")
                else:
                    done(SimpleNamespace(source=flt), None)

        self.SCShareableContent, self.SCContentFilter = Shareable, Filter
        self.SCStreamConfiguration, self.SCScreenshotManager = Config, Manager


def _quartz(granted: bool = True, **more: Any) -> FakeModule:
    def bounds(i: int) -> SimpleNamespace:
        l, t, r, b = DISPLAYS[i]
        return SimpleNamespace(origin=SimpleNamespace(x=l, y=t), size=SimpleNamespace(width=r - l, height=b - t))

    return FakeModule(CGPreflightScreenCaptureAccess=lambda: granted, CGRequestScreenCaptureAccess=lambda: False,
                      CGDisplayBounds=bounds, CGRectMake=lambda *r: r, CGRectNull="null",
                      kCGWindowListOptionOnScreenOnly=1, kCGWindowListOptionIncludingWindow=8, kCGNullWindowID=0,
                      kCGWindowImageNominalResolution=16, kCGWindowImageBoundsIgnoreFraming=1, **more)


@pytest.fixture
def conv(monkeypatch):  # type: ignore[no-untyped-def]
    """``to_qimage`` without CoreGraphics: a picture of the asked size, colored by display (or two-tone)."""
    calls: list[tuple[Any, int, int, bool]] = []

    def fake(cg: Any, w: int, h: int, smooth: bool = True) -> QImage:
        calls.append((cg, w, h, smooth))
        img = QImage(w, h, QImage.Format.Format_RGB32)
        flt = getattr(cg, "source", None)
        if getattr(flt, "window", None) is not None:
            img.fill(QColor("white"))
            if flt.window != 43:            # 43 draws nothing: one flat color
                for y in range(h):
                    for x in range(w // 2):
                        img.setPixel(x, y, 0xFF000000)
        else:
            img.fill(QColor({1: "red", 2: "blue"}.get(getattr(flt, "display", 0), "green")))
        return img

    monkeypatch.setattr(capture, "to_qimage", fake)
    return calls


@pytest.fixture
def sck(monkeypatch):  # type: ignore[no-untyped-def]
    fake = FakeSCK()
    monkeypatch.setattr(_objc, "SCK", fake)
    monkeypatch.setattr(_objc, "Q", _quartz())
    return fake


def test_a_grab_spanning_two_displays_is_as_big_as_its_rect_in_points(sck, conv):  # type: ignore[no-untyped-def]
    img = capture.MacCapture().grab((50, 10, 150, 60))
    assert (img.width(), img.height()) == (100, 50) and img.format() == QImage.Format.Format_RGB32
    assert QColor(img.pixel(10, 10)) == QColor("red") and QColor(img.pixel(90, 40)) == QColor("blue")
    assert [(f.display, f.excluded, c.set["sourceRect"], c.set["width"], c.set["height"]) for f, c in sck.shots] == [
        (1, [], (50, 10, 50, 50), 50, 50), (2, [], (0, 10, 50, 50), 50, 50)]
    cfg = sck.shots[0][1].set
    assert cfg["showsCursor"] is False and cfg["pixelFormat"] == capture.BGRA
    assert [c[1:] for c in conv] == [(50, 50, True), (50, 50, True)]


def test_off_screen_parts_stay_black(sck, conv):  # type: ignore[no-untyped-def]
    img = capture.MacCapture().grab((150, 60, 250, 100))
    assert (img.width(), img.height()) == (100, 40)
    assert QColor(img.pixel(10, 5)) == QColor("blue") and QColor(img.pixel(80, 30)) == QColor("black")


def test_grab_without_and_excluding_leave_the_cats_app_out(sck, conv):  # type: ignore[no-untyped-def]
    cap = capture.MacCapture()
    cap.grab_without((0, 0, 10, 10), OWN_PID)
    with cap.excluding(OWN_PID):
        with cap.excluding(OWN_PID):
            cap.grab((0, 0, 10, 10))
        cap.grab((0, 0, 10, 10))
    cap.grab((0, 0, 10, 10))
    assert [f.excluded for f, _c in sck.shots] == [[OWN_PID], [OWN_PID], [OWN_PID], []]


def test_grab_window_draws_the_window_itself_and_gives_up_on_flat_pictures(sck, conv):  # type: ignore[no-untyped-def]
    cap = capture.MacCapture()
    img = cap.grab_window(42, (10, 20, 70, 60))
    assert img is not None and (img.width(), img.height()) == (60, 40)
    flt, cfg = sck.shots[-1]
    assert flt.window == 42 and cfg.set["ignoreShadowsSingleWindow"] is True and cfg.set["width"] == 60
    assert cap.grab_window(43, (10, 20, 70, 60)) is None          # one flat color
    n = len(sck.shots)
    assert cap.grab_window(44, (10, 20, 70, 60)) is None          # not in ScreenCaptureKit's list
    assert cap.grab_window(_objc.OWN_ID_MIN + 1, (10, 20, 70, 60)) is None
    assert len(sck.shots) == n


def test_grab_window_looks_again_for_a_window_newer_than_the_reused_list(sck, conv):  # type: ignore[no-untyped-def]
    cap = capture.MacCapture()
    assert cap.grab_window(42, (10, 20, 70, 60)) is not None and sck.listed == 1
    sck.window_ids.append(44)                                      # (a dialog that just opened)
    img = cap.grab_window(44, (10, 20, 70, 60))
    assert img is not None and sck.shots[-1][0].window == 44 and sck.listed == 2
    assert cap.grab_window(45, (10, 20, 70, 60)) is None and sck.listed == 3     # (looked once more, not again)


def test_without_screencapturekit_the_old_call_takes_nominal_size_pictures(monkeypatch, conv):  # type: ignore[no-untyped-def]
    q = _quartz(CGWindowListCreateImage=lambda rect, opt, win, flags: SimpleNamespace(rect=rect, flags=flags))
    monkeypatch.setattr(_objc, "Q", q)
    monkeypatch.setattr(_objc, "SCK", SimpleNamespace())          # no SCScreenshotManager (macOS 13)
    img = capture.MacCapture().grab_without((5, 6, 45, 26), OWN_PID)
    assert (img.width(), img.height()) == (40, 20)
    assert q.called("CGWindowListCreateImage") == [((5, 6, 40, 20), 1, 0, 16)]
    assert conv[0][1:] == (40, 20, True)


def test_a_failing_or_silent_screencapturekit_falls_back(sck, conv, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(capture, "WAIT_S", 0.05)
    monkeypatch.setattr(_objc, "is_main_thread", lambda: False)    # (the ledge search's worker thread)
    q = _quartz(CGWindowListCreateImage=lambda rect, opt, win, flags: SimpleNamespace())
    monkeypatch.setattr(_objc, "Q", q)
    cap = capture.MacCapture()
    sck.fail = True
    assert cap.grab((0, 0, 10, 10)).width() == 10
    sck.fail, sck.silent = False, True
    assert cap.snapshot((0, 0, 10, 10)) is not None
    assert len(q.called("CGWindowListCreateImage")) == 2


def test_the_ledge_snapshot_is_fast_scaled_and_none_on_failure(sck, conv, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_objc, "is_main_thread", lambda: False)    # (the ledge search's worker thread: it waits)
    cap = capture.MacCapture()
    img = cap.snapshot((0, 0, 30, 20))
    assert img is not None and (img.width(), img.height()) == (30, 20) and conv[-1][3] is False
    assert cap.snapshot((5, 5, 5, 9)) is None
    monkeypatch.setattr(_objc, "SCK", SimpleNamespace())
    monkeypatch.setattr(_objc, "Q", _quartz(CGWindowListCreateImage=lambda *a: None))
    assert cap.snapshot((0, 0, 30, 20)) is None


def _settled(cap: capture.MacCapture, sck: FakeSCK) -> None:
    """Let the background snapshots finish (answering the held ones)."""
    end = time.monotonic() + 5
    while cap._snapping and time.monotonic() < end:
        while sck.held:
            flt, done = sck.held.pop()
            done(SimpleNamespace(source=flt), None)
        time.sleep(0.005)
    assert not cap._snapping


def test_the_gui_thread_never_waits_for_a_ledge_snapshot(sck, conv, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_objc, "is_main_thread", lambda: True)
    sck.silent = True                                              # (ScreenCaptureKit takes its time)
    cap = capture.MacCapture()
    rect = (0, 0, 30, 20)
    t0 = time.perf_counter()
    assert cap.snapshot(rect) is None and cap.snapshot(rect) is None          # can't tell yet
    assert time.perf_counter() - t0 < 0.05
    end = time.monotonic() + 5
    while not sck.held and time.monotonic() < end:
        time.sleep(0.005)
    assert len(sck.held) == 1                                      # one picture at a time per rect
    _settled(cap, sck)
    t0 = time.perf_counter()
    img = cap.snapshot(rect)
    assert time.perf_counter() - t0 < 0.05
    assert img is not None and (img.width(), img.height()) == (30, 20) and conv[-1][3] is False
    assert cap.snapshot((0, 0, 31, 20)) is None                    # another rect: not this picture
    _settled(cap, sck)
    monkeypatch.setattr(capture, "SNAPSHOT_FRESH_S", 0.0)
    time.sleep(0.01)
    assert cap.snapshot(rect) is None                              # too old to tell
    _settled(cap, sck)


def test_without_screen_recording_background_pictures_are_none_and_nothing_asks(monkeypatch):  # type: ignore[no-untyped-def]
    q = _quartz(granted=False)
    monkeypatch.setattr(_objc, "Q", q)
    monkeypatch.delenv("PIXELCAT_NO_PROMPTS")
    cap = capture.MacCapture()
    assert cap.snapshot((0, 0, 100, 100)) is None
    with cap.quiet():
        for grab in (lambda: cap.grab((0, 0, 9, 9)), lambda: cap.grab_without((0, 0, 9, 9), 1),
                     lambda: cap.grab_window(42, (0, 0, 9, 9))):
            with pytest.raises(PermissionNeeded) as err:
                grab()
            assert err.value.permission == "screen_recording" and "restart" in str(err.value)
    assert q.called("CGRequestScreenCaptureAccess") == []


def test_without_screen_recording_a_tool_asks_once_then_only_raises(monkeypatch, fake_run):  # type: ignore[no-untyped-def]
    q = _quartz(granted=False)
    monkeypatch.setattr(_objc, "Q", q)
    monkeypatch.delenv("PIXELCAT_NO_PROMPTS")
    fake_run.answer(["/usr/bin/open"])
    cap = capture.MacCapture()
    for _ in range(3):
        with pytest.raises(PermissionNeeded):
            cap.grab_without((0, 0, 9, 9), OWN_PID)
    assert len(q.called("CGRequestScreenCaptureAccess")) == 1 and len(fake_run.calls) == 1


def test_no_prompts_in_tests_even_at_tool_boundaries(monkeypatch):  # type: ignore[no-untyped-def]
    q = _quartz(granted=False)
    monkeypatch.setattr(_objc, "Q", q)
    with pytest.raises(PermissionNeeded):
        capture.MacCapture().grab((0, 0, 9, 9))
    assert q.called("CGRequestScreenCaptureAccess") == []


@pytest.mark.macos
def test_a_real_cgimage_becomes_an_upright_rgb32_qimage_scaled_to_points():
    import Quartz as Q

    ctx = Q.CGBitmapContextCreate(None, 40, 20, 8, 0, Q.CGColorSpaceCreateWithName(Q.kCGColorSpaceSRGB),
                                  Q.kCGImageAlphaPremultipliedFirst | Q.kCGBitmapByteOrder32Little)
    Q.CGContextSetRGBFillColor(ctx, 1, 0, 0, 1)
    Q.CGContextFillRect(ctx, Q.CGRectMake(0, 10, 40, 10))      # CoreGraphics' y points up: the top half
    Q.CGContextSetRGBFillColor(ctx, 0, 0, 1, 1)
    Q.CGContextFillRect(ctx, Q.CGRectMake(0, 0, 40, 10))
    img = capture.to_qimage(Q.CGBitmapContextCreateImage(ctx), 20, 10)      # a 2x "Retina" picture to points
    assert (img.width(), img.height(), img.format()) == (20, 10, QImage.Format.Format_RGB32)
    assert img.pixel(5, 1) == 0xFFFF0000 and img.pixel(5, 8) == 0xFF0000FF


@pytest.mark.macos
def test_the_real_screen_recording_check_never_prompts():
    from pixelcat.adapters.macos import permissions

    granted = permissions.screen_recording()          # CGPreflightScreenCaptureAccess only
    if not granted:
        assert capture.MacCapture().snapshot((0, 0, 50, 50)) is None
