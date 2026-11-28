"""The macOS privacy permissions (TCC) the cat needs: checks that never prompt, and requests that do.

* Checks (:func:`accessibility`, :func:`screen_recording`, :func:`post_events`, :func:`listen_events`) never show
  anything; background code (ledges, window titles, watches) only ever checks and quietly does without.
* :func:`need` is for the moments the user (or the chat's helper, for the user) asks for something that needs a
  permission: if it is missing it asks macOS once per run (the system prompt), the next time opens the settings
  pane once per run, and raises :class:`~pixelcat.domain.PermissionNeeded` with a message telling the user where to
  turn it on.
* ``PIXELCAT_NO_PROMPTS=1`` (set for every test) turns all requests and settings panes off.
"""
from __future__ import annotations

import logging
import os
import platform as _platform
import sys
import threading

from . import _objc
from ...domain import PermissionNeeded

log = logging.getLogger(__name__)

ACCESSIBILITY = "accessibility"          # control the mouse and keyboard, read and use other apps' windows
SCREEN_RECORDING = "screen_recording"    # see the screen (screenshots, window titles)
INPUT_MONITORING = "input_monitoring"    # notice key presses in other apps
KINDS = (ACCESSIBILITY, SCREEN_RECORDING, INPUT_MONITORING)

# the anchors of System Settings → Privacy & Security
_ANCHORS = {ACCESSIBILITY: "Privacy_Accessibility", SCREEN_RECORDING: "Privacy_ScreenCapture",
            INPUT_MONITORING: "Privacy_ListenEvent"}
_PANE_URLS = ("x-apple.systempreferences:com.apple.preference.security?{anchor}",
              "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?{anchor}")
_WHAT = {ACCESSIBILITY: "control the mouse and keyboard and use other apps' windows",
         SCREEN_RECORDING: "see the screen",
         INPUT_MONITORING: "notice when you press Esc"}

_lock = threading.Lock()
_requested: set[str] = set()     # asked macOS for it this run
_opened: set[str] = set()        # opened its settings pane this run


def no_prompts() -> bool:
    """True when nothing may prompt or open settings (``PIXELCAT_NO_PROMPTS=1``, set by the tests)."""
    return os.environ.get("PIXELCAT_NO_PROMPTS", "") == "1"


def reset() -> None:
    """Forget what was requested and opened this run (tests)."""
    with _lock:
        _requested.clear()
        _opened.clear()


# ------------------------------------------------------------------ checks (never prompt)
def _quiet(check: str) -> bool:
    try:
        if check == "accessibility":
            return bool(_objc.AX.AXIsProcessTrusted())
        if check == "screen_recording":
            return bool(_objc.Q.CGPreflightScreenCaptureAccess())
        if check == "post_events":
            return bool(_objc.Q.CGPreflightPostEventAccess())
        if check == "listen_events":
            return bool(_objc.Q.CGPreflightListenEventAccess())
    except Exception:  # noqa: BLE001 - pyobjc missing or the API gone: not granted
        log.debug("permission check %s failed", check, exc_info=True)
    return False


def accessibility() -> bool:
    """The cat may use other apps through Accessibility (AXIsProcessTrusted)."""
    return _quiet("accessibility")


def screen_recording() -> bool:
    """The cat may see the screen (CGPreflightScreenCaptureAccess)."""
    return _quiet("screen_recording")


def post_events() -> bool:
    """The cat may post mouse and keyboard events (CGPreflightPostEventAccess; part of Accessibility)."""
    return _quiet("post_events")


def listen_events() -> bool:
    """The cat may watch key presses in other apps (CGPreflightListenEventAccess: Input Monitoring)."""
    return _quiet("listen_events")


def granted(kind: str) -> bool:
    """The check for one of :data:`KINDS`."""
    return {ACCESSIBILITY: accessibility, SCREEN_RECORDING: screen_recording, INPUT_MONITORING: listen_events}[kind]()


# ------------------------------------------------------------------ requests (prompt; once per run)
def _system_request(kind: str) -> None:
    if kind == ACCESSIBILITY:
        _objc.AX.AXIsProcessTrustedWithOptions({_objc.AX.kAXTrustedCheckOptionPrompt: True})
    elif kind == SCREEN_RECORDING:
        _objc.Q.CGRequestScreenCaptureAccess()
    elif kind == INPUT_MONITORING:
        _objc.Q.CGRequestListenEventAccess()


def request(kind: str) -> bool:
    """Ask for a missing permission: the system prompt the first time this run, its settings pane the second
    time, nothing after that.  True if it is granted now."""
    if granted(kind):
        return True
    if no_prompts():
        return False
    with _lock:
        first = kind not in _requested
        _requested.add(kind)
    if first:
        try:
            _system_request(kind)
        except Exception:  # noqa: BLE001 - a prompt that can't be shown: the pane still helps
            log.warning("couldn't ask macOS for %s", kind, exc_info=True)
            open_pane(kind)
    else:
        open_pane(kind)
    return granted(kind)


def open_pane(kind: str) -> bool:
    """Open System Settings → Privacy & Security at the permission's list (at most once per run)."""
    if no_prompts():
        return False
    with _lock:
        if kind in _opened:
            return False
        _opened.add(kind)
    for url in _PANE_URLS:
        try:
            if _objc.run(["/usr/bin/open", url.format(anchor=_ANCHORS[kind])], timeout=10).returncode == 0:
                return True
        except Exception:  # noqa: BLE001 - try the other address
            log.debug("open %s failed", url, exc_info=True)
    return False


# ------------------------------------------------------------------ what the user is told
def pane_name(kind: str) -> str:
    """The list's name in System Settings → Privacy & Security."""
    if kind == SCREEN_RECORDING:
        major = _platform.mac_ver()[0].split(".")[0]
        return "Screen & System Audio Recording" if major.isdigit() and int(major) >= 15 else "Screen Recording"
    return {ACCESSIBILITY: "Accessibility", INPUT_MONITORING: "Input Monitoring"}[kind]


def bundled() -> bool:
    """True when the cat runs as its own app (not python started from a terminal or an editor)."""
    return bool(getattr(sys, "frozen", False))


def message(kind: str) -> str:
    """What the user reads when the permission is missing."""
    who = ("Pixel Cat" if bundled() else
           "the app you started Pixel Cat from (Terminal, iTerm, VS Code ...) - or Python, if it is listed")
    then = "then quit and restart Pixel Cat" if kind == SCREEN_RECORDING else "then try again"
    return (f"macOS hasn't allowed Pixel Cat to {_WHAT[kind]} yet. Open System Settings → Privacy & Security → "
            f"{pane_name(kind)} and turn on {who}, {then}.")


def need(kind: str) -> None:
    """Raise :class:`PermissionNeeded` unless the permission is granted (asking for it first, see :func:`request`).
    Only for things the user asked for, never from background code."""
    if granted(kind) or request(kind):
        return
    raise PermissionNeeded(kind, message(kind))
