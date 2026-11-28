"""Permissions the user grants the cat in the system's settings (no platform calls here)."""
from __future__ import annotations

from typing import Any


class PermissionNeeded(OSError):
    """The system hasn't allowed the cat to do this yet; the message tells the user where to allow it.

    An OSError, so the tools' existing error handling shows the message without a traceback.
    ``permission`` names what is missing (e.g. "accessibility", "screen_recording").
    """

    def __init__(self, permission: str, message: str) -> None:
        super().__init__(message)
        self.permission = permission

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.permission, str(self)))
