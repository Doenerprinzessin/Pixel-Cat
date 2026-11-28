"""chats: saved chats (encrypted per user account), their index, 100 at most."""
from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

from pixelcat import chats, paths
from pixelcat.chats import ChatStore
from pixelcat.domain.secrets import SecretStoreUnavailable


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """time.time() goes up by one second per call."""
    ticks = itertools.count(1_000_000)
    monkeypatch.setattr(chats.time, "time", lambda: float(next(ticks)))


@pytest.fixture
def store(dpapi, clock, tmp_path: Path) -> ChatStore:  # type: ignore[no-untyped-def]
    return ChatStore(tmp_path / "chats", secrets=dpapi)


def cid(n: int) -> str:
    return f"20260101-{n:06d}-{n % 65536:04x}"


def chat(n: int, text: str = "hello cat", **extra) -> dict:  # type: ignore[no-untyped-def]
    return {"id": cid(n), "created": 100.0 + n, "msgs": [["user", text], ["cat", "Meow!"]],
            "history": [{"role": "user", "content": text}, {"role": "assistant", "content": "Meow!"}],
            "model": "qwen3.5:9b", "chat_model": "", "chat_effort": "", **extra}


# ---------------------------------------------------------------- titles / cleaning
@pytest.mark.parametrize("history, title", [
    ([], "(no text)"),
    ([{"role": "user", "content": "  open   notepad\nplease "}], "open notepad please"),
    ([{"role": "assistant", "content": "Hi!"}, {"role": "user", "content": "first"}, {"role": "user", "content": "2nd"}],
     "first"),
    ([{"role": "user", "content": "tool result", "_tool": True}, {"role": "user", "content": "woke", "_wake": True},
      {"role": "user", "content": "the real one"}], "the real one"),
    ([{"role": "user", "content": None}], "(no text)"),
    ([{"role": "user", "content": "x" * 60}], "x" * 60),
    ([{"role": "user", "content": "x" * 61}], "x" * 57 + "…"),
    ([{"role": "user", "content": "word " * 20}], ("word " * 12)[:57].rstrip() + "…"),
])
def test_title_of(history: list, title: str) -> None:
    assert chats.title_of(history) == title


def test_clean_history_drops_screenshots_and_replay_blocks() -> None:
    history = [{"role": "user", "content": "look", "images": ["AAAA"]},
               {"role": "assistant", "content": "ok", "raw": {"blocks": 1}},
               {"role": "tool", "content": "x" * (chats.MAX_TEXT + 5)},
               {"role": "assistant", "content": None, "tool_calls": [{"f": 1}]}]
    out = chats.clean_history(history)
    assert out[0] == {"role": "user", "content": "look (a screenshot was here - not kept)"}
    assert out[1] == {"role": "assistant", "content": "ok"}
    assert out[2]["content"] == "x" * chats.MAX_TEXT + " ...(shortened)"
    assert out[3] == {"role": "assistant", "content": None, "tool_calls": [{"f": 1}]}
    assert "images" in history[0] and len(history[2]["content"]) == chats.MAX_TEXT + 5


def test_new_id_format() -> None:
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", chats.new_id())


# ---------------------------------------------------------------- one chat
def test_save_and_load(store: ChatStore) -> None:
    store.save(chat(1, extra_field=[1, 2]))
    got = store.load(cid(1))
    assert got is not None
    assert got["title"] == "hello cat" and got["v"] == 1 and got["updated"] >= 1_000_000
    assert got["msgs"] == [["user", "hello cat"], ["cat", "Meow!"]]
    assert got["model"] == "qwen3.5:9b" and got["extra_field"] == [1, 2]


def test_saved_chat_is_encrypted(store: ChatStore, dpapi) -> None:  # type: ignore[no-untyped-def]
    store.save(chat(1, "my secret plan"))
    raw = (store.folder() / f"{cid(1)}.chat").read_bytes()
    assert b"secret" not in raw
    assert sorted(p.name for p in store.folder().iterdir()) == [f"{cid(1)}.chat", "index.dat"]
    assert b"secret" not in (store.folder() / "index.dat").read_bytes()


