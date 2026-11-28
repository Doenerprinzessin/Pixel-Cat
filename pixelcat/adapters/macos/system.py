"""SystemPort on macOS: this copy of the cat (an flock'ed lock file), message boxes (osascript), system
information (pmset, sysctl, vm_stat), locking the screen, media keys and the volume (osascript).

Volume and message boxes use AppleScript's Standard Additions (``display dialog``, ``get volume settings``,
``set volume``): they need no Automation permission and work before Qt starts.  Media keys are posted as
system-defined events, so they need Accessibility.  The installer's "please quit" event doesn't exist on macOS
(no installer yet): ``create_quit_event`` answers 0, which the controller treats as "nothing to listen to".
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from typing import IO, Any

from . import _objc, hardware, permissions
from ...domain import Hardware

log = logging.getLogger(__name__)

OSASCRIPT = "/usr/bin/osascript"
LOGIN_FRAMEWORK = "/System/Library/PrivateFrameworks/login.framework"
# NX_KEYTYPE_* of <IOKit/hidsystem/ev_keymap.h>
MEDIA_KEYS = {"play_pause": 16, "next": 17, "previous": 18}
NO_STOP_KEY = "Macs have no stop media key - use play/pause to pause instead"
NO_VOLUME = "this sound output has no volume control on the Mac (e.g. HDMI or digital) - change it on the device"
_SYSTEM_DEFINED_SUBTYPE_MEDIA = 8
_KEY_Q = 12                   # kVK_ANSI_Q

_lock_file: IO[str] | None = None      # the single-instance lock, held for the process lifetime


def _osascript(*lines: str, args: tuple[str, ...] = (), timeout: float = 10) -> str:
    """Run an AppleScript; its result text.  Raises OSError if it fails."""
    argv = [OSASCRIPT]
    for line in lines:
        argv += ["-e", line]
    if args:
        argv += ["--", *args]      # (data as arguments, never pasted into the script: nothing to escape)
    try:
        res = _objc.run(argv, timeout=timeout)
    except OSError:
        raise
    except Exception as exc:  # noqa: BLE001 - subprocess.TimeoutExpired
        raise OSError(f"osascript didn't answer: {exc}") from exc
    if res.returncode != 0:
        raise OSError((res.stderr or "").strip() or f"osascript failed ({res.returncode})")
    return (res.stdout or "").strip()


def parse_volume(text: str) -> tuple[int, bool]:
    """'output volume:6, input volume:50, alert volume:100, output muted:false' -> (6, False)."""
    fields = dict(part.strip().split(":", 1) for part in text.split(",") if ":" in part)
    level = fields.get("output volume", "").strip()
    if not level.isdigit():
        raise OSError(NO_VOLUME)    # "missing value": the output can't be turned up or down here
    return int(level), fields.get("output muted", "").strip() == "true"


# ------------------------------------------------------------------ system information
def _battery() -> str | None:
    out = _objc.run(["/usr/bin/pmset", "-g", "batt"], timeout=5).stdout or ""
    line = next((ln for ln in out.splitlines() if "InternalBattery" in ln), None)
    if line is None:
        return "Power: no battery (desktop)"
    m = re.search(r"(\d+)%", line)
    if not m:
        return None
    plug = "plugged in" if "AC Power" in out else "on battery"
    return f"Battery: {m.group(1)}% ({plug})"


def _memory() -> str | None:
    total = int(hardware.sysctl("hw.memsize") or 0)
    out = _objc.run(["/usr/bin/vm_stat"], timeout=5).stdout or ""
    m = re.search(r"page size of (\d+) bytes", out)
    if not total or not m:
        return None
    pages = {k.strip(): int(v) for k, v in re.findall(r"^([^:\n]+):\s+(\d+)\.?$", out, re.M)}
    # what can be handed to apps right away (like psutil's "available"): free, inactive and speculative pages
    free_pages = sum(pages.get(f"Pages {kind}", 0) for kind in ("free", "inactive", "speculative"))
    used = max(0, total - free_pages * int(m.group(1)))
    return f"RAM: {used / 2**30:.1f} of {total / 2**30:.1f} GB used ({round(used * 100 / total)}%)"


def _cpu() -> str:
    load = os.getloadavg()[0]
    cores = os.cpu_count() or 1
    return f"CPU: {min(100.0, load * 100 / cores):.0f}% (load {load:.2f} on {cores} cores)"


def _disk() -> str:
    du = shutil.disk_usage("/")
    return f"Disk: {du.free / 2**30:.0f} GB free of {du.total / 2**30:.0f} GB"


def _uptime() -> str | None:
    m = re.search(r"sec\s*=\s*(\d+)", hardware.sysctl("kern.boottime"))
    if not m:
        return None
    up = max(0.0, time.time() - int(m.group(1)))
    return f"Uptime: {int(up // 86400)}d {int(up % 86400 // 3600)}h {int(up % 3600 // 60)}m"


def system_info() -> str:
    """Battery, memory, CPU, disk and uptime in one line, like the Windows adapter's."""
    parts: list[str] = []
    for part in (_battery, _memory, _cpu, _disk, _uptime):
        try:
            text = part()
        except Exception:  # noqa: BLE001 - one missing figure never hides the others
            log.debug("system info: %s failed", part.__name__, exc_info=True)
            continue
        if text:
            parts.append(text)
    return "; ".join(parts)


