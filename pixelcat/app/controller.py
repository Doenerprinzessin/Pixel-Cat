"""The app's composition root and controller: ``PixelCatApp`` gets the whole ``Platform`` and the model providers
(from ``pixelcat.bootstrap``), makes the engine, the windows and the parts of this package, hands each part the
single ports and objects it uses (and late-bound callbacks into the others), and owns the timers, start-up,
saving, quitting, the self-test, the autostart question and the menu's dispatcher (``_on_menu``)."""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .. import naming, paths, persistence, progress
from ..chats import ChatStore
from ..domain import monitor_at, primary
from ..engine.activity import ActivityTracker
from ..engine.animations import all_poses
from ..engine.ballgame import BallGame
from ..engine.brain import Brain
from ..engine.needs import Needs
from ..engine.states import S
from ..llm.model_status import ModelStatus
from ..llm.qt_chat import QtNotifier
from ..llm.tools import AppHooks, ToolBox
from ..llm.watch import WatchManager
from ..logsetup import RateLimitedLogger
from ..persistence import SavedState, save_state
from ..ports import ModelProvider, Platform
from ..settings import Config
from ..sprites.library import SpriteLibrary
from ..ui.ball import BallOverlay
from ..ui.dialogs import PixelDialog
from ..ui.ledges import LedgeFinder
from ..llm import external
from ..ui import geometry, theme
from ..ui.menu import PixelMenu
from ..ui.overlay import CatOverlay
from ..ui.tray import Tray
from ..games.qt_host import QtHost
from .accounts import Accounts
from .chat_ui import ChatUI
from .commands import ChatCommands
from .limits import Limits
from .popups import Popups
from .menus import Menus
from .minigames import Minigames
from .models_menu import ModelChoice
from .progress_io import ProgressIO
from .provider_settings import ProviderSettings
from .session import AgentSession
from .state import ChatState, GameState, WorldState
from .world import World

log = logging.getLogger("pixelcat.app")   # (the name it had as pixelcat/app.py; tests look for it)


