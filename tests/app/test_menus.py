"""The cat's menu and its name setting without the app: fake windows and a fake minigame runner."""
from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from pixelcat import naming
from pixelcat.app.menus import Menus
from pixelcat.persistence import SavedState
from pixelcat.settings import Config


class FakeDialog:
    def __init__(self, closed: bool = False) -> None:
        self.closed = closed
        self.answers: list[tuple[bool, str]] = []

    def checked(self, ok: bool, msg: str) -> None:
        if self.closed:
            raise RuntimeError("Internal C++ object already deleted.")
        self.answers.append((ok, msg))


@pytest.fixture(autouse=True)
def no_name() -> Iterator[None]:
    yield
    naming.set_name("")


def make(qapp: Any, running: str | None = None) -> tuple[Menus, SimpleNamespace]:
    saved = SavedState()
    f = SimpleNamespace(saves=0, renamed=0, renames=[], menu=SimpleNamespace(title="PIXEL CAT"))
    f.tray = SimpleNamespace(rename=lambda: f.renames.append("tray"))
    f.chat = SimpleNamespace(rename=lambda: f.renames.append("chat"))
    x = SimpleNamespace()
    menus = Menus(saved, Config(), x, x, x, f.menu, f.tray, None, x, x,  # type: ignore[arg-type]
                  SimpleNamespace(running_game=lambda: running), x, x, x,
                  save=lambda: setattr(f, "saves", f.saves + 1), chat=lambda: f.chat, quiet=lambda: False,
                  renamed=lambda: setattr(f, "renamed", f.renamed + 1))
    return menus, f


def test_a_new_name_is_checked_then_shown_everywhere(qapp) -> None:  # type: ignore[no-untyped-def]
    menus, f = make(qapp)
    dlg = FakeDialog()
    menus._check_name("Mr. <Whiskers>", dlg)
    assert dlg.answers == [(False, "Letters, numbers, spaces and - ' . & ! ~ _ only, please.")]
    assert menus.saved.cat_name == "" and f.saves == 0 and f.renamed == 0
    menus._check_name("Tom", FakeDialog(closed=True))             # (the dialog was closed meanwhile)
    assert menus.saved.cat_name == "" and f.saves == 0

    menus._check_name("  Sir   Tom ", dlg)
    assert dlg.answers[-1] == (True, "") and menus.saved.cat_name == "Sir Tom" == naming.custom()
    assert f.menu.title == "SIR TOM" and f.renames == ["tray", "chat"] and f.saves == 1 and f.renamed == 1
    menus._check_name("", dlg)                                    # (empty: no name again)
    assert menus.saved.cat_name == "" and f.menu.title == "PIXEL CAT" and f.renamed == 2


def test_the_minigames_show_whats_running_locked_new_and_best(qapp) -> None:  # type: ignore[no-untyped-def]
    menus, _ = make(qapp, running="laser")
    menus.saved.plays, menus.saved.best_round = 3, 7
    menus.saved.game_plays["treats"], menus.saved.game_best["treats"] = 2, 12.5
    items = {i.key: i for i in menus._game_items()}
    assert items["game:ball"].note == "BEST 7"
    assert "game:laser" not in items and items["stopgame"].label == "Stop Laser pointer"
    assert items["game:treats"].note == "BEST 12.5"
    assert items["game:knock"].locked and items["game:knock"].note == "STAGE 3"
    menus.saved.plays = 0
    assert {i.key: i for i in menus._game_items()}["game:ball"].note == "NEW!"
    assert menus._game_items()[-1].key == "feed"