def test_save_keeps_messages_short_and_drops_empty_ones(store: ChatStore) -> None:
    c = chat(1)
    c["msgs"] = [["user", "y" * (chats.MAX_TEXT + 10)], ["cat", ""], ["cat", "ok"]]
    c["history"].append({"role": "user", "content": "see", "images": ["AAA"]})
    store.save(c)
    got = store.load(cid(1))
    assert got["msgs"] == [["user", "y" * chats.MAX_TEXT], ["cat", "ok"]]
    assert "images" not in got["history"][-1]


def test_save_writes_odd_values_as_text(store: ChatStore) -> None:
    store.save(chat(1, when=Path("x")))
    assert store.load(cid(1))["when"] == "x"


@pytest.mark.parametrize("bad", ["../evil", "x", "20260101-000001-ZZZZ", "20260101-000001-0001.chat"])
def test_bad_ids_are_refused(store: ChatStore, bad: str) -> None:
    with pytest.raises(ValueError):
        store.save(dict(chat(1), id=bad))
    assert store.load(bad) is None


def test_load_missing_or_unreadable_is_none(store: ChatStore) -> None:
    assert store.load(cid(5)) is None
    (store.folder() / f"{cid(5)}.chat").write_bytes(b"garbage")
    assert store.load(cid(5)) is None


def test_load_from_another_account_is_none(store: ChatStore, dpapi) -> None:  # type: ignore[no-untyped-def]
    store.save(chat(1))
    dpapi.account = b"someone else"
    assert store.load(cid(1)) is None


def test_load_refuses_a_chat_under_another_id(store: ChatStore) -> None:
    store.save(chat(1))
    (store.folder() / f"{cid(1)}.chat").rename(store.folder() / f"{cid(2)}.chat")
    assert store.load(cid(2)) is None


# ---------------------------------------------------------------- the index
def test_index_most_recent_first(store: ChatStore) -> None:
    for n in (1, 2, 3):
        store.save(chat(n, f"chat {n}"))
    store.save(chat(1, "chat 1 again"))
    assert [i.id for i in store.index()] == [cid(1), cid(3), cid(2)]


def test_index_entry(store: ChatStore) -> None:
    c = chat(1, "Open NOTEPAD")
    c["history"] += [{"role": "user", "content": "tool", "_tool": True}, {"role": "user", "content": "more"}]
    c["msgs"].append(["tool", "HIDDEN tool text"])
    store.save(c)
    (info,) = store.index()
    assert (info.id, info.title, info.created, info.count, info.model) == (cid(1), "Open NOTEPAD", 101.0, 2, "qwen3.5:9b")
    assert info.find == "open notepad meow!"


def test_index_search_text_is_limited(store: ChatStore) -> None:
    store.save(chat(1, "z" * (chats.FIND_CHARS + 100)))
    assert len(store.index()[0].find) == chats.FIND_CHARS


def test_only_the_newest_100_chats_are_kept(store: ChatStore) -> None:
    for n in range(chats.KEEP + 2):
        store.save(chat(n))
    ids = [i.id for i in store.index()]
    assert len(ids) == chats.KEEP == 100
    assert cid(0) not in ids and cid(1) not in ids and ids[0] == cid(chats.KEEP + 1)
    assert len(list(store.folder().glob("*.chat"))) == chats.KEEP
    assert store.load(cid(0)) is None


def test_damaged_index_is_rebuilt_from_the_chats(store: ChatStore) -> None:
    for n in (1, 2):
        store.save(chat(n, f"chat {n}"))
    (store.folder() / "index.dat").write_bytes(b"damaged")
    (store.folder() / f"{cid(9)}.chat").write_bytes(b"not ours")
    assert [i.id for i in store.index()] == [cid(2), cid(1)]
    assert [i.title for i in store.index()] == ["chat 2", "chat 1"]
    assert store._read(store.folder() / "index.dat")          # written again


def test_missing_index_is_rebuilt(store: ChatStore) -> None:
    store.save(chat(1))
    (store.folder() / "index.dat").unlink()
    assert [i.id for i in store.index()] == [cid(1)]
    assert (store.folder() / "index.dat").is_file()


def test_index_skips_chats_whose_file_is_gone(store: ChatStore) -> None:
    store.save(chat(1))
    store.save(chat(2))
    (store.folder() / f"{cid(1)}.chat").unlink()
    assert [i.id for i in store.index()] == [cid(2)]


