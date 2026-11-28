"""The Keychain secret store: a data key in a generic-password item, data sealed with it (fake Security)."""
from __future__ import annotations

import os
import threading
import uuid
from typing import Any

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, keychain
from pixelcat.domain import SecretStoreUnavailable, Unreadable

ITEM_KEYS = ("class", "service", "account")


class FakeKeychain:
    """A dict-backed Security module: generic-password items by (service, account)."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.read_status: int | None = None       # answer every read with this OSStatus
        self.add_status: int | None = None
        self.before_add: Any = None                # runs first in SecItemAdd (another cat adding its key)
        self.module = FakeModule(
            kSecClass="class", kSecClassGenericPassword="genp", kSecAttrService="service",
            kSecAttrAccount="account", kSecAttrLabel="label", kSecValueData="data", kSecReturnData="return-data",
            kSecMatchLimit="limit", kSecMatchLimitOne="one",
            SecItemCopyMatching=self.copy_matching, SecItemAdd=self.add)

    def copy_matching(self, query: dict[str, Any], result: None) -> tuple[int, Any]:
        assert query["class"] == "genp" and query["return-data"] is True and query["limit"] == "one"
        if self.read_status is not None:
            return self.read_status, None
        item = self.items.get((query["service"], query["account"]))
        return (0, item["data"]) if item else (keychain.NOT_FOUND, None)

    def add(self, attrs: dict[str, Any], result: None) -> tuple[int, None]:
        if self.before_add:
            self.before_add()
        if self.add_status is not None:
            return self.add_status, None
        key = (attrs["service"], attrs["account"])
        if key in self.items:
            return keychain.DUPLICATE, None
        self.items[key] = dict(attrs)
        return 0, None

    def key(self, service: str = "PixelCat-tests-never") -> bytes:
        return self.items[(service, keychain.ACCOUNT)]["data"]


@pytest.fixture
def kc(monkeypatch: pytest.MonkeyPatch) -> FakeKeychain:
    fake = FakeKeychain()
    monkeypatch.setattr(_objc, "SEC", fake.module)
    return fake


def test_round_trip_makes_one_key_item_once(kc: FakeKeychain) -> None:
    store = keychain.MacSecretStore()
    blob = store.protect(b"the cat pass")
    assert blob.startswith(b"PXK1" + keychain.key_id(kc.key()))
    assert b"the cat pass" not in blob
    assert store.unprotect(blob) == b"the cat pass"
    item = kc.items[("PixelCat-tests-never", "data-key-v1")]
    assert item["label"] == "Pixel Cat data key" and item["class"] == "genp" and len(item["data"]) == 32
    store.protect(b"again")
    assert len(kc.module.called("SecItemAdd")) == 1
    assert len(kc.module.called("SecItemCopyMatching")) == 1           # then the key comes from memory
    assert keychain.MacSecretStore().unprotect(blob) == b"the cat pass"   # a new run reads the same key


def test_big_data_is_fine(kc: FakeKeychain) -> None:
    data = os.urandom(9_000_000)                                       # above the 8 MB export cap
    store = keychain.MacSecretStore()
    assert store.unprotect(store.protect(data)) == data


@pytest.mark.parametrize("change", [
    lambda b: b[:-1] + bytes([b[-1] ^ 1]),                             # the tag
    lambda b: b[:40] + bytes([b[40] ^ 1]) + b[41:],                    # the ciphertext
    lambda b: b"PXK2" + b[4:],                                         # the header
    lambda b: b[:30],                                                  # cut short
    lambda b: b"",
])
def test_a_changed_blob_is_unreadable(kc: FakeKeychain, change: Any) -> None:
    store = keychain.MacSecretStore()
    blob = store.protect(b"x" * 100)
    with pytest.raises(Unreadable):
        store.unprotect(change(blob))


def test_another_users_key_is_unreadable(kc: FakeKeychain, monkeypatch: pytest.MonkeyPatch) -> None:
    blob = keychain.MacSecretStore().protect(b"mine")
    kc.items.clear()
    other = keychain.MacSecretStore().protect(b"theirs")               # a new key: another user / key made anew
    assert keychain.MacSecretStore().unprotect(other) == b"theirs"
    with pytest.raises(Unreadable, match="another key"):
        keychain.MacSecretStore().unprotect(blob)


def test_a_missing_key_is_unreadable_and_not_made(kc: FakeKeychain) -> None:
    blob = keychain.MacSecretStore().protect(b"mine")
    kc.items.clear()
    with pytest.raises(Unreadable):
        keychain.MacSecretStore().unprotect(blob)
    assert kc.items == {} and len(kc.module.called("SecItemAdd")) == 1


def test_a_windows_save_never_asks_the_keychain(kc: FakeKeychain) -> None:
    with pytest.raises(Unreadable):
        keychain.MacSecretStore().unprotect(b"\x01\x00\x00\x00" + os.urandom(200))
    assert kc.module.calls == []


@pytest.mark.parametrize("status", [-25308, -128, -25293, -25315, -25291])
def test_a_locked_keychain_is_unavailable_not_unreadable(kc: FakeKeychain, status: int) -> None:
    blob = keychain.MacSecretStore().protect(b"mine")
    kc.read_status = status
    store = keychain.MacSecretStore()
    with pytest.raises(SecretStoreUnavailable) as e:
        store.unprotect(blob)
    assert e.value.errno == status
    with pytest.raises(SecretStoreUnavailable):
        store.protect(b"new")
    kc.read_status = None
    assert store.unprotect(blob) == b"mine"                            # unlocked: it works (nothing cached)


def test_a_denied_add_is_unavailable(kc: FakeKeychain) -> None:
    kc.add_status = -128
    with pytest.raises(SecretStoreUnavailable):
        keychain.MacSecretStore().protect(b"x")
    assert kc.items == {}


def test_other_keychain_errors_are_oserrors(kc: FakeKeychain) -> None:
    kc.add_status = -34018                                             # errSecMissingEntitlement
    with pytest.raises(OSError) as e:
        keychain.MacSecretStore().protect(b"x")
    assert type(e.value) is OSError and e.value.errno == -34018
    kc.add_status = None
    blob = keychain.MacSecretStore().protect(b"x")
    kc.read_status = -26276                                            # errSecInternalComponent
    with pytest.raises(OSError) as e:
        keychain.MacSecretStore().unprotect(blob)
    assert type(e.value) is OSError


def test_another_cat_adding_the_key_first_is_fine(kc: FakeKeychain) -> None:
    theirs = keychain.MacSecretStore()
    kc.before_add = lambda: (setattr(kc, "before_add", None), theirs.protect(b"theirs"))
    blob = keychain.MacSecretStore().protect(b"mine")                  # its add says duplicate: read theirs
    assert theirs.unprotect(blob) == b"mine"
    assert len(kc.items) == 1


def test_a_strange_item_is_never_overwritten(kc: FakeKeychain) -> None:
    kc.items[("PixelCat-tests-never", "data-key-v1")] = {"data": b"short"}
    with pytest.raises(OSError):
        keychain.MacSecretStore().protect(b"x")
    with pytest.raises(Unreadable):
        keychain.MacSecretStore().unprotect(b"PXK1" + keychain.key_id(b"short") + os.urandom(60))
    assert kc.items[("PixelCat-tests-never", "data-key-v1")] == {"data": b"short"}


def test_the_service_follows_the_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIXELCAT_KEYCHAIN_SERVICE")
    monkeypatch.setenv("PIXELCAT_INSTANCE", "-dev")
    assert keychain.service() == "PixelCat-dev"
    monkeypatch.delenv("PIXELCAT_INSTANCE")
    assert keychain.service() == "PixelCat"


def test_threads_share_one_key(kc: FakeKeychain) -> None:
    store = keychain.MacSecretStore()
    blobs: list[bytes] = []
    threads = [threading.Thread(target=lambda: blobs.append(store.protect(b"t"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(kc.module.called("SecItemAdd")) == 1
    assert {b[:12] for b in blobs} == {b"PXK1" + keychain.key_id(kc.key())}


# ------------------------------------------------------------------ the real Keychain
@pytest.mark.macos
def test_real_keychain_without_the_item_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read only: a service that doesn't exist (no prompt, nothing written)."""
    monkeypatch.setenv("PIXELCAT_KEYCHAIN_SERVICE", f"PixelCat-tests-absent-{uuid.uuid4().hex}")
    with pytest.raises(Unreadable, match="no Pixel Cat key"):
        keychain.MacSecretStore().unprotect(b"PXK1" + os.urandom(80))


@pytest.mark.macos_live
def test_real_keychain_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    import Security as S

    svc = f"PixelCat-tests-{uuid.uuid4().hex}"
    monkeypatch.setenv("PIXELCAT_KEYCHAIN_SERVICE", svc)
    try:
        blob = keychain.MacSecretStore().protect(b"live cat")
        assert keychain.MacSecretStore().unprotect(blob) == b"live cat"
    finally:
        status = S.SecItemDelete({S.kSecClass: S.kSecClassGenericPassword, S.kSecAttrService: svc,
                                  S.kSecAttrAccount: keychain.ACCOUNT})
        assert status in (0, keychain.NOT_FOUND)
