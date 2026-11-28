"""NotificationPort on macOS: macOS keeps other apps' notifications to itself (reading them would need Full
Disk Access), so the feed is never available and its readers raise OSError with a friendly reason."""
from __future__ import annotations

from ...domain import Notification

UNREADABLE = "macOS doesn't let apps read other apps' notifications"


class MacFeed:
    def available(self) -> bool:
        return False

    def last_id(self) -> int:
        raise OSError(UNREADABLE)

    def since(self, last_id: int) -> list[Notification]:
        raise OSError(UNREADABLE)

    def badges(self) -> dict[str, str]:
        raise OSError(UNREADABLE)


class MacNotifications:
    def open_feed(self) -> MacFeed:
        return MacFeed()

    def remember_own(self, *texts: str) -> None:
        pass
