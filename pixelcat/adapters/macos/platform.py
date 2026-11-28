"""The macOS adapter put together: :func:`build` makes the :class:`~pixelcat.ports.Platform`."""
from __future__ import annotations

from .apps import MacApps
from .autostart import MacAutostart
from .ax import MacAccessibility
from .capture import MacCapture
from .clipboard import MacClipboard
from .display import MacDisplay
from .inputs import MacInput
from .keychain import MacSecretStore
from .notifications import MacNotifications
from .processes import MacProcesses
from .profile import MAC
from .storage import MacInstaller, MacKnownFolders, MacSaveMarks
from .system import MacSystem
from .windows import MacWindows
from ...ports import Platform


def build() -> Platform:
    return Platform(
        name="macos", display=MacDisplay(), windows=MacWindows(), input=MacInput(), clipboard=MacClipboard(),
        accessibility=MacAccessibility(), capture=MacCapture(), system=MacSystem(), apps=MacApps(),
        processes=MacProcesses(), notifications=MacNotifications(), secrets=MacSecretStore(),
        folders=MacKnownFolders(), autostart=MacAutostart(), save_marks=MacSaveMarks(), installer=MacInstaller(),
        profile=MAC,
    )
