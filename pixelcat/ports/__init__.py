"""The ports: what the cat needs from the platform it runs on, as plain interfaces.

The core gets a :class:`Platform` (built by ``pixelcat.bootstrap``) and only talks to these ports, never to
an adapter.  Ports use only the standard library and ``pixelcat.domain`` (Qt types only for type checking).
"""
from __future__ import annotations

from dataclasses import dataclass

from .desktop import (AccessibilityPort, AppsPort, CapturePort, ClipboardBackup, ClipboardPort, DisplayPort,
                      EscapeWatcher, InputPort, Keys, NotificationFeed, NotificationPort, ProcessPort, Rect,
                      SystemPort, WindowPort)
from .storage import AutostartPort, InstallerPort, KnownFolders, SaveMarks, SecretStore


@dataclass(frozen=True)
class Platform:
    """Every port, implemented for one platform."""

    name: str                           # "windows", "null" ...
    display: DisplayPort
    windows: WindowPort
    input: InputPort
    clipboard: ClipboardPort
    accessibility: AccessibilityPort
    capture: CapturePort
    system: SystemPort
    apps: AppsPort
    processes: ProcessPort
    notifications: NotificationPort
    secrets: SecretStore
    folders: KnownFolders
    autostart: AutostartPort
    save_marks: SaveMarks
    installer: InstallerPort


__all__ = [
    "AccessibilityPort", "AppsPort", "AutostartPort", "CapturePort", "ClipboardBackup", "ClipboardPort", "DisplayPort",
    "EscapeWatcher", "InputPort", "InstallerPort", "Keys", "KnownFolders", "NotificationFeed", "NotificationPort",
    "Platform", "ProcessPort", "Rect", "SaveMarks", "SecretStore", "SystemPort", "WindowPort",
]
