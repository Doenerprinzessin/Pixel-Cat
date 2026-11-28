"""The popups next to the cat and the background runner, without the whole app."""
from __future__ import annotations

import logging
import threading
from typing import Any

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from pixelcat.app.popups import Popups
from pixelcat.sprites.library import SpriteLibrary

WORK = (0, 0, 1920, 1040)


@pytest.fixture(scope="module")
def sprite_lib(qapp) -> SpriteLibrary:  # type: ignore[no-untyped-def]
    return SpriteLibrary("charcoal")


@pytest.fixture
def where() -> dict[str, Any]:
    return {"anchor": ((900, 900, 1000, 1000), WORK, 2, 96), "hidden": False, "asked": 0}


@pytest.fixture
def popups(qapp, sprite_lib, fake_platform, where):  # type: ignore[no-untyped-def]
    def anchor() -> Any:
        where["asked"] += 1
        return where["anchor"]

    before = set(QApplication.topLevelWidgets())
    yield Popups(sprite_lib, fake_platform.windows, anchor=anchor, hidden=lambda: where["hidden"])
    for w in QApplication.topLevelWidgets():
        if w not in before:
            w.hide()
            w.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_a_popup_shows_where_the_cat_is_then_and_leaves_the_list_when_answered(popups, where) -> None:  # type: ignore[no-untyped-def]
    assert where["asked"] == 0                          # (asked when a popup opens, not before)
    where["anchor"] = ((100, 100, 200, 200), (0, 0, 800, 600), 1, 96)
    pop = popups.popup("Hi", "<p>text</p>", "OK", "NO")
    assert where["asked"] == 1 and popups.open == [pop] and pop.isVisible()
    assert pop.x() + pop.width() <= 800 and pop.y() + pop.height() <= 600
    other = popups.popup("Two", "<p>more</p>", "OK")
    pop.ok.click()
    assert popups.open == [other]


def test_a_popup_while_hidden_behind_a_fullscreen_window_stays_hidden(popups, where) -> None:  # type: ignore[no-untyped-def]
    where["hidden"] = True
    pop = popups.popup("Hi", "<p>text</p>", "OK")
    assert not pop.isVisible() and popups.open == [pop]


def test_dismiss_none_unknown_and_deleted_popups(popups, qapp) -> None:  # type: ignore[no-untyped-def]
    popups.dismiss(None)
    kept = popups.popup("Kept", "<p>x</p>", "OK")
    stray = popups.popup("Stray", "<p>x</p>", "OK")
    popups.open.remove(stray)
    popups.dismiss(stray)                               # not in the list: only closed
    assert stray._done and not stray.isVisible() and popups.open == [kept]
    gone = popups.popup("Gone", "<p>x</p>", "OK")
    gone.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    popups.dismiss(gone)                                # (its window is deleted already)
    assert popups.open == [kept]
    popups.dismiss(kept)
    assert popups.open == [] and not kept.isVisible()


@pytest.mark.parametrize("fails", [False, True])
def test_in_background_hands_the_result_or_the_error_to_cb_on_the_gui_thread(popups, qtbot, fails: bool) -> None:  # type: ignore[no-untyped-def]
    ran_in: list[threading.Thread] = []
    got: list[tuple[Any, threading.Thread]] = []

    def work() -> int:
        ran_in.append(threading.current_thread())
        if fails:
            raise ValueError("no")
        return 42

    popups.in_background(work, lambda res: got.append((res, threading.current_thread())))
    qtbot.waitUntil(lambda: bool(got), timeout=2000)
    res, cb_thread = got[0]
    assert ran_in[0] is not threading.main_thread() and cb_thread is threading.main_thread()
    assert (isinstance(res, ValueError) and str(res) == "no") if fails else res == 42


def test_a_failing_result_handler_is_logged(popups, qtbot, caplog) -> None:  # type: ignore[no-untyped-def]
    def boom(_res: Any) -> None:
        raise RuntimeError("handler")

    with caplog.at_level(logging.ERROR, logger="pixelcat.app"):
        popups.in_background(lambda: 1, boom)
        qtbot.waitUntil(lambda: any(r.message == "background result handler failed" for r in caplog.records),
                        timeout=2000)
    assert [r.name for r in caplog.records if r.message == "background result handler failed"] == ["pixelcat.app"]