# ------------------------------------------------------------------ locking the screen
def _lock_now() -> bool:
    """login.framework's SACLockScreenImmediate: what Control+Command+Q does, without any permission."""
    bundle = _objc.FN.NSBundle.bundleWithPath_(LOGIN_FRAMEWORK)
    if bundle is None:
        return False
    found: dict[str, Any] = {}
    _objc.objc.loadBundleFunctions(bundle, found, [("SACLockScreenImmediate", b"i")])
    fn = found.get("SACLockScreenImmediate")
    return fn is not None and fn() == 0


def _press_lock_keys() -> bool:
    """Control+Command+Q, posted (only when the cat may post events: never asks)."""
    if not permissions.post_events():
        return False
    q = _objc.Q
    flags = q.kCGEventFlagMaskControl | q.kCGEventFlagMaskCommand
    for down in (True, False):
        ev = q.CGEventCreateKeyboardEvent(None, _KEY_Q, down)
        q.CGEventSetFlags(ev, flags)
        q.CGEventPost(q.kCGHIDEventTap, ev)
    return True


def _display_sleep() -> bool:
    """Turn the displays off (it locks when "require password after the screen is off" is "immediately")."""
    return _objc.run(["/usr/bin/pmset", "displaysleepnow"], timeout=5).returncode == 0


def lock_screen() -> bool:
    for way in (_lock_now, _press_lock_keys, _display_sleep):
        try:
            if way():
                return True
        except Exception:  # noqa: BLE001 - try the next way
            log.debug("lock_screen: %s failed", way.__name__, exc_info=True)
    return False


# ------------------------------------------------------------------ media keys
def media_key(action: str) -> None:
    if action == "stop":
        raise OSError(NO_STOP_KEY)
    key = MEDIA_KEYS[action]
    permissions.need(permissions.ACCESSIBILITY)
    ak, q = _objc.AK, _objc.Q
    for state in (0xA, 0xB):    # key down, key up
        ev = ak.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            ak.NSEventTypeSystemDefined, (0, 0), state << 8, 0, 0, None, _SYSTEM_DEFINED_SUBTYPE_MEDIA,
            (key << 16) | (state << 8), -1)
        q.CGEventPost(q.kCGHIDEventTap, ev.CGEvent())


class MacSystem:
    @property
    def instance(self) -> str:
        return os.environ.get("PIXELCAT_INSTANCE", "")

    def acquire_single_instance(self) -> bool:
        """Lock ``instance<PIXELCAT_INSTANCE>.lock`` in the data folder; False if another cat holds it.
        The lock goes when the process ends (even when it crashes)."""
        global _lock_file
        if _lock_file is not None:
            return True
        import fcntl

        from ... import paths

        try:
            fh = open(paths.data_dir() / f"instance{self.instance}.lock", "a+", encoding="utf-8")
        except OSError as exc:
            log.warning("can't open the instance lock (%s) - continuing without guard", exc)
            return True
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            return False
        except OSError as exc:
            fh.close()
            log.warning("can't lock the instance lock (%s) - continuing without guard", exc)
            return True
        _lock_file = fh
        return True

    def create_quit_event(self) -> int:
        return 0        # no installer on macOS: nothing asks the cat to quit

    def quit_requested(self, handle: int) -> bool:
        return False

    def message_box(self, text: str, title: str = "Pixel Cat") -> None:
        """A dialog with an OK button (waits until it is closed)."""
        try:
            _osascript("on run argv",
                       'display dialog (item 1 of argv) with title (item 2 of argv) buttons {"OK"} '
                       'default button "OK" with icon note',
                       "end run", args=(text, title), timeout=24 * 3600)
        except OSError as exc:
            log.warning("%s: %s (no dialog: %s)", title, text, exc)

    def system_info(self) -> str:
        return system_info()

    def lock_screen(self) -> bool:
        return lock_screen()

    def media_key(self, action: str) -> None:
        media_key(action)

    def get_volume(self) -> tuple[int, bool]:
        return parse_volume(_osascript("get volume settings"))

    def set_volume(self, percent: int) -> int:
        percent = max(0, min(100, int(percent)))
        _osascript(f"set volume output volume {percent}" + (" without output muted" if percent > 0 else ""))
        return percent

    def set_mute(self, muted: bool) -> None:
        _osascript(f"set volume output muted {'true' if muted else 'false'}")

    def hardware(self, fresh: bool = False) -> Hardware:
        return hardware.detect(fresh)
