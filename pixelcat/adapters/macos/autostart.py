"""AutostartPort on macOS: a LaunchAgent (``~/Library/LaunchAgents/com.pixelcat.PixelCat<instance>.plist``,
RunAtLoad) that starts the cat at the next login; no launchctl.

Skeleton: the cat doesn't start with the Mac yet.
"""
from __future__ import annotations


class MacAutostart:
    def enabled(self) -> bool:
        return False

    def set(self, enabled: bool) -> bool:
        return False
