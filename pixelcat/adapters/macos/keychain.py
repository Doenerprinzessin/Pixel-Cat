"""SecretStore on macOS: a random per-user data key in the Keychain (service ``PIXELCAT_KEYCHAIN_SERVICE`` or
"PixelCat" + instance, account "data-key-v1"), data sealed with it (``savecrypt.seal_keyed``):
``PXK1 | key id (8) | nonce (16) | ciphertext | tag (32)``.

Skeleton: nothing is stored yet (``protect`` refuses, so saves stay signed instead of protected).
"""
from __future__ import annotations

import os

from ...domain import Unreadable

ACCOUNT = "data-key-v1"
NOT_YET = "the Keychain store is not implemented yet"


def service() -> str:
    """The Keychain item's service name (tests use their own, never the real one)."""
    return os.environ.get("PIXELCAT_KEYCHAIN_SERVICE") or "PixelCat" + os.environ.get("PIXELCAT_INSTANCE", "")


class MacSecretStore:
    def protect(self, data: bytes) -> bytes:
        raise OSError(NOT_YET)

    def unprotect(self, blob: bytes) -> bytes:
        raise Unreadable(f"protected data can't be opened: {NOT_YET}")