class PixelCatApp(QObject):
    """The desktop pet: wires the parts together and runs them (see the module docstring)."""

    def __init__(self, qapp: QApplication, cfg: Config, saved: SavedState, selftest: float = 0.0, *,
                 platform: Platform, providers: Mapping[str, ModelProvider]) -> None:
        super().__init__()
        self.qapp = qapp
        self.platform = platform        # the ports (from pixelcat.bootstrap)
        self.providers = providers      # the AI model providers by key (from pixelcat.bootstrap)
        geometry.set_qt_logical(platform.display.qt_logical())   # the ports' coordinates are Qt's (macOS)?
        self.cfg = cfg
        self.t = cfg.tuning
        self.saved = saved
        self.selftest = selftest
        self._quitting = False
        self.rl = RateLimitedLogger(log)
        self.own_pid = os.getpid()
        self.quiet = saved.quiet
        self.world_state = WorldState(self.platform.display.monitors())   # the monitors, hidden behind fullscreen
        log.info("monitors: %s", [(m.device, m.rect, m.dpi) for m in self.world_state.monitors])

        self.needs = Needs(saved.energy, saved.hunger, saved.mood)
        self.needs.apply_offline(saved.last_seen, self.t)
        if not progress.is_unlocked(saved.xp, "skin", saved.skin):
            saved.skin = "charcoal"
        if saved.hat and not progress.is_unlocked(saved.xp, "hat", saved.hat):
            saved.hat = ""
        self.sprites = SpriteLibrary(saved.skin)       # Cosmetics -> Skins
        self.brain = Brain(self.t, self.needs)
        self.activity = ActivityTracker(self.platform.input)
        self.overlay = CatOverlay(self.brain, self.sprites, self.t, windows=self.platform.windows,
                                  input=self.platform.input)
        self.overlay.hat = saved.hat                   # Cosmetics -> Hats
        self.game = BallGame(self.brain)              # "Play": grab the ball, the cat hunts it
        self.game.best = saved.best_round
        self.brain.game = self.game
        self.ball_win = BallOverlay(self.game, self.sprites, self.t, windows=self.platform.windows,
                                    input=self.platform.input)
        theme.configure(saved.ui_style, saved.ui_accent, saved.ui_size, saved.ui_font)   # the user's look
        theme.on_change(lambda: self.menus.on_style_changed())   # (the menus are made below)
        naming.set_name(saved.cat_name)                                                  # Settings -> Name...
        # the minigames: their things (mice, shelves, the laser dot...) are small windows of their own
        self.game_state = GameState()                  # the running minigame (the world reads it too)
        self.ledges = LedgeFinder(self.platform.capture)  # borders inside windows the cat can sit on
        self.host = QtHost(self.brain, self.sprites, lambda: self.world.game_windows(),
                           lambda mon: self.world.scale_for(mon),
                           self.t.min_surface_len, on_world=lambda: self.world.rebuild_world(),
                           work_area=lambda: self.minigames.game_work(), ui_unit=lambda: self.minigames.game_unit(),
                           cat_hwnd=self.overlay.hwnd, on_hud_close=lambda: self.minigames.stop_minigame(),
                           windows=self.platform.windows, input=self.platform.input)
        self.host.ctx = {"fish_book": saved.fish_book}
        self.host.ended = lambda game: self.minigames.mini_ended(game)
        self.hud = self.host.hud
        self.menu = PixelMenu(self.sprites)
        self.menu.title = naming.title().upper()
        self.model_menu = PixelMenu(self.sprites)        # the chat's model button
        self.model_menu.title = "MODEL FOR THIS CHAT"
        self.model_menu.triggered.connect(lambda key: self.model_choice.on_model_menu(key))
        self.chat_state = ChatState()                    # the open chat: its messages, its own model and effort
        creds = external.Credentials(secrets=platform.secrets)  # API keys and sign-ins (keys.dat, encrypted)
        self.accounts = Accounts(providers, saved, creds, save=lambda: self.save())   # the providers' accounts
        self.popups = Popups(self.sprites, self.platform.windows, anchor=lambda: self._popup_anchor(),
                             hidden=lambda: self.world_state.hidden_fullscreen)   # popups next to the cat, background work
        self.progress_io = ProgressIO(saved, self.popups, platform.folders, self.menu, self.game,   # export / import
                                      save=lambda: self.save(), stop_minigame=lambda: self.minigames.stop_minigame(),
                                      show_progress=lambda: self._show_progress(),
                                      menu_items=lambda: self.menus.menu_items(),
                                      other_account=platform.profile.other_account)
        self.provider_settings = ProviderSettings(   # the providers' menus, key and limit dialogs, sign-ins
            self.accounts, providers, saved, cfg, self.popups, self.menu, self.overlay, self.chat_state,
            parent=self,
            save=lambda: self.save(), ui_unit=lambda mon: self.world.ui_unit(mon),
            cat_monitor=lambda: self.world.cat_monitor(),
            menu_items=lambda: self.menus.menu_items(), open_menu_at=lambda keys: self.menus.open_menu_at(keys),
            advice=lambda: self.model_choice.advice(), credit_budget=lambda: self.limits.credit_budget(),
            model_choice=lambda: self.model_choice, model_status=lambda: self.model_status,
            drop_copilot_chat=lambda: self.session.drop_copilot_chat(),
            stop_copilot=lambda background=False: self.session.stop_copilot(background=background),
            copilot_quota=lambda force=False: self.limits.copilot_quota(force=force),
            show_model_banner=lambda: self.limits.show_model_banner(), account=platform.profile.account)
        for _p in self.accounts.external:                        # the external models' names (for labels)
            for _m in self.accounts.models(_p):
                external.NAMES[f"{_p}:{_m['id']}"] = _m["name"]
        self.menu.triggered.connect(self._on_menu)
        self.tray = Tray(self.sprites)
        self.tray.action.connect(self._on_menu)
        self.tray.messageClicked.connect(lambda: self.chat_ui.open_chat())
        self.minigames = Minigames(   # Play ball and the minigames: start/stop, XP, records, unlocks
            self.game_state, saved, self.brain, self.game, self.host, self.overlay, self.sprites, self.popups,
            self.menu, self.tray, self.platform.windows, save=lambda: self.save(),
            rebuild_world=lambda: self.world.rebuild_world(), cat_monitor=lambda: self.world.cat_monitor(),
            ui_unit=lambda mon: self.world.ui_unit(mon), hidden_fullscreen=lambda: self.world_state.hidden_fullscreen,
            selftest=lambda: self.selftest, quitting=lambda: self._quitting,
            reward_picture=lambda kind, key, u, hat_scale: self.menus.reward_picture(kind, key, u, hat_scale))
        self.game.on_end = self.minigames.game_ended

        # the AI: Ollama started and the model loaded in the background, status shown in the chat
        self._status_notifier = QtNotifier()           # (the status changes in a background thread)
        self.model_status = ModelStatus(cfg, may_download=lambda: (self.saved.model_download_ok and
                                                                  self.model_status.model == cfg.model)
                                        or self.model_status.model in self.model_choice.download_ok,
                                        ollama_finder=platform.apps.find_ollama, starter=platform.apps.start_ollama,
                                        notify=self._status_notifier.changed.emit,
                                        setup_hint=platform.profile.setup_hint)
        self.watches = WatchManager(self.own_pid, platform=self.platform)  # wait_for watches in the background
        self.session = AgentSession(   # the AI's turns: requests, tool rounds, tasks, watches and timers waking it up
            self.chat_state, cfg, saved, providers, self.accounts, self.provider_settings, self.watches,
            self.brain, self.game, self.minigames, self.overlay, self.tray, self.platform.notifications,
            self.platform.display, self.platform.capture, self.platform.folders, self.model_status, parent=self,
            chat_ui=lambda: self.chat_ui, model_choice=lambda: self.model_choice, save=lambda: self.save(),
            sync_tray=lambda: self.world.sync_tray(),
            ext_gate=lambda p: self.limits.ext_gate(p), credit_gate=lambda: self.limits.credit_gate(),
            copilot_credits=lambda credits: self.limits.on_copilot_credits(credits),
            copilot_quota=lambda: self.limits.copilot_quota(), limit_popup=lambda p, info: self.limits.limit_popup(p, info),
            show_model_banner=lambda: self.limits.show_model_banner(),
            local_command=lambda text: self.commands.local_command(text), profile=platform.profile)
        self.watches.fired.connect(self.session.on_watch_fired)
        self.watches.changed.connect(self.session.on_watches_changed)
        self.session.toolbox = ToolBox(cfg, AppHooks(
            self.session.tool_cat_action, self.session.tool_set_timer, self.session.tool_screenshot,
            self.session.tool_clipboard, self.own_pid, watches=self.watches, task_request=self.session.task_request),
            platform=self.platform)
        self.session.agent = self.session.toolbox.agent
        self.session.esc_watch = self.platform.input.escape_watcher(self.session.on_escape)
        self.world = World(   # the desktop the cat lives on and the game loop
            self.world_state, self.game_state, cfg, saved, self.brain, self.activity, self.overlay, self.ball_win,
            self.game, self.host, self.ledges, self.menu, self.model_menu, self.popups, self.tray, self.watches,
            self.model_status, self.minigames, self.platform.display, self.platform.windows, self.rl, self.own_pid,
            parent=self, chat=lambda: self.chat_ui.chat, quiet=lambda: self.quiet, tick_timer=lambda: self.tick_timer)
        self.model_choice = ModelChoice(   # the default model, and this chat's model and effort (its button)
            self.chat_state, cfg, saved, providers, self.accounts, self.model_status, self.model_menu, self.brain,
            self.world_state, self.world, self.platform.system, parent=self, chat=lambda: self.chat_ui.chat,
            open_chat=lambda: self.chat_ui.open_chat(), show_model_banner=lambda: self.limits.show_model_banner(),
            switched=lambda: self.session.switched_model())
        self.menus = Menus(   # the cat's menu, the look and the cat's name
            saved, cfg, self.brain, self.overlay, self.sprites, self.menu, self.tray, self.hud, self.world_state,
            self.world, self.minigames, self.provider_settings, self.model_choice, self.platform.autostart,
            parent=self, save=lambda: self.save(), chat=lambda: self.chat_ui.chat, quiet=lambda: self.quiet,
            renamed=lambda: setattr(self.session, "copilot_stale", True), start_with=platform.profile.start_with)
        self.chat_ui = ChatUI(   # the chat window and the saved chats (%APPDATA%\PixelCat\chats)
            self.chat_state, ChatStore(secrets=platform.secrets), saved, self.sprites, self.brain, self.overlay,
            self.world_state, self.world, self.watches, self.model_choice, self.model_status, self.platform.display,
            parent=self, on_user_message=lambda text: self.session.on_user_message(text),
            cancel_chat=lambda: self.session.cancel_chat(), new_chat=lambda: self.session.new_chat(),
            set_auto_mode=lambda on: self.session.set_auto_mode(on),
            cancel_watch=lambda wid: self.session.cancel_watch(wid),
            open_saved_chat=lambda cid: self.session.open_saved_chat(cid), save=lambda: self.save(),
            show_model_banner=lambda: self.limits.show_model_banner(), selftest=lambda: self.selftest,
            new_chat_keys=platform.profile.new_chat_keys)
        self._status_notifier.changed.connect(self.chat_ui.on_model_status)   # (made above; from any thread)
        self.limits = Limits(   # the token and credit limits at request time, the limit popups, the model banner
            self.accounts, providers, saved, self.popups, self.menu, self.model_status, self.session,
            self.chat_ui, self.model_choice, self.provider_settings, self.menus, save=lambda: self.save())
        self.commands = ChatCommands(   # the chat's own commands (/new, /model, /force ...), never sent to the model
            self.chat_state, cfg, saved, self.brain, self.watches, self.model_status, self.session, self.chat_ui,
            self.model_choice, save=lambda: self.save(), profile=platform.profile)

        # overlay input -> brain
        ov = self.overlay
        ov.clicked.connect(self.world.on_cat_click)
        ov.double_clicked.connect(self.world.on_cat_double)
        ov.petting.connect(self.brain.petting)
        ov.drag_started.connect(self.world.on_drag_start)
        ov.dragged.connect(self.world.on_drag)
        ov.dropped.connect(self.world.on_drop)
        ov.menu_requested.connect(self.menus.open_menu)

        # timers
        self.tick_timer = self._timer(int(1000 / self.t.fps_active), self.world.tick)
        self.win_timer = self._timer(int(1000 / self.t.window_refresh_hz), self.world.refresh_windows)
        self.mon_timer = self._timer(int(self.t.monitor_refresh_s * 1000), self.world.refresh_monitors)
        self.fs_timer = self._timer(int(self.t.fullscreen_check_s * 1000), self.world.check_fullscreen)
        self.save_timer = self._timer(int(self.t.autosave_s * 1000), self.save)
        self.top_timer = self._timer(5000, self.world.keep_on_top)
        self.tray_timer = self._timer(2000, self.world.sync_tray)
        self._quit_event = 0
        self.quit_timer = self._timer(1000, self._check_quit_request)   # Setup asks us to close (update)
        for sig in (QGuiApplication.instance().screenAdded, QGuiApplication.instance().screenRemoved,
                    QGuiApplication.instance().primaryScreenChanged):
            sig.connect(lambda *_: QTimer.singleShot(600, self.world.refresh_monitors))

    @property
    def creds(self) -> external.Credentials:
        """The API keys and sign-ins: the accounts' own (the parts read them there; swap them there)."""
        return self.accounts.creds

    def _timer(self, ms: int, fn) -> QTimer:  # type: ignore[no-untyped-def]
        tm = QTimer(self)
        tm.setInterval(max(1, ms))
        tm.timeout.connect(fn)
        return tm

    # ================================================================ startup / shutdown
    def start(self, warm_model: bool = False) -> None:
        """``warm_model``: start Ollama and load the model in the background (the real app)."""
        t0 = time.perf_counter()
        mon = primary(self.world_state.monitors)
        n = self.sprites.prewarm(all_poses(), self.world.scale_for(mon))
        log.info("sprite sheet built: %d poses in %.0f ms", n, (time.perf_counter() - t0) * 1000)
        self.world.refresh_windows()
        self.world.place_initial()
        self.overlay.scale = self.world.scale_for(mon)
        self.overlay.start()
        self.overlay.sync(self.world.scale_for(mon))
        self.session.agent.overlay_hwnd = self.overlay.hwnd()
        self.tray.show()
        self.platform.apps.prefetch()  # warm the Start-menu/Store app list in the background
        self.brain.set_state(S.LOAFING if self.quiet else S.SITTING, travel=False)
        for tm in (self.tick_timer, self.win_timer, self.mon_timer, self.fs_timer, self.save_timer,
                   self.top_timer, self.tray_timer):
            tm.start()
        if not self.selftest or self.platform.system.instance:
            self._quit_event = self.platform.system.create_quit_event()
            self.quit_timer.start()
        if not self.saved.autostart_asked and not self.selftest:
            QTimer.singleShot(2500, self._ask_autostart)
        if self.saved.watches and not self.selftest:
            self.watches.restore(self.saved.watches)   # long tasks from before a restart keep going
        if persistence.last_load == "unreadable" and not self.selftest:
            QTimer.singleShot(3000, self, self.progress_io.tell_unreadable_save)   # (dropped if the app goes first)
        elif persistence.last_load == "locked" and not self.selftest:
            QTimer.singleShot(3000, self, self.progress_io.tell_locked_save)
        if self.selftest:
            QTimer.singleShot(int(self.selftest * 1000), self._selftest_finish)
        if warm_model and self.cfg.preload_on_start and not self.selftest:
            if self.model_choice.model_fit(self.cfg.model) == "too_big":
                log.info("not loading %s at start: it's too big for this PC (it would make it lag)", self.cfg.model)
            else:
                QTimer.singleShot(3000, self.model_status.ensure)   # after the cat is on screen
        log.info("Pixel Cat started (pid %s)", self.own_pid)

    def save(self) -> None:
        st = self.saved
        st.energy, st.hunger, st.mood = self.needs.energy, self.needs.hunger, self.needs.mood
        st.quiet = self.quiet
        st.watches = self.watches.to_list()
        if self.brain.mode == "ground":
            st.pos_x, st.pos_y = int(self.brain.x), int(self.brain.y)
        save_state(st, secrets=self.platform.secrets, marks=self.platform.save_marks)
        self.chat_ui.store_chat()

    def quit(self) -> None:
        log.info("quitting")
        self._quitting = True
        try:
            self.save()
            for tm in (self.tick_timer, self.win_timer, self.mon_timer, self.fs_timer, self.save_timer):
                tm.stop()
            self.session.agent.cancel.set()
            self.minigames.stop_minigame()
            for pop in list(self.popups.open):
                pop.close()
            self.session.esc_watch.stop()
            self.watches.stop()
            self.model_status.stop()
            if self.session.worker is not None:
                self.session.worker.cancel()
                self.session.worker.wait(1500)
            for th in list(self.session.live_tools):
                th.wait(3000)
            self.session.stop_copilot()
            self.tray.hide()
            self.overlay.hide()
            self.ball_win.hide()
            if self.chat_ui.chat is not None:
                self.chat_ui.chat.hide()
        finally:
            self.qapp.quit()

    def _selftest_finish(self) -> None:
        try:
            out = paths.cache_dir() / "selftest_overlay.png"
            self.overlay.grab().save(str(out))
            log.info("selftest: state=%s mode=%s pos=(%.0f,%.0f) surfaces=%d walls=%d -> %s",
                     self.brain.state.value, self.brain.mode, self.brain.x, self.brain.y,
                     len(self.brain.smap.surfaces) if self.brain.smap else 0,
                     len(self.brain.smap.walls) if self.brain.smap else 0, out)
            self._selftest_providers()
        finally:
            self.quit()

    def _selftest_providers(self) -> None:
        """The providers' parts are in the build: one line per part (the external models, Copilot's SDK)."""
        parts: dict[str, list[ModelProvider]] = {}
        for prov in self.providers.values():
            if prov.info.build_part:
                parts.setdefault(prov.info.build_part, []).append(prov)
        for part, provs in parts.items():
            try:
                details = [prov.self_check() for prov in provs]
                log.info("selftest: %s ok (%s)", part, ", ".join(d for d in details if d))
            except Exception:  # noqa: BLE001
                log.exception("selftest: %s broken", part)

    def _ask_autostart(self) -> None:
        dlg = PixelDialog(f"Meow! {self.platform.profile.start_with}?",
                          "Should I wake up automatically when you log in? (=^･ω･^=)<br>"
                          "You can change this any time in my right-click menu.", "YES PLEASE", "NO THANKS")
        dlg.answered.connect(self._autostart_answer)
        mon = monitor_at(self.world_state.monitors, self.brain.x, self.brain.y - 1)
        dlg.show_near(self.overlay.cat_rect(), mon.work, self.world.ui_unit(mon))
        self._dialog = dlg

    def _autostart_answer(self, yes: bool) -> None:
        self.saved.autostart_asked = True
        self.platform.autostart.set(yes)
        self.save()

    def _check_quit_request(self) -> None:
        if self.platform.system.quit_requested(self._quit_event):
            log.info("Setup asked me to close (update or uninstall)")
            self.quit_timer.stop()
            self.quit()

    def _on_menu(self, key: str) -> None:
        try:
            b = self.brain
            if key.startswith("ext:"):
                self.provider_settings.ext_action(key[4:])
                return
            for kind in ("style", "accent", "size", "font"):
                if key.startswith(kind + ":"):
                    self.menus.set_look(kind, key.split(":", 1)[1])
                    return
            if key.startswith("model:"):
                self.model_choice.choose_default_model(key.split(":", 1)[1])
                if self.menu.isVisible():
                    self.menu.refresh(self.menus.menu_items())
                return
            if key.startswith("game:"):
                self.minigames.start_minigame(key.split(":", 1)[1])
                return
            if key.startswith("skin:"):
                self.minigames.set_skin(key.split(":", 1)[1])
            elif key.startswith("hat:"):
                self.minigames.set_hat(key.split(":", 1)[1])
            elif key == "stopgame":
                self.minigames.stop_minigame()
                if self.game.active:
                    self.game.stop()
            elif key == "pause":
                if not b.paused:
                    self.minigames.stop_minigame()
                    if self.game.active:
                        self.game.stop()
                b.set_paused(not b.paused)
            elif key == "chat":
                self.chat_ui.open_chat()
            elif key == "set_name":
                self.menu.close()
                self.menus.ask_name()
            elif key == "feed":
                b.feed()
            elif key == "play":
                if self.game.active:
                    self.game.stop()
                else:
                    self.minigames.start_minigame("ball")
            elif key == "quiet":
                self.quiet = not self.quiet
                if self.quiet and not b.paused:
                    b.plan.clear()
                    b.set_state(S.LOAFING, travel=False)
                self.save()
            elif key == "automode":
                self.session.set_auto_mode(not self.saved.auto_mode)
            elif key == "autostart":
                self.platform.autostart.set(not self.platform.autostart.enabled())
                self.saved.autostart_asked = True
            elif key == "preload_ai":
                from ..settings import save_user_setting

                self.cfg.preload_on_start = not self.cfg.preload_on_start
                save_user_setting("preload_on_start", self.cfg.preload_on_start)
                if self.cfg.preload_on_start and not self.selftest:
                    self.model_status.ensure()
            elif key == "explore":
                from ..settings import save_user_setting

                self.cfg.explore_windows = not self.cfg.explore_windows
                save_user_setting("explore_windows", self.cfg.explore_windows)
                if not self.cfg.explore_windows:
                    self.ledges.clear()
                self.world.ledge_t = 0.0
                self.world.rebuild_world()
            elif key == "find":
                self.minigames.stop_minigame()              # (hide and seek: no cheating - the round ends)
                p = primary(self.world_state.monitors)
                b.plan.clear()
                b.place((p.work[0] + p.work[2]) / 2, p.work[3] - 200)
                b.show_bubble("bubble_exclaim", 2.0)
            elif key == "export":
                self.progress_io.export_progress()
            elif key == "import":
                self.progress_io.import_progress()
            elif key == "quit":
                self.quit()
            self.world.sync_tray()
            if self.menu.isVisible():
                self.menu.refresh(self.menus.menu_items())   # a toggled setting shows its new state
        except Exception:  # noqa: BLE001
            log.exception("menu action %s failed", key)

    # ================================================================ where popups show
    def _popup_anchor(self) -> tuple[Any, tuple[int, int, int, int], int, int]:
        """Where a popup shows: next to the cat, on the cat's monitor."""
        mon = self.world.cat_monitor()
        return self.overlay.cat_rect(), mon.work, self.world.ui_unit(mon), mon.dpi

    # ================================================================ your progress (export / import)
    def _show_progress(self) -> None:
        """The progress changed under the app (import): skin, hat, records and look follow."""
        st = self.saved
        if not progress.is_unlocked(st.xp, "skin", st.skin):
            st.skin = "charcoal"
        if st.hat and not progress.is_unlocked(st.xp, "hat", st.hat):
            st.hat = ""
        self.sprites.set_skin(st.skin)
        self.tray.refresh_icon()
        self.overlay.hat = st.hat
        self.overlay.update()
        self.game.best = st.best_round
        self.host.ctx["fish_book"] = st.fish_book
        theme.configure(st.ui_style, st.ui_accent, st.ui_size, st.ui_font)
        self.brain.show_bubble("bubble_heart", 1.5)
