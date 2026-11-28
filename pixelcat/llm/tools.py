"""Whitelisted desktop tools the chat model may request.

Safety rules enforced here (not by the model):
* Only the tools below exist; there is no shell or arbitrary command execution.
* Arguments are validated and normalised (paths must live under allowed roots,
  URLs must be http(s), apps must be whitelisted or Start-menu shortcuts,
  windows must match something that is actually open).  read_web_page reads public
  pages only - never this PC or the local network (webpage.py).
* ``confirm`` levels: "none" (read-only or in-app only), "soft" (changes
  something small - the user may choose ALWAYS to stop being asked for that
  tool), "hard" (always asks: closing apps, writing files, locking the PC).
* Window/app control only happens on an explicit chat request plus the user's
  confirmation.  Closing is graceful (like clicking X); nothing is ever killed.
  The cat's own behaviour never uses these tools except harmless ``cat_action``.
* Seeing and using the desktop (look_at_screen, read_window, watch_screen, click, drag,
  move_mouse, zoom, type_text, press_keys, scroll): real mouse/keyboard input into the app the
  user means; typing, clicking, dragging or zooming in command lines / the Run box always needs
  the user's OK, even in auto mode; closing shortcuts (Alt+F4, Ctrl+W...) are "hard" confirms.
  Screenshots go to the chat's model.  More guard rails live in agent.py.
* Anything that needs administrator rights ALWAYS asks first - even in auto mode
  and even for tools set to ALWAYS: apps that need admin rights (manifest,
  shortcut flag, compatibility setting), /force open, and /force end when a
  process can only be ended with admin rights.  Windows then shows its own UAC
  prompt as well, which only the user can answer.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

from .. import paths
from ..domain import MEDIA_ACTIONS, MODIFIERS, MOUSE_BUTTONS, SETTINGS_PAGES, find_windows, friendly
from ..settings import Config
from .agent import AgentError, DesktopAgent, ToolOutput

if TYPE_CHECKING:
    from ..ports import Platform

log = logging.getLogger(__name__)

CAT_ACTIONS = ["sit", "sleep", "come_here", "trick", "play", "climb", "wake", "groom", "stretch", "zoomies", "loaf", "happy"]


class ToolError(Exception):
    """Raised for invalid tool requests; the message is shown to the user/model."""


FILE_TOOLS = {"read_file", "edit_file", "write_file", "open_file"}
# what models call the wait_for events -> the real names
_EVENT_SYN = {"notifications": "notification", "message": "notification", "messages": "notification",
              "toast": "notification", "email": "notification", "mail": "notification", "chat": "notification",
              "text": "window_text", "window": "window_text", "screen_text": "window_text",
              "window_open": "window_opened", "open": "window_opened", "opened": "window_opened",
              "app_opened": "window_opened", "window_close": "window_closed", "close": "window_closed",
              "closed": "window_closed", "app_closed": "window_closed", "download": "file", "folder": "file",
              "new_file": "file", "timer": "time", "clock": "time", "at": "time", "reminder": "time",
              "delay": "time", "minutes": "time"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".css", ".scss", ".html", ".htm", ".java", ".cs", ".cpp",
            ".c", ".h", ".hpp", ".go", ".rs", ".rb", ".php", ".sql", ".yml", ".yaml", ".toml", ".xml", ".sh", ".kt",
            ".swift", ".vue", ".svelte", ".lua", ".r", ".ini", ".cfg"}
AGENT_TOOLS = {"look_at_screen", "read_window", "click", "type_text", "replace_text", "press_keys", "scroll", "wait",
               "drag", "move_mouse", "zoom", "watch_screen"}
# the chat's eyes on the screen: read-only, so auto mode doesn't count them as actions
SCREEN_EYES = {"look_at_screen", "read_window", "watch_screen"}
# keys that may be held while using the mouse
HELD_KEYS = MODIFIERS
# shortcuts that close things / lock the PC: always a real confirmation (no ALWAYS)
CLOSING_KEYS = {"alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+q", "ctrl+f4", "shift+delete", "win+l"}
# shortcuts that open a command line or system menus: ask even in auto mode
SYSTEM_KEYS = {"win+r", "win+x", "ctrl+shift+esc", "ctrl+alt+delete", "ctrl+alt+del", "win+ctrl+d"}


@dataclass
class ToolSpec:
    name: str
    description: str
    properties: dict[str, Any]
    required: list[str]
    confirm: str                       # none | soft | hard
    run: Callable[[dict[str, Any]], Any]   # returns str or ToolOutput
    describe: Callable[[dict[str, Any]], str]
    llm: bool = True                   # False: only the user's own /force command may use it
    background: bool = False           # runs on a worker thread (input, screenshots, waiting)
    group: str = ""                    # ALWAYS-approval group ("desktop" = click/type/keys/scroll)

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.properties, "required": self.required}}}


@dataclass
class AppHooks:
    """Callbacks into the app for tools that need Qt or the cat."""

    cat_action: Callable[[str], str]
    set_timer: Callable[[float, str], str]
    screenshot: Callable[[], str]
    set_clipboard: Callable[[str], str]
    own_pid: int = 0
    watches: Any = None                                  # watch.WatchManager (wait_for)
    task_request: Callable[[], str] | None = None        # the user's request of the running task


class ToolBox:
    def __init__(self, cfg: Config, hooks: AppHooks, *, platform: Platform, agent: DesktopAgent | None = None) -> None:
        self.cfg = cfg
        self.hooks = hooks
        self.platform = platform
        self.folders = platform.folders       # the user's real Documents, Desktop ... (the platform's)
        self.specs: dict[str, ToolSpec] = {}
        self.agent = agent if agent is not None else DesktopAgent(cfg, hooks.own_pid, platform=platform)
        self._admin_seen: set[str] = set()   # apps Windows refused to start without admin rights
        self._register()

    # ------------------------------------------------------------- registry
    def _add(self, name: str, desc: str, props: dict[str, Any], required: list[str], confirm: str,
             run: Callable[[dict[str, Any]], Any], describe: Callable[[dict[str, Any]], str], llm: bool = True,
             background: bool = False, group: str = "") -> None:
        self.specs[name] = ToolSpec(name, desc, props, required, confirm, run, describe, llm, background, group)

    def _register(self) -> None:
        apps = sorted(self.cfg.apps)
        s = {"type": "string"}
        start_menu = " or any app installed in the Start menu" if self.cfg.allow_start_menu_apps else ""
        # read-only / in-app -----------------------------------------------------------
        self._add("get_time", "Get the current local date and time.", {}, [], "none",
                  self._get_time, lambda a: "tell the time")
        self._add("system_info", "Battery, RAM, CPU, disk space and uptime.", {}, [], "none",
                  lambda a: self.platform.system.system_info(), lambda a: "check the system")
        self._add("list_windows", "List the open app windows (app name and title).", {}, [], "none",
                  self._list_windows, lambda a: "list open windows")
        self._add("list_files", "List files in a folder (aliases as for open_folder).", {"folder": s}, ["folder"], "none",
                  self._list_files, lambda a: f"list files in {a['_path']}")
        self._add("get_volume", "Get the master volume level.", {}, [], "none", self._get_volume, lambda a: "check the volume")
        self._add("set_timer", "Just an alarm: the cat rings and shows the label when it ends. To DO something later "
                  "(or when something happens) use wait_for.",
                  {"minutes": {"type": "number"}, "label": s}, ["minutes"], "none",
                  self._set_timer, lambda a: f"set a {a['minutes']} minute timer")
        self._add("set_reminder", "Just an alarm with a text at a local time today/tomorrow (HH:MM, 24h). To DO "
                  "something at a time use wait_for event=time.",
                  {"time": s, "text": s}, ["time", "text"], "none", self._set_reminder,
                  lambda a: f"remind you at {a['time']}")
        self._add("cat_action", "Ask the cat to do a harmless action.",
                  {"action": {"type": "string", "enum": CAT_ACTIONS}}, ["action"], "none",
                  lambda a: self.hooks.cat_action(a["action"]), lambda a: f"cat action {a['action']}")
        # small changes (user may choose ALWAYS) ----------------------------------------
        self._add("open_app", f"Open an app by name: {', '.join(apps)}{start_menu}. "
                  "Waits until its window is open and in front.",
                  {"name": s}, ["name"], "soft", self._open_app,
                  lambda a: f"open {a['_label']}" + (" - it needs ADMIN RIGHTS (Windows will ask you too)"
                                                      if a.get("_admin") else ""), background=True)
        self._add("window_action", "Focus (switch to), minimize, maximize or restore an open window. "
                  "target = app name (e.g. chrome, outlook, notepad) or part of the window title.",
                  {"target": s, "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "restore"]}},
                  ["target", "action"], "soft", self._window_action,
                  lambda a: f"{a['action']} the window {a['_win'].label()}")
        self._add("open_url", "Open an http(s) URL in the default browser.", {"url": s}, ["url"], "soft",
                  self._open_url, lambda a: f"open {a['url']} in your browser")
        self._add("search_web", "Search the web for a query in the default browser.", {"query": s}, ["query"], "soft",
                  self._search_web, lambda a: f"search the web for '{a['query']}'")
        self._add("read_web_page", "Read the text of a public web page (http/https) without opening the browser: its "
                  "title, text and links. For looking things up - a link the user gave you, a link from a page you "
                  "read, or a site's own search page. Long pages come in parts: call again with the start given "
                  "at the end of a part.",
                  {"url": s, "start": {"type": "integer"}}, ["url"], "soft", self._read_web_page,
                  lambda a: f"read the web page {a['url']}", background=True, group="web")
        self._add("open_folder", "Open a folder in File Explorer. Use aliases: desktop, documents, downloads, "
                  "pictures, music, videos, notes, or a path inside them.", {"folder": s}, ["folder"], "soft",
                  self._open_folder, lambda a: f"open the folder {a['_path']}")
        self._add("open_settings", "Open a Windows Settings page.",
                  {"page": {"type": "string", "enum": sorted(SETTINGS_PAGES)}}, ["page"], "soft",
                  self._open_settings, lambda a: f"open the {a['page']} settings")
        self._add("media_control", "Control music/video playback (like the keyboard media keys).",
                  {"action": {"type": "string", "enum": sorted(MEDIA_ACTIONS)}}, ["action"], "soft",
                  self._media, lambda a: f"press media {a['action'].replace('_', '/')}")
        self._add("copy_to_clipboard", "Copy text to the clipboard.", {"text": s}, ["text"], "soft",
                  lambda a: self.hooks.set_clipboard(a["text"]), lambda a: f"copy {len(a['text'])} characters to your clipboard")
        self._add("take_screenshot", "Save a screenshot of all monitors to Pictures/PixelCat Screenshots.", {}, [], "soft",
                  lambda a: self.hooks.screenshot(), lambda a: "take a screenshot of your screens")
        self._add("set_volume", "Set the master volume (0-100).", {"level": {"type": "integer"}}, ["level"], "soft",
                  self._set_volume, lambda a: f"set the volume to {a['level']}%")
        self._add("mute", "Mute or unmute the speakers.", {"muted": {"type": "boolean"}}, ["muted"], "soft",
                  self._mute, lambda a: "mute the sound" if a["muted"] else "unmute the sound")
        # always confirm ---------------------------------------------------------------
        self._add("close_app", "Close an app's windows gracefully (like clicking X; the app may ask to save). "
                  "target = app name or part of the window title.", {"target": s}, ["target"], "hard",
                  self._close_app, lambda a: "close " + "; ".join(w.label() for w in a["_wins"]))
        self._add("create_note", "Create a new .txt or .md note file. folder: alias or path inside allowed folders "
                  "(default 'notes').", {"folder": s, "filename": s, "content": s}, ["filename", "content"], "hard",
                  self._create_note, lambda a: f"create the file {a['_target']} ({len(a['content'])} characters)")
        self._add("lock_screen", "Lock the PC (Windows+L).", {}, [], "hard",
                  lambda a: "Locked the PC." if self.platform.system.lock_screen() else "Could not lock the PC.",
                  lambda a: "lock your PC")
        # seeing and using the desktop ---------------------------------------------------
        eyes = "none" if self._local_model() else "soft"
        tgt = {"type": "string", "description": "app name or part of the window title; "
               "leave empty for the window you used last"}
        num = {"type": "integer"}
        pos = {"type": "number"}
        mods = {"type": "string", "description": "keys held down meanwhile: ctrl, shift, alt, win, or e.g. 'ctrl+shift'"}
        self._add("look_at_screen", "SEE a window (or the whole screen when target is empty): returns a screenshot "
                  "with pink numbered boxes on the clickable elements plus their list. Use it to find buttons and "
                  "to check that an action worked.", {"target": tgt}, [], eyes, self._look,
                  lambda a: f"look at {a['_label']}", background=True)
        self._add("read_window", "READ a window exactly: its text content (documents, editors, web pages) and "
                  "its controls with ids. Faster and more precise than a screenshot for reading text.",
                  {"target": tgt}, [], eyes, self._read, lambda a: f"read {a['_label']}", background=True)
        self._add("click", "Click like a real mouse: a UI element by id (element, from look_at_screen/read_window), or "
                  "a spot (x, y = pixel position in your latest screenshot). button: left (default), right, middle, "
                  "back, forward. clicks: 2 = double-click, 3 = triple-click (selects a line or paragraph). keys = "
                  "held while clicking ('ctrl' opens a link in a new tab / adds to a selection, 'shift' selects a "
                  "range). hold = seconds to keep the button pressed (a long press).",
                  {"element": num, "x": pos, "y": pos, "button": {"type": "string", "enum": list(MOUSE_BUTTONS)},
                   "clicks": num, "double": {"type": "boolean"}, "keys": mods, "hold": {"type": "number"}}, [], "soft",
                  self._click, lambda a: f"{a['_verb']} {a['_what']}", background=True, group="desktop")
        self._add("drag", "DRAG like a hand on the mouse: press at one place, move, let go at another. Moves game "
                  "pieces (chess...), cards, files and icons, sliders, scroll bars, splitters; selects text or an "
                  "area; draws. From: from_element or from_x/from_y. To: to_element or to_x/to_y. path = points to "
                  "pass through on the way, [[x, y], ...] (for drawing; without 'to', the last point is the end). All "
                  "x/y are pixels in your latest screenshot. button (default left), keys held meanwhile ('shift', "
                  "'ctrl' copies files, 'alt'), seconds = how long the movement takes (default 0.6). Returns a "
                  "screenshot afterwards.",
                  {"from_element": num, "from_x": pos, "from_y": pos, "to_element": num, "to_x": pos, "to_y": pos,
                   "path": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                   "button": {"type": "string", "enum": ["left", "right", "middle"]}, "keys": mods,
                   "seconds": {"type": "number"}}, [], "soft", self._drag, lambda a: a["_desc"], background=True,
                  group="desktop")
        self._add("move_mouse", "HOVER: move the mouse pointer onto an element or a spot (x, y in your latest "
                  "screenshot) and leave it there - shows tooltips, opens hover menus and previews. Returns a "
                  "screenshot after 'seconds' (default 1).",
                  {"element": num, "x": pos, "y": pos, "seconds": {"type": "number"}}, [], "soft", self._hover,
                  lambda a: f"move the mouse onto {a['_what']}", background=True, group="desktop")
        self._add("zoom", "ZOOM in or out (maps, pictures, web pages, documents, design apps) at an element or a spot "
                  "(x, y in your latest screenshot); without one: the middle of the window (target). steps 1-10 "
                  "(default 3). how: 'wheel' = ctrl + mouse wheel (default, works in most apps), 'pinch' = two "
                  "fingers like on a touch screen (maps, photo apps). Returns a screenshot afterwards.",
                  {"direction": {"type": "string", "enum": ["in", "out"]}, "steps": num, "element": num, "x": pos,
                   "y": pos, "how": {"type": "string", "enum": ["wheel", "pinch"]}, "target": tgt}, ["direction"],
                  "soft", self._zoom, lambda a: f"zoom {a['direction']} {a['_where']}", background=True,
                  group="desktop")
        self._add("type_text", "INSERT exactly this text where the text cursor is. Nothing is added for you: if "
                  "each item should be on its own line, the text must end with a line break. Several lines at once "
                  "are fine (one call). Only for writing new text - to change or fix text that is already there use "
                  "replace_text. element = id of a text field to click first. For a new document use press_keys "
                  "ctrl+n first.",
                  {"text": s, "target": tgt, "element": num}, ["text"], "soft", self._type,
                  lambda a: f"type {len(a['text']):,} characters into {a['_label']}", background=True,
                  group="desktop")
        self._add("replace_text", "CHANGE existing text in an editor (Notepad, VS Code, ...): replaces the exact "
                  "text 'old' with 'new' wherever it is in the document - use it to fix bugs, typos or lines. "
                  "'old' must be copied exactly from the document (include enough of the line to be unique); "
                  "'new' is what should stand there instead.",
                  {"old": s, "new": s, "target": tgt, "replace_all": {"type": "boolean"}}, ["old", "new"], "soft",
                  self._replace, lambda a: f"replace {a['old'][:50]!r} with {a['new'][:50]!r} in {a['_label']}",
                  background=True, group="desktop")
        self._add("press_keys", "Press keys/shortcuts in a window, one or more separated by spaces: "
                  "'ctrl+n', 'ctrl+s', 'enter', 'tab', 'esc', 'ctrl+a', 'down down enter', 'f5'. hold = seconds to "
                  "keep each one pressed (games: hold 'w' to walk, 'space' to charge).",
                  {"keys": s, "target": tgt, "hold": {"type": "number"}}, ["keys"], "soft", self._keys,
                  lambda a: f"press {a['_keys']}{a['_held']} in {a['_label']}", background=True, group="desktop")
        self._add("scroll", "Scroll with the mouse wheel up/down/left/right by some steps (default 5, at most 50): "
                  "over the middle of a window (target), an element id, or a spot (x, y in your latest screenshot). "
                  "keys = held meanwhile (ctrl usually zooms, shift scrolls sideways).",
                  {"direction": {"type": "string", "enum": ["up", "down", "left", "right"]}, "amount": num,
                   "target": tgt, "element": num, "x": pos, "y": pos, "keys": mods}, ["direction"], "soft",
                  self._scroll, lambda a: f"scroll {a['direction']} in {a['_label']}", background=True,
                  group="desktop")
        self._add("watch_screen", "WATCH THE SCREEN and react fast, inside this task: waits until something "
                  "happens, then returns a fresh screenshot (with element ids) so you can act right away. until: "
                  "'change' (default - compared with your latest screenshot, so a change that already happened counts "
                  "at once), 'text' (this text appears), 'gone' (this text disappears), 'still' (it stops changing: "
                  "an animation or loading ended). target = the window (empty: what your latest screenshot showed; "
                  "'screen' = the whole screen). area = watch only part of it: [x, y, width, height] in your latest "
                  "screenshot (the chess board, the question) so clocks and timers don't count. sensitivity: normal "
                  "(default), high (tiny changes too), low (only big ones like a new page). seconds = how long to "
                  "wait at most (default 120, max 600). Use it in a loop for quizzes (Kahoot), turn-based games "
                  "(chess), anything that will appear soon. For minutes or hours, or while the user keeps chatting, "
                  "use wait_for.",
                  {"until": {"type": "string", "enum": ["change", "text", "gone", "still"]}, "text": s,
                   "target": tgt, "area": {"type": "array", "items": {"type": "number"}},
                   "sensitivity": {"type": "string", "enum": ["normal", "high", "low"]},
                   "seconds": {"type": "number"}}, [], eyes, self._watch,
                  lambda a: f"watch {a['_label']} {a['_cond']}", background=True)
        self._add("wait", "Pause 1-10 seconds inside a task (e.g. for a page or app to load). For anything longer, "
                  "or until something happens, use wait_for.", {"seconds": {"type": "number"}},
                  ["seconds"], "none", lambda a: self.agent.wait(a["seconds"]), lambda a: f"wait {a['seconds']} s",
                  background=True)
        # long tasks: wait in the background until something happens ---------------------------
        from .watch import KINDS
        self._add("wait_for", "WAIT IN THE BACKGROUND until something happens later, then carry on with the task: a "
                  "new message or email (Teams, Outlook, WhatsApp...), a program or download finishing, a window "
                  "opening or closing, a file appearing, or a clock time. The app watches for you (costs nothing, the "
                  "user can keep chatting) and wakes you up with what happened and your 'then' note. Use it for EVERY "
                  "'wait for...', 'tell me when...', 'let me know if...', 'once X, do Y', 'at 5 pm do Z', 'keep an "
                  "eye on...'. After calling it, end your turn with one short sentence.",
                  {"event": {"type": "string", "enum": list(KINDS), "description":
                             "notification = a new Windows notification (chat messages, emails, calls...); "
                             "window_text = a window shows a text (gone=true: until it no longer does, e.g. "
                             "'Downloading...'); window_opened / window_closed = a window appears / closes; file = a "
                             "file appears or changes, or any new file lands in a folder; time = at a clock time or "
                             "after some minutes"},
                   "app": {"type": "string", "description": "notification: the app it comes from (teams, outlook, "
                           "whatsapp...; empty = any app). Window events: app name or part of the window title"},
                   "text": {"type": "string", "description": "only when this text is in it (a person's name, a "
                            "subject...). window_text: the text to wait for"},
                   "gone": {"type": "boolean"},
                   "path": {"type": "string", "description": "file: a file ('downloads/report.pdf') or a folder "
                            "('downloads' = any new file there)"},
                   "at": {"type": "string", "description": "time: HH:MM, 24h (the next time it is that time)"},
                   "minutes": {"type": "number", "description": "time: minutes from now. Other events: give up "
                               "after this many minutes (default 480)"},
                   "then": {"type": "string", "description": "note to yourself: what to do when it happens, e.g. "
                            "'tell the user who wrote and what', 'open the file and summarise it'"}},
                  ["event", "then"], eyes, self._wait_for,
                  lambda a: f"watch for {a['_watch'].short()}", background=True)
        self._add("stop_waiting", "Stop a wait_for (the user doesn't need it any more). id = its number; without id "
                  "you get the list of what is being watched.", {"id": num, "all": {"type": "boolean"}}, [], "none",
                  self._stop_waiting, lambda a: "stop watching")
        # files (direct, much more reliable than steering an editor) -------------------------
        fp = {"type": "string", "description": "file path: folder alias + name like 'desktop/notes.txt' or "
              "'downloads/project/app.js', or a full path"}
        self._add("read_file", "Read a text file (code, notes, lists...) exactly. Use it before editing a file.",
                  {"path": fp}, ["path"], eyes, self._read_file, lambda a: f"read {a['_path']}", background=True)
        self._add("edit_file", "CHANGE a text file directly (best way to fix code or edit a document): replaces the "
                  "exact text 'old' with 'new' in the file. Read the file first and copy 'old' exactly. A backup "
                  "is kept.", {"path": fp, "old": s, "new": s, "replace_all": {"type": "boolean"}},
                  ["path", "old", "new"], "soft", self._edit_file,
                  lambda a: f"edit {a['_path'].name}: replace {a['old'][:50]!r} with {a['new'][:50]!r}",
                  background=True, group="files")
        self._add("write_file", "Create a text file with the given content (code, notes, lists, plans...). "
                  "overwrite=true replaces an existing file (a backup is kept).",
                  {"path": fp, "content": s, "overwrite": {"type": "boolean"}}, ["path", "content"], "soft",
                  self._write_file,
                  lambda a: (f"OVERWRITE {a['_path']} with {len(a['content']):,} characters (a backup is kept)"
                             if a.get("_exists") else f"create the file {a['_path']} ({len(a['content']):,} characters)"),
                  background=True, group="files")
        self._add("open_file", "Show a text file to the user in Notepad or VS Code (never runs it).",
                  {"path": fp, "app": {"type": "string", "enum": ["notepad", "vscode"]}}, ["path"], "soft",
                  self._open_file, lambda a: f"open {a['_path'].name} in {a['app']}", background=True)
        # /force only (never offered to the model) ------------------------------------
        self._add("force_end", "Force-end an app like Task Manager.", {"target": s}, ["target"], "hard",
                  self._force_end, lambda a: f"FORCE-END {a['_plan'].label} right now - unsaved work in it will be lost"
                  " (if it needs admin rights, I'll ask you separately)", llm=False)
        self._add("run_as_admin", "Start an app as administrator.", {"name": s}, ["name"], "hard",
                  self._run_as_admin, lambda a: f"open {a['_label']} as administrator (Windows will ask for admin rights)",
                  llm=False)

    def schemas(self) -> list[dict[str, Any]]:
        return [sp.schema() for sp in self.specs.values() if sp.llm]

    def names(self) -> list[str]:
        return [n for n, sp in self.specs.items() if sp.llm]

    # ------------------------------------------------------------- validation
    def prepare(self, name: str, args: dict[str, Any], internal: bool = False) -> tuple[ToolSpec, dict[str, Any]]:
        """Validate a request. Returns the spec and normalised args or raises ToolError.
        ``internal`` is True only for the user's own /force command."""
        spec = self.specs.get(name)
        if spec is None or (not spec.llm and not internal):
            raise ToolError(f"'{name}' is not an allowed tool")
        if not isinstance(args, dict):
            raise ToolError("arguments must be an object")
        a = {k: v for k, v in args.items() if k in spec.properties}
        for req in spec.required:
            if name == "replace_text" and req == "new" and a.get(req) == "":
                continue   # replacing with nothing = deleting text
            if name == "wait_for" and req == "then":
                continue   # defaults to telling the user
            if req not in a or a[req] in (None, ""):
                if name == "create_note" and req == "filename":
                    a["filename"] = datetime.now().strftime("note_%Y%m%d_%H%M%S.txt")
                    continue
                raise ToolError(f"missing argument '{req}'")
        if name == "open_app":
            key = str(a["name"]).strip().lower()
            if key in self.cfg.apps:
                a["_target"], a["_label"] = list(self.cfg.apps[key]), f"the app '{key}'"
            else:
                hit = self.platform.apps.find_shortcut(key) if self.cfg.allow_start_menu_apps else None
                if hit is None:
                    raise ToolError(f"I can't find an app called '{a['name']}'")
                a["_target"], a["_label"] = hit[1], f"'{hit[0]}' (Start menu)"
            a["name"] = key
            if self.platform.apps.needs_admin(a["_target"]) or str(a["_target"]) in self._admin_seen:
                a["_admin"] = True
                a["_confirm"] = "hard"          # no ALWAYS for admin things
                a["_force_confirm"] = True      # ...and asked even in auto mode
        elif name in ("window_action", "close_app"):
            target = str(a["target"]).strip()
            wins = find_windows(target, self.platform.windows.app_windows(self.hooks.own_pid))
            if not wins:
                raise ToolError(f"no open window matches '{target}'")
            if name == "close_app":
                a["_wins"] = wins[:10]
            else:
                action = str(a["action"]).strip().lower()
                if action not in ("focus", "minimize", "maximize", "restore"):
                    raise ToolError("action must be focus, minimize, maximize or restore")
                a["action"] = action
                pref = [w for w in wins if w.minimized] if action == "restore" else [w for w in wins if not w.minimized]
                a["_win"] = (pref or wins)[0]
        elif name in FILE_TOOLS:
            self._prepare_file(name, a)
        elif name == "wait_for":
            self._prepare_wait(a)
        elif name in AGENT_TOOLS:
            try:
                self._prepare_agent(name, a)
            except AgentError as exc:
                raise ToolError(str(exc)) from None
        elif name == "force_end":
            try:
                a["_plan"] = self.platform.processes.plan_force_end(str(a["target"]), self.hooks.own_pid)
            except ValueError as exc:
                raise ToolError(str(exc)) from None
        elif name == "run_as_admin":
            key = str(a["name"]).strip().lower()
            if key in self.cfg.apps and not re.match(r"^[a-z][a-z0-9+.-]*:", self.cfg.apps[key][0], re.I):
                a["_target"], a["_label"] = self.cfg.apps[key][0], f"'{key}'"
            else:
                hit = self.platform.apps.find_shortcut(key)
                if hit is None:
                    raise ToolError(f"I can't find an app called '{a['name']}'")
                if isinstance(hit[1], str) and hit[1].startswith("shell:"):
                    raise ToolError(f"'{hit[0]}' is a Store app - those can't run as administrator")
                a["_target"], a["_label"] = str(hit[1]), f"'{hit[0]}'"
        elif name == "search_web":
            q = str(a["query"]).strip()
            if not q:
                raise ToolError("empty search")
            a["query"] = q[:300]
        elif name == "open_settings":
            page = str(a["page"]).strip().lower()
            if page not in SETTINGS_PAGES:
                raise ToolError(f"unknown settings page '{page}'")
            a["page"] = page
        elif name == "media_control":
            act = str(a["action"]).strip().lower().replace("/", "_").replace(" ", "_")
            act = {"play": "play_pause", "pause": "play_pause", "prev": "previous", "skip": "next"}.get(act, act)
            if act not in MEDIA_ACTIONS:
                raise ToolError(f"unknown media action '{act}'")
            a["action"] = act
        elif name == "copy_to_clipboard":
            a["text"] = str(a["text"])[:20_000]
        elif name in ("open_url", "read_web_page"):
            url = str(a["url"]).strip()
            if not re.match(r"^https?://", url, re.I):
                url = "https://" + url
            u = urlparse(url)
            if u.scheme not in ("http", "https") or not u.netloc or " " in u.netloc:
                raise ToolError("only http(s) URLs are allowed")
            a["url"] = url
            if name == "read_web_page":
                try:
                    a["start"] = max(0, int(a.get("start") or 0))
                except (TypeError, ValueError):
                    a["start"] = 0
        elif name in ("open_folder", "list_files"):
            a["_path"] = self._resolve_dir(str(a["folder"]), must_exist=True)
        elif name == "create_note":
            folder = self._resolve_dir(str(a.get("folder") or "notes"), must_exist=False)
            fname = self._safe_filename(str(a["filename"]))
            target = folder / fname
            n = 2
            while target.exists():
                target = folder / f"{Path(fname).stem} ({n}){Path(fname).suffix}"
                n += 1
            a["content"] = str(a["content"])[:100_000]
            a["_target"] = target
        elif name == "set_volume":
            try:
                a["level"] = max(0, min(100, int(float(a["level"]))))
            except (TypeError, ValueError):
                raise ToolError("level must be a number 0-100") from None
        elif name == "mute":
            a["muted"] = str(a["muted"]).lower() in ("true", "1", "yes", "on")
        elif name == "set_timer":
            try:
                minutes = float(a["minutes"])
            except (TypeError, ValueError):
                raise ToolError("minutes must be a number") from None
            if not 0 < minutes <= 24 * 60:
                raise ToolError("timers must be between 0 and 1440 minutes")
            a["minutes"] = minutes
            a["label"] = str(a.get("label") or "Timer")[:80]
        elif name == "set_reminder":
            m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", str(a["time"]))
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                raise ToolError("time must be HH:MM (24h)")
            a["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
            a["text"] = str(a["text"])[:200]
        elif name == "cat_action":
            act = str(a["action"]).strip().lower().replace(" ", "_")
            if act not in CAT_ACTIONS:
                raise ToolError(f"unknown cat action '{act}'")
            a["action"] = act
        return spec, a

    def _resolve_file(self, raw: str, must_exist: bool) -> Path:
        """'desktop/x.txt', 'Downloads\\proj\\a.js', 'C:\\Users\\...\\a.js' -> a real path inside the allowed folders."""
        from . import files

        text = str(raw).strip().strip('"').strip("'")
        if not text:
            raise ToolError("no file path given")
        parts = re.split(r"[\\/]", text, maxsplit=1)
        alias = paths.known_folder(parts[0].rstrip(":"), self.folders) \
            if len(parts) == 2 and not re.match(r"^[a-zA-Z]:", text) and not text.startswith("\\\\") else None
        p = alias / parts[1] if alias is not None else Path(os.path.expandvars(text)).expanduser()
        if not p.is_absolute():
            p = (paths.known_folder("documents", self.folders) or Path.home()) / p
        p = p.resolve()
        if not any(p == r or r in p.parents for r in self._allowed_roots()):
            raise ToolError(f"{p} is outside the folders I'm allowed to touch ({', '.join(self.cfg.allowed_roots)})")
        if must_exist and not p.is_file():
            hint = ""
            if p.parent.is_dir():
                stem = p.stem.lower()
                near = [c.name for c in p.parent.iterdir() if c.is_file() and (stem[:4] in c.name.lower())][:8]
                hint = f" Files there with a similar name: {', '.join(near)}" if near else \
                    f" ({p.parent} has: {', '.join(c.name for c in list(p.parent.iterdir())[:15])})"
            raise ToolError(f"{p} does not exist.{hint}")
        if p.is_dir():
            raise ToolError(f"{p} is a folder, not a file")
        try:
            if not must_exist:
                files.check_writable_name(p)
        except files.FileError as exc:
            raise ToolError(str(exc)) from None
        return p

    def _prepare_file(self, name: str, a: dict[str, Any]) -> None:
        from . import files

        if name == "read_file":
            a["_path"] = self._resolve_file(a["path"], True)
        elif name == "edit_file":
            p = self._resolve_file(a["path"], True)
            try:
                files.check_writable_name(p)
                tf = files.read_text(p)
                old = str(a["old"]).replace("\r\n", "\n")
                if not old.strip():
                    raise ToolError("'old' must be existing text of the file (use write_file to create files)")
                if old == str(a.get("new", "")).replace("\r\n", "\n"):
                    raise ToolError("'old' and 'new' are the same - nothing would change")
                DesktopAgent.find_span(tf.text, old, str(a.get("replace_all")).lower() in ("true", "1"))
            except (files.FileError, AgentError) as exc:
                raise ToolError(str(exc)) from None
            a["_path"], a["old"], a["new"] = p, old, str(a.get("new") or "").replace("\r\n", "\n")
            a["replace_all"] = str(a.get("replace_all")).lower() in ("true", "1")
        elif name == "write_file":
            p = self._resolve_file(a["path"], False)
            content = str(a["content"])
            if len(content) > files.MAX_WRITE_CHARS:
                raise ToolError("that's too much text for one file")
            a["_path"], a["content"] = p, content
            a["_exists"] = p.exists()
            if a["_exists"]:
                if str(a.get("overwrite")).lower() not in ("true", "1"):
                    raise ToolError(f"{p} already exists - use edit_file to change it, or overwrite=true to replace "
                                    "all of it")
                a["_confirm"] = "hard"   # replacing a whole file: always a real YES
        elif name == "open_file":
            p = self._resolve_file(a["path"], True)
            app = str(a.get("app") or "").lower().replace(" ", "")
            if app not in ("notepad", "vscode", "code"):
                app = "vscode" if p.suffix.lower() in CODE_EXT and self._vscode_exe() else "notepad"
            a["_path"], a["app"] = p, "vscode" if app == "code" else app

    def _vscode_exe(self) -> str | None:
        hit = self.platform.apps.find_shortcut("visual studio code")
        if hit is not None and str(hit[1]).lower().endswith(".lnk"):
            exe, _runas = self.platform.apps.lnk_target(hit[1])
            if exe.lower().endswith("code.exe") and os.path.isfile(exe):
                return exe
        guess = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe")
        return guess if os.path.isfile(guess) else None

    def _local_model(self) -> bool:
        host = (urlparse(self.cfg.ollama_url).hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")

    def _target(self, a: dict[str, Any]) -> None:
        w = self.agent.resolve(a.get("target"))
        a["_hwnd"], a["_label"] = w.hwnd, w.label()

    @staticmethod
    def _element_arg(a: dict[str, Any], key: str = "element") -> int | None:
        raw = a.get(key)
        if raw in (None, "", 0, "0"):
            return None
        m = re.search(r"\d+", str(raw))
        if not m:
            raise ToolError(f"{key} must be a number from the last look")
        return int(m.group())

    @staticmethod
    def _num(a: dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
        raw = a.get(key)
        if raw in (None, ""):
            return default
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise ToolError(f"{key} must be a number") from None
        return max(lo, min(hi, v)) if v == v else default

    @staticmethod
    def _held(a: dict[str, Any]) -> list[str]:
        """Keys held down during a mouse action (ctrl-click, shift-drag...)."""
        raw = str(a.get("keys") or "").strip().lower()
        if raw in ("", "none", "no", "nothing"):
            return []
        out: list[str] = []
        for part in re.split(r"[\s+,]+", raw):
            if not part:
                continue
            key = HELD_KEYS.get(part)
            if key is None:
                raise ToolError("keys: only ctrl, shift, alt and win can be held with the mouse "
                                "(to hold other keys use press_keys with hold)")
            if key not in out:
                out.append(key)
        return out

    @staticmethod
    def _held_note(keys: list[str]) -> str:
        return " holding " + "+".join(keys) if keys else ""

    def _spot_arg(self, a: dict[str, Any], prefix: str, verb: str,
                  required: bool = True) -> tuple[tuple, int, str] | None:
        """``{prefix}element`` or ``{prefix}x``/``{prefix}y`` -> (ref for the agent, the window there, a
        description).  The window is the one at that spot now when the screenshot was of the whole screen."""
        ag = self.agent
        eid = self._element_arg(a, prefix + "element")
        if eid is not None:
            el, hwnd = ag.element(eid)
            name_part = f" '{el.name[:40]}'" if el.name else ""
            return ("el", eid), hwnd, f"[{eid}] {el.kind}{name_part} in {ag.describe_hwnd(hwnd)}"
        raw_x, raw_y = a.get(prefix + "x"), a.get(prefix + "y")
        if raw_x in (None, "") and raw_y in (None, ""):
            if not required:
                return None
            raise ToolError(f"{verb} needs {prefix}element=ID, or {prefix}x and {prefix}y from the last screenshot")
        try:
            x, y = float(raw_x), float(raw_y)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ToolError(f"{verb} needs {prefix}element=ID, or {prefix}x and {prefix}y from the last "
                            "screenshot") from None
        if ag.shot is None:
            raise ToolError(f"look_at_screen first, so I know where to {verb}")
        try:
            px, py = ag.to_screen(x, y)
        except AgentError as exc:
            raise ToolError(str(exc).replace("x/y", f"{prefix}x/{prefix}y")) from None
        hwnd = ag.shot.hwnd
        if not hwnd:
            at = self.platform.windows.window_at(px, py)
            window = at if at and self.platform.windows.pid(at) != self.hooks.own_pid else 0
            return ("xy", x, y), window, f"{int(x)},{int(y)} on the screen"
        return ("xy", x, y), hwnd, f"{int(x)},{int(y)} in {ag.describe_hwnd(hwnd)}"

    def _path(self, raw: Any) -> list[tuple[float, float]]:
        """drag's path: [[x, y], ...] (or {"x":..,"y":..} items, or "x1,y1 x2,y2") in the last screenshot."""
        if raw in (None, "", []):
            return []
        pts: list[tuple[float, float]] = []
        try:
            if isinstance(raw, str):
                nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", raw)]
                pts = list(zip(nums[0::2], nums[1::2]))
            elif isinstance(raw, list):
                for p in raw:
                    if isinstance(p, dict):
                        pts.append((float(p["x"]), float(p["y"])))
                    elif isinstance(p, (list, tuple)) and len(p) >= 2:
                        pts.append((float(p[0]), float(p[1])))
                    else:
                        raise ValueError
            else:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ToolError("path must be a list of points like [[120, 80], [160, 95]]") from None
        if len(pts) > 1000:
            raise ToolError("path: at most 1000 points")
        shot = self.agent.shot
        if pts and shot is None:
            raise ToolError("look_at_screen first, so I know where the path goes")
        if shot is not None:
            sw, sh = shot.size
            bad = next((p for p in pts if not (0 <= p[0] <= sw and 0 <= p[1] <= sh)), None)
            if bad is not None:
                raise ToolError(f"path point {bad[0]:g},{bad[1]:g} is outside the last screenshot ({sw}x{sh})")
        return pts

    def _prepare_watch(self, a: dict[str, Any]) -> None:
        from .agent import SENSITIVITY

        ag = self.agent
        until = str(a.get("until") or "change").strip().lower()
        until = {"changes": "change", "changed": "change", "appear": "text", "appears": "text", "shows": "text",
                 "visible": "text", "disappear": "gone", "disappears": "gone", "hidden": "gone", "calm": "still",
                 "stable": "still", "stop": "still", "stops": "still", "idle": "still"}.get(until, until)
        if until not in ("change", "text", "gone", "still"):
            raise ToolError("until must be change, text, gone or still")
        text = str(a.get("text") or "").strip()
        if until in ("text", "gone") and not text:
            raise ToolError(f"until='{until}' needs the text to watch for")
        sens = str(a.get("sensitivity") or "normal").strip().lower()
        a["_sens"] = sens if sens in SENSITIVITY else "normal"
        secs = self._num(a, "seconds", 120.0, 1.0, 600.0)
        target = str(a.get("target") or "").strip()
        shot = ag.shot
        hwnd, screen = 0, None
        if target.lower() in ("screen", "the screen", "whole screen", "desktop", "monitor"):
            if shot is not None and not shot.hwnd:
                screen = ag.control_monitor(shot.origin[0] + 4, shot.origin[1] + 4)
        elif target:
            self._target(a)
            hwnd = a["_hwnd"]
        elif shot is not None and (not shot.hwnd or self.platform.windows.exists(shot.hwnd)):
            hwnd = shot.hwnd
            if not hwnd:
                screen = ag.control_monitor(shot.origin[0] + 4, shot.origin[1] + 4)
        else:
            wins = [w for w in self.platform.windows.app_windows(self.hooks.own_pid) if not w.minimized]
            if wins and (r := self.platform.windows.frame_bounds(wins[0].hwnd)):
                screen = ag.control_monitor((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)
        if not hwnd and screen is None:
            screen = ag.control_monitor(*self.platform.input.cursor_pos())
        if until in ("text", "gone") and not hwnd:
            raise ToolError(f"until='{until}' needs a window to read (target)")
        area = None
        raw = a.get("area")
        if raw not in (None, "", []):
            try:
                if isinstance(raw, dict):
                    vals = [float(raw[k]) for k in ("x", "y", "width", "height")]
                elif isinstance(raw, str):
                    vals = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", raw)]
                else:
                    vals = [float(v) for v in raw]
            except (KeyError, TypeError, ValueError):
                vals = []
            if len(vals) != 4 or vals[2] <= 0 or vals[3] <= 0:
                raise ToolError("area must be [x, y, width, height] in your latest screenshot")
            if shot is None or shot.hwnd != hwnd:
                raise ToolError("area is measured in your latest screenshot of that same window - look_at_screen "
                                "at it first")
            x, y, w, h = vals
            ox, oy, s = shot.origin[0], shot.origin[1], shot.scale
            area = (int(ox + x / s), int(oy + y / s), int(ox + (x + w) / s), int(oy + (y + h) / s))
        a["_hwnd"], a["_screen"], a["_area"] = hwnd, screen, area
        a["_until"], a["_text"], a["_seconds"] = until, text, secs
        a["_label"] = ag.describe_hwnd(hwnd) if hwnd else "the screen"
        cond = {"change": "until something changes", "text": f"until {text!r} appears",
                "gone": f"until {text!r} is gone", "still": "until it stops changing"}[until]
        a["_cond"] = cond + (" in a part of it" if area else "") + f" (up to {secs:g} s)"

    def _prepare_agent(self, name: str, a: dict[str, Any]) -> None:  # noqa: C901 - one branch per tool
        ag = self.agent
        if name in ("look_at_screen", "read_window"):
            if name == "read_window" or str(a.get("target") or "").strip():
                self._target(a)
            else:
                a["_hwnd"], a["_label"] = 0, "your screen"
        elif name == "click":
            button = str(a.get("button") or "left").strip().lower()
            button = {"primary": "left", "secondary": "right", "wheel": "middle", "x1": "back", "x2": "forward",
                      "mouse4": "back", "mouse5": "forward"}.get(button, button)
            if button not in MOUSE_BUTTONS:
                raise ToolError("button must be left, right, middle, back or forward")
            count = int(self._num(a, "clicks", 1, 1, 3))
            if str(a.get("double")).lower() in ("true", "1", "yes"):
                count = max(count, 2)
            hold = self._num(a, "hold", 0.0, 0.0, 10.0)
            keys = self._held(a)
            ref, window, what = self._spot_arg(a, "", "click")
            a["_eid"], a["_hwnd"] = (ref[1], window) if ref[0] == "el" else (None, ag.shot.hwnd if ag.shot else 0)
            a["_x"], a["_y"] = (None, None) if ref[0] == "el" else (ref[1], ref[2])
            a["_what"] = what if ref[0] == "el" else "at " + what
            a["_button"], a["_double"], a["_count"], a["_hold"], a["_keys"] = button, count == 2, count, hold, keys
            verb = {1: "click", 2: "double-click", 3: "triple-click"}[count]
            if button != "left":
                verb = f"{button}-{verb}" if button in ("right", "middle") else f"{verb} the mouse's {button} button on"
            if hold >= 0.3:
                verb = f"press and hold ({hold:g} s)" if button == "left" else verb + f" (held {hold:g} s)"
            a["_verb"] = verb + self._held_note(keys)
            a["_label"] = a["_what"]
            if window and ag.is_command_line(window):
                a["_force_confirm"] = True
        elif name == "drag":
            src = self._spot_arg(a, "from_", "drag")
            dst = self._spot_arg(a, "to_", "drag", required=False)
            path = self._path(a.get("path"))
            if dst is None and not path:
                raise ToolError("drag needs where to: to_element, or to_x and to_y (or a path of points)")
            button = str(a.get("button") or "left").strip().lower()
            if button not in ("left", "right", "middle"):
                raise ToolError("button must be left, right or middle")
            keys = self._held(a)
            default = 0.6 if not path else min(8.0, 0.6 + 0.02 * len(path))
            a["_src"], a["_dst"], a["_path"] = src[0], dst[0] if dst else None, path
            a["_button"], a["_keys"] = button, keys
            a["_seconds"] = self._num(a, "seconds", default, 0.1, 10.0)
            a["_hwnd"], a["_label"] = src[1], src[2]
            to = dst[2] if dst else f"{path[-1][0]:.0f},{path[-1][1]:.0f}"
            via = f" along {len(path)} point{'s' if len(path) != 1 else ''}" if path else ""
            btn = "" if button == "left" else f" with the {button} button"
            a["_desc"] = f"drag from {src[2]} to {to}{via}{btn}{self._held_note(keys)}"
            if any(h and ag.is_command_line(h) for h in (src[1], dst[1] if dst else 0)):
                a["_force_confirm"] = True
        elif name == "move_mouse":
            ref, window, what = self._spot_arg(a, "", "move the mouse")
            a["_ref"], a["_what"], a["_label"], a["_hwnd"] = ref, what, what, window
            a["_seconds"] = self._num(a, "seconds", 1.0, 0.0, 5.0)
        elif name == "zoom":
            d = str(a["direction"]).strip().lower().replace("zoom", "").strip()
            d = {"+": "in", "closer": "in", "bigger": "in", "larger": "in", "-": "out", "smaller": "out",
                 "away": "out"}.get(d, d)
            if d not in ("in", "out"):
                raise ToolError("direction must be in or out")
            a["direction"] = d
            a["_steps"] = int(self._num(a, "steps", 3, 1, 10))
            how = str(a.get("how") or "wheel").strip().lower()
            a["_how"] = "pinch" if "pinch" in how or "touch" in how or "finger" in how else "wheel"
            spot = self._spot_arg(a, "", "zoom", required=False)
            if spot is not None:
                a["_ref"], a["_hwnd"], a["_label"] = spot[0], spot[1], spot[2]
                a["_where"] = f"at {spot[2]}"
            else:
                self._target(a)
                a["_ref"], a["_where"] = ("mid", a["_hwnd"]), f"in {a['_label']}"
            if a["_hwnd"] and ag.is_command_line(a["_hwnd"]):
                a["_force_confirm"] = True
        elif name == "watch_screen":
            self._prepare_watch(a)
        elif name == "replace_text":
            old_t, new_t = str(a["old"]), str(a.get("new") if a.get("new") is not None else "")
            if not old_t.strip():
                raise ToolError("'old' must be some existing text (to insert new text use type_text)")
            if old_t == new_t:
                raise ToolError("'old' and 'new' are the same - nothing would change")
            if len(old_t) > 20_000 or len(new_t) > 50_000:
                raise ToolError("that's too much text for one replacement")
            a["old"], a["new"] = old_t, new_t
            a["replace_all"] = str(a.get("replace_all")).lower() in ("true", "1", "yes")
            self._target(a)
            if ag.is_command_line(a["_hwnd"]):
                a["_force_confirm"] = True
        elif name == "type_text":
            text = str(a["text"])
            if not text:
                raise ToolError("nothing to type")
            if len(text) > 20_000:
                raise ToolError("that's too much text to type at once (max 20,000 characters)")
            a["text"] = text
            eid = self._element_arg(a)
            if eid is not None:
                _el, hwnd = ag.element(eid)
                a["_eid"], a["_hwnd"], a["_label"] = eid, hwnd, ag.describe_hwnd(hwnd)
            else:
                a["_eid"] = None
                self._target(a)
            if ag.is_command_line(a["_hwnd"]):
                a["_force_confirm"] = True
        elif name == "press_keys":
            raw = str(a["keys"]).strip()
            parts = [p for p in re.split(r"[\s,;]+", re.sub(r"\s*\+\s*", "+", raw)) if p]
            if not parts or len(parts) > 20:
                raise ToolError("give 1-20 keys, e.g. 'ctrl+s' or 'tab tab enter'")
            combos = []
            for p in parts:
                try:
                    combos.append((p.lower(), self.platform.input.parse_combo(p)))
                except ValueError as exc:
                    raise ToolError(str(exc)) from None
            a["_combos"], a["_keys"] = combos, " ".join(c[0] for c in combos)
            hold = self._num(a, "hold", 0.0, 0.0, 10.0)
            a["_hold"] = min(hold, 60.0 / len(combos))
            a["_held"] = f" (held {a['_hold']:g} s)" if a["_hold"] >= 0.1 else ""
            self._target(a)
            names = {c[0].replace("control", "ctrl").replace("windows", "win") for c in combos}
            if names & CLOSING_KEYS:
                a["_confirm"] = "hard"
            if names & SYSTEM_KEYS or ag.is_command_line(a["_hwnd"]):
                a["_force_confirm"] = True
        elif name == "scroll":
            d = str(a["direction"]).strip().lower()
            if d not in ("up", "down", "left", "right"):
                raise ToolError("direction must be up, down, left or right")
            a["direction"] = d
            try:
                a["amount"] = max(1, min(50, int(float(a.get("amount") or 5))))
            except (TypeError, ValueError):
                a["amount"] = 5
            a["_keys"] = self._held(a)
            a["_eid"], a["_xy"] = None, None
            spot = self._spot_arg(a, "", "scroll", required=False)
            if spot is None:
                self._target(a)
            elif spot[0][0] == "el":
                a["_eid"], a["_hwnd"], a["_label"] = spot[0][1], spot[1], ag.describe_hwnd(spot[1])
            else:
                a["_xy"], a["_hwnd"], a["_label"] = (spot[0][1], spot[0][2]), spot[1], spot[2]
        elif name == "wait":
            try:
                secs = float(a["seconds"])
            except (TypeError, ValueError):
                raise ToolError("seconds must be a number") from None
            if secs > 15:
                raise ToolError("wait only pauses up to 10 seconds. To wait longer, or until something happens, "
                                "use wait_for (it watches in the background and wakes you up)")
            a["seconds"] = max(0.5, min(10.0, secs))

    def _prepare_wait(self, a: dict[str, Any]) -> None:  # noqa: C901 - one branch per event
        from .watch import DEFAULT_MINUTES, KINDS, MAX_MINUTES, Watch

        if self.hooks.watches is None:
            raise ToolError("watching isn't available right now")
        ev = re.sub(r"[\s-]+", "_", str(a.get("event") or "").strip().lower())
        ev = _EVENT_SYN.get(ev, ev)
        if ev not in KINDS:
            raise ToolError("event must be one of: " + ", ".join(KINDS))
        app = str(a.get("app") or "").strip().strip("'\"")[:100]
        text = str(a.get("text") or "").strip().strip("'\"")[:200]
        gone = str(a.get("gone")).lower() in ("true", "1", "yes")
        then = str(a.get("then") or "").strip()[:600] or "tell the user what happened"
        raw_min = a.get("minutes")
        try:
            minutes = float(raw_min) if raw_min not in (None, "") else None
        except (TypeError, ValueError):
            raise ToolError("minutes must be a number") from None
        now = time.time()
        w = Watch(0, ev, "", then=then, app=app, text=text, gone=gone, started=now)
        if ev == "time":
            at_raw = str(a.get("at") or "").strip().lower()
            if at_raw:
                m = re.match(r"^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|uhr|h)?$", at_raw)
                if not m:
                    raise ToolError("at must be a time like 17:30")
                hh, mm = int(m.group(1)), int(m.group(2) or 0)
                if m.group(3) == "pm" and hh < 12:
                    hh += 12
                elif m.group(3) == "am" and hh == 12:
                    hh = 0
                if hh > 23 or mm > 59:
                    raise ToolError("at must be a time like 17:30")
                when = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
                if when.timestamp() <= now + 5:
                    when += timedelta(days=1)
                at = when.timestamp()
            elif minutes is not None:
                if not 0 < minutes <= MAX_MINUTES:
                    raise ToolError(f"minutes must be between 0 and {MAX_MINUTES}")
                at = now + minutes * 60
            else:
                raise ToolError("event time needs at (HH:MM) or minutes")
            w.at = w.until = at
            when_dt = datetime.fromtimestamp(at)
            w.label = f"the time {when_dt:%H:%M}" + ("" if when_dt.date() == datetime.now().date() else " tomorrow"
                                              if (when_dt.date() - datetime.now().date()).days == 1 else
                                              f" on {when_dt:%a %d %b}")
        else:
            limit = DEFAULT_MINUTES if minutes is None or minutes <= 0 else min(float(MAX_MINUTES), minutes)
            w.until = now + limit * 60
            if ev == "notification":
                who = friendly(app) if app else ""
                who = app if who.lower() == app.lower().replace(" ", "") or not who else who
                w.label = (f"a new {who} notification" if app else "a new notification from any app") + \
                    (f" mentioning '{text}'" if text else "")
            elif ev == "window_text":
                if not app or not text:
                    raise ToolError("window_text needs app (which window) and text (what to wait for)")
                w.label = f"'{text}' to {'disappear from' if gone else 'appear in'} {app}"
            elif ev == "window_opened":
                if not app and not text:
                    raise ToolError("window_opened needs app (or text from the window title)")
                w.label = f"a {app or text} window to open"
            elif ev == "window_closed":
                if not app:
                    raise ToolError("window_closed needs app: the window to wait for")
                w.label = f"{app} to close"
            elif ev == "file":
                raw = str(a.get("path") or "").strip()
                if not raw:
                    raise ToolError("event file needs path (a file, or a folder for any new file)")
                fp = self._resolve_watch_path(raw)
                w.path = str(fp)
                w.label = (f"a new file in {fp}" + (f" named like '{text}'" if text else "")) if fp.is_dir() else \
                    f"{fp.name} to {'change' if fp.exists() else 'appear'}"
        w.request = self.hooks.task_request() if self.hooks.task_request else ""
        a["_watch"] = w

    def _resolve_watch_path(self, raw: str) -> Path:
        """Like file paths elsewhere ('downloads', 'downloads/x.pdf', full paths), but the file
        may not exist yet and may be any type (it is only looked at, never opened)."""
        text = str(raw).strip().strip('"').strip("'").rstrip("\\/")
        alias = paths.known_folder(text, self.folders)
        if alias is not None:
            p = alias
        else:
            parts = re.split(r"[\\/]", text, maxsplit=1)
            alias = paths.known_folder(parts[0].rstrip(":"), self.folders) \
                if len(parts) == 2 and not re.match(r"^[a-zA-Z]:", text) and not text.startswith("\\\\") else None
            p = alias / parts[1] if alias is not None else Path(os.path.expandvars(text)).expanduser()
            if not p.is_absolute():
                p = (paths.known_folder("documents", self.folders) or Path.home()) / p
        p = p.resolve()
        if not any(p == r or r in p.parents for r in self._allowed_roots()):
            raise ToolError(f"{p} is outside the folders I'm allowed to look at ({', '.join(self.cfg.allowed_roots)})")
        return p

    def _allowed_roots(self) -> list[Path]:
        roots = []
        for alias in self.cfg.allowed_roots:
            p = paths.known_folder(alias, self.folders)
            if p is not None:
                roots.append(p.resolve())
        return roots

    def _resolve_dir(self, folder: str, must_exist: bool) -> Path:
        folder = folder.strip().strip('"')
        alias = paths.known_folder(folder, self.folders)
        p = alias if alias is not None else Path(os.path.expandvars(folder)).expanduser()
        if not p.is_absolute():
            p = (paths.known_folder("documents", self.folders) or Path.home()) / p
        p = p.resolve()
        if not any(p == r or r in p.parents for r in self._allowed_roots()):
            raise ToolError(f"{p} is outside the folders I'm allowed to touch "
                            f"({', '.join(self.cfg.allowed_roots)})")
        if must_exist and not p.is_dir():
            raise ToolError(f"folder {p} does not exist")
        return p

    @staticmethod
    def _safe_filename(name: str) -> str:
        name = Path(name.replace("\\", "/")).name
        name = re.sub(r"[^\w\-. ()]", "_", name).strip(" .") or "note"
        if Path(name).suffix.lower() not in (".txt", ".md"):
            name += ".txt"
        return name[:120]

    # ------------------------------------------------------------- execution
    def describe(self, spec: ToolSpec, args: dict[str, Any]) -> str:
        return spec.describe(args)

    def run(self, spec: ToolSpec, args: dict[str, Any]) -> Any:
        log.info("tool %s %s", spec.name, {k: (f"<{len(v)} chars>" if k in ("content", "text") else v)
                                         for k, v in args.items() if not k.startswith("_")})
        return spec.run(args)

    # desktop agent ----------------------------------------------------------------
    def _look(self, a: dict[str, Any]) -> ToolOutput:
        out = self.agent.look(a["_hwnd"] or None)
        out.display = f"👀 Looked at {a['_label']}"
        return out

    def _read(self, a: dict[str, Any]) -> ToolOutput:
        text = self.agent.read(a["_hwnd"])
        return ToolOutput(text, display=f"📖 Read {a['_label']}")

    def _click(self, a: dict[str, Any]) -> str:
        return self.agent.click(a["_eid"], a["_x"], a["_y"], a["_hwnd"] or None, a["_button"], a["_double"],
                                count=a.get("_count", 1), hold=a.get("_hold", 0.0), keys=a.get("_keys"))

    def _drag(self, a: dict[str, Any]) -> ToolOutput:
        return self.agent.drag(a["_src"], a["_dst"], a["_path"], a["_button"], a["_keys"], a["_seconds"])

    def _hover(self, a: dict[str, Any]) -> ToolOutput:
        return self.agent.hover(a["_ref"], a["_seconds"])

    def _zoom(self, a: dict[str, Any]) -> ToolOutput:
        return self.agent.zoom(a["_ref"], a["direction"], a["_steps"], a["_how"])

    def _watch(self, a: dict[str, Any]) -> ToolOutput:
        return self.agent.watch(a["_hwnd"], a["_screen"], a["_until"], a["_text"], a["_area"], a["_seconds"],
                                a["_sens"])

    def _type(self, a: dict[str, Any]) -> str:
        return self.agent.type(a["text"], a["_hwnd"], a["_eid"])

    # files ------------------------------------------------------------------------------
    def _read_file(self, a: dict[str, Any]) -> ToolOutput:
        from . import files

        try:
            tf = files.read_text(a["_path"])
        except files.FileError as exc:
            raise ToolError(str(exc)) from None
        return ToolOutput(files.show(tf), display=f"📖 Read {a['_path']}")

    def _edit_file(self, a: dict[str, Any]) -> str:
        from . import files

        p: Path = a["_path"]
        try:
            tf = files.read_text(p)
            spans = DesktopAgent.find_span(tf.text, a["old"], a["replace_all"])
            out, pos = [], 0
            for x, y in spans:
                out += [tf.text[pos:x], a["new"]]
                pos = y
            result = "".join(out) + tf.text[pos:]
            bk = files.backup(p)
            files.write_text(tf, result)
            ok = files.read_text(p).text == result
        except (files.FileError, AgentError, OSError) as exc:
            raise ToolError(str(exc)) from None
        if not ok:
            raise ToolError(f"I wrote {p.name}, but reading it back it doesn't match - the backup is {bk}")
        n = len(spans)
        return (f"Edited {p}: replaced {a['old'][:60]!r} with {a['new'][:60]!r} ({n} place{'s' if n != 1 else ''}). "
                f"Checked: the file now reads exactly as intended. Backup of the old version: {bk.name}. "
                "(Editors like VS Code show the change automatically; if it is open in one with unsaved changes, "
                "that window still shows the old text.)")

    def _write_file(self, a: dict[str, Any]) -> str:
        from . import files

        p: Path = a["_path"]
        try:
            if p.exists():
                bk = files.backup(p)
                tf = files.read_text(p)
                note = f" Backup of the old version: {bk.name}."
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                tf = files.TextFile(p, "", "utf-8", "\r\n" if p.suffix.lower() in (".txt", ".md", ".csv") else "\n",
                                    b"")
                note = ""
            files.write_text(tf, a["content"])
            ok = files.read_text(p).text == a["content"].replace("\r\n", "\n").replace("\r", "\n")
        except (files.FileError, OSError, UnicodeEncodeError) as exc:
            raise ToolError(str(exc)) from None
        if not ok:
            raise ToolError(f"I wrote {p}, but reading it back it doesn't match")
        verb = "Replaced the content of" if note else "Created"
        return f"{verb} {p} ({len(a['content']):,} characters). Checked: the file reads exactly as intended.{note}"

    def _open_file(self, a: dict[str, Any]) -> str:
        p: Path = a["_path"]
        before = {w.hwnd for w in self.agent.windows()}
        if a["app"] == "vscode":
            exe = self._vscode_exe()
            if exe is None:
                raise ToolError("VS Code isn't installed - try notepad")
            subprocess.Popen([exe, str(p)], close_fds=True)
            key = "code"
        else:
            subprocess.Popen(["notepad.exe", str(p)], close_fds=True)
            key = "notepad"
        return f"Opened {p.name} in {a['app']}." + self.agent.wait_for_window(before, key)

    def _wait_for(self, a: dict[str, Any]) -> ToolOutput:
        """Runs on a tool thread: note how things are now, then hand the watch to the background."""
        from .watch import WatchError

        mgr = self.hooks.watches
        w = a["_watch"]
        dup = mgr.duplicate_of(w)
        if dup is not None:
            return ToolOutput(f"Already watching for this (wait_for #{dup.id}: {dup.short()}) - nothing more to do. "
                              "End your turn now with ONE short sentence.", display=f"👀 Already watching for {dup.short()}")
        w.id, w.started = mgr.new_id(), time.time()
        try:
            already = mgr.checker.baseline(w)
        except WatchError as exc:
            raise ToolError(str(exc)) from None
        if already:
            return ToolOutput(f"No need to wait - it is already the case: {already} Carry on with the task now "
                              f"('{w.then[:120]}').", display=f"👀 Already the case: {already}")
        if self.agent.cancel.is_set():
            raise ToolError("stopped by the user")
        try:
            mgr.add(w)
        except WatchError as exc:
            raise ToolError(str(exc)) from None
        note = f" Note: {w.state['note']}." if w.state.get("note") else ""
        return ToolOutput(f"Watching now (wait_for #{w.id}): {w.short()}. Saved your note for then: '{w.then[:200]}'. "
                          f"The app checks in the background and wakes you up when it happens - don't call wait_for "
                          f"again and don't check yourself. End your turn now with ONE short sentence telling the user "
                          f"what you are watching for.{note}", display=f"👀 Watching for {w.short()}")

    def _stop_waiting(self, a: dict[str, Any]) -> str:
        mgr = self.hooks.watches
        act = mgr.active() if mgr is not None else []
        if not act:
            return "I'm not watching anything right now."
        listing = "; ".join(f"#{w.id} {w.short()}" for w in act)
        raw = a.get("id")
        if str(a.get("all")).lower() in ("true", "1", "yes"):
            mgr.cancel(None)
            return f"Stopped all watches: {listing}."
        if raw in (None, "", 0, "0"):
            if len(act) > 1:
                return f"I'm watching several things - call stop_waiting again with the id: {listing}"
            wid = act[0].id
        else:
            m = re.search(r"\d+", str(raw))
            wid = int(m.group()) if m else -1
        gone = mgr.cancel(wid)
        if not gone:
            return f"There is no watch #{wid}. Active: {listing}"
        return f"Stopped watching for {gone[0].short()}."

    def _replace(self, a: dict[str, Any]) -> str:
        return self.agent.replace(a["_hwnd"], a["old"], a["new"], a["replace_all"])

    def _keys(self, a: dict[str, Any]) -> str:
        return self.agent.keys(a["_combos"], a["_hwnd"], a.get("_hold", 0.0))

    def _scroll(self, a: dict[str, Any]) -> str:
        return self.agent.scroll(a["direction"], a["amount"], a["_hwnd"], a["_eid"], a.get("_xy"), a.get("_keys"))

    @staticmethod
    def _get_time(_a: dict[str, Any]) -> str:
        return datetime.now().strftime("It is %H:%M on %A, %d %B %Y.")

    def _open_app(self, a: dict[str, Any]) -> str:
        before = {w.hwnd for w in self.agent.windows()}
        target = a["_target"]
        if not isinstance(target, list):  # Start-menu shortcut or Store app (shell:AppsFolder\\AppID)
            self.platform.apps.open(str(target))     # (needs-admin apps were confirmed by the user: Windows shows UAC)
            msg = f"Opened {a['_label']}."
        else:
            first = target[0]
            if re.match(r"^[a-z][a-z0-9+.-]*:", first, re.I) and not re.match(r"^[a-z]:\\", first, re.I):
                self.platform.apps.open(first)  # protocol URI such as ms-settings: or https:
            elif a.get("_admin"):
                self.platform.apps.open_elevated(first, target[1:])  # Windows shows its UAC prompt
            else:
                try:
                    subprocess.Popen(target, shell=False, close_fds=True)
                except OSError as exc:
                    if getattr(exc, "winerror", 0) == 740:  # ERROR_ELEVATION_REQUIRED: never elevate unasked
                        self._admin_seen.add(str(target))
                        return (f"{a['name']} needs administrator rights, so I did NOT start it. "
                                "Ask the user; if they agree, call open_app again and they will get a confirmation.")
                    raise
            msg = f"Opened {a['name']}."
        if a.get("_admin"):
            msg += " Windows asks for admin rights now (UAC) - only the user can answer that."
        return msg + self.agent.wait_for_window(before, a["name"], timeout=20.0 if a.get("_admin") else 8.0)

    def _force_end(self, a: dict[str, Any]) -> str:
        plan = a["_plan"]
        name = ", ".join(plan.exes[:3])
        ended, denied = self.platform.processes.force_end(plan)
        a["_denied"] = denied   # the app asks the user before using admin rights for these
        msg = f"Force-ended {name}: {len(ended)} process{'es' if len(ended) != 1 else ''} stopped."
        if denied:
            msg += f" {len(denied)} process(es) can only be ended with admin rights."
        left = self.platform.processes.alive(ended)
        if left:
            msg += f" ({len(left)} still shutting down.)"
        return msg

    def force_end_admin(self, denied: list[int]) -> str:
        """Second step of /force end, only after the user said yes to using admin rights."""
        ok, why = self.platform.processes.force_end_elevated(denied)
        still = self.platform.processes.alive(denied)
        if ok and not still:
            return f"With admin rights: the remaining {len(denied)} process(es) are gone too."
        if ok:
            return f"{len(still)} process(es) are still running even with admin rights."
        return f"{len(denied)} process(es) need admin rights, but {why}."

    def _run_as_admin(self, a: dict[str, Any]) -> str:
        ok, why = self.platform.apps.run_as_admin(a["_target"])
        return f"Started {a['_label']} as administrator." if ok else f"Couldn't start {a['_label']} as admin: {why}."

    def _list_windows(self, _a: dict[str, Any]) -> str:
        wins = self.platform.windows.app_windows(self.hooks.own_pid)
        if not wins:
            return "No app windows are open."
        return "Open windows: " + " | ".join(w.label() for w in wins[:25])

    def _window_action(self, a: dict[str, Any]) -> str:
        self.platform.windows.window_action(a["_win"], a["action"])
        past = {"focus": "Switched to", "minimize": "Minimized", "maximize": "Maximized", "restore": "Restored"}
        return f"{past[a['action']]} {a['_win'].label()}."

    def _close_app(self, a: dict[str, Any]) -> str:
        n = self.platform.windows.close_windows(a["_wins"])
        return f"Asked {n} window(s) to close: " + "; ".join(w.label() for w in a["_wins"])

    def _search_web(self, a: dict[str, Any]) -> str:
        from urllib.parse import quote_plus

        url = self.cfg.search_url.replace("{q}", quote_plus(a["query"]))
        webbrowser.open(url, new=2)
        return f"Searched the web for '{a['query']}'."

    @staticmethod
    def _read_web_page(a: dict[str, Any]) -> ToolOutput:
        from . import webpage

        try:
            text = webpage.read(a["url"], a.get("start", 0))
        except webpage.WebError as e:
            raise ToolError(str(e)) from e
        first = text.split("\n", 1)[0].removeprefix("Web page:").strip()
        return ToolOutput(text, display=f"🌐 Read {first or a['url']}")

    def _open_settings(self, a: dict[str, Any]) -> str:
        self.platform.apps.open_settings(a["page"])
        return f"Opened the {a['page']} settings."

    def _media(self, a: dict[str, Any]) -> str:
        self.platform.system.media_key(a["action"])
        return f"Pressed media {a['action'].replace('_', '/')}."

    @staticmethod
    def _open_url(a: dict[str, Any]) -> str:
        webbrowser.open(a["url"], new=2)
        return f"Opened {a['url']} in the browser."

    def _open_folder(self, a: dict[str, Any]) -> str:
        self.platform.apps.open(str(a["_path"]))
        return f"Opened folder {a['_path']}."

    @staticmethod
    def _list_files(a: dict[str, Any]) -> str:
        p: Path = a["_path"]
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        names = [e.name + ("/" if e.is_dir() else "") for e in entries[:40]]
        more = f" (+{len(entries) - 40} more)" if len(entries) > 40 else ""
        return f"{p}: " + (", ".join(names) if names else "(empty)") + more

    @staticmethod
    def _create_note(a: dict[str, Any]) -> str:
        target: Path = a["_target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as fh:  # 'x' = never overwrite
            fh.write(a["content"])
        return f"Created {target}."

    def _get_volume(self, _a: dict[str, Any]) -> str:
        level, muted = self.platform.system.get_volume()
        return f"Volume is {level}%{' (muted)' if muted else ''}."

    def _set_volume(self, a: dict[str, Any]) -> str:
        return f"Volume set to {self.platform.system.set_volume(a['level'])}%."

    def _mute(self, a: dict[str, Any]) -> str:
        self.platform.system.set_mute(a["muted"])
        return "Sound muted." if a["muted"] else "Sound unmuted."

    def _set_timer(self, a: dict[str, Any]) -> str:
        return self.hooks.set_timer(a["minutes"] * 60.0, a["label"])

    def _set_reminder(self, a: dict[str, Any]) -> str:
        hh, mm = map(int, a["time"].split(":"))
        now = datetime.now()
        when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        secs = (when - now).total_seconds()
        self.hooks.set_timer(secs, a["text"])
        return f"Reminder set for {when.strftime('%a %H:%M')}: {a['text']}"
