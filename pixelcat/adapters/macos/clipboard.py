"""ClipboardPort on macOS: ``NSPasteboard.generalPasteboard()``.

* A backup is a list of the pasteboard's items, each ``{type: bytes}`` (every type the item holds), up to
  256 MB; :meth:`MacClipboard.write` puts such a list back as new items.
* Private writes carry the ``org.nspasteboard.ConcealedType`` / ``TransientType`` markers clipboard managers
  honour (nspasteboard.org), so the cat's temporary text stays out of their history.
* Pasteboard privacy (macOS 15.4+): when the user set Pixel Cat's paste access to "Deny"
  (``accessBehavior`` AlwaysDeny), reads answer None - the core takes that for "clipboard busy" and types
  instead or says so.  With "Ask", macOS asks the user when the helper reads it (only in actions the user
  started: the chat's helper pasting or replacing text).  The change count and the types never ask.
"""
from __future__ import annotations

import logging
from typing import Any

from . import _objc
from ...domain import ClipboardKind
from ...ports import ClipboardBackup

log = logging.getLogger(__name__)

TEXT = "public.utf8-plain-text"
CONCEALED = "org.nspasteboard.ConcealedType"
TRANSIENT = "org.nspasteboard.TransientType"
MARKERS = (CONCEALED, TRANSIENT)
KINDS: dict[str, tuple[str, ...]] = {
    "files": ("public.file-url", "NSFilenamesPboardType"),
    "rtf": ("public.rtf", "com.apple.flat-rtfd", "NeXT Rich Text Format v1.0 pasteboard type"),
    "html": ("public.html", "Apple HTML pasteboard type"),
}
ACCESS_ALWAYS_DENY = 3          # NSPasteboardAccessBehaviorAlwaysDeny
BACKUP_LIMIT = 256 * 1024 * 1024


def _pb() -> Any:
    return _objc.AK.NSPasteboard.generalPasteboard()


def _denied(pb: Any) -> bool:
    """True if the user doesn't let the cat read the pasteboard (macOS 15.4+)."""
    behavior = getattr(pb, "accessBehavior", None)
    if behavior is None:
        return False
    try:
        return int(behavior()) == ACCESS_ALWAYS_DENY
    except Exception:  # noqa: BLE001 - older macOS: always allowed
        return False


def _data(raw: bytes) -> Any:
    return _objc.FN.NSData.dataWithBytes_length_(raw, len(raw))


def _item(entries: dict[str, bytes], private: bool) -> Any:
    item = _objc.AK.NSPasteboardItem.alloc().init()
    for kind, raw in entries.items():
        item.setData_forType_(_data(raw), kind)
    if private:
        for marker in MARKERS:
            item.setData_forType_(_data(b""), marker)
    return item


class MacClipboard:
    def backup(self) -> ClipboardBackup | None:
        """Every item with every type it holds; None if it can't be read (denied, too big, failed)."""
        try:
            pb = _pb()
            if _denied(pb):
                return None
            items: list[dict[str, bytes]] = []
            total = 0
            for item in pb.pasteboardItems() or []:
                entries: dict[str, bytes] = {}
                for kind in item.types() or []:
                    data = item.dataForType_(kind)
                    if data is None:
                        continue
                    raw = bytes(data)
                    total += len(raw)
                    if total > BACKUP_LIMIT:
                        return None
                    entries[str(kind)] = raw
                if entries:
                    items.append(entries)
            return items
        except Exception:  # noqa: BLE001 - can't read it safely: don't paste
            log.debug("clipboard backup failed", exc_info=True)
            return None

    def write(self, backup: ClipboardBackup, private: bool = True) -> bool:
        try:
            pb = _pb()
            pb.clearContents()
            items = [_item(entries, private) for entries in backup or [] if entries]
            return bool(pb.writeObjects_(items)) if items else True
        except Exception:  # noqa: BLE001
            log.debug("clipboard write failed", exc_info=True)
            return False

    def set_private_text(self, text: str) -> bool:
        try:
            pb = _pb()
            pb.clearContents()
            item = _item({}, True)
            if not item.setString_forType_(text, TEXT):
                return False
            return bool(pb.writeObjects_([item]))
        except Exception:  # noqa: BLE001
            log.debug("clipboard write failed", exc_info=True)
            return False

    def clear(self) -> None:
        try:
            _pb().clearContents()
        except Exception:  # noqa: BLE001
            log.debug("clipboard clear failed", exc_info=True)

    def sequence(self) -> int:
        try:
            return int(_pb().changeCount())
        except Exception:  # noqa: BLE001
            return 0

    def has(self, kind: ClipboardKind) -> bool:
        try:
            types = {str(t) for t in (_pb().types() or [])}
        except Exception:  # noqa: BLE001
            return False
        return any(t in types for t in KINDS.get(kind, ()))

    def text(self) -> str | None:
        try:
            pb = _pb()
            if _denied(pb):
                return None
            s = pb.stringForType_(TEXT)
            return None if s is None else str(s)
        except Exception:  # noqa: BLE001
            log.debug("clipboard read failed", exc_info=True)
            return None
