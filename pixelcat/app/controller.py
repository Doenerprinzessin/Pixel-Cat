"""Application controller: owns the timers and wires brain, overlay, menu, chat,
tray, desktop queries, persistence and the LLM tool layer together."""
from __future__ import annotations

import html
import logging
import os
import re
import time
from collections.abc import Mapping
from datetime import datetime
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
from ..llm.tools import AppHooks, ToolBox, ToolError, ToolSpec
from ..llm.watch import WatchManager
from ..logsetup import RateLimitedLogger
from ..persistence import SavedState, save_state
from ..ports import ModelProvider, Platform
from ..settings import Config
from ..sprites.library import SpriteLibrary
from ..ui.ball import BallOverlay
from ..ui.dialogs import PixelDialog
from ..ui.ledges import LedgeFinder
from ..llm import external, models
from ..llm.model_status import model_on_disk
from ..ui import theme
from ..ui.menu import PixelMenu
from ..ui.overlay import CatOverlay
from ..ui.tray import Tray
from ..games.qt_host import QtHost
from .accounts import Accounts
from .chat_ui import ChatUI
from .popups import Popups, paras
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
    def __init__(self, qapp: QApplication, cfg: Config, saved: SavedState, selftest: float = 0.0, *,
                 platform: Platform, providers: Mapping[str, ModelProvider]) -> None:
        super().__init__()
        self.qapp = qapp
        self.platform = platform        # the ports (from pixelcat.bootstrap)
        self.providers = providers      # the AI model providers by key (from pixelcat.bootstrap)
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
        self.creds = external.Credentials(secrets=platform.secrets)  # API keys and sign-ins (keys.dat, encrypted)
        self.accounts = Accounts(providers, saved, self.creds, save=lambda: self.save())   # the providers' accounts
        self.popups = Popups(self.sprites, self.platform.windows, anchor=lambda: self._popup_anchor(),
                             hidden=lambda: self.world_state.hidden_fullscreen)   # popups next to the cat, background work
        self.progress_io = ProgressIO(saved, self.popups, platform.folders, self.menu, self.game,   # export / import
                                      save=lambda: self.save(), stop_minigame=lambda: self.minigames.stop_minigame(),
                                      show_progress=lambda: self._show_progress(),
                                      menu_items=lambda: self.menus.menu_items())
        self.provider_settings = ProviderSettings(   # the providers' menus, key and limit dialogs, sign-ins
            self.accounts, providers, self.creds, saved, cfg, self.popups, self.menu, self.overlay, parent=self,
            save=lambda: self.save(), ui_unit=lambda mon: self.world.ui_unit(mon),
            cat_monitor=lambda: self.world.cat_monitor(),
            menu_items=lambda: self.menus.menu_items(), open_menu_at=lambda keys: self.menus.open_menu_at(keys),
            advice=lambda: self.model_choice.advice(), credit_budget=lambda: self._credit_budget(),
            ext_disconnect=lambda p, only_key=False: self._ext_disconnect(p, only_key=only_key),
            drop_copilot_chat=lambda: self.session.drop_copilot_chat(),
            copilot_quota=lambda force=False: self._copilot_quota(force=force),
            show_model_banner=lambda: self._show_model_banner())
        self._quota_t = 0.0
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

        # chat / tools state
        # the AI: Ollama started and the model loaded in the background, status shown in the chat
        self._status_notifier = QtNotifier()           # (the status changes in a background thread)
        self.model_status = ModelStatus(cfg, may_download=lambda: (self.saved.model_download_ok and
                                                                  self.model_status.model == cfg.model)
                                        or self.model_status.model in self.model_choice.download_ok,
                                        notify=self._status_notifier.changed.emit)
        self._status_notifier.changed.connect(self._on_model_status)
        self.watches = WatchManager(self.own_pid, platform=self.platform)  # wait_for watches in the background
        self.session = AgentSession(   # the AI's turns: requests, tool rounds, tasks, watches and timers waking it up
            self.chat_state, cfg, saved, providers, self.accounts, self.creds, self.provider_settings, self.watches,
            self.brain, self.game, self.minigames, self.overlay, self.tray, self.platform.notifications,
            self.platform.display, self.platform.folders, parent=self, chat_ui=lambda: self.chat_ui,
            model_choice=lambda: self.model_choice, save=lambda: self.save(), sync_tray=lambda: self.world.sync_tray(),
            ext_gate=lambda p: self._ext_gate(p), credit_gate=lambda: self._credit_gate(),
            copilot_credits=lambda credits: self._on_copilot_credits(credits),
            copilot_quota=lambda: self._copilot_quota(), limit_popup=lambda p, info: self._limit_popup(p, info))
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
            open_chat=lambda: self.chat_ui.open_chat(), show_model_banner=lambda: self._show_model_banner(),
            switched=lambda: self._switched_model())
        self.menus = Menus(   # the cat's menu, the look and the cat's name
            saved, cfg, self.brain, self.overlay, self.sprites, self.menu, self.tray, self.hud, self.world_state,
            self.world, self.minigames, self.provider_settings, self.model_choice, self.platform.autostart,
            parent=self, save=lambda: self.save(), chat=lambda: self.chat_ui.chat, quiet=lambda: self.quiet,
            renamed=lambda: setattr(self.session, "copilot_stale", True))
        self.chat_ui = ChatUI(   # the chat window and the saved chats (%APPDATA%\PixelCat\chats)
            self.chat_state, ChatStore(secrets=platform.secrets), saved, self.sprites, self.brain, self.overlay,
            self.world_state, self.world, self.watches, self.model_choice, self.model_status, self.platform.display,
            parent=self, on_user_message=lambda text: self._on_user_message(text),
            cancel_chat=lambda: self.session.cancel_chat(), new_chat=lambda: self.new_chat(),
            set_auto_mode=lambda on: self.set_auto_mode(on), cancel_watch=lambda wid: self.session.cancel_watch(wid),
            open_saved_chat=lambda cid: self.open_saved_chat(cid), save=lambda: self.save(),
            show_model_banner=lambda: self._show_model_banner(), selftest=lambda: self.selftest)

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
            QTimer.singleShot(3000, self.progress_io.tell_unreadable_save)
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
        dlg = PixelDialog("Meow! Start with Windows?",
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

    # ================================================================ external AI models
    def _credit_budget(self) -> external.CreditBudget:
        return external.CreditBudget(self.accounts.ext_p(self.accounts.credit_p),
                                     self.saved.ext_usage.setdefault("copilot_credits", {}))

    def _on_copilot_credits(self, credits: float) -> None:
        cb = self._credit_budget()
        cb.add(credits)
        self.save()
        log.info("copilot: %.3f credits (%.1f this month)", credits, cb.used())

    def _credit_gate(self) -> bool:
        """The credit limit before a Copilot request: fine, or the chat asks (switch / raise / stop)."""
        if self._credit_budget().status() == "ok":
            return True
        assert self.chat_ui.chat is not None
        self.chat_ui.chat.set_busy(False)
        self._credit_card()
        return False

    def _credit_card(self) -> None:
        assert self.chat_ui.chat is not None
        cb = self._credit_budget()
        fc = external.fmt_credits
        used, allowed, nxt = cb.used(), cb.allowed(), cb.next_allowed()
        q = self.accounts.ext_p(self.accounts.credit_p).get("quota")
        plan = (f" (Copilot's own counter for your whole plan: {fc(q.get('used', 0))} of {fc(q['total'])})"
                if isinstance(q, dict) and q.get("total") and not q.get("unlimited") else "")
        text = (f"🐾 <b>Copilot credit limit reached.</b> I've used {fc(used)} of the {fc(allowed)} AI credits you gave "
                f"me for Copilot models this month{html.escape(plan)}. Switch to another model, or keep going with "
                f"{html.escape(models.label(self.model_choice.effective_model()))} and raise my credit limit to {fc(nxt)} until it "
                f"resets {html.escape(external.fmt_reset(cb.reset_at()))}.")
        log.info("copilot credit limit reached: %.1f of %.1f", used, allowed)
        self.chat_ui.chat.ask_choice(text, [("switch", "SWITCH MODEL"), ("raise", f"RAISE TO {fc(nxt).upper()}"),
                                    ("stop", "STOP")], self._credit_answer)

    def _credit_answer(self, key: str) -> None:
        if self.chat_ui.chat is None or not self.session.task_active:
            return
        if key != "raise":
            self._limit_answer(self.accounts.credit_p, key)
            return
        cb = self._credit_budget()
        new = cb.raise_limit()
        self.save()
        self.chat_ui.chat.add("info", f"Okay - my Copilot credit limit is {external.fmt_credits(new)} until it resets "
                              f"{external.fmt_reset(cb.reset_at())}.")
        self.session.request()

    def _copilot_quota(self, force: bool = False) -> None:
        """The Copilot plan's own allowance for the menu (after requests, at most every 2 minutes)."""
        cp = self.accounts.credit_p
        auth = self.accounts.auth(cp)
        if not auth or (not force and time.time() - self._quota_t < 120):
            return
        self._quota_t = time.time()
        prov = self.providers[cp]

        def got(q: Any) -> None:
            if isinstance(q, dict):
                self.accounts.ext_p(cp)["quota"] = q
                self.save()
                if self.menu.isVisible():
                    self.menu.refresh(self.menus.menu_items())
        self.popups.in_background(lambda: prov.quota(self.creds, auth, start=force), got)

    def _ext_disconnect(self, p: str, only_key: bool = False) -> None:
        prov = self.providers[p].info
        auth = self.accounts.auth(p)
        s = self.accounts.ext_p(p)
        if only_key or auth == "key":
            self.creds.set(f"{p}_key", None)
            if s.get("auth") == "key":
                s["auth"] = ""
            msg = prov.key_removed
        elif auth in ("signin", "chatgpt"):
            signin = self.providers[p].signin()
            s["auth"] = ""
            msg = prov.signed_out
            if prov.signin_kind == "browser":
                self.popups.in_background(lambda: signin.sign_out(self.creds), lambda ok: None if ok is True else self.popups.popup(
                    "Signed out", paras(["OpenAI couldn't confirm the sign-out. To be sure, disconnect Pixel Cat "
                                               "in ChatGPT's settings (Connected apps)."]), "OK"))
            elif prov.signin_kind == "device":
                signin.sign_out(self.creds)              # (quick: it forgets the token)
            else:
                self.popups.in_background(lambda: signin.sign_out(self.creds), lambda _r: None)
        else:
            return
        if prov.credits:
            s.pop("quota", None)
        if prov.shows_login and not self.accounts.auth(p):
            s.pop("login", None)
        if prov.keeps_chat:
            self.session.stop_copilot(background=True)
        self.save()
        if not self.accounts.auth(p):
            if (external.parse(self.cfg.model) or ("",))[0] == p:
                self.model_choice.set_default_model(models.DEFAULT)
            if (external.parse(self.chat_state.chat_model) or ("",))[0] == p:
                self.chat_state.chat_model = None
                self.model_status.override = None
                self.model_choice.update_model_chip()
        self.popups.popup(prov.brand, paras([msg]), "OK")

    def _ext_gate(self, p: str) -> bool:
        """The token limit before a request: fine / suggest switching or raising / all used."""
        st = self.accounts.budget(p).status()
        if st == "ok":
            return True
        assert self.chat_ui.chat is not None
        self.chat_ui.chat.set_busy(False)
        if st == "hard":
            self._limit_popup(p, {"why": "own"})
        else:
            self._limit_card(p)
        return False

    def _limit_card(self, p: str) -> None:
        assert self.chat_ui.chat is not None
        b = self.accounts.budget(p)
        prov = self.providers[p].info
        fmt = external.fmt_tokens
        used, allowed, nxt = b.used(), b.allowed(), b.next_allowed()
        text = (f"🐾 <b>Token limit reached.</b> I've used {fmt(used)} of your {fmt(allowed)}-token limit for "
                f"{prov.brand} models {external.PERIOD_WORD[b.period]}. Switch to another model, or keep going with "
                f"{html.escape(models.label(self.model_choice.effective_model()))} and raise the limit to {fmt(nxt)} until it "
                f"resets {html.escape(external.fmt_reset(b.reset_at()))}.")
        log.info("%s token limit reached: %d of %d", p, used, allowed)
        self.chat_ui.chat.ask_choice(text, [("switch", "SWITCH MODEL"), ("raise", f"RAISE TO {fmt(nxt).upper()}"),
                                    ("stop", "STOP")], lambda k, p=p: self._limit_answer(p, k))

    def _limit_answer(self, p: str, key: str) -> None:
        if self.chat_ui.chat is None or not self.session.task_active:
            return
        if key == "raise":
            b = self.accounts.budget(p)
            new = b.raise_limit()
            self.save()
            self.chat_ui.chat.add("info", f"Okay - the {self.providers[p].info.brand} limit is {external.fmt_tokens(new)} until "
                                  f"it resets {external.fmt_reset(b.reset_at())}.")
            self.session.request()
        elif key == "switch":
            self.session.resume_after_switch = True
            self.chat_ui.chat.add("info", "Pick the model to go on with (the button next to SEND).")
            self.model_choice.open_model_menu()
        else:
            self.chat_ui.chat.add("info", "Okay, I stopped here. Say 'continue' when you want me to go on.")
            self.session.end_task()

    def _limit_popup(self, p: str, info: dict[str, Any]) -> None:
        """All tokens are used (our limit), or the provider's own limit: a popup that says when it resets."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        ext = self.accounts.external
        prov = ext.get(p) or external.ANTHROPIC     # (an unknown provider: told Anthropic's way)
        why = info.get("why", "")
        ok, url = "SWITCH MODEL", None
        if why == "own":
            b = self.accounts.budget(p)
            title = "All tokens used"
            lines = [f"All {external.fmt_tokens(b.maximum or b.allowed())} tokens for {prov.brand} models "
                     f"{external.PERIOD_WORD[b.period]} are used up.",
                     f"The limit resets <b>{html.escape(external.fmt_reset(b.reset_at()))}</b>.",
                     "Until then I can go on with a local model or the other provider."]
        elif why == "rate":
            title = "Rate limit reached"
            reset = info.get("reset")
            lines = [f"{prov.name}'s rate limit for your account is reached.",
                     f"It lifts <b>{html.escape(external.fmt_reset(datetime.fromtimestamp(reset)))}</b>." if reset
                     else "It usually lifts within a minute or so."]
        elif why == "plan":
            title = "ChatGPT plan limit reached"
            lines = ["You've reached your ChatGPT plan's usage limit for Pixel Cat.",
                     "ChatGPT's usage page shows when it resets (OpenAI doesn't tell apps that)."]
            ok, url = "MANAGE USAGE", prov.usage_url
        elif why == "copilot":
            title = "Copilot allowance used up"
            q = self.accounts.ext_p(self.accounts.credit_p).get("quota")
            when = self.provider_settings.quota_reset_note(q).replace("RESETS ", "").title() if isinstance(q, dict) else ""
            lines = ["Your Copilot plan's AI credits are used up for now - or a budget your organization set for "
                     "Copilot is reached.",
                     f"The plan's allowance resets <b>{html.escape(when)}</b>." if when else
                     "GitHub's Copilot settings show when it resets.",
                     "Until then I can go on with a local model or another provider."]
            ok, url = "COPILOT SETTINGS", prov.usage_url
        elif why == "credits":
            title = "Credits used up"
            lines = [(ext.get(p) or external.OPENAI).out_of_credits]
            ok, url = "OPEN BILLING", external.BILLING.get(p)
        else:
            title = "Spend limit reached"
            lines = [f"Your {prov.name} organization's spend limit is reached - it's set in your {prov.name} "
                     "account's billing settings."]
            ok, url = "OPEN BILLING", external.BILLING.get(p)
        log.info("%s limit (%s) - task paused", p, why)
        if self.chat_ui.chat is not None:
            self.chat_ui.chat.add("error", re.sub(r"<[^>]+>", "", " ".join(lines)))
        pop = self.popups.popup(title, paras(lines), ok, "OK")

        def answer(yes: bool) -> None:
            if yes and url:
                QDesktopServices.openUrl(QUrl(url))
            if yes and not url and self.session.task_active:
                self.session.resume_after_switch = True
                self.chat_ui.open_chat()
                self.model_choice.open_model_menu()
            elif self.session.task_active and not self.session.resume_after_switch:
                self.session.end_task()
        pop.answered.connect(answer)

    def _switched_model(self) -> None:
        """Another model was picked for this chat (the model menu): a task stopped at the token limit goes on."""
        if self.session.resume_after_switch:            # (the token limit: go on with the new model)
            self.session.resume_after_switch = False
            if self.session.task_active and self.session.worker is None and self.chat_ui.chat is not None:
                QTimer.singleShot(0, self.session.request)

    def _show_model_banner(self) -> None:
        if self.chat_ui.chat is None:
            return
        text = self.model_status.banner()
        ext = external.parse(self.model_choice.effective_model())
        prov = self.providers[ext[0]].info if ext else None
        if (text is None and prov is not None and prov.signin_kind == "browser"
                and self.accounts.auth(prov.key) == "chatgpt"):
            self.chat_ui.chat.set_model_banner("Using ChatGPT plan", ("Manage usage", prov.usage_url))
        else:
            self.chat_ui.chat.set_model_banner(text)

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
                self.set_auto_mode(not self.saved.auto_mode)
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

    # ================================================================ where the cat is
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

    # ================================================================ chat
    def new_chat(self) -> None:
        """Forget the conversation (frees the model's context) and start fresh.  The one so far
        stays in the chat history."""
        self.save()
        if self.session.worker is not None:
            self.session.worker.cancel()
            self.session.worker = None
        self.session.end_task(cancelled=True)
        self.session.agent.snap = None
        self.session.agent.shot = None
        self.session.agent.last_hwnd = 0
        self.chat_state.history.clear()
        self.chat_ui.chat_id, self.chat_ui.chat_created, self.chat_ui.chat_sig = None, 0.0, ""
        self.session.drop_copilot_chat()
        if self.chat_state.chat_model is not None or self.chat_state.chat_effort is not None:   # (a new chat: the defaults)
            self.chat_state.chat_effort = None
            if self.chat_state.chat_model is not None:
                self.chat_state.chat_model = None
                self.model_status.override = None
                self.model_status.ensure(force=True)
            self.model_choice.update_model_chip()
        self.session._sent = None
        self.session._pending_calls.clear()
        self.session._tool_round = 0
        self.session._had_results = False
        self.session._long_hint_shown = False
        self.session._raw = ""
        if self.chat_ui.chat is not None:
            self.chat_ui.chat.reset()
        log.info("new chat started")

    # ---------------------------------------------------------------- the chat history
    def open_saved_chat(self, cid: str) -> None:
        """Reopen a saved chat: what it showed, what the AI remembers, its model and effort."""
        chat = self.chat_ui.ensure_chat()
        if cid == self.chat_ui.chat_id:
            chat.hide_history()
            return
        data = self.chat_ui.chats.load(cid)
        if data is None:
            chat.set_history(self.chat_ui.history_items(), self.chat_ui.chat_id)
            chat.add("error", "That chat can't be opened any more - it was deleted or is damaged.")
            return
        self.new_chat()                                  # (stops what runs; the open chat stays saved)
        self.chat_state.history[:] = [dict(m) for m in data.get("history") or [] if isinstance(m, dict) and m.get("role")]
        self.chat_ui.chat_id, self.chat_ui.chat_created = cid, float(data.get("created") or time.time())
        notes = []
        tag = data.get("chat_model")
        if isinstance(tag, str) and tag:
            ext = external.parse(tag)
            if (self.accounts.auth(ext[0]) if ext else models.find(tag) is not None and model_on_disk(tag)):
                self.chat_state.chat_model = tag
                self.model_status.override = tag
                self.model_status.ensure(force=True)
            else:
                notes.append(f"It used {models.label(tag)}, which isn't available now - so it uses the default, "
                             f"{models.label(self.cfg.model)}.")
        eff = data.get("chat_effort")
        self.chat_state.chat_effort = eff if eff in external.EFFORTS else None
        self.model_choice.update_model_chip()
        self._show_model_banner()
        chat.load([(r, t) for r, t in data.get("msgs") or [] if isinstance(r, str) and isinstance(t, str)])
        when = datetime.fromtimestamp(float(data.get("updated") or time.time()))
        chat.add("info", f"🕘 Our chat from {when:%d %b %Y, %H:%M} - I remember it, just keep talking. "
                         + " ".join(notes))
        self.chat_ui.chat_sig = self.chat_ui.chat_signature()          # (only opened, not changed: it keeps its place)
        log.info("saved chat %s reopened (%d messages)", cid, len(self.chat_state.history))

    def set_auto_mode(self, on: bool) -> None:
        self.saved.auto_mode = on
        self.save()
        if self.chat_ui.chat is not None:
            self.chat_ui.chat.set_auto_mode(on)
            self.chat_ui.chat.add("info", "⚡ Auto mode ON: I'll just do what you ask, no permission cards. "
                                  "(Closing more than a few windows at once still asks.)" if on else
                          "Auto mode OFF: I'll ask before doing things again 🐾")
        log.info("chat auto mode %s", "on" if on else "off")

    _FORCE_RE = re.compile(r"^/force(?:\s+(?:(end task|end|kill|close|quit|stop|terminate)|(open|start|run|launch)))?"
                           r"(?:\s+(.*))?$", re.I | re.S)

    def _force_command(self, text: str) -> None:
        """/force end <app>: end it like Task Manager. /force open <app>: run as admin.
        Parsed here directly - the AI is never involved in /force."""
        assert self.chat_ui.chat is not None
        m = self._FORCE_RE.match(text.strip())
        target = (m.group(3) or "").strip() if m else ""
        target = re.sub(r"\s+(as|with)\s+(admin(istrator)?|force)$", "", target, flags=re.I)
        target = re.sub(r"^(the\s+)?(app\s+)?", "", target, flags=re.I).strip().strip("\"'")
        if not m or not target:
            self.chat_ui.chat.add("info", "Usage: /force end <app> (like Task Manager's End task, e.g. /force end teams) "
                                  "or /force open <app> (run as administrator).")
            return
        tool = "run_as_admin" if m.group(2) else "force_end"
        try:
            spec, args = self.session.toolbox.prepare(tool, {"name" if tool == "run_as_admin" else "target": target},
                                              internal=True)
        except ToolError as exc:
            self.chat_ui.chat.add("error", f"🙀 {exc}")
            return
        # Ending your own non-admin app may run right away in auto mode; anything that uses
        # admin rights (run as admin, or ending admin processes) is ALWAYS asked first.
        if self.saved.auto_mode and tool == "force_end":
            self._run_local(spec, args, auto=True)
            return
        self.chat_ui.chat.ask_confirm(self.session.toolbox.describe(spec, args) + "?",
                              lambda ans, sp=spec, a=args: self._run_local(sp, a) if ans == "yes"
                              else self.chat_ui.chat.add("info", "Okay, I won't."))

    def _run_local(self, spec: ToolSpec, args: dict[str, Any], auto: bool = False) -> None:
        assert self.chat_ui.chat is not None
        try:
            result = self.session.toolbox.run(spec, args)
            self.chat_ui.chat.add("info", result + (" (auto)" if auto else ""))
            self.brain.show_bubble("bubble_exclaim", 1.5)
        except Exception as exc:  # noqa: BLE001
            log.exception("/force %s failed", spec.name)
            self.chat_ui.chat.add("error", f"😿 {spec.name} failed: {exc}")
            return
        denied = args.get("_denied") or []
        if denied:
            self.chat_ui.chat.ask_confirm(f"{len(denied)} process(es) of {args['_plan'].label} can only be ended with "
                                  "ADMIN RIGHTS. Use admin rights to end them (Windows will ask you too)?",
                                  lambda ans, d=list(denied): self._force_admin(d, ans))

    def _force_admin(self, denied: list[int], answer: str) -> None:
        assert self.chat_ui.chat is not None
        if answer != "yes":
            self.chat_ui.chat.add("info", "Okay, no admin rights - those processes keep running.")
            return
        try:
            self.chat_ui.chat.add("info", self.session.toolbox.force_end_admin(denied))
        except Exception as exc:  # noqa: BLE001
            log.exception("elevated force-end failed")
            self.chat_ui.chat.add("error", f"😿 that didn't work: {exc}")

    def _local_command(self, text: str) -> bool:
        """Chat commands handled by the app itself (never sent to the model)."""
        cmd = text.strip().lower()
        if cmd == "/force" or cmd.startswith("/force "):
            self._force_command(text)
            return True
        if cmd in ("/auto", "/auto on", "/auto off"):
            self.set_auto_mode(cmd != "/auto off" and (cmd == "/auto on" or not self.saved.auto_mode))
            return True
        if cmd in ("/new", "/clear"):
            self.new_chat()
            return True
        if cmd in ("/history", "/chats"):
            if not self.chat_ui.ensure_chat().history_shown:
                self.chat_ui.toggle_history()
            return True
        if cmd == "/effort" or cmd.startswith("/effort "):
            assert self.chat_ui.chat is not None
            arg = cmd[7:].strip()
            arg = {"default": "", "normal": "medium", "med": "medium", "extra high": "xhigh", "extra": "xhigh",
                   "x-high": "xhigh", "maximum": "max"}.get(arg, arg)
            levels = self.model_choice.effort_levels()
            if cmd == "/effort" or (arg and arg not in external.EFFORTS):
                now = external.EFFORTS.get(self.chat_state.chat_effort or "", "") or \
                    f"the default ({external.EFFORTS.get(self.model_choice.default_effort(), 'Medium')})"
                pick = ", ".join(e for e in levels) if levels else "none - this model has no effort levels"
                self.chat_ui.chat.add("info", f"This chat's effort: {now}. /effort LEVEL changes it for this chat "
                                      f"({pick}), /effort default goes back. Or use the model button next to SEND.")
            elif arg and levels is not None and arg not in levels:
                self.chat_ui.chat.add("info", f"{models.label(self.model_choice.effective_model())} has these effort levels: "
                                      f"{', '.join(levels)}.")
            else:
                self.model_choice.set_chat_effort(arg or None)
            return True
        if cmd == "/permissions":
            allowed = ", ".join(self.saved.auto_approved_tools) or "none"
            assert self.chat_ui.chat is not None
            self.chat_ui.chat.add("info", f"Actions I may do without asking: {allowed}. Type /permissions reset to ask again every time.")
            return True
        if cmd == "/permissions reset":
            self.saved.auto_approved_tools.clear()
            self.save()
            assert self.chat_ui.chat is not None
            self.chat_ui.chat.add("info", "Okay! I'll ask before every action again 🐾")
            return True
        if cmd == "/model" or cmd.startswith("/model "):
            assert self.chat_ui.chat is not None
            arg = cmd[6:].strip()
            if not arg:
                self.chat_ui.chat.add("info", f"This chat uses {models.label(self.model_choice.effective_model())}. "
                                      "Pick another for this chat with the model button next to SEND or /model NAME "
                                      "(" + ", ".join(m.short for m in models.MODELS) + "); /model default goes back. "
                                      "The default model is in Settings -> AI model.")
            else:
                self.model_choice.choose_chat_model(None if arg == "default" else arg)
            return True
        if cmd in ("/getmodel", "/get model", "/downloadmodel", "/download-model"):
            assert self.chat_ui.chat is not None
            if external.parse(self.cfg.model):
                self.chat_ui.chat.add("info", f"The default model is {models.label(self.cfg.model)} - an online one, so "
                                      "there's nothing to download.")
                return True
            self.saved.model_download_ok = True
            self.save()
            self.chat_ui.chat.add("info", f"Okay! I'll download the AI model {self.cfg.model} now - the bar above shows "
                                  "how far it is. You can keep using your PC meanwhile.")
            self.model_status.ensure(force=True)
            return True
        if cmd in ("/watches", "/watching", "/waits"):
            assert self.chat_ui.chat is not None
            act = self.watches.active()
            self.chat_ui.chat.add("info", ("I'm watching for: " + "; ".join(f"#{w.id} {w.short()}" for w in act)
                                   + ". /unwatch stops all, /unwatch <number> one.") if act else
                          "I'm not watching anything right now.")
            return True
        if cmd == "/unwatch" or cmd.startswith("/unwatch "):
            assert self.chat_ui.chat is not None
            m = re.search(r"\d+", cmd)
            gone = self.watches.cancel(int(m.group()) if m else None)
            self.chat_ui.chat.add("info", ("Stopped watching for: " + "; ".join(w.short() for w in gone)) if gone else
                          "Nothing to stop - I'm not watching that.")
            return True
        if cmd in ("/help", "/?"):
            assert self.chat_ui.chat is not None
            self.chat_ui.chat.add("info", "Commands: /new (fresh chat), /history (our earlier chats), /auto on|off "
                                  "(no permission cards), "
                                  "/force end <app> (like Task Manager), /force open <app> (as admin), "
                                  "/watches, /unwatch, /model (AI model for this chat), /effort (how hard it "
                                  "thinks, this chat), /getmodel (download "
                                  "the AI model), /permissions, "
                                  "/permissions reset. "
                                  "Ask me to open, close, switch or minimize apps, search the web, control music, "
                                  "take screenshots, set timers, check the system and more! I can also SEE your "
                                  "screen and use apps for you (click, type, shortcuts) - e.g. 'open Notepad and "
                                  "write a packing list'. I can also wait for things in the background - "
                                  "'tell me when Anna writes on Teams', 'when the download is done, open it', "
                                  "'at 17:00 remind me and open Outlook'. Press Esc anywhere (or STOP) to stop me.")
            return True
        return False

    _CONTINUE_RE = re.compile(r"^\s*(?:(?:ok(?:ay)?|yes|ja|go|please|bitte)[\s,!.]*)*(?:continue|go on|keep going|"
                              r"carry on|resume|proceed|weiter|mach weiter|weitermachen|fortfahren|los)"
                              r"\b(?:(?![?]).){0,60}$", re.I)   # e.g. "continue with the plan", not questions

    def _on_user_message(self, text: str) -> None:
        if self._local_command(text):
            return
        if self.session.copilot_stale:                  # (between tasks: nothing of it is running)
            self.session.copilot_stale = False
            self.session.drop_copilot_chat()
        self.session.resume_after_switch = False
        if self.chat_ui.chat is not None:
            self.chat_ui.chat.drop_choice()                      # (a token-limit question nobody answered)
        msg: dict[str, Any] = {"role": "user", "content": text}
        if self._CONTINUE_RE.match(text) and self.chat_state.history:
            msg["_continue"] = True   # same task: its original request stays at the start of the context
        self.chat_state.history.append(msg)
        self.session.reset_task_counters()
        self.session.begin_task()
        if getattr(self.session, "_forgotten", 0) > 0 and not getattr(self.session, "_long_hint_shown", False):
            self.session._long_hint_shown = True
            assert self.chat_ui.chat is not None
            self.chat_ui.chat.add("info", "Psst, our chat is getting long, so I only remember the latest part. "
                                  "Press ＋ (or Ctrl+N) for a fresh one 🐾")
        self.session.request()

    def _on_model_status(self) -> None:
        if self.chat_ui.chat is not None:
            self._show_model_banner()
        self.world.sync_tray()

