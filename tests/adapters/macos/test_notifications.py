"""pixelcat.adapters.macos.notifications: macOS keeps other apps' notifications private - said plainly."""
from __future__ import annotations

import pytest

from pixelcat.adapters.macos import notifications


def test_the_feed_is_never_available_and_says_why():
    feed = notifications.MacNotifications().open_feed()
    assert feed.available() is False
    for read in (feed.last_id, lambda: feed.since(0), feed.badges):
        with pytest.raises(OSError, match="^macOS doesn't let apps read other apps' notifications$"):
            read()


def test_remembering_own_popups_is_harmless():
    assert notifications.MacNotifications().remember_own("Pixel Cat", "Timer done") is None
