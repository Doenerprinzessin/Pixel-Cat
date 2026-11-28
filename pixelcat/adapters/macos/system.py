"""SystemPort on macOS: this copy of the cat (an flock'ed lock file), message boxes (osascript), system
information (pmset, sysctl, vm_stat), locking the screen, media keys and the volume (osascript).

Skeleton: answers "nothing" (and lets a second cat run).
"""
from __future__ import annotations

import logging
import os

from . import hardware
from ...domain import Hardware

log = logging.getLogger(__name__)


class MacSystem:
    @property
    def instance(self) -> str:
        return os.environ.get("PIXELCAT_INSTANCE", "")

    def acquire_single_instance(self) -> bool:
        return True

    def create_quit_event(self) -> int:
        return 0        # no installer on macOS: nothing asks the cat to quit

    def quit_requested(self, handle: int) -> bool:
        return False

    def message_box(self, text: str, title: str = "Pixel Cat") -> None:
        log.warning("%s: %s", title, text)

    def system_info(self) -> str:
        return "System information is not available on this Mac yet."

    def lock_screen(self) -> bool:
        return False

    def media_key(self, action: str) -> None:
        pass

    def get_volume(self) -> tuple[int, bool]:
        return (0, False)

    def set_volume(self, percent: int) -> int:
        return 0

    def set_mute(self, muted: bool) -> None:
        pass

    def hardware(self, fresh: bool = False) -> Hardware:
        return hardware.detect(fresh)
