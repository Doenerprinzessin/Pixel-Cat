"""The AI model choice: the default model (Settings -> AI model), and the chat's model button - a model and
an effort for this chat only, what suits this PC, and the button's label."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject

from ..domain import monitor_at
from ..llm import external, models
from ..llm.model_status import model_on_disk
from ..ui.menu import MenuItem

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..engine.brain import Brain
    from ..llm.model_status import ModelStatus
    from ..persistence import SavedState
    from ..ports import ModelProvider
    from ..ports.desktop import SystemPort
    from ..settings import Config
    from ..ui.chat import ChatWindow
    from ..ui.menu import PixelMenu
    from .accounts import Accounts
    from .state import ChatState, WorldState
    from .world import World

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)

# a chat's effort with a local model: how much it thinks (shown next to each level)
LOCAL_EFFORTS = {"low": "NO THINKING", "medium": "THINKS WHEN PLANNING", "high": "ALWAYS THINKS"}
EFFORT_SHORT = {"low": "LOW", "medium": "MED", "high": "HIGH", "xhigh": "XHIGH", "max": "MAX"}   # the model button


class ModelChoice(QObject):
    """Which model a chat uses: the default (``cfg.model``) unless the chat picked its own (``chat_state``, shared
    with the chat).  ``download_ok``: the models the user said yes to downloading (the model status asks).

    The controller connects the model menu's ``triggered`` to ``on_model_menu`` and the chat's model button to
    ``open_model_menu``.  Asked at call time: ``chat()`` (the chat window, if made yet), ``open_chat()``,
    ``show_model_banner()`` and ``switched()`` (another model was picked: a task stopped at the token limit goes on)."""

    def __init__(self, chat_state: ChatState, cfg: Config, saved: SavedState, providers: Mapping[str, ModelProvider],
                 accounts: Accounts, model_status: ModelStatus, model_menu: PixelMenu, brain: Brain,
                 world_state: WorldState, world: World, system: SystemPort, *, parent: QObject | None = None,
                 chat: Callable[[], ChatWindow | None], open_chat: Callable[[], None],
                 show_model_banner: Callable[[], None], switched: Callable[[], None]) -> None:
        super().__init__(parent)
        self.chat_state = chat_state
        self.cfg = cfg
        self.saved = saved
        self.providers = providers
        self.accounts = accounts
        self.model_status = model_status
        self.model_menu = model_menu
        self.brain = brain
        self.world_state = world_state
        self.world = world
        self.system = system
        self._chat = chat
        self._open_chat = open_chat
        self._show_model_banner = show_model_banner
        self._switched = switched
        self.download_ok: set[str] = set()
        self._pc_advice: Any = None
        self._hw: Any = None

    def advice(self) -> Any:
        """What suits this PC (looked at once)."""
        if getattr(self, "_pc_advice", None) is None:
            self._hw = self.system.hardware()
            self._pc_advice = models.advise(self._hw)
        return self._pc_advice

    def model_fit(self, tag: str) -> str:
        m = models.find(tag)
        if m is None:
            return "ok"
        self.advice()
        return models.fit(m, self._hw)

    def model_items(self, prefix: str, current: str) -> list[MenuItem]:
        """One radio item per model: name, download size, whether it is here and how it suits this PC."""
        adv = self.advice()
        best = adv.model.tag if adv.model is not None and not adv.online else ""
        out = []
        for m in models.MODELS:
            note = f"{m.download_gb:g} GB"
            if model_on_disk(m.tag):
                note += " READY"
            fit = models.fit(m, self._hw)
            if fit == "too_big":
                note += " TOO BIG"
            elif fit == "slow":
                note += " SLOW"
            if m.tag == best:
                note += " BEST"
            out.append(MenuItem(prefix + m.tag, f"{m.name} {m.short}", radio=True,
                                checked=models.find(current) is m if models.find(current) else False, note=note))
        return out

    def effective_model(self) -> str:
        return self.chat_state.chat_model or self.cfg.model

    def update_model_chip(self) -> None:
        if self._chat() is None:
            return
        m = models.find(self.effective_model())
        short = m.short if m else external.short_label(self.effective_model())
        if self.chat_state.chat_effort:
            short += " · " + EFFORT_SHORT.get(self.chat_state.chat_effort, self.chat_state.chat_effort.upper())
        tip = (f"This chat uses {models.label(self.effective_model())}"
               + (f" with {external.EFFORTS.get(self.chat_state.chat_effort, self.chat_state.chat_effort)} effort"
                  if self.chat_state.chat_effort else "")
               + f" - click to change the model or the effort. The default is {models.label(self.cfg.model)} "
               "(Settings -> AI model).")
        self._chat().set_model_chip(short, self.chat_state.chat_model is not None
                                    or self.chat_state.chat_effort is not None, tip)

    def choose_default_model(self, tag: str) -> None:
        if tag == self.cfg.model:
            return
        ext = external.parse(tag)
        if ext:
            if self.accounts.auth(ext[0]):
                self.set_default_model(tag)
            return
        if model_on_disk(tag):
            self.set_default_model(tag)
            return
        m = models.find(tag)
        self._open_chat()
        assert self._chat() is not None
        size = f" ({m.download_gb:g} GB)" if m else ""
        self._chat().ask_confirm(f"download {models.label(tag)}{size} and make it the default model",
                                 lambda ans, t=tag: self.set_default_model(t, download=True) if ans == "yes"
                                 else self._chat().add("info",
                                                       f"Okay, the default stays {models.label(self.cfg.model)}."))

    def set_default_model(self, tag: str, download: bool = False) -> None:
        from ..settings import save_user_setting

        self.cfg.model = tag
        save_user_setting("model", tag)
        if models.find(tag) is not None and self.cfg.ai_mode != "local":
            self.cfg.ai_mode = "local"
            save_user_setting("ai_mode", "local")
        if download:
            self.download_ok.add(tag)
        log.info("default model: %s", tag)
        if self._chat() is not None:
            self._chat().add("info", f"Default AI model: {models.label(tag)}" +
                             (" - downloading it now, the bar above shows how far it is." if download else "."))
        self.model_status.ensure(force=True)
        self.update_model_chip()

    def open_model_menu(self) -> None:
        """The chat's model button: pick a model (and the effort) for this chat only."""
        if self._chat() is None:
            return
        self.model_menu.items = self.model_menu_items()
        g = self._chat().model_btn.mapToGlobal(self._chat().model_btn.rect().topLeft())
        mon = monitor_at(self.world_state.monitors, self.brain.x, self.brain.y - 1)
        r = (g.x(), g.y(), g.x() + self._chat().model_btn.width(), g.y() + self._chat().model_btn.height())
        self.model_menu.popup_near(r, mon.work, self.world.ui_unit(mon))

    def model_menu_items(self) -> list[MenuItem]:
        items = [MenuItem("chatmodel:default", f"Default ({models.label(self.cfg.model)})", radio=True,
                          checked=self.chat_state.chat_model is None)]
        items += self.model_items("chatmodel:", self.chat_state.chat_model or "")
        items += self.ext_chat_items()
        items.append(self._effort_menu())
        return items

    def on_model_menu(self, key: str) -> None:
        if key.startswith("chateffort:"):
            level = key.split(":", 1)[1]
            if level != "none":
                self.set_chat_effort(None if level == "default" else level)
            if self.model_menu.isVisible():
                self.model_menu.refresh(self.model_menu_items())
            return
        if not key.startswith("chatmodel:"):
            return
        self.model_menu.close()
        tag = key.split(":", 1)[1]
        self.choose_chat_model(None if tag == "default" else tag)

    def effort_levels(self) -> list[str] | None:
        """The effort levels the chat's model has (None: it has none to pick)."""
        ext = external.parse(self.effective_model())
        if not ext:
            return self.accounts.local.effort_levels({})
        p, mid = ext
        return self.providers[p].effort_levels(self.accounts.info(p, mid))

    def default_effort(self) -> str:
        """The effort a chat has without its own: Settings (online models) or the thinking setting."""
        if external.parse(self.effective_model()):
            return str(self.saved.ext_settings.get("effort", "medium"))
        mode = self.cfg.think
        if isinstance(mode, str) and mode.lower() != "auto":
            mode = mode.lower() in ("true", "on", "yes")
        return "high" if mode is True else "low" if mode is False else "medium"

    def _effort_menu(self) -> MenuItem:
        levels = self.effort_levels()
        local = not external.parse(self.effective_model())
        cur = self.chat_state.chat_effort
        if levels is None:
            kids = [MenuItem("chateffort:none", "This model has no effort levels", info=True)]
        else:
            dflt = external.EFFORTS.get(self.default_effort(), "Medium")
            kids = [MenuItem("chateffort:default", f"Default ({dflt})", radio=True, checked=cur is None)]
            kids += [MenuItem(f"chateffort:{e}", external.EFFORTS[e], radio=True, checked=cur == e,
                              note=LOCAL_EFFORTS[e] if local else "") for e in levels]
        return MenuItem("chateffort", "Effort", "bolt_on", children=kids, separator_before=True,
                        note=external.EFFORTS[cur].upper() if cur in external.EFFORTS else "DEFAULT")

    def set_chat_effort(self, level: str | None) -> None:
        if level is not None and level not in external.EFFORTS:
            return
        if level == self.chat_state.chat_effort:
            return
        self.chat_state.chat_effort = level
        self.update_model_chip()
        log.info("chat effort: %s", level or "default")
        if self._chat() is None:
            return
        if level is None:
            self._chat().add("info", f"This chat uses the default effort again "
                                     f"({external.EFFORTS.get(self.default_effort(), 'Medium')}).")
            return
        local = not external.parse(self.effective_model())
        how = {"low": " - no thinking, the fastest answers", "medium": " - I think when I plan a task",
               "high": " - I always think first"}.get(level, " - I always think first") if local else ""
        if not local and not self.saved.ext_settings.get("thinking", True):
            how = " (thinking is on for this chat, although it's off in Settings)"
        self._chat().add("info", f"This chat now uses {external.EFFORTS[level]} effort{how}. A new chat goes back "
                                 "to the default.")

    def ext_think_effort(self) -> tuple[bool, str]:
        """Thinking and effort for an online model: this chat's effort, else Settings."""
        st = self.saved.ext_settings
        if self.chat_state.chat_effort is not None:
            return True, self.chat_state.chat_effort
        return bool(st.get("thinking", True)), str(st.get("effort", "medium"))

    def choose_chat_model(self, tag: str | None) -> None:
        assert self._chat() is not None
        ext = external.parse(tag)
        if ext:
            if not self.accounts.auth(ext[0]):
                self._chat().add("info", f"{self.providers[ext[0]].info.brand} models aren't connected - "
                                         "Settings > AI model > External AI models.")
                return
            self.set_chat_model(None if tag == self.cfg.model else tag)
            return
        if tag is not None and models.find(tag) is None:
            self._chat().add("info", "I don't know that model. Models: " +
                             ", ".join(f"{m.name} ({m.short})" for m in models.MODELS) + ".")
            return
        if tag is not None and models.find(tag).tag == self.cfg.model:
            tag = None
        if tag is not None and not model_on_disk(models.find(tag).tag):
            m = models.find(tag)
            self._chat().ask_confirm(f"download {models.label(m.tag)} ({m.download_gb:g} GB) for this chat",
                                     lambda ans, t=m.tag: (self.download_ok.add(t), self.set_chat_model(t))
                                     if ans == "yes" else self._chat().add("info", "Okay, no download."))
            return
        self.set_chat_model(models.find(tag).tag if tag else None)

    def set_chat_model(self, tag: str | None) -> None:
        self.chat_state.chat_model = tag
        self.model_status.override = tag
        self.model_status.ensure(force=True)
        self.update_model_chip()
        if self._chat() is not None:
            self._chat().add("info", f"This chat now uses {models.label(tag)}. A new chat goes back to the "
                                     f"default, {models.label(self.cfg.model)}." if tag else
                             f"This chat uses the default model again: {models.label(self.cfg.model)}.")
            self._show_model_banner()
        self._switched()                                 # (the token limit: go on with the new model)

    def ext_chat_items(self) -> list[MenuItem]:
        """The chat's model menu: the connected providers' models."""
        out = []
        for p, prov in self.accounts.external.items():
            ms = self.accounts.models(p) if self.accounts.auth(p) else []
            if ms:
                out.append(MenuItem(f"chatmodels_{p}", f"{prov.brand} models", "chat", separator_before=not out,
                                    children=[MenuItem(f"chatmodel:{p}:{m['id']}", m["name"], radio=True,
                                                       checked=self.chat_state.chat_model == f"{p}:{m['id']}")
                                              for m in ms]))
        return out
