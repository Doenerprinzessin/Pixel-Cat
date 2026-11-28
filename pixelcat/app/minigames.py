"""The games: Play ball and the minigames (Menu -> Minigames) - starting and stopping them, the first-time
explanation, and what a game is worth afterwards (Cat Pass XP, records, unlocked skins, hats and games)."""
from __future__ import annotations

import html
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtGui import QPixmap

from .. import games as minigames
from .. import persistence, progress
from ..engine.states import S
from ..games.qt_host import art_pixmap

if TYPE_CHECKING:
    from ..domain import Monitor
    from ..engine.ballgame import BallGame
    from ..engine.brain import Brain
    from ..games.base import MiniGame
    from ..games.qt_host import QtHost
    from ..persistence import SavedState
    from ..ports import WindowPort
    from ..sprites.library import SpriteLibrary
    from ..ui.menu import PixelMenu
    from ..ui.overlay import CatOverlay
    from ..ui.tray import Tray
    from .popups import Popups
    from .state import GameState

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)


class Minigames:
    """Starts and stops the games; the running minigame is ``state.mini`` (a ``GameState`` the world reads too).

    The controller's world answers ``rebuild_world()``, ``cat_monitor()`` and ``ui_unit(monitor)``,
    ``hidden_fullscreen()`` (the app hides behind a fullscreen window), ``selftest()`` / ``quitting()`` (no
    windows then) and ``reward_picture(kind, key, u, hat_scale)`` (the menu's pictures); asked at call time."""

    def __init__(self, state: GameState, saved: SavedState, brain: Brain, game: BallGame, host: QtHost,
                 overlay: CatOverlay, sprites: SpriteLibrary, popups: Popups, menu: PixelMenu, tray: Tray,
                 windows: WindowPort, *, save: Callable[[], None], rebuild_world: Callable[[], None],
                 cat_monitor: Callable[[], Monitor], ui_unit: Callable[[Monitor], int],
                 hidden_fullscreen: Callable[[], bool], selftest: Callable[[], float],
                 quitting: Callable[[], bool],
                 reward_picture: Callable[[str, str, int, int], QPixmap | None]) -> None:
        self.state = state
        self.saved = saved
        self.brain = brain
        self.game = game
        self.host = host
        self.overlay = overlay
        self.sprites = sprites
        self.popups = popups
        self.menu = menu
        self.tray = tray
        self.windows = windows
        self._save = save
        self._rebuild_world = rebuild_world
        self._cat_monitor = cat_monitor
        self._ui_unit = ui_unit
        self._hidden_fullscreen = hidden_fullscreen
        self._selftest = selftest
        self._quitting = quitting
        self._reward_picture = reward_picture

    def game_ended(self, rounds: int) -> None:
        """A ball game is over: remember the best one, XP for the Cat Pass."""
        log.info("ball game over after %d round(s) (best so far %d)", rounds, self.saved.best_round)
        self.saved.plays += 1
        best = ""
        if rounds > self.saved.best_round:
            if self.saved.best_round:
                best = f"New record: {rounds} catches!"
            self.saved.best_round = rounds
            self.game.best = rounds
        xp = min(80, 25 + rounds * 12) if rounds else 0
        result = f"The cat caught the ball {rounds} time{'s' if rounds != 1 else ''}!" if rounds else ""
        self._award("ball", "Play ball", xp, result, best)

    def mini_active(self) -> bool:
        return self.state.mini is not None and self.state.mini.active

    def running_game(self) -> str | None:
        if self.mini_active():
            assert self.state.mini is not None
            return self.state.mini.key
        return "ball" if self.game.active else None

    def game_work(self) -> tuple[int, int, int, int]:
        return self._cat_monitor().work

    def game_unit(self) -> int:
        return self._ui_unit(self._cat_monitor())

    def start_minigame(self, key: str) -> None:
        """Menu -> Minigames.  The first time, a little window explains the game."""
        if key != "ball" and key not in minigames.GAMES:
            return
        if not progress.is_unlocked(self.saved.xp, "game", key):
            self.brain.show_bubble("bubble_question", 1.5)
            return
        if self._hidden_fullscreen():
            return
        if key not in self.saved.tutorials_seen and not self._selftest():
            self._show_tutorial(key)
            return
        self._begin_game(key)

    def _show_tutorial(self, key: str) -> None:
        for pop in list(self.popups.open):
            if getattr(pop, "kind", "") == "tutorial":
                pop.close()
                self.popups.open.remove(pop)
        text = "".join(f"<p style='margin:0 0 6px 0'>{html.escape(t)}</p>" for t in minigames.tutorial(key))
        pop = self.popups.popup(minigames.title(key), text, "LET'S PLAY!", "NOT NOW", self._game_picture(key))
        pop.kind = "tutorial"  # type: ignore[attr-defined]
        pop.answered.connect(lambda ok, k=key: self._tutorial_answer(k, ok))

    def _tutorial_answer(self, key: str, ok: bool) -> None:
        if not ok:
            return                         # explained again next time
        if key not in self.saved.tutorials_seen:
            self.saved.tutorials_seen.append(key)
            self._save()
        self._begin_game(key)

    def _game_picture(self, key: str) -> QPixmap | None:
        u = self.game_unit()
        try:
            if key == "ball":
                return self.sprites.effect("yarn0", u + 1)
            return art_pixmap(minigames.PICTURES[key], u + 1)[0]
        except Exception:  # noqa: BLE001
            log.exception("no picture for %s", key)
            return None

    def _begin_game(self, key: str) -> None:
        if self._hidden_fullscreen() or not progress.is_unlocked(self.saved.xp, "game", key):
            return
        self.menu.close()
        self.stop_minigame()
        if self.game.active:
            self.game.stop()
        b = self.brain
        if b.state == S.HELD:
            self.overlay.cancel_press()
            b.drop(0.0, 0.0)
        if key == "ball":
            b.game = self.game
            b.play()
            return
        self.saved.game_plays[key] = self.saved.game_plays.get(key, 0) + 1
        self._rebuild_world()
        mini = minigames.GAMES[key](self.host)
        self.state.mini = mini
        self.overlay.raise_on_move = False      # the game's windows stay above the cat
        try:
            mini.start()
        except Exception:  # noqa: BLE001
            log.exception("minigame %s failed to start", key)
            self.state.mini = None
            b.game = self.game
            self.overlay.raise_on_move = True
            self.host.clear()
            self._rebuild_world()

    def stop_minigame(self) -> None:
        """The HUD's X, Pause, Quit, or another game: end the running minigame (not Play ball)."""
        if self.mini_active():
            assert self.state.mini is not None
            self.state.mini.stop()

    def mini_ended(self, game: MiniGame) -> None:
        """A minigame is over (finished or stopped): XP, best score, the result."""
        self.overlay.raise_on_move = True
        if self.brain.game is game:
            self.brain.game = self.game
        if self.state.mini is game:
            self.state.mini = None
        try:
            if self.overlay.hwnd():
                self.windows.keep_topmost(self.overlay.hwnd())
        except Exception:  # noqa: BLE001
            pass
        st = self.saved
        st.fish_book = self.host.ctx.get("fish_book", st.fish_book)
        xp = game.xp()
        if game.ends_by_stop:                   # a sandbox: XP for the time spent playing
            xp = int(xp * min(1.0, game.t / 60.0))
        elif not game.finished:                 # stopped early: a little XP for the time played
            xp = int(xp * 0.5 * min(1.0, game.t / max(30.0, game.duration or 60.0)))
        if game.t < 8:
            xp = 0
        best = ""
        if (game.finished or game.ends_by_stop) and game.score:
            old = st.game_best.get(game.key)
            better = old is None or (game.score < old if game.lower_is_better else game.score > old)
            if better:
                st.game_best[game.key] = game.score
                if old is not None:
                    best = "New record!"
        self._award(game.key, game.title, xp, game.result, best)

    def _award(self, key: str, title: str, xp: int, result: str, best: str) -> None:
        """Cat Pass XP; a little window shows the result and anything newly unlocked."""
        st = self.saved
        old = st.xp
        xp = min(persistence.MAX_XP_PER_PLAY, max(0, int(xp)))   # (the save's XP check relies on it)
        st.xp = old + xp
        unlocks = progress.new_unlocks(old, st.xp)
        self._save()
        log.info("%s: +%d XP (total %d), unlocked %s", key, xp, st.xp, unlocks)
        if self._quitting() or self._selftest() or (not xp and not result):
            return
        b = self.brain
        if xp:
            self.host.float_text(f"+{xp} XP", b.x, b.y - 46 * b.scale, "#ffd84a")
        ps = progress.state(st.xp)
        lines = [html.escape(result or "Game over!")]
        if best:
            lines.append(f"<b>{html.escape(best)}</b>")
        if xp:
            stage = (f"Cat Pass stage {ps.stage}: {ps.into}/{ps.cost} XP" if ps.cost
                     else "The Cat Pass is complete!")
            lines.append(f"<b>+{xp} XP</b> &middot; {stage}")
        ok, cancel, picture = "NICE!", None, None
        for n, kind, k in unlocks:
            what = progress.kind_label(kind, k)
            lines.append(f"<b>Stage {n}! New {what}: {html.escape(progress.name_of(kind, k))}</b>")
        if unlocks:
            n, kind, k = unlocks[-1]
            u = self.game_unit()
            picture = self._reward_picture(kind, k, u + (2 if kind == "game" else 0), max(2, u))
            if len(unlocks) == 1 and kind in ("skin", "hat"):
                ok, cancel = "PUT IT ON", "LATER"
            elif len(unlocks) == 1 and kind == "game":
                ok, cancel = "PLAY IT", "LATER"
            if kind == "game" and picture is None:
                picture = self._game_picture(k)
        text = "".join(f"<p style='margin:0 0 5px 0'>{ln}</p>" for ln in lines)
        pop = self.popups.popup(title, text, ok, cancel, picture)
        if unlocks and cancel:
            n, kind, k = unlocks[-1]
            pop.answered.connect(lambda yes, kd=kind, kk=k: self._use_unlock(kd, kk) if yes else None)
        pop.close_later(40 if unlocks else 12)

    def _use_unlock(self, kind: str, key: str) -> None:
        if kind == "skin":
            self.set_skin(key)
        elif kind == "hat":
            self.set_hat(key)
        elif kind == "game":
            self.start_minigame(key)

    def set_skin(self, key: str) -> None:
        if key not in progress.SKINS or not progress.is_unlocked(self.saved.xp, "skin", key):
            return
        self.saved.skin = key
        self.sprites.set_skin(key)
        self.tray.refresh_icon()
        self.overlay.update()
        self.brain.show_bubble("bubble_heart", 1.2)
        self._save()
        log.info("skin: %s", key)

    def set_hat(self, key: str) -> None:
        if key and (key not in progress.HATS or not progress.is_unlocked(self.saved.xp, "hat", key)):
            return
        self.saved.hat = key
        self.overlay.hat = key
        self.overlay.update()
        if key:
            self.brain.show_bubble("bubble_heart", 1.2)
        self._save()
        log.info("hat: %s", key or "none")
