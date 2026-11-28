"""The games' lifecycle and rewards, without the whole app (fake brain, ball game, host, popups and windows)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from pixelcat import games, persistence, progress
from pixelcat.app.minigames import Minigames
from pixelcat.app.state import GameState
from pixelcat.engine.states import S
from pixelcat.persistence import SavedState


class FakePop:
    def __init__(self, title: str, text: str, ok: str, cancel: str | None) -> None:
        self.title, self.text, self.ok, self.cancel = title, text, ok, cancel
        self.answered = SimpleNamespace(connect=self._cbs_append)
        self._cbs: list[Any] = []
        self.closes_in: int | None = None

    def _cbs_append(self, cb: Any) -> None:
        self._cbs.append(cb)

    def answer(self, ok: bool) -> None:
        for cb in self._cbs:
            cb(ok)

    def close(self) -> None:
        self.closed = True

    def close_later(self, s: int) -> None:
        self.closes_in = s


class FakePopups:
    def __init__(self) -> None:
        self.open: list[FakePop] = []
        self.shown: list[FakePop] = []

    def popup(self, title: str, text: str, ok: str, cancel: str | None = None, picture: Any = None) -> FakePop:
        pop = FakePop(title, text, ok, cancel)
        self.shown.append(pop)
        self.open.append(pop)
        return pop


class FakeMini:
    """A minigame that ends the way the real ones do: ``stop()`` -> ``host.ended(game)``."""

    title, tutorial = "Laser pointer", ["Point at the dot."]

    def __init__(self, host: Any, *, fail: bool = False) -> None:
        self.host, self.fail = host, fail
        self.key, self.result = "laser", "12 pounces!"
        self.active = self.finished = self.ends_by_stop = self.lower_is_better = False
        self.t, self.duration, self.score = 0.0, 60.0, 0

    def start(self) -> None:
        if self.fail:
            raise RuntimeError("no windows")
        self.active = True

    def stop(self) -> None:
        self.active = False
        self.host.ended(self)

    def xp(self) -> int:
        return 40


def make(saved: SavedState, *, quitting: bool = False) -> tuple[Minigames, SimpleNamespace]:
    calls: list[str] = []
    brain = SimpleNamespace(x=100.0, y=500.0, scale=2, state=S.SITTING, game=None,
                            show_bubble=lambda name, s: calls.append(f"bubble {name}"),
                            play=lambda: calls.append("play"))
    game = SimpleNamespace(active=False, best=0, stop=lambda: calls.append("ball stop"))
    brain.game = game
    host = SimpleNamespace(ctx={"fish_book": {}}, clear=lambda: calls.append("host clear"),
                           float_text=lambda text, x, y, color: calls.append(f"float {text}"))
    overlay = SimpleNamespace(raise_on_move=True, hat="", update=lambda: None, hwnd=lambda: 7,
                              cancel_press=lambda: None)
    sprites = SimpleNamespace(set_skin=lambda key: calls.append(f"skin {key}"))
    tray = SimpleNamespace(refresh_icon=lambda: calls.append("tray icon"))
    windows = SimpleNamespace(keep_topmost=lambda hwnd: calls.append(f"topmost {hwnd}"))
    menu = SimpleNamespace(close=lambda: calls.append("menu close"))
    popups = FakePopups()
    monitor = SimpleNamespace(work=(0, 0, 1920, 1040))
    pictures: list[tuple[str, str, int, int]] = []

    def reward_picture(kind: str, key: str, u: int, hat_scale: int) -> Any:
        pictures.append((kind, key, u, hat_scale))
        return "picture"

    mg = Minigames(GameState(), saved, brain, game, host, overlay, sprites, popups, menu, tray,  # type: ignore[arg-type]
                   windows, save=lambda: calls.append("save"),
                   rebuild_world=lambda: calls.append("rebuild world"), cat_monitor=lambda: monitor,
                   ui_unit=lambda mon: 3, hidden_fullscreen=lambda: False, selftest=lambda: 0.0,
                   quitting=lambda: quitting, reward_picture=reward_picture)
    host.ended = lambda g: mg.mini_ended(g)
    return mg, SimpleNamespace(calls=calls, brain=brain, game=game, host=host, overlay=overlay, popups=popups,
                               pictures=pictures)


def test_an_unlocked_hat_can_be_put_on_from_the_result() -> None:
    st = SavedState(xp=progress.xp_to_reach(2) - 10)
    mg, f = make(st)
    mg._award("ball", "Play ball", 50, "ok", "")
    assert st.xp == progress.xp_to_reach(2) + 40 and f.calls[:2] == ["save", "float +50 XP"]
    pop = f.popups.shown[-1]
    assert (pop.title, pop.ok, pop.cancel, pop.closes_in) == ("Play ball", "PUT IT ON", "LATER", 40)
    assert "New hat: Party hat" in pop.text and f.pictures == [("hat", "party", 3, 3)]
    pop.answer(True)
    assert st.hat == "party" and f.overlay.hat == "party" and f.calls[-2:] == ["bubble bubble_heart", "save"]


def test_xp_is_capped_per_play_and_kept_while_quitting() -> None:
    st = SavedState(xp=0)
    mg, f = make(st, quitting=True)
    mg._award("mice", "Mouse hunt", 10**6, "", "")
    assert st.xp == persistence.MAX_XP_PER_PLAY and f.calls == ["save"] and f.popups.shown == []


def test_a_ball_record_is_kept_and_rewarded() -> None:
    st = SavedState(xp=0, best_round=2)
    mg, f = make(st)
    mg.game_ended(3)
    assert (st.best_round, f.game.best, st.plays, st.xp) == (3, 3, 1, 61)
    assert "New record: 3 catches!" in f.popups.shown[-1].text
    mg.game_ended(1)
    assert (st.best_round, st.plays, st.xp) == (3, 2, 61 + 37)


def test_a_minigame_runs_until_stopped_then_its_play_time_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(games.GAMES, "laser", FakeMini)
    st = SavedState(xp=0, tutorials_seen=["laser"])
    mg, f = make(st)
    mg.start_minigame("laser")
    mini = mg.state.mini
    assert isinstance(mini, FakeMini) and mini.active and mg.mini_active() and mg.running_game() == "laser"
    assert st.game_plays == {"laser": 1} and f.overlay.raise_on_move is False
    assert f.calls == ["menu close", "rebuild world"]
    f.brain.game = mini
    mini.t = 15.0
    mg.stop_minigame()
    assert mg.state.mini is None and not mg.mini_active() and mg.running_game() is None
    assert f.brain.game is f.game and f.overlay.raise_on_move is True and "topmost 7" in f.calls
    assert st.xp == int(40 * 0.5 * 15.0 / 60.0) and f.popups.shown[-1].title == "Laser pointer"


def test_a_minigame_that_fails_to_start_leaves_nothing_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(games.GAMES, "laser", lambda host: FakeMini(host, fail=True))
    st = SavedState(xp=0, tutorials_seen=["laser"])
    mg, f = make(st)
    mg.start_minigame("laser")
    assert mg.state.mini is None and f.brain.game is f.game and f.overlay.raise_on_move is True
    assert f.calls == ["menu close", "rebuild world", "host clear", "rebuild world"]


def test_the_first_time_a_tutorial_asks_and_a_locked_game_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(games.GAMES, "laser", FakeMini)
    st = SavedState(xp=0)
    mg, f = make(st)
    monkeypatch.setattr(mg, "_game_picture", lambda key: None)
    mg.start_minigame("knock")                     # (stage 3)
    assert f.calls == ["bubble bubble_question"] and f.popups.shown == []
    mg.start_minigame("laser")
    mg.start_minigame("laser")                     # a second click: still one explanation
    tut = f.popups.shown[-1]
    assert [p.title for p in f.popups.open] == ["Laser pointer"] and mg.state.mini is None
    tut.answer(True)
    assert st.tutorials_seen == ["laser"] and mg.mini_active() and f.calls[1:3] == ["save", "menu close"]
