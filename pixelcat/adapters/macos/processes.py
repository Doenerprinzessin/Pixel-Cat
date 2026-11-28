"""ProcessPort on macOS: running programs (NSRunningApplication, ``ps``), ending them with signals.

``/force end`` works like the Windows one (Task Manager's "End task"): an app's name ends every process of
that app and its helpers, but not other apps it started.  On a Mac "another app" is one with its own Dock
icon (an NSRunningApplication with the regular activation policy).  The core of macOS (launchd, WindowServer,
Finder, Dock ...), the cat itself and the programs that started the cat (its Terminal, say) are never ended.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import time
from typing import Callable

from . import _objc
from .profile import WINDOW_ALIASES
from ...domain import KillPlan, Proc

log = logging.getLogger(__name__)

NO_ADMIN = "ending that needs admin rights on macOS - I won't do that"

# ending any of these could log the user out or break the Mac until a restart (lower case)
PROTECTED_PROCESSES = {
    "launchd", "kernel_task", "windowserver", "loginwindow", "dock", "finder", "systemuiserver", "controlcenter",
    "notificationcenter", "coreaudiod", "coreservicesd", "launchservicesd", "cfprefsd", "distnoted", "logd",
    "opendirectoryd", "securityd", "syslogd", "configd", "powerd", "mds", "fseventsd", "usereventagent",
    "watchdogd", "kextd", "kernelmanagerd", "trustd", "tccd",
}
# programs of macOS itself (never matched by a part of their name: "/force end code" must not reach
# com.apple.CodeSigningHelper)
_SYSTEM_DIRS = ("/System/Library/", "/usr/libexec/", "/usr/sbin/", "/sbin/", "/Library/Apple/")
_ALIASES = dict(WINDOW_ALIASES)
_PS_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(.+?)\s*$")


def _basename(comm: str) -> str:
    return comm.rstrip("/").rsplit("/", 1)[-1]


def ps_table() -> list[tuple[Proc, str]]:
    """Every process with its program's path (``ps -axo pid=,ppid=,comm=``)."""
    out = _objc.run(["/bin/ps", "-axo", "pid=,ppid=,comm="], timeout=10).stdout or ""
    rows: list[tuple[Proc, str]] = []
    for line in out.splitlines():
        m = _PS_LINE.match(line)
        if m:
            path = m.group(3)
            rows.append((Proc(int(m.group(1)), int(m.group(2)), _basename(path)), path))
    return rows


def list_processes() -> list[Proc]:
    return [p for p, _path in ps_table()]


def _running_apps() -> list[tuple[int, str, bool]]:
    """(pid, name, has its own Dock icon) of the running apps; [] if AppKit can't say."""
    try:
        regular = _objc.AK.NSApplicationActivationPolicyRegular
        return [(int(app.processIdentifier()), str(app.localizedName() or ""), app.activationPolicy() == regular)
                for app in _objc.AK.NSWorkspace.sharedWorkspace().runningApplications()]
    except Exception:  # noqa: BLE001 - pyobjc missing
        log.debug("running apps unavailable", exc_info=True)
        return []


def _ancestors(pid: int, by_pid: dict[int, Proc]) -> set[int]:
    """``pid`` and the processes that started it (up to launchd)."""
    out: set[int] = set()
    while pid > 1 and pid not in out:
        out.add(pid)
        proc = by_pid.get(pid)
        if proc is None:
            break
        pid = proc.ppid
    return out


def _protected(p: Proc) -> bool:
    return p.exe.lower() in PROTECTED_PROCESSES or p.pid in (0, 1)


def process_exe(pid: int) -> str:
    """The program's file name ("Safari", "Google Chrome Helper"); "" if unknown."""
    try:
        app = _objc.AK.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        url = app.executableURL() if app is not None else None
        if url is not None:
            return str(url.lastPathComponent())
    except Exception:  # noqa: BLE001 - pyobjc missing: ps knows it too
        log.debug("NSRunningApplication failed for %s", pid, exc_info=True)
    try:
        res = _objc.run(["/bin/ps", "-o", "comm=", "-p", str(int(pid))], timeout=5)
    except Exception:  # noqa: BLE001
        return ""
    comm = (res.stdout or "").strip()
    return _basename(comm) if res.returncode == 0 and comm else ""


