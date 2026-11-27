"""The save and the chats on real Windows: with the Windows secret store (DPAPI) and save marks (registry).

The marks go to a test registry key, never the real one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelcat import persistence, savecrypt
from pixelcat.adapters.windows import registry
from pixelcat.adapters.windows.platform import WindowsSaveMarks, WindowsSecretStore
from pixelcat.chats import ChatStore
from pixelcat.persistence import SavedState, load_state, save_state

WIN = b"PXCAT1W\n"
STORAGE = {"secrets": WindowsSecretStore(), "marks": WindowsSaveMarks()}
CHAT_ID = "20260101-000001-0001"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    d = tmp_path / "save"
    d.mkdir()
    return d


def played(xp: int = 900) -> SavedState:
    return SavedState(xp=xp, plays=2, game_plays={"laser": 3, "mice": 2}, skin="ginger", best_round=7,
                      auto_mode=True, auto_approved_tools=["open_app"])


@pytest.fixture
def real_reg_path(test_reg_key: str, monkeypatch: pytest.MonkeyPatch) -> str:
    path = test_reg_key + r"\ProtectedSaves"
    monkeypatch.setattr(registry, "REG_PATH", path)
    monkeypatch.setattr(persistence, "last_load", persistence.last_load)
    return path


@pytest.mark.windows
def test_real_save_round_trip_with_dpapi_and_registry(real_reg_path: str, real_reg_read, folder: Path) -> None:  # type: ignore[no-untyped-def]
    save_state(played(), folder, **STORAGE)
    raw = (folder / "save.dat").read_bytes()
    assert raw.startswith(WIN) and b"ginger" not in raw
    assert load_state(folder, **STORAGE).xp == 900 and persistence.last_load == "save"
    assert real_reg_read(real_reg_path, persistence._folder_id(folder)) == 1
    persistence.forget_folder(folder, STORAGE["marks"])
    assert real_reg_read(real_reg_path, persistence._folder_id(folder)) is None


@pytest.mark.windows
def test_real_state_json_conversion_is_remembered(real_reg_path: str, folder: Path) -> None:
    (folder / "state.json").write_text(json.dumps({"xp": 450, "game_plays": {"treats": 4}}), "utf-8")
    assert load_state(folder, **STORAGE).xp == 450 and persistence.last_load == "converted"
    for n in ("save.dat", "save.bak"):
        (folder / n).unlink()
    (folder / "state.json").write_text(json.dumps({"xp": 600, "game_plays": {"treats": 4}}), "utf-8")
    assert load_state(folder, **STORAGE).xp == 0 and persistence.last_load == "fresh"


@pytest.mark.windows
def test_real_save_for_another_account_is_unreadable(real_reg_path: str, folder: Path,  # type: ignore[no-untyped-def]
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    real = savecrypt._ENTROPY
    monkeypatch.setattr(savecrypt, "_ENTROPY", b"another account or PC")
    save_state(played(), folder, **STORAGE)
    monkeypatch.setattr(savecrypt, "_ENTROPY", real)
    assert load_state(folder, **STORAGE).xp == 0 and persistence.last_load == "unreadable"


@pytest.mark.windows
def test_real_chats_round_trip(tmp_path: Path) -> None:
    store = ChatStore(tmp_path / "chats", secrets=WindowsSecretStore())
    store.save({"id": CHAT_ID, "created": 1.0, "msgs": [["user", "secret plan"]],
                "history": [{"role": "user", "content": "secret plan"}], "model": "m"})
    assert b"secret" not in (store.folder() / f"{CHAT_ID}.chat").read_bytes()
    assert store.load(CHAT_ID)["title"] == "secret plan"
    assert [i.id for i in ChatStore(tmp_path / "chats", secrets=WindowsSecretStore()).index()] == [CHAT_ID]
    with pytest.raises(savecrypt.Unreadable):
        WindowsSecretStore().unprotect(b"\x00" * 64)
