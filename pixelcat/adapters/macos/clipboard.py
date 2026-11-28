"""ClipboardPort on macOS: NSPasteboard.generalPasteboard(); private writes carry the
``org.nspasteboard.ConcealedType`` / ``TransientType`` markers clipboard managers honour.

Skeleton: nothing can be read or written yet.
"""
from __future__ import annotations

from ...domain import ClipboardKind
from ...ports import ClipboardBackup


class MacClipboard:
    def backup(self) -> ClipboardBackup | None:
        return None

    def write(self, backup: ClipboardBackup, private: bool = True) -> bool:
        return False

    def set_private_text(self, text: str) -> bool:
        return False

    def clear(self) -> None:
        pass

    def sequence(self) -> int:
        return 0

    def has(self, kind: ClipboardKind) -> bool:
        return False

    def text(self) -> str | None:
        return None
