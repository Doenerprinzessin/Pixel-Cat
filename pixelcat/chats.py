"""Saved chats (the chat's clock button, /history).

Every conversation is kept in the PixelCat data folder's ``chats`` - the newest ``KEEP``, older
ones are deleted - encrypted for this user account by the platform's secret store (like the
save), so another account or PC can't read them.  A chat holds what the chat window showed and what the AI remembers (its
history - without screenshots, and without a model's own replay blocks, which only count
within a running task), plus the chat's own model and effort.  An index (encrypted too)
keeps titles, dates and some text to search, so the list opens without opening every chat.
If the secret store can't be used right now (SecretStoreUnavailable), the store is ``locked``:
nothing is written for the rest of the run, so the saved chats and their index stay as they are.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import paths, savecrypt
from .domain.secrets import SecretStoreUnavailable

if TYPE_CHECKING:
    from .ports import SecretStore

log = logging.getLogger(__name__)

KEEP = 100                     # chats kept (the oldest go first)
MAX_TEXT = 30_000              # characters of one message that are kept
FIND_CHARS = 8_000             # characters of a chat that the search looks at
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


@dataclass
class ChatInfo:
    id: str
    title: str
    created: float
    updated: float
    count: int                 # messages the user wrote
    model: str                 # the model it used last (a tag)
    find: str = ""             # lower-case text to search


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _is_request(m: dict[str, Any]) -> bool:
    return m.get("role") == "user" and not m.get("_tool") and not m.get("_wake")


def title_of(history: list[dict[str, Any]]) -> str:
    """The first thing the user asked, shortened."""
    first = next((str(m.get("content") or "") for m in history if _is_request(m)), "")
    line = " ".join(first.split())
    return (line[:57].rstrip() + "…") if len(line) > 60 else (line or "(no text)")


def clean_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What is kept of the AI's history: no screenshots, no replay blocks, no huge texts."""
    out = []
    for m in history:
        c = {k: v for k, v in m.items() if k not in ("images", "raw")}
        text = c.get("content")
        if isinstance(text, str):
            if m.get("images"):
                text += " (a screenshot was here - not kept)"
            if len(text) > MAX_TEXT:
                text = text[:MAX_TEXT] + " ...(shortened)"
            c["content"] = text
        out.append(c)
    return out


class ChatStore:
    def __init__(self, folder: Path | None = None, *, secrets: SecretStore) -> None:
        self._folder = folder
        self.secrets = secrets
        self.locked = False             # the secret store couldn't open a chat this run: nothing is written

    # ------------------------------------------------------------------ files
    def folder(self) -> Path:
        d = self._folder or paths.data_dir() / "chats"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _file(self, cid: str) -> Path:
        if not _ID.match(cid):
            raise ValueError(f"not a chat id: {cid!r}")
        return self.folder() / f"{cid}.chat"

    def _read(self, path: Path) -> Any:
        try:
            plain = self.secrets.unprotect(path.read_bytes())
        except SecretStoreUnavailable:
            self.locked = True
            raise
        return json.loads(plain.decode("utf-8"))

    def _write(self, path: Path, data: Any) -> None:
        if self.locked:
            raise SecretStoreUnavailable("the saved chats couldn't be opened this run - not writing them")
        try:
            blob = self.secrets.protect(json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                                                   default=str).encode("utf-8"))
        except SecretStoreUnavailable:
            self.locked = True
            raise
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(blob)
        for attempt in range(20):        # (a virus scanner may hold the file for a moment)
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

    # ------------------------------------------------------------------ the list
    def index(self) -> list[ChatInfo]:
        """All saved chats, the most recent first (read fresh: another cat may have saved one)."""
        path = self.folder() / "index.dat"
        items: list[ChatInfo] | None = None
        if path.is_file():
            try:
                items = [ChatInfo(**d) for d in self._read(path)]
            except SecretStoreUnavailable as exc:
                log.warning("the saved chats can't be opened right now: %s", exc)
                return []
            except Exception as exc:  # noqa: BLE001 - damaged: built again from the chats
                log.warning("chat index unreadable (%s) - rebuilding it", exc)
        if items is None:
            items = self._rebuild()
        have = {p.stem for p in self.folder().glob("*.chat")}
        items = [i for i in items if i.id in have]
        return sorted(items, key=lambda i: i.updated, reverse=True)

    def _rebuild(self) -> list[ChatInfo]:
        out = []
        for p in self.folder().glob("*.chat"):
            try:
                out.append(self._info(self._read(p)))
            except Exception as exc:  # noqa: BLE001 - not ours / damaged: left alone
                log.warning("chat %s unreadable: %s", p.name, exc)
        if out:
            try:
                self._write_index(out)
            except OSError as exc:       # (shown anyway; written next time)
                log.warning("couldn't write the chat index: %s", exc)
        return out

    def _write_index(self, items: list[ChatInfo]) -> None:
        self._write(self.folder() / "index.dat", [asdict(i) for i in items])

    @staticmethod
    def _info(chat: dict[str, Any]) -> ChatInfo:
        msgs = chat.get("msgs") or []
        find = " ".join(str(t) for r, t in msgs if r in ("user", "cat")).lower()[:FIND_CHARS]
        return ChatInfo(str(chat["id"]), str(chat.get("title") or "(no text)"), float(chat.get("created") or 0),
                        float(chat.get("updated") or 0), sum(1 for m in chat.get("history") or [] if _is_request(m)),
                        str(chat.get("model") or ""), find)

    # ------------------------------------------------------------------ one chat
    def save(self, chat: dict[str, Any]) -> None:
        """Keep a chat (``id``, ``created``, ``msgs`` [[role, text]...], ``history``, ``model``,
        ``chat_model``, ``chat_effort``); the oldest beyond ``KEEP`` are deleted."""
        chat = dict(chat, v=1, updated=time.time(), title=title_of(chat.get("history") or []))
        chat["history"] = clean_history(chat.get("history") or [])
        chat["msgs"] = [[r, t[:MAX_TEXT]] for r, t in chat.get("msgs") or [] if t]
        self._write(self._file(chat["id"]), chat)
        items = [i for i in self.index() if i.id != chat["id"]]
        items.insert(0, self._info(chat))
        for old in items[KEEP:]:
            self._remove(old.id)
        self._write_index(items[:KEEP])

    def load(self, cid: str) -> dict[str, Any] | None:
        try:
            chat = self._read(self._file(cid))
        except (OSError, ValueError, savecrypt.Unreadable) as exc:
            log.warning("chat %s can't be opened: %s", cid, exc)
            return None
        return chat if isinstance(chat, dict) and chat.get("id") == cid else None

    def _remove(self, cid: str) -> None:
        try:
            self._file(cid).unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            log.warning("couldn't delete chat %s: %s", cid, exc)

    def delete(self, cid: str) -> None:
        items = self.index()
        if self.locked:                  # (the index can't be written: the chat stays in it and on disk)
            raise SecretStoreUnavailable("the saved chats couldn't be opened this run - not deleting one")
        self._remove(cid)
        self._write_index([i for i in items if i.id != cid])

    def delete_all(self) -> int:
        items = self.index()
        for i in items:
            self._remove(i.id)
        self._write_index([])
        return len(items)
