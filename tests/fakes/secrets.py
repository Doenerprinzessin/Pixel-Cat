"""A fake Windows data protection (DPAPI) for the save, chats and keys (``from fakes.secrets import FakeDpapi``)."""
from __future__ import annotations

import hashlib
import hmac

from pixelcat import savecrypt


class FakeDpapi:
    """Stands in for Windows data protection: readable only by the same ``account``,
    a changed byte is refused, and the result isn't plain text."""

    def __init__(self) -> None:
        self.account = b"this account"
        self.protect_error: BaseException | None = None
        self.unprotect_error: BaseException | None = None
        self.protected = 0

    def protect(self, data: bytes) -> bytes:
        if self.protect_error is not None:
            raise self.protect_error
        self.protected += 1
        mac = hmac.new(self.account, data, hashlib.sha256).digest()
        return b"FAKEDPAPI" + mac + bytes(b ^ 0xA5 for b in data)

    def unprotect(self, blob: bytes) -> bytes:
        if self.unprotect_error is not None:
            raise self.unprotect_error
        if not blob.startswith(b"FAKEDPAPI") or len(blob) < 9 + 32:
            raise savecrypt.Unreadable("not protected")
        mac, body = blob[9:41], blob[41:]
        data = bytes(b ^ 0xA5 for b in body)
        if not hmac.compare_digest(mac, hmac.new(self.account, data, hashlib.sha256).digest()):
            raise savecrypt.Unreadable("Windows refused to open it (error 13)")
        return data
