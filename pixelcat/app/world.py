"""The cat's world and the game loop: the monitors and windows it walks, climbs and sits on (with the
ledges inside them), the frame tick, hiding behind fullscreen apps, staying on top, the tray's status,
and the mouse on the cat (click, double click, drag and drop)."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject

from ..domain import Monitor, UserState, monitor_at, primary
from ..engine.brain import Ctx
from ..engine.states import STATES, S
from ..engine.surfaces import WINDOW_KINDS, SurfaceMap
from ..ui import theme
from .geometry import rect_gap

if TYPE_CHECKING:
    from PySide6.QtCore import QTimer

    from ..engine.activity import ActivityTracker
    from ..engine.ballgame import BallGame
    from ..engine.brain import Brain
    from ..games.qt_host import QtHost
    from ..llm.model_status import ModelStatus
    from ..llm.watch import WatchManager
    from ..logsetup import RateLimitedLogger
    from ..persistence import SavedState
    from ..ports import DisplayPort, WindowPort
    from ..settings import Config
    from ..ui.ball import BallOverlay
    from ..ui.chat import ChatWindow
    from ..ui.ledges import LedgeFinder
    from ..ui.menu import PixelMenu
    from ..ui.overlay import CatOverlay
    from ..ui.tray import Tray
    from .minigames import Minigames
    from .popups import Popups
    from .state import GameState, WorldState

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)


class World(QObject):
    """The desktop world and the loop.  Shared state is ``state`` (a ``WorldState``: the monitors, hidden
    behind a fullscreen app); the running minigame is read from ``game_state``.

    The controller owns the timers and connects them (and the overlay's mouse signals) to ``tick``,
    ``refresh_windows``, ``refresh_monitors``, ``check_fullscreen``, ``keep_on_top``, ``sync_tray`` and
    ``on_*``.  Asked at call time: ``chat()`` (the chat window, if made yet), ``quiet()`` (quiet mode) and
    ``tick_timer()`` (its interval follows the cat's frame rate)."""

    def __init__(self, state: WorldState, game_state: GameState, cfg: Config, saved: SavedState, brain: Brain,
                 activity: ActivityTracker, overlay: CatOverlay, ball_win: BallOverlay, game: BallGame,
                 host: QtHost, ledges: LedgeFinder, menu: PixelMenu, model_menu: PixelMenu, popups: Popups,
                 tray: Tray, watches: WatchManager, model_status: ModelStatus, minigames: Minigames,
                 display: DisplayPort, windows: WindowPort, rl: RateLimitedLogger, own_pid: int, *,
                 parent: QObject | None = None, chat: Callable[[], ChatWindow | None], quiet: Callable[[], bool],
                 tick_timer: Callable[[], QTimer]) -> None:
        super().__init__(parent)
        self.state = state
        self.game_state = game_state
        self.cfg = cfg
        self.t = cfg.tuning
        self.saved = saved
        self.brain = brain
        self.activity = activity
        self.overlay = overlay
        self.ball_win = ball_win
        self.game = game
        self.host = host
        self.ledges = ledges
        self.menu = menu
        self.model_menu = model_menu
        self.popups = popups
        self.tray = tray
        self.watches = watches
        self.model_status = model_status
        self.minigames = minigames
        self.display = display
        self.windows = windows
        self.rl = rl
        self.own_pid = own_pid
        self._chat = chat
        self._quiet = quiet
        self._tick_timer = tick_timer
        self._known_hwnds: set[int] | None = None
        self._real_wins: list[Any] = []                  # the last window list (props come on top)
        self.ledge_t = -100.0                            # the last ledge search (0: search again soon)
        self._ledge_sig: tuple = ()
        self._under_t = 0.0
        self._ledge_quick = 2
        self._own_hwnds: set[int] = set()
        self._last_tick = time.monotonic()

    # ================================================================ where the cat is
    def scale_for(self, mon: Monitor) -> int:
        if self.cfg.pixel_scale_override > 0:
            return max(1, int(round(self.cfg.pixel_scale_override)))
        return max(1, int(round(self.t.base_pixel_scale * mon.dpi / 96.0)))

    def ui_unit(self, mon: Monitor) -> int:
        return theme.unit_for_dpi(mon.dpi)       # follows Settings -> Size

    def cat_monitor(self) -> Monitor:
        return monitor_at(self.state.monitors, self.brain.x, self.brain.y - 1)

    def place_initial(self) -> None:
        x, y = self.saved.pos_x, self.saved.pos_y
        if x is not None and y is not None and any(m.contains(x, y - 2) for m in self.state.monitors):
            self.brain.place(x, y - 2)
        else:
            m = primary(self.state.monitors)
            self.brain.place(m.work[2] - 160 * self.scale_for(m) / 3, m.work[3] - 1)
        if self.brain.smap is not None:
            f = self.brain.smap.landing_between(self.brain.x, self.brain.y - 4, self.brain.y + 4)
            if f is not None:
                self.brain._land(f, quiet=True)  # start on the ground, no landing animation

    # ================================================================ loops
    def tick(self) -> None:
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        try:
            if self.state.hidden_fullscreen:
                return
            self.activity.poll(self.overlay.cursor_over)
            mon = monitor_at(self.state.monitors, self.brain.x, self.brain.y - 1)
            scale = self.scale_for(mon)
            ctx = Ctx(cursor=self.activity.cursor, busy=self.activity.busy(self.t.busy_grace_s),
                      user_idle=self.activity.idle_seconds(), hour=datetime.now().hour, quiet=self._quiet(),
                      scale=scale, cursor_over_cat=self.overlay.cursor_over)
            self._ride_window()
            self.brain.update(dt, ctx)
            mini = self.game_state.mini
            if mini is not None and mini.active:
                mini.update(dt, self.activity.cursor)
            self.overlay.sync(scale)
            if self.game.active or self.game.fx or self.ball_win.isVisible():
                self.game.update(dt, self.activity.cursor)
                self.ball_win.sync(scale)
        except Exception:  # noqa: BLE001 - the loop must survive anything
            self.rl.exception("tick", "update loop error")
        finally:
            fps = self.t.fps_hidden if self.state.hidden_fullscreen else self.brain.fps_hint()
            interval = int(1000 / max(1, fps))
            if interval != self._tick_timer().interval():
                self._tick_timer().setInterval(interval)

    def _own_surfaces(self) -> set[int]:
        """Our own windows the cat may sit on: the chat, the menu, popups."""
        out: set[int] = set()
        for w in [self._chat(), self.menu, self.model_menu, *self.popups.open]:
            try:
                if w is not None and w.isVisible():
                    out.add(int(w.winId()))
            except RuntimeError:          # (a popup that was just deleted)
                pass
        return out

    def refresh_windows(self) -> None:
        if self.state.hidden_fullscreen:
            return
        try:
            self._own_hwnds = self._own_surfaces()
            wins = self.windows.top_level(self.own_pid, self._own_hwnds)
            self._real_wins = wins
            rects = {w.hwnd: w.rect for w in wins}
            if self._known_hwnds is not None and self.brain.mode == "ground" and not self.minigames.mini_active():
                cat = self.overlay.cat_rect()
                s = self.scale_for(monitor_at(self.state.monitors, self.brain.x, self.brain.y - 1))
                for w in wins:
                    if (w.hwnd not in self._known_hwnds and w.hwnd not in self._own_hwnds
                            and rect_gap(w.rect, cat) < self.t.startle_radius * s):
                        self.brain.startle()
                        break
            self._known_hwnds = set(rects)
            self._look_for_ledges()
            self.rebuild_world()
        except Exception:  # noqa: BLE001
            self.rl.exception("windows", "window refresh failed")

    def _ledges_on(self) -> bool:
        return self.cfg.explore_windows and not self.minigames.mini_active() and not self.game.active

    def _cat_box(self) -> tuple[int, int, int, int]:
        """The cat's own pixels (a little bigger): it covers the line it sits on."""
        s = self.scale_for(monitor_at(self.state.monitors, self.brain.x, self.brain.y - 1))
        x1, y1, x2, y2 = self.overlay.cat_rect()
        return x1 - 3 * s, y1 - 6 * s, x2 + 3 * s, y2 + 3 * s

    def _look_for_ledges(self) -> None:
        """Surfaces inside the windows on the cat's screen (text, pictures, buttons...), searched
        in the background: soon after the windows change (and once more to confirm what is
        new), otherwise every 30 s.  And every second: is the line under the cat still there?"""
        if not self._ledges_on():
            return
        self.ledges.poll()
        b = self.brain
        now = time.monotonic()
        if b.ride_t > 0:
            self._under_t = now                          # (its window is moving: look once it stands still)
        if b.mode == "ground" and b.surf is not None and b.surf.kind == "ledge" and now - self._under_t >= 1.0:
            self._under_t = now
            sf = b.surf
            if self.ledges.check_line(sf.x1, sf.x2, sf.y, self._cat_box()) is False:
                self.ledges.forget(sf.hwnd, sf.key)      # scrolled away: the cat drops off
                self.rebuild_world()
        if (self.ledges.busy() or STATES[b.state].asleep or b.mode != "ground" or b.ride_t > 0
                or b.state == S.HELD):
            return
        mon = monitor_at(self.state.monitors, b.x, b.y - 1)
        s = self.scale_for(mon)
        area = mon.work
        others = [w for w in self._real_wins if w.hwnd not in self._own_hwnds]
        sig = tuple(sorted((w.hwnd, w.rect) for w in others if w.left < area[2] and area[0] < w.right))
        if sig != self._ledge_sig:                       # windows opened/moved/resized: look again soon,
            self._ledge_sig = sig                        # and once more to confirm what is new
            self.ledge_t = min(self.ledge_t, now - 30.0 + 1.0)
            self._ledge_quick = 2
        took = max(0.0, float(getattr(self.ledges, "last_ms", 0.0) or 0.0)) / 1000.0
        wait = max(1.5, took * 20) if self._ledge_quick > 0 else max(30.0, took * 100)   # (slow PCs: less often)
        if now - self.ledge_t < wait:
            return
        self.ledge_t = now
        if self.display.session_locked():                # (the lock screen: nothing to see)
            return
        self._ledge_quick = max(0, self._ledge_quick - 1)
        own = [w.rect for w in self._real_wins if w.hwnd in self._own_hwnds]
        own.append(self._cat_box())
        self.ledges.start(others, area, own, 16 * s, s)

    def game_windows(self) -> list[Any]:
        """Other apps' windows only: a game never uses the chat or its own popups (a result
        window is always on top - hiding behind it, mice running on it...)."""
        return [w for w in self._real_wins if w.hwnd not in self._own_hwnds]

    def rebuild_world(self) -> None:
        """The cat's surfaces: real windows (and the chat, the menu) with the borders inside
        them, plus the game's furniture (it is always on top)."""
        base = self.game_windows() if self.minigames.mini_active() else list(self._real_wins)
        wins = base + self.host.prop_infos()
        inner = self.ledges.current(self._real_wins) if self._ledges_on() else None
        smap = SurfaceMap.build(self.state.monitors, wins, self.scale_for, self.t.min_surface_len, inner)
        self.brain.set_world(smap, {w.hwnd: w.rect for w in wins})

    def _ride_window(self) -> None:
        """Every frame: the window the cat stands on or climbs - dragged around, it carries the
        cat along smoothly; closed or minimised, the cat falls from where it is."""
        b = self.brain
        hwnd = 0
        if b.mode == "ground" and b.surf is not None and b.surf.kind in WINDOW_KINDS:
            hwnd = b.surf.hwnd
        elif b.mode in ("climb", "hang") and b.wall is not None:
            hwnd = b.wall.hwnd
        if hwnd <= 0 or any(p.hwnd == hwnd for p in self.host.props):
            return
        w = self.windows
        try:
            rect = None
            if w.exists(hwnd) and w.is_visible(hwnd) and not w.is_minimized(hwnd) and not w.is_cloaked(hwnd):
                rect = w.frame_bounds(hwnd)
        except Exception:  # noqa: BLE001
            return
        if rect is None:
            self.ledge_t = 0.0
        b.ride_window(hwnd, rect)

    def refresh_monitors(self) -> None:
        try:
            mons = self.display.monitors()
            if [(m.rect, m.work, m.dpi) for m in mons] != [(m.rect, m.work, m.dpi) for m in self.state.monitors]:
                log.info("monitor layout changed: %s", [(m.device, m.rect, m.dpi) for m in mons])
                self.state.monitors = mons
                if not any(m.contains(self.brain.x, self.brain.y - 2) for m in mons):
                    p = primary(mons)
                    self.brain.place((p.work[0] + p.work[2]) / 2, p.work[1] + 40)
                self.refresh_windows()
        except Exception:  # noqa: BLE001
            self.rl.exception("monitors", "monitor refresh failed")

    def check_fullscreen(self) -> None:
        try:
            st = self.display.user_state()
            hide = st == UserState.FULLSCREEN_APP or (
                self.cfg.hide_during_presentations and st == UserState.PRESENTATION)
            if not hide:
                fg = self.windows.foreground()
                if fg and self.windows.pid(fg) != self.own_pid:
                    mon = monitor_at(self.state.monitors, self.brain.x, self.brain.y - 1)
                    hide = self.windows.is_fullscreen(fg, mon.rect)
            if hide != self.state.hidden_fullscreen:
                self._set_hidden(hide)
        except Exception:  # noqa: BLE001
            self.rl.exception("fullscreen", "fullscreen check failed")

    def _set_hidden(self, hide: bool) -> None:
        log.info("fullscreen app %s - cat %s", "detected" if hide else "gone", "hidden" if hide else "back")
        self.state.hidden_fullscreen = hide
        if hide:
            self.menu.close()
            self.overlay.hide()
            self.ball_win.hide()          # the game waits; the ball comes back with the cat
            self.host.set_hidden(True)
            for pop in self.popups.open:
                pop.hide()
            self.state.chat_was_visible = self._chat() is not None and self._chat().isVisible()
            if self.state.chat_was_visible and self._chat() is not None:
                self._chat().hide()
        else:
            self.overlay.reshow()
            self.host.set_hidden(False)
            for pop in self.popups.open:
                pop.show()
            if self.state.chat_was_visible and self._chat() is not None:
                self._chat().show()
            self._last_tick = time.monotonic()
            self.refresh_windows()

    def keep_on_top(self) -> None:
        if not self.state.hidden_fullscreen and self.overlay.hwnd():
            mini = self.game_state.mini if self.minigames.mini_active() else None
            if mini is None or not mini.cat_z_locked:     # hide and seek: the cat stays behind its window
                try:
                    self.windows.keep_topmost(self.overlay.hwnd())
                except Exception:  # noqa: BLE001
                    pass
            self.ball_win.keep_on_top()   # the ball stays above the cat (it's in its mouth sometimes)
            if mini is not None:
                self.host.raise_all()     # the game's things (mice, shelves...) above the cat

    def sync_tray(self) -> None:
        n = len(self.watches.active())
        status = self.brain.status() + (f" - watching {n} thing{'s' if n != 1 else ''}" if n else "")
        ai = self.model_status.short()
        status += f" - {ai}" if ai else ""
        self.tray.sync(self.brain.paused, self._quiet(), status)

    # ================================================================ drag & drop
    def on_drag_start(self) -> None:
        self.menu.close()
        self.brain.pick_up()

    def on_cat_click(self) -> None:
        mini = self.game_state.mini if self.minigames.mini_active() else None
        if mini is not None and mini.on_cat_click():     # hide and seek: found you!
            return
        self.brain.poke()

    def on_cat_double(self) -> None:
        mini = self.game_state.mini if self.minigames.mini_active() else None
        if mini is not None and mini.on_cat_click():
            return
        self.brain.double_click()

    def on_drag(self, x: int, y: int) -> None:
        self.brain.drag_to(x, y)
        mon = monitor_at(self.state.monitors, x, y)
        self.overlay.sync(self.scale_for(mon))

    def on_drop(self, vx: float, vy: float) -> None:
        if self.brain.state == S.HELD:
            self.brain.drop(vx * 0.6, vy * 0.6)
