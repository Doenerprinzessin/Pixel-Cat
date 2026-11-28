"""CapturePort on macOS: screenshots with ScreenCaptureKit (CGWindowListCreateImage as a fallback), scaled to
points so an image is exactly as big as its rect (the chat's helper maps a picture's pixel to the screen as
``rect origin + px / scale``, the ledge finder reads it the same way).

Needs Screen Recording.  Every method first only *checks* it (``CGPreflightScreenCaptureAccess``, never a prompt):
* :meth:`MacCapture.snapshot` (the ledge finder, background) returns None without it;
* :meth:`~MacCapture.grab`, :meth:`~MacCapture.grab_without` and :meth:`~MacCapture.grab_window` (the chat's
  helper and the chat's screenshots, things the user asked for) ask macOS for it (at most once per run, see
  :func:`permissions.need`) and raise :class:`~pixelcat.domain.PermissionNeeded`; inside :meth:`MacCapture.quiet`
  they raise without asking (for pictures nobody asked for, e.g. checking whether a starting app is drawn).

Everything here may run on any thread: ScreenCaptureKit answers on its own queue, the caller waits for it (with a
timeout).  Pictures stay in memory.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Callable, Iterator

from . import _objc, permissions
from ...domain import PermissionNeeded, WindowId
from ...ports import Rect

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

WAIT_S = 3.0                 # how long a ScreenCaptureKit request may take
CONTENT_TTL_S = 1.0          # the list of displays, windows and apps is reused this long
BGRA = 0x42475241            # kCVPixelFormatType_32BGRA ('BGRA')


# ------------------------------------------------------------------ CGImage -> QImage
def to_qimage(cgimage: Any, width: int, height: int, smooth: bool = True) -> QImage:
    """A CGImage as an RGB32 QImage of ``width`` x ``height`` (scaled; sRGB).  Drawing it into a bitmap of a
    known layout (32 bit BGRA, premultiplied: Qt's ARGB32_Premultiplied) copes with every pixel format and color
    space a capture can come in, and scales it in the same step."""
    from PySide6.QtGui import QImage

    Q = _objc.Q
    w, h = max(1, int(width)), max(1, int(height))
    ctx = Q.CGBitmapContextCreate(None, w, h, 8, 0, Q.CGColorSpaceCreateWithName(Q.kCGColorSpaceSRGB),
                                  Q.kCGImageAlphaPremultipliedFirst | Q.kCGBitmapByteOrder32Little)
    if ctx is None:
        raise OSError("couldn't make room for the screenshot")
    Q.CGContextSetInterpolationQuality(ctx, Q.kCGInterpolationHigh if smooth else Q.kCGInterpolationLow)
    Q.CGContextDrawImage(ctx, Q.CGRectMake(0, 0, w, h), cgimage)
    out = Q.CGBitmapContextCreateImage(ctx)
    if out is None:
        raise OSError("couldn't read the screenshot")
    data = bytes(Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(out)))
    img = QImage(data, w, h, int(Q.CGImageGetBytesPerRow(out)), QImage.Format.Format_ARGB32_Premultiplied)
    return img.convertToFormat(QImage.Format.Format_RGB32).copy()


def _blank(img: QImage) -> bool:
    """True if a picture is one flat color (a window that can't be drawn off screen comes back like that)."""
    step_x, step_y = max(1, img.width() // 9), max(1, img.height() // 9)
    colors = {img.pixel(x, y) for x in range(step_x // 2, img.width(), step_x)
              for y in range(step_y // 2, img.height(), step_y)}
    return len(colors) <= 1


# ------------------------------------------------------------------ ScreenCaptureKit
def _wait(start: Callable[[Callable[[Any, Any], None]], None], what: str) -> Any:
    """Run an asynchronous ScreenCaptureKit call and wait for its completion handler (result, error)."""
    done = threading.Event()
    box: dict[str, Any] = {}

    def handler(result: Any, error: Any) -> None:
        box["result"], box["error"] = result, error
        done.set()

    start(handler)
    if not done.wait(WAIT_S):
        raise OSError(f"the screen didn't answer in time ({what})")
    if box.get("error") is not None or box.get("result") is None:
        raise OSError(f"couldn't {what}: {box.get('error')}")
    return box["result"]


def _sck() -> bool:
    """True if ScreenCaptureKit can take screenshots here (macOS 14 and later)."""
    try:
        return bool(_objc.SCK.SCScreenshotManager)
    except Exception:  # noqa: BLE001 - older macOS / pyobjc without it
        return False


def _shot(content_filter: Any, w: int, h: int, source: tuple[float, float, float, float] | None = None,
          window: bool = False) -> Any:
    """One ScreenCaptureKit screenshot (a CGImage) of ``w`` x ``h`` pixels."""
    S, Q = _objc.SCK, _objc.Q
    cfg = S.SCStreamConfiguration.alloc().init()
    cfg.setWidth_(max(1, int(w)))
    cfg.setHeight_(max(1, int(h)))
    cfg.setShowsCursor_(False)
    cfg.setPixelFormat_(BGRA)
    if source is not None:
        cfg.setSourceRect_(Q.CGRectMake(*source))
    if window:
        cfg.setIgnoreShadowsSingleWindow_(True)
    return _wait(lambda done: S.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
        content_filter, cfg, done), "take the screenshot")


class _Content:
    """ScreenCaptureKit's displays, windows and apps (fetched at most every CONTENT_TTL_S seconds)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: Any = None
        self._at = 0.0

    def get(self) -> Any:
        with self._lock:
            if self._value is not None and time.monotonic() - self._at < CONTENT_TTL_S:
                return self._value
        value = _wait(lambda done: _objc.SCK.SCShareableContent
                      .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                          False, True, done), "list the screen's windows")
        with self._lock:
            self._value, self._at = value, time.monotonic()
        return value


def _intersect(a: Rect, b: Rect) -> Rect | None:
    l, t, r, b2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (l, t, r, b2) if r > l and b2 > t else None


def _display_rect(display_id: int) -> Rect:
    b = _objc.Q.CGDisplayBounds(display_id)
    x, y = round(b.origin.x), round(b.origin.y)
    return (x, y, x + round(b.size.width), y + round(b.size.height))


def _canvas(w: int, h: int) -> QImage:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    img = QImage(max(1, w), max(1, h), QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.black)            # what isn't on any display stays black (as on Windows)
    return img


# ------------------------------------------------------------------ the port
class MacCapture:
    def __init__(self) -> None:
        self._content = _Content()
        self._lock = threading.Lock()
        self._excluded: dict[int, int] = {}      # pid -> how many excluding() blocks hide it
        self._local = threading.local()

    # -- permission
    @contextmanager
    def _quiet(self) -> Iterator[None]:
        self._local.quiet = getattr(self._local, "quiet", 0) + 1
        try:
            yield
        finally:
            self._local.quiet -= 1

    def quiet(self) -> AbstractContextManager[None]:
        """While inside (on this thread), a missing Screen Recording permission raises PermissionNeeded without
        asking macOS for it: for pictures the user didn't ask for (whether a starting app is drawn yet)."""
        return self._quiet()

    def _allowed(self) -> None:
        if permissions.screen_recording():
            return
        if getattr(self._local, "quiet", 0):
            raise PermissionNeeded(permissions.SCREEN_RECORDING, permissions.message(permissions.SCREEN_RECORDING))
        permissions.need(permissions.SCREEN_RECORDING)

    # -- pictures
    def _grab(self, rect: Rect, exclude: set[int], smooth: bool = True) -> QImage:
        l, t, r, b = rect
        w, h = max(1, r - l), max(1, b - t)
        if _sck():
            try:
                return self._grab_sck(rect, exclude, smooth)
            except Exception:  # noqa: BLE001 - try the old way
                log.debug("ScreenCaptureKit screenshot failed", exc_info=True)
        Q = _objc.Q
        try:     # (shows the cat too: this old call can't leave an app out)
            img = Q.CGWindowListCreateImage(Q.CGRectMake(l, t, w, h), Q.kCGWindowListOptionOnScreenOnly,
                                            Q.kCGNullWindowID, Q.kCGWindowImageNominalResolution)
        except Exception as exc:  # noqa: BLE001 - gone in a future macOS
            raise OSError("couldn't take a screenshot") from exc
        if img is None:
            raise OSError("couldn't take a screenshot")
        return to_qimage(img, w, h, smooth)

    def _grab_sck(self, rect: Rect, exclude: set[int], smooth: bool) -> QImage:
        from PySide6.QtGui import QPainter

        content = self._content.get()
        S = _objc.SCK
        apps = [a for a in (content.applications() or []) if int(a.processID()) in exclude]
        canvas = _canvas(rect[2] - rect[0], rect[3] - rect[1])
        painter = QPainter(canvas)
        try:
            for display in content.displays() or []:
                drect = _display_rect(display.displayID())
                part = _intersect(rect, drect)
                if part is None:
                    continue
                pw, ph = part[2] - part[0], part[3] - part[1]
                flt = S.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
                    display, apps, [])
                cg = _shot(flt, pw, ph, (part[0] - drect[0], part[1] - drect[1], pw, ph))
                painter.drawImage(part[0] - rect[0], part[1] - rect[1], to_qimage(cg, pw, ph, smooth))
        finally:
            painter.end()
        return canvas

    def _excluding_now(self) -> set[int]:
        with self._lock:
            return set(self._excluded)

    def grab(self, rect: Rect) -> QImage:
        self._allowed()
        return self._grab(rect, self._excluding_now())

    def grab_without(self, rect: Rect, pid: int) -> QImage:
        self._allowed()
        return self._grab(rect, self._excluding_now() | {int(pid)})

    def grab_window(self, win: WindowId, frame: Rect) -> QImage | None:
        self._allowed()
        w, h = frame[2] - frame[0], frame[3] - frame[1]
        if _objc.is_own(win) or w < 2 or h < 2:
            return None
        img = None
        if _sck():
            try:
                scwin = next((x for x in (self._content.get().windows() or []) if int(x.windowID()) == int(win)), None)
                if scwin is None:
                    return None
                flt = _objc.SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(scwin)
                img = to_qimage(_shot(flt, w, h, window=True), w, h)
            except Exception:  # noqa: BLE001 - try the old way
                log.debug("ScreenCaptureKit window screenshot failed", exc_info=True)
        if img is None:
            Q = _objc.Q
            try:
                cg = Q.CGWindowListCreateImage(Q.CGRectNull, Q.kCGWindowListOptionIncludingWindow, int(win),
                                               Q.kCGWindowImageBoundsIgnoreFraming | Q.kCGWindowImageNominalResolution)
            except Exception:  # noqa: BLE001
                log.debug("CGWindowListCreateImage failed", exc_info=True)
                cg = None
            if cg is None:
                return None
            img = to_qimage(cg, w, h)
        return None if _blank(img) else img

    @contextmanager
    def _excluding(self, pid: int) -> Iterator[None]:
        pid = int(pid)
        with self._lock:
            self._excluded[pid] = self._excluded.get(pid, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                left = self._excluded.get(pid, 1) - 1
                if left > 0:
                    self._excluded[pid] = left
                else:
                    self._excluded.pop(pid, None)

    def excluding(self, pid: int) -> AbstractContextManager[None]:
        return self._excluding(pid)

    def snapshot(self, rect: Rect) -> QImage | None:
        if rect[2] <= rect[0] or rect[3] <= rect[1] or not permissions.screen_recording():
            return None
        try:
            return self._grab(rect, set(), smooth=False)
        except Exception:  # noqa: BLE001 - no picture: no ledges this time
            log.debug("ledge snapshot failed", exc_info=True)
            return None
