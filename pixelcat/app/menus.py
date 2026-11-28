"""The cat's menu (right click): Pause, Chat, the Minigames, Cosmetics (Cat Pass, skins, hats) and Settings, and
the settings it changes itself - the look (style, accent, size, font) and the cat's name."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QColor, QPixmap

from .. import games as minigames
from .. import naming, progress
from ..domain import WINDOWS, monitor_at
from ..engine.states import S
from ..sprites import hats as hat_art
from ..ui import theme
from ..ui.menu import MenuItem

if TYPE_CHECKING:
    from ..engine.brain import Brain
    from ..games.qt_host import HudWin
    from ..persistence import SavedState
    from ..ports.storage import AutostartPort
    from ..settings import Config
    from ..sprites.library import SpriteLibrary
    from ..ui.chat import ChatWindow
    from ..ui.menu import PixelMenu
    from ..ui.overlay import CatOverlay
    from ..ui.tray import Tray
    from .minigames import Minigames
    from .models_menu import ModelChoice
    from .provider_settings import ProviderSettings
    from .state import WorldState
    from .world import World

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)


class Menus(QObject):
    """Builds and opens the cat's menu (``menu``), and the settings it changes itself.

    The controller connects the overlay's ``menu_requested`` to ``open_menu`` and the menu's ``triggered`` to its
    own dispatcher, which calls ``set_look`` and ``ask_name``; the theme calls ``on_style_changed``.  Asked at call
    time: ``save()``, ``chat()`` (the chat window, if made yet), ``quiet()`` (quiet mode) and ``renamed()`` (the
    AI hears the new name: Copilot's session is made again)."""

    def __init__(self, saved: SavedState, cfg: Config, brain: Brain, overlay: CatOverlay, sprites: SpriteLibrary,
                 menu: PixelMenu, tray: Tray, hud: HudWin | None, world_state: WorldState, world: World,
                 minigames: Minigames, provider_settings: ProviderSettings, model_choice: ModelChoice,
                 autostart: AutostartPort, *, parent: QObject | None = None, save: Callable[[], None],
                 chat: Callable[[], ChatWindow | None], quiet: Callable[[], bool],
                 renamed: Callable[[], None], start_with: str = WINDOWS.start_with) -> None:
        super().__init__(parent)
        self.saved = saved
        self.cfg = cfg
        self.brain = brain
        self.overlay = overlay
        self.sprites = sprites
        self.menu = menu
        self.tray = tray
        self.hud = hud
        self.world_state = world_state
        self.world = world
        self.minigames = minigames
        self.provider_settings = provider_settings
        self.model_choice = model_choice
        self.autostart = autostart
        self._save = save
        self._chat = chat
        self._quiet = quiet
        self._renamed = renamed
        self.start_with = start_with      # the autostart item: "Start with Windows" (the platform's words)

    def open_menu(self) -> None:
        if self.world_state.hidden_fullscreen:
            return
        if self.overlay.is_pressing() or self.brain.state == S.HELD:
            self.overlay.cancel_press()  # never open a popup while the cat is carried
            if self.brain.state == S.HELD:
                self.brain.drop(0.0, 0.0)
            return
        b = self.brain
        self.menu.items = self.menu_items()
        self.menu.subtitle = b.state.value.replace("_", " ")
        mon = monitor_at(self.world_state.monitors, b.x, b.y - 1)
        self.menu.popup_near(self.overlay.cat_rect(), mon.work, self.world.ui_unit(mon))
        QTimer.singleShot(60, self.world.refresh_windows)       # the menu is something to sit on, too

    def menu_items(self) -> list[MenuItem]:
        """Pause, Chat and Quit on top; everything else in the Minigames, Cosmetics and Settings sections."""
        b = self.brain
        games = self._game_items()
        settings = [
            MenuItem("set_name", "Name...", "paw", note=naming.custom().upper()),
            MenuItem("set_style", "Style", "paint", children=[
                MenuItem(f"style:{k}", s.name, radio=True, checked=theme.choice("style") == k)
                for k, s in theme.STYLES.items()]),
            MenuItem("set_accent", "Accent color", "dot", children=[
                MenuItem(f"accent:{k}", "Style's own" if k == "default" else k.capitalize(),
                         swatch=QColor(theme.STYLES[theme.choice("style")].accent if v is None else v[0]),
                         checked=theme.choice("accent") == k)
                for k, v in theme.ACCENTS.items()]),
            MenuItem("set_size", "Size", "plus", children=[
                MenuItem(f"size:{k}", k.capitalize(), radio=True, checked=theme.choice("size") == k)
                for k in theme.SIZES]),
            MenuItem("set_font", "Font", "chat", children=[
                MenuItem(f"font:{k}", {"default": "Style's own", "pixel": "Pixel", "clean": "Clean"}[k], radio=True,
                         checked=theme.choice("font") == k) for k in theme.FONTS]),
            MenuItem("set_model", "AI model", "bolt_on", children=self.model_choice.model_items("model:", self.cfg.model)
                     + [self.provider_settings.external_menu()]),
            MenuItem("progress", "Your progress", "disk", children=[
                MenuItem("export", "Export progress...", "export"),
                MenuItem("import", "Import progress...", "import")]),
            MenuItem("quiet", "Quiet mode", checked=self._quiet(), separator_before=True),
            MenuItem("autostart", self.start_with, checked=self.autostart.enabled()),
            MenuItem("automode", "Chat auto mode", checked=self.saved.auto_mode),
            MenuItem("preload_ai", "Load AI at start", checked=self.cfg.preload_on_start),
            MenuItem("explore", "Sit inside windows", checked=self.cfg.explore_windows)]
        return [
            MenuItem("pause", "Resume" if b.paused else "Pause", "play" if b.paused else "pause"),
            MenuItem("chat", "Chat", "chat"),
            MenuItem("games", "Minigames", "gamepad", children=games, separator_before=True),
            MenuItem("cosmetics", "Cosmetics", "hat", children=self._cosmetic_items()),
            MenuItem("settings", "Settings", "gear", children=settings),
            MenuItem("quit", "Quit", "power", separator_before=True),
        ]

    def _game_items(self) -> list[MenuItem]:
        """Every minigame: locked ones show the Cat Pass stage that unlocks them."""
        st = self.saved
        running = self.minigames.running_game()
        out: list[MenuItem] = []
        for key in minigames.ORDER:
            title = minigames.title(key)
            if key == running:
                out.append(MenuItem("stopgame", "Stop playing ball" if key == "ball" else f"Stop {title}", "close"))
            elif not progress.is_unlocked(st.xp, "game", key):
                out.append(MenuItem(f"game:{key}", title, minigames.ICONS[key], locked=True,
                                    note=f"STAGE {progress.stage_of('game', key)}"))
            else:
                plays = st.plays if key == "ball" else st.game_plays.get(key, 0)
                out.append(MenuItem(f"game:{key}", title, minigames.ICONS[key], note="NEW!" if not plays
                                    else self._best_note(key)))
        out.append(MenuItem("feed", "Feed", "fish", separator_before=True))
        return out

    def _best_note(self, key: str) -> str:
        if key == "ball":
            return f"BEST {self.saved.best_round}" if self.saved.best_round else ""
        best = self.saved.game_best.get(key)
        if best is None:
            return ""
        return f"BEST {best:g} S" if minigames.GAMES[key].lower_is_better else f"BEST {best:g}"

    def _cosmetic_items(self) -> list[MenuItem]:
        """The Cat Pass (stages and what they unlock), skins and hats."""
        st = self.saved
        ps = progress.state(st.xp)
        u = self.minigames.game_unit()
        hat_scale = max(1, (9 * u + 2) // 13)
        stages = [MenuItem("pass_total", f"{st.xp} XP in total", "star", info=True,
                           note=(f"NEXT STAGE IN {ps.cost - ps.into} XP" if ps.cost else "ALL DONE!"))]
        hi = min(progress.MAX_STAGE, max(ps.stage + 5, 8))
        lo = max(2, hi - 7)
        for n in range(lo, hi + 1):
            kind, key = progress.REWARDS[n - 2]
            got = n <= ps.stage
            need = progress.xp_to_reach(n) - st.xp
            stages.append(MenuItem(f"pass:{n}", f"{n}. {progress.name_of(kind, key)}", "star", info=True,
                                   locked=not got, note=progress.kind_label(kind, key).upper() if got
                                   else f"{need} XP",
                                   picture=self.reward_picture(kind, key, u, hat_scale)))
        skins = []
        for key, name in progress.SKINS.items():
            ok = progress.is_unlocked(st.xp, "skin", key)
            skins.append(MenuItem(f"skin:{key}", name, radio=True, checked=st.skin == key, locked=not ok,
                                  note="" if ok else f"STAGE {progress.stage_of('skin', key)}",
                                  picture=self.sprites.skin_icon(key, 9 * u)))
        hats = [MenuItem("hat:", "No hat", radio=True, checked=not st.hat)]
        for group, title in hat_art.GROUPS.items():      # 115 hats: in groups, in unlock order
            keys = sorted((k for k, h in hat_art.HATS.items() if h.group == group),
                          key=lambda k: progress.stage_of("hat", k))
            rows, got = [], 0
            for key in keys:
                ok = progress.is_unlocked(st.xp, "hat", key)
                got += ok
                tier = hat_art.TIERS.get(hat_art.HATS[key].tier, "")
                note = (tier.upper() if tier in ("rare", "epic", "legendary") else "") if ok \
                    else f"STAGE {progress.stage_of('hat', key)}"
                rows.append(MenuItem(f"hat:{key}", progress.HATS[key], radio=True, checked=st.hat == key,
                                     locked=not ok, note=note, picture=self.sprites.hat(key, hat_scale, False)))
            worn = " - WEARING" if st.hat in keys else ""
            hats.append(MenuItem(f"hatgroup:{group}", title, "hat", note=f"{got}/{len(keys)}{worn}", children=rows))
        return [MenuItem("pass", "Cat Pass", "star", note=f"STAGE {ps.stage}", bar=ps.fraction, children=stages),
                MenuItem("skins", "Skins", "skin", children=skins),
                MenuItem("hats", "Hats", "hat", children=hats)]

    def reward_picture(self, kind: str, key: str, u: int, hat_scale: int) -> QPixmap | None:
        try:
            if kind == "skin":
                return self.sprites.skin_icon(key, 9 * u)
            if kind == "hat":
                return self.sprites.hat(key, hat_scale, False)
            return self.sprites.icon(minigames.ICONS.get(key, "gamepad"), u)
        except Exception:  # noqa: BLE001
            return None

    def on_style_changed(self) -> None:
        """Settings -> Style/Accent/Size/Font: every window takes the new look at once."""
        if self._chat() is not None:
            self._chat().restyle()
        self.tray.restyle()
        if self.menu.isVisible():
            mon = monitor_at(self.world_state.monitors, self.brain.x, self.brain.y - 1)
            self.menu.refresh(self.menu_items(), self.world.ui_unit(mon))
        else:
            self.menu.update()
        hud = getattr(self, "hud", None)
        if hud is not None:
            hud.restyle()

    def set_look(self, kind: str, value: str) -> None:
        setattr(self.saved, f"ui_{kind}", value)
        self._save()
        theme.configure(**{kind: value})
        log.info("look: %s = %s", kind, value)

    def ask_name(self) -> None:
        """Settings -> Name...: the cat's own name (empty: none, as before)."""
        from ..ui.dialogs import NameDialog

        dlg = NameDialog("Your cat's name", "What should I be called? The chat, the menu, the tray and the "
                         f"notifications show it - up to {naming.MAX_LEN} letters, numbers or spaces. "
                         f"Leave it empty and I'm just {naming.APP} again.", naming.custom(), naming.MAX_LEN)
        dlg.submitted.connect(lambda text, d=dlg: self._check_name(text, d))
        self.provider_settings.show_amount_dialog(dlg)

    def _check_name(self, text: str, dlg: Any) -> None:
        name, problem = naming.check(text)
        try:
            dlg.checked(not problem, problem)
        except RuntimeError:
            return                                       # (the dialog was closed meanwhile)
        if not problem:
            self.set_cat_name(name)

    def set_cat_name(self, name: str) -> None:
        """A new name for the cat: everything that shows it follows at once; the AI hears it with the
        next message (Copilot's session keeps its instructions, so it gets a fresh one then)."""
        self.saved.cat_name = naming.set_name(name)
        self._save()
        self.menu.title = naming.title().upper()
        self.tray.rename()
        if self._chat() is not None:
            self._chat().rename()
        self._renamed()
        log.info("the cat's name: %s", self.saved.cat_name or "(none)")

    def open_menu_at(self, keys: list[str]) -> None:
        self.open_menu()
        self.menu.expanded = set(keys)
        self.menu.refresh(self.menu_items())
