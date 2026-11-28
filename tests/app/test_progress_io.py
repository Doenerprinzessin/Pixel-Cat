"""Export / import of the progress, without the whole app (fake popups, menu and game)."""
from __future__ import annotations

import html
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pixelcat import paths, persistence
from pixelcat.app.progress_io import ProgressIO
from pixelcat.persistence import SavedState


class FakePop:
    def __init__(self, title: str, text: str) -> None:
        self.title, self.text = title, text
        self.answered = SimpleNamespace(connect=self._connect)
        self._cbs: list[Any] = []

    def _connect(self, cb: Any) -> None:
        self._cbs.append(cb)

    def answer(self, ok: bool) -> None:
        for cb in self._cbs:
            cb(ok)


class FakePopups:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.shown: list[FakePop] = []

    def popup(self, title: str, text: str, ok: str, cancel: str | None = None, picture: Any = None) -> FakePop:
        self.calls.append(f"popup {title}")
        pop = FakePop(title, text)
        self.shown.append(pop)
        return pop


class FakeMenu:
    def __init__(self, calls: list[str], visible: bool = False) -> None:
        self.calls, self.visible = calls, visible

    def close(self) -> None:
        self.calls.append("menu close")

    def isVisible(self) -> bool:
        return self.visible

    def refresh(self, items: Any) -> None:
        self.calls.append(f"menu refresh {items}")


def make(saved: SavedState, documents: Path, *, answer: str = "", game_active: bool = False,
         menu_visible: bool = False) -> tuple[ProgressIO, list[str], FakePopups]:
    calls: list[str] = []
    popups = FakePopups(calls)
    game = SimpleNamespace(active=game_active)
    game.stop = lambda: calls.append("game stop")
    folders = SimpleNamespace(known_folder=lambda name: documents if name == "documents" else None)
    io = ProgressIO(saved, popups, folders, FakeMenu(calls, menu_visible), game,  # type: ignore[arg-type]
                    save=lambda: calls.append("save"), stop_minigame=lambda: calls.append("stop minigame"),
                    show_progress=lambda: calls.append(f"show progress {saved.xp}"),
                    menu_items=lambda: ["items"])  # type: ignore[list-item]
    asked: list[tuple[bool, str]] = []

    def ask(save: bool, title: str, start: str) -> str:
        asked.append((save, start))
        calls.append(f"ask {'save' if save else 'open'} {start}")
        return answer

    io._ask_file = ask  # type: ignore[method-assign]
    return io, calls, popups


def test_an_export_loads_back_in_the_same_order_as_ever(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    src, calls, pops = make(SavedState(xp=1234, best_round=9, plays=10), docs, answer=str(tmp_path / "mine"))
    src.export_progress()
    exported = tmp_path / ("mine" + persistence.EXPORT_EXT)
    assert exported.is_file() and pops.shown[-1].title == "Progress exported"
    assert calls[:2] == ["menu close", "save"] and calls[3:] == ["popup Progress exported"]
    assert calls[2].startswith(f"ask save {docs / 'PixelCat-progress-'}") and calls[2].endswith(persistence.EXPORT_EXT)

    dst_state = SavedState(xp=5, plays=1)
    dst, calls, pops = make(dst_state, docs, answer=str(exported), game_active=True, menu_visible=True)
    dst.import_progress()
    ask = pops.shown[-1]
    assert ask.title == "Import progress?" and "1234 XP" in ask.text and dst_state.xp == 5
    assert calls == ["menu close", f"ask open {docs}", "popup Import progress?"]
    ask.answer(True)
    assert calls[3:] == ["stop minigame", "game stop", "show progress 1234", "save", "popup Progress loaded",
                         "menu refresh ['items']"]
    assert (dst_state.xp, dst_state.best_round) == (1234, 9)
    backups = list((paths.data_dir() / "progress-backups").glob("*"))
    assert len(backups) == 1 and persistence.read_export(backups[0])["xp"] == 5
    assert str(backups[0]) in pops.shown[-1].text


def test_an_import_said_no_to_changes_nothing(tmp_path: Path) -> None:
    exported = tmp_path / ("x" + persistence.EXPORT_EXT)
    persistence.export_progress(SavedState(xp=99), exported)
    st = SavedState(xp=5)
    io, calls, pops = make(st, tmp_path, answer=str(exported))
    io.import_progress()
    pops.shown[-1].answer(False)
    assert st.xp == 5 and calls[-1] == "popup Import progress?"


@pytest.mark.parametrize("content", [b"not a cat", None])
def test_an_unreadable_file_is_told_and_nothing_changes(tmp_path: Path, content: bytes | None) -> None:
    bad = tmp_path / "bad.pixelcat"
    if content is not None:
        bad.write_bytes(content)
    st = SavedState(xp=5)
    io, calls, pops = make(st, tmp_path, answer=str(bad))
    io.import_progress()
    try:
        persistence.read_export(bad)
    except persistence.SaveError as e:
        why = str(e)
    assert [p.title for p in pops.shown] == ["Import"] and st.xp == 5
    assert pops.shown[0].text == f"<p style='margin:0 0 6px 0'>{html.escape(why)}</p>"


def test_cancelled_dialogs_and_the_unreadable_save_message(tmp_path: Path) -> None:
    io, calls, pops = make(SavedState(xp=5), tmp_path, answer="")
    io.export_progress()
    io.import_progress()
    assert pops.shown == []
    io.tell_unreadable_save()
    assert pops.shown[-1].title == "A fresh start" and "Import progress" in pops.shown[-1].text


def test_the_locked_save_message(tmp_path: Path) -> None:
    io, calls, pops = make(SavedState(xp=5), tmp_path, answer="")
    io.tell_locked_save()
    assert pops.shown[-1].title == "Not saving this time"
    assert "won't overwrite it" in pops.shown[-1].text and "quit Pixel Cat" in pops.shown[-1].text
