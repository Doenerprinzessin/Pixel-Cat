"""pixelcat.adapters.macos.clipboard: NSPasteboard backups, private writes with the nspasteboard.org markers,
and pasteboard privacy ("Deny" reads as busy)."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, clipboard


class Item:
    def __init__(self, data: dict[str, bytes] | None = None) -> None:
        self.data: dict[str, bytes] = dict(data or {})

    def init(self) -> Item:
        return self

    def types(self) -> list[str]:
        return list(self.data)

    def dataForType_(self, kind: str) -> bytes | None:
        return self.data.get(kind)

    def setData_forType_(self, data: bytes, kind: str) -> bool:
        self.data[kind] = bytes(data)
        return True

    def setString_forType_(self, s: str, kind: str) -> bool:
        self.data[kind] = s.encode()
        return True


class Pasteboard:
    def __init__(self, behavior: int | None = 2) -> None:
        self.items: list[Item] = []
        self.count = 7
        if behavior is not None:
            self.accessBehavior = lambda: behavior

    def pasteboardItems(self) -> list[Item]:
        return self.items

    def types(self) -> list[str]:
        return [t for it in self.items for t in it.types()]

    def changeCount(self) -> int:
        return self.count

    def clearContents(self) -> int:
        self.items = []
        self.count += 1
        return self.count

    def writeObjects_(self, items: list[Item]) -> bool:
        self.items += items
        return True

    def stringForType_(self, kind: str) -> str | None:
        for it in self.items:
            if kind in it.data:
                return it.data[kind].decode()
        return None


@pytest.fixture
def pb(monkeypatch: pytest.MonkeyPatch) -> Pasteboard:
    board = Pasteboard()
    monkeypatch.setattr(_objc, "AK", FakeModule(NSPasteboard=SimpleNamespace(generalPasteboard=lambda: board),
                                                NSPasteboardItem=SimpleNamespace(alloc=Item)))
    monkeypatch.setattr(_objc, "FN", FakeModule(NSData=SimpleNamespace(
        dataWithBytes_length_=lambda raw, n: bytes(raw[:n]))))
    return board

    class NSPasteboardItem:
        @staticmethod
        def alloc() -> Item:
            return Item()
    monkeypatch.setattr(_objc, "AK", FakeModule(NSPasteboard=NSPasteboard, NSPasteboardItem=NSPasteboardItem))
    monkeypatch.setattr(_objc, "FN", FakeModule(NSData=type("NSData", (), {
        "dataWithBytes_length_": staticmethod(lambda raw, n: bytes(raw[:n]))})))
    return board


def test_backup_and_write_round_trip(pb: Pasteboard) -> None:
    pb.items = [Item({"public.utf8-plain-text": b"hi", "public.html": b"<b>hi</b>"}), Item({"public.png": b"\x89PNG"})]
    cb = clipboard.MacClipboard()
    saved = cb.backup()
    assert saved == [{"public.utf8-plain-text": b"hi", "public.html": b"<b>hi</b>"}, {"public.png": b"\x89PNG"}]
    cb.clear()
    assert pb.items == []
    assert cb.write(saved, private=False)
    assert [it.data for it in pb.items] == saved


def test_private_writes_carry_the_markers(pb: Pasteboard) -> None:
    cb = clipboard.MacClipboard()
    assert cb.set_private_text("secret")
    (item,) = pb.items
    assert item.data == {clipboard.CONCEALED: b"", clipboard.TRANSIENT: b"", "public.utf8-plain-text": b"secret"}
    assert cb.write([{"public.utf8-plain-text": b"x"}])
    assert set(pb.items[0].data) == {"public.utf8-plain-text", clipboard.CONCEALED, clipboard.TRANSIENT}


def test_writing_an_empty_backup_leaves_it_empty(pb: Pasteboard) -> None:
    pb.items = [Item({"public.utf8-plain-text": b"old"})]
    assert clipboard.MacClipboard().write([], private=False)
    assert pb.items == []


def test_sequence_has_and_text(pb: Pasteboard) -> None:
    cb = clipboard.MacClipboard()
    assert cb.sequence() == 7 and cb.text() is None
    pb.items = [Item({"public.utf8-plain-text": b"abc", "public.rtf": b"{\\rtf1}"})]
    assert cb.text() == "abc"
    assert cb.has("rtf") and not cb.has("html") and not cb.has("files")
    pb.items = [Item({"public.file-url": b"file:///x"})]
    assert cb.has("files")
    pb.items = [Item({"com.apple.flat-rtfd": b""}), Item({"public.html": b""})]
    assert cb.has("rtf") and cb.has("html")


def test_denied_reads_are_busy(pb: Pasteboard) -> None:
    pb.accessBehavior = lambda: 3              # NSPasteboardAccessBehaviorAlwaysDeny
    pb.items = [Item({"public.utf8-plain-text": b"abc"})]
    cb = clipboard.MacClipboard()
    assert cb.backup() is None and cb.text() is None
    assert cb.sequence() == 7 and not cb.has("html")     # (these never read the contents)


def test_older_macos_has_no_access_behavior(pb: Pasteboard) -> None:
    del pb.accessBehavior
    pb.items = [Item({"public.utf8-plain-text": b"abc"})]
    assert clipboard.MacClipboard().text() == "abc"


def test_a_huge_clipboard_isnt_backed_up(pb: Pasteboard, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "BACKUP_LIMIT", 10)
    pb.items = [Item({"public.png": b"x" * 11})]
    assert clipboard.MacClipboard().backup() is None


def test_failures_are_answers_not_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    # (the conftest's stub makes every NSPasteboard use fail)
    cb = clipboard.MacClipboard()
    assert cb.backup() is None and cb.text() is None and cb.sequence() == 0 and not cb.has("files")
    assert not cb.write([{"a": b""}]) and not cb.set_private_text("x")
    cb.clear()


@pytest.mark.macos
def test_the_real_pasteboard_reads_without_touching_it() -> None:
    cb = clipboard.MacClipboard()
    before = cb.sequence()
    assert isinstance(before, int) and before >= 0    # (0: never written, e.g. a fresh CI runner)
    assert all(isinstance(cb.has(k), bool) for k in ("files", "rtf", "html"))
    assert cb.sequence() == before


@pytest.mark.macos_live
def test_a_real_round_trip_puts_the_users_clipboard_back() -> None:
    cb = clipboard.MacClipboard()
    saved = cb.backup()
    assert saved is not None
    try:
        token = f"pixelcat-test-{os.getpid()}-{time.time()}"
        assert cb.set_private_text(token)
        assert cb.text() == token
        types = {str(t) for t in _objc.AK.NSPasteboard.generalPasteboard().types()}
        assert {clipboard.CONCEALED, clipboard.TRANSIENT} <= types
    finally:
        assert cb.write(saved, private=False)
    assert cb.backup() == saved


def test_any_kind_name_is_safe(pb: Pasteboard) -> None:
    kind: Any = "image"
    assert not clipboard.MacClipboard().has(kind)
