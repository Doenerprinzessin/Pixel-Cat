"""The chat's own commands, handled by the app and never sent to the model: /new, /history, /auto, /effort,
/model, /getmodel, /permissions, /watches, /unwatch, /help - and /force (end an app like Task Manager, or run one as
administrator), parsed here directly and run with the toolbox."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..llm import external, models
from ..llm.tools import ToolError

if TYPE_CHECKING:
    from ..engine.brain import Brain
    from ..llm.model_status import ModelStatus
    from ..llm.tools import ToolSpec
    from ..llm.watch import WatchManager
    from ..persistence import SavedState
    from ..settings import Config
    from .chat_ui import ChatUI
    from .models_menu import ModelChoice
    from .session import AgentSession
    from .state import ChatState

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)


class ChatCommands:
    """``local_command(text)``: True when the text was one of the chat's own commands (then it's done).  The
    session asks it first for every message the user sends.  The controller answers ``save()``."""

    def __init__(self, chat_state: ChatState, cfg: Config, saved: SavedState, brain: Brain, watches: WatchManager,
                 model_status: ModelStatus, session: AgentSession, chat_ui: ChatUI, model_choice: ModelChoice, *,
                 save: Callable[[], None]) -> None:
        self.chat_state = chat_state
        self.cfg = cfg
        self.saved = saved
        self.brain = brain
        self.watches = watches
        self.model_status = model_status
        self.session = session
        self.chat_ui = chat_ui
        self.model_choice = model_choice
        self._save = save

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

    def local_command(self, text: str) -> bool:
        """Chat commands handled by the app itself (never sent to the model)."""
        cmd = text.strip().lower()
        if cmd == "/force" or cmd.startswith("/force "):
            self._force_command(text)
            return True
        if cmd in ("/auto", "/auto on", "/auto off"):
            self.session.set_auto_mode(cmd != "/auto off" and (cmd == "/auto on" or not self.saved.auto_mode))
            return True
        if cmd in ("/new", "/clear"):
            self.session.new_chat()
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
            self._save()
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
            self._save()
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
