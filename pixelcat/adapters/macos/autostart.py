"""AutostartPort on macOS: a LaunchAgent (``~/Library/LaunchAgents/com.pixelcat.PixelCat<instance>.plist``,
RunAtLoad) that starts the cat at the next login.

No ``launchctl``: bootstrapping it now would start a second cat, booting it out would quit a cat launchd
started.  macOS 13+ shows a "Background item added" notice.  A signed app (PR 6) should use
``SMAppService.mainApp`` instead.
"""
from __future__ import annotations

import logging
import os
import plistlib
import sys
from pathlib import Path
from typing import Any

from .storage import _write_atomic

log = logging.getLogger(__name__)


def label() -> str:
    return "com.pixelcat.PixelCat" + os.environ.get("PIXELCAT_INSTANCE", "")


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label()}.plist"


def launch_command() -> list[str]:
    """How launchd starts this copy of the cat (like the Windows ``system.launch_command``)."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]      # <bundle>/Contents/MacOS/PixelCat
    main = Path(__file__).resolve().parents[3] / "main.py"
    return [sys.executable, str(main)]                    # (not resolved: a venv's python is a symlink)


def agent() -> dict[str, Any]:
    """The LaunchAgent's contents."""
    out: dict[str, Any] = {
        "Label": label(),
        "ProgramArguments": launch_command(),
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
    }
    instance = os.environ.get("PIXELCAT_INSTANCE", "")
    if instance:
        out["EnvironmentVariables"] = {"PIXELCAT_INSTANCE": instance}
    return out


class MacAutostart:
    def enabled(self) -> bool:
        try:
            with plist_path().open("rb") as f:
                data = plistlib.load(f)
        except FileNotFoundError:
            return False
        except (OSError, ValueError, plistlib.InvalidFileException):
            log.warning("could not read the LaunchAgent", exc_info=True)
            return False
        return isinstance(data, dict) and data.get("ProgramArguments") == launch_command()

    def set(self, enabled: bool) -> bool:
        path = plist_path()
        try:
            if enabled:
                _write_atomic(path, plistlib.dumps(agent()))
            else:
                path.unlink(missing_ok=True)
        except OSError:
            log.exception("could not change autostart")
            return False
        log.info("autostart %s", "enabled" if enabled else "disabled")
        return True
