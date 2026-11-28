"""The macOS adapter: every port implemented with pyobjc (AppKit, Quartz, Accessibility, ScreenCaptureKit,
Security) and a few system programs (open, osascript, pmset, ps ...).

Only this package imports pyobjc, and only through the lazy proxies of ``_objc``: every module here imports
without pyobjc, so ``pixelcat.bootstrap`` can fall back to the null adapter when it is missing and the
boundary and conformance tests run on every OS.  No ctypes, no other adapter.

Coordinates are Quartz points (top-left of the main display, y down), which are Qt's logical coordinates
(``MacDisplay.qt_logical()`` is True).  The cat's own windows are told apart from other apps' by their id
(``_objc.is_own``).  Permissions (Accessibility, Screen Recording) are checked without prompting; see
``permissions``.  ``platform.build()`` puts it all together.
"""