def test_empty_store(store: ChatStore) -> None:
    assert store.index() == []
    assert not (store.folder() / "index.dat").exists()


def test_delete_and_delete_all(store: ChatStore) -> None:
    for n in (1, 2, 3):
        store.save(chat(n))
    store.delete(cid(2))
    assert [i.id for i in store.index()] == [cid(3), cid(1)]
    assert not (store.folder() / f"{cid(2)}.chat").exists()
    store.delete("not-an-id")
    assert store.delete_all() == 2
    assert store.index() == [] and list(store.folder().glob("*.chat")) == []


# ---------------------------------------------------------------- the secret store can't be used right now
def files(store: ChatStore) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in store.folder().iterdir()}


@pytest.mark.parametrize("first", ["index", "load"])
def test_a_locked_secret_store_writes_nothing_this_run(store: ChatStore, dpapi, first: str) -> None:  # type: ignore[no-untyped-def]
    for n in (1, 2):
        store.save(chat(n))
    before = files(store)
    dpapi.unprotect_error = SecretStoreUnavailable("the keychain is locked")
    if first == "index":
        assert store.index() == []
    else:
        assert store.load(cid(1)) is None
    assert store.locked
    dpapi.unprotect_error = None                       # (even if it works again later in the run)
    with pytest.raises(SecretStoreUnavailable):
        store.save(chat(3))
    assert files(store) == before
    assert [i.id for i in ChatStore(store.folder(), secrets=dpapi).index()] == [cid(2), cid(1)]


def test_a_missing_index_with_a_locked_secret_store_isnt_rebuilt(store: ChatStore, dpapi) -> None:  # type: ignore[no-untyped-def]
    store.save(chat(1))
    (store.folder() / "index.dat").unlink()
    dpapi.unprotect_error = SecretStoreUnavailable("the keychain is locked")
    assert store.index() == [] and store.locked
    assert not (store.folder() / "index.dat").exists()


def test_a_secret_store_that_cant_protect_locks_the_store(store: ChatStore, dpapi) -> None:  # type: ignore[no-untyped-def]
    store.save(chat(1))
    before = files(store)
    dpapi.protect_error = SecretStoreUnavailable("the keychain is locked")
    with pytest.raises(SecretStoreUnavailable):
        store.save(chat(2))
    assert store.locked and files(store) == before


def test_deleting_with_a_locked_secret_store_keeps_the_chat(store: ChatStore, dpapi) -> None:  # type: ignore[no-untyped-def]
    for n in (1, 2):
        store.save(chat(n))
    before = files(store)
    dpapi.unprotect_error = SecretStoreUnavailable("the keychain is locked")
    with pytest.raises(SecretStoreUnavailable):
        store.delete(cid(1))
    assert store.locked and files(store) == before


def test_default_folder_is_in_the_data_dir(dpapi) -> None:  # type: ignore[no-untyped-def]
    store = ChatStore(secrets=dpapi)
    assert store.folder() == paths.data_dir() / "chats"
    assert store.folder().is_dir()


def test_write_retries_while_the_file_is_busy(store: ChatStore, monkeypatch: pytest.MonkeyPatch) -> None:
    real = chats.os.replace
    busy = {"n": 3}

    def replace(src, dst):  # type: ignore[no-untyped-def]
        if str(dst).endswith(".chat") and busy["n"]:
            busy["n"] -= 1
            raise PermissionError(13, "in use")
        return real(src, dst)

    monkeypatch.setattr(chats.os, "replace", replace)
    monkeypatch.setattr(chats.time, "sleep", lambda s: None)
    store.save(chat(1))
    assert store.load(cid(1)) is not None and busy["n"] == 0


def test_write_gives_up_after_20_tries(store: ChatStore, monkeypatch: pytest.MonkeyPatch) -> None:
    real = chats.os.replace
    tries: list[str] = []

    def replace(src, dst):  # type: ignore[no-untyped-def]
        if str(dst).endswith(".chat"):
            tries.append(str(dst))
            raise PermissionError(13, "in use")
        return real(src, dst)

    monkeypatch.setattr(chats.os, "replace", replace)
    monkeypatch.setattr(chats.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        store.save(chat(1))
    assert len(tries) == 20