def plan_force_end(target: str, own_pid: int) -> KillPlan:
    """Which processes "/force end <target>" means.  Raises ValueError if nothing matches or the target is a
    core part of macOS."""
    q = target.strip().strip("\"'").lower().removesuffix(".app")
    if not q:
        raise ValueError("tell me which app to end, e.g. /force end safari")
    stem = _ALIASES.get(q, q)
    table = ps_table()
    procs = [p for p, _path in table]
    by_pid = {p.pid: p for p in procs}
    apps = _running_apps()

    def by_app_name(hit: Callable[[str], bool]) -> list[Proc]:
        """By the name the Dock shows ("Microsoft Teams" runs as MSTeams)."""
        return [by_pid[pid] for pid, name, _own in apps if pid in by_pid and hit(name.lower())]

    roots = [p for p in procs if p.exe.lower() in (stem, q)] or by_app_name(lambda n: n in (stem, q))
    if not roots and len(stem) >= 3:
        roots = [p for p, path in table if stem in p.exe.lower() and not path.startswith(_SYSTEM_DIRS)] or \
            by_app_name(lambda n: stem in n or q in n)
    if not roots:
        raise ValueError(f"no running app matches '{target}'")
    blocked = sorted({p.exe for p in roots if _protected(p)})
    if blocked:
        raise ValueError(f"I won't end {', '.join(blocked)} - it's a core part of macOS")
    # like Task Manager's "End task": take the app's own helpers along, but never other apps it started
    window_pids = {pid for pid, _name, own in apps if own}
    root_pids = {p.pid for p in roots}
    keep = _ancestors(own_pid, by_pid)       # the cat and whatever started it (its Terminal, say)
    chosen: dict[int, Proc] = {}
    frontier = [p.pid for p in roots]
    while frontier:
        pid = frontier.pop()
        if pid in chosen or pid in keep or pid not in by_pid:
            continue
        proc = by_pid[pid]
        if _protected(proc):
            continue
        chosen[pid] = proc
        for c in procs:
            if c.ppid == pid and (c.exe.lower() == proc.exe.lower() or c.pid not in window_pids or c.pid in root_pids):
                frontier.append(c.pid)
    if not chosen:
        raise ValueError(f"nothing I'm allowed to end matches '{target}'")
    exes = sorted({p.exe for p in chosen.values()}, key=str.lower)
    label = f"{', '.join(exes[:3])}{' ...' if len(exes) > 3 else ''} ({len(chosen)} process{'es' if len(chosen) != 1 else ''})"
    return KillPlan(label, sorted(chosen), exes)


def alive(pids: list[int]) -> list[int]:
    out = []
    for pid in pids:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass            # it runs, as another user
        except OSError:
            continue
        out.append(pid)
    return out


def force_end(plan: KillPlan) -> tuple[list[int], list[int]]:
    """SIGKILL every planned process that still runs the planned program.  Returns (ended, denied)."""
    try:
        now = {p.pid: p.exe for p in list_processes()}
    except Exception:  # noqa: BLE001 - can't check: end nothing rather than a stranger reusing a pid
        log.warning("force_end: ps failed", exc_info=True)
        return [], []
    planned = set(plan.exes)
    ended, denied = [], []
    for pid in plan.pids:
        if now.get(pid) not in planned:
            continue            # gone (or the pid now belongs to another program)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except PermissionError:
            denied.append(pid)
            continue
        ended.append(pid)
    end = time.monotonic() + 2.0
    while ended and alive(ended) and time.monotonic() < end:
        time.sleep(0.05)
    return ended, denied


class MacProcesses:
    def process_exe(self, pid: int) -> str:
        return process_exe(pid)

    def plan_force_end(self, target: str, own_pid: int) -> KillPlan:
        return plan_force_end(target, own_pid)

    def force_end(self, plan: KillPlan) -> tuple[list[int], list[int]]:
        return force_end(plan)

    def force_end_elevated(self, pids: list[int]) -> tuple[bool, str]:
        return (False, NO_ADMIN)

    def alive(self, pids: list[int]) -> list[int]:
        return alive(pids)

    def is_elevated(self, pid: int) -> bool:
        return False

    def self_elevated(self) -> bool:
        return os.geteuid() == 0

    def uac_prompt_open(self) -> bool:
        return False
