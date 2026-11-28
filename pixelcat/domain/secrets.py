"""Secrets kept by the platform (no platform calls here)."""
from __future__ import annotations


class Unreadable(Exception):
    """Changed, damaged, or made for another user account."""


class SecretStoreUnavailable(OSError):
    """The platform's secret store can't be used right now (locked, or access denied for this run).

    Unlike :class:`Unreadable` the data itself is fine: callers keep it untouched and don't overwrite it.
    """
