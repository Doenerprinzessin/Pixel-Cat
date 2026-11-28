"""SecretStore on macOS: a random per-user data key in the Keychain (service ``PIXELCAT_KEYCHAIN_SERVICE`` or
"PixelCat" + instance, account "data-key-v1"), data sealed with it (``savecrypt.seal_keyed``):
``PXK1 | key id (8) | nonce (16) | ciphertext | tag (32)``.

* The key is 32 random bytes in one generic-password item of the login keychain, made on the first
  :meth:`MacSecretStore.protect` and kept in memory after the first read.  (Not the data-protection keychain:
  an unbundled Python lacks the entitlement for it, ``errSecMissingEntitlement``.)
* The key id (``SHA256(key)[:8]``) tells data made with another key (another Mac user's, or a key that was
  deleted and made anew) from changed data; both are ``Unreadable``.
* Any failure to read or make the key item (a locked keychain, denied access, any other Keychain error, an
  item there that isn't a Pixel Cat key) raises ``SecretStoreUnavailable``: the data may be fine, so keep it and
  don't overwrite it.  ``Unreadable`` is only for the data itself (changed, another key) and for a missing key
  item when reading.  Nothing is ever stored unprotected.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from typing import Any

from ... import savecrypt
from ...domain import SecretStoreUnavailable, Unreadable
from . import _objc

log = logging.getLogger(__name__)

ACCOUNT = "data-key-v1"
LABEL = "Pixel Cat data key"
MAGIC = b"PXK1"
KEY_LEN = 32
ID_LEN = 8
_MIN_BLOB = len(MAGIC) + ID_LEN + 16 + 32

# OSStatus codes (Security/SecBase.h)
OK = 0
NOT_FOUND = -25300                        # errSecItemNotFound
DUPLICATE = -25299                        # errSecDuplicateItem
# why the store can't be used right now (any other status says its OSStatus); the data may be fine
UNAVAILABLE = {
    -25308: "user interaction is not allowed (the keychain is locked)",   # errSecInteractionNotAllowed
    -25315: "user interaction is required but not allowed",               # errSecInteractionRequired
    -128: "access was denied",                                             # errSecUserCanceled
    -25293: "the keychain password was not accepted",                     # errSecAuthFailed
    -25291: "no keychain is available",                                    # errSecNotAvailable
    -25294: "the keychain could not be found",                             # errSecNoSuchKeychain
    -25307: "there is no default keychain",                                # errSecNoDefaultKeychain
}


def service() -> str:
    """The Keychain item's service name (tests use their own, never the real one)."""
    return os.environ.get("PIXELCAT_KEYCHAIN_SERVICE") or "PixelCat" + os.environ.get("PIXELCAT_INSTANCE", "")


def key_id(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()[:ID_LEN]


def _keys(key: bytes) -> tuple[bytes, bytes]:
    """The encryption and signing keys made from the data key."""
    return (hmac.new(key, b"pixelcat enc", hashlib.sha256).digest(),
            hmac.new(key, b"pixelcat mac", hashlib.sha256).digest())


def _error(status: int, doing: str) -> SecretStoreUnavailable:
    why = UNAVAILABLE.get(status, f"OSStatus {status}")
    return SecretStoreUnavailable(status, f"the Keychain can't be used right now ({doing}): {why}")


def _sec() -> Any:
    """pyobjc's Security module (or a test's fake)."""
    try:
        sec = _objc.SEC
        sec.kSecClass                     # imports it now: without pyobjc the store can't be used
    except ImportError as e:
        raise SecretStoreUnavailable(f"the Keychain can't be used: pyobjc's Security module is missing ({e})") from e
    return sec


def _item(sec: Any, svc: str) -> dict[Any, Any]:
    return {sec.kSecClass: sec.kSecClassGenericPassword, sec.kSecAttrService: svc, sec.kSecAttrAccount: ACCOUNT}


def _read(svc: str) -> bytes | None:
    """The stored key, None if there is none."""
    sec = _sec()
    query = _item(sec, svc)
    query[sec.kSecReturnData] = True
    query[sec.kSecMatchLimit] = sec.kSecMatchLimitOne
    status, data = sec.SecItemCopyMatching(query, None)
    if status == NOT_FOUND:
        return None
    if status != OK:
        raise _error(status, "reading the data key")
    return bytes(data) if data is not None else b""


def _add(svc: str, key: bytes) -> int:
    sec = _sec()
    attrs = _item(sec, svc)
    attrs[sec.kSecAttrLabel] = LABEL
    attrs[sec.kSecValueData] = key
    status, _ = sec.SecItemAdd(attrs, None)
    return int(status)


class MacSecretStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, bytes] = {}            # service -> data key

    def _key(self, create: bool) -> bytes:
        svc = service()
        with self._lock:
            key = self._cache.get(svc)
            if key is not None:
                return key
            key = _read(svc)
            if key is None and create:
                new = os.urandom(KEY_LEN)
                status = _add(svc, new)
                if status == OK:
                    key = new
                    log.info("made a new data key in the Keychain (service %s)", svc)
                elif status == DUPLICATE:             # another cat made it just now
                    key = _read(svc)
                    if key is None:
                        raise SecretStoreUnavailable(DUPLICATE, "the Keychain says the data key exists but "
                                                                "can't find it")
                else:
                    raise _error(status, "storing the data key")
            if key is None:
                raise Unreadable("there is no Pixel Cat key in the Keychain: it was deleted, or this was made "
                                 "for another Mac user")
            if len(key) != KEY_LEN:                  # never overwritten: someone else's item, or damaged
                raise SecretStoreUnavailable(f"the Keychain item {svc!r} / {ACCOUNT!r} isn't a Pixel Cat data key")
            self._cache[svc] = key
            return key

    def protect(self, data: bytes) -> bytes:
        key = self._key(create=True)
        enc, mac = _keys(key)
        header = MAGIC + key_id(key)
        return header + savecrypt.seal_keyed(enc, mac, data, header)

    def unprotect(self, blob: bytes) -> bytes:
        if len(blob) < _MIN_BLOB or not blob.startswith(MAGIC):
            raise Unreadable("not protected by Pixel Cat on this Mac")
        header = blob[:len(MAGIC) + ID_LEN]
        key = self._key(create=False)
        if header[len(MAGIC):] != key_id(key):
            raise Unreadable("made with another key: for another Mac user, or before the key was made anew")
        enc, mac = _keys(key)
        return savecrypt.unseal_keyed(enc, mac, blob[len(header):], header)
