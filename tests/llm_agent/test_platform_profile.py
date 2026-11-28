"""The agent side speaks the platform's words (Platform.profile): Windows' exactly as before, a Mac's with Command
shortcuts, Finder and its own apps - and the system's "not allowed yet" (PermissionNeeded) reaches the user."""
from __future__ import annotations

import dataclasses
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from llm_agent.agent_fakes import OWN_PID, el, parse_combo
from pixelcat.adapters.macos.profile import MAC
from pixelcat.domain import WINDOWS, PermissionNeeded, Snapshot, find_windows
from pixelcat.llm import prompt, replies
from pixelcat.llm import tools as tools_mod
from pixelcat.llm import watch as watch_mod
from pixelcat.llm.agent import AgentError, DesktopAgent, Shot
from pixelcat.llm.tools import ToolBox, ToolError
from pixelcat.llm.watch import Checker, Watch, WatchError

NOTEPAD, TEXTEDIT = 21, 31
NO_AX = PermissionNeeded("accessibility", "macOS hasn't allowed Pixel Cat to read other apps yet.")
NO_PICTURES = PermissionNeeded("screen_recording", "macOS hasn't allowed Pixel Cat to see the screen yet.")


def _mac_keys(combo: str) -> list[int]:
    """The fake parse_combo, knowing the Mac's key names too (as the macOS InputPort does)."""
    aliases = MAC.key_alias_map()
    return parse_combo("+".join(aliases.get(k, k) for k in combo.lower().split("+")))


@pytest.fixture
def mac(desk):  # type: ignore[no-untyped-def]
    desk.add_window(TEXTEDIT, "Notes.txt", "TextEdit")
    return dataclasses.replace(desk.platform, profile=MAC)


@pytest.fixture
def mac_tb(cfg, hooks, mac) -> ToolBox:  # type: ignore[no-untyped-def]
    tb = ToolBox(cfg, hooks.hooks(), platform=mac)
    tb.agent.shot = Shot(TEXTEDIT, (100, 100), 0.5, (400, 300))
    tb.agent.snap = Snapshot(TEXTEDIT, (100, 100, 900, 700), [el(1, "edit", "Title", (150, 150, 350, 170))])
    return tb


@pytest.fixture
def mac_ag(mac_tb: ToolBox) -> DesktopAgent:
    return mac_tb.agent


# ================================================================ Windows: as before
def test_the_windows_tool_texts_are_the_old_ones(toolbox: ToolBox) -> None:
    d = {n: sp.description for n, sp in toolbox.specs.items()}
    assert d["open_folder"].startswith("Open a folder in File Explorer. Use aliases")
    assert d["open_settings"] == "Open a Windows Settings page."
    assert d["lock_screen"] == "Lock the PC (Windows+L)."
    assert d["force_end"] == "Force-end an app like Task Manager."
    assert d["open_file"] == "Show a text file to the user in Notepad or VS Code (never runs it)."
    assert d["open_app"].endswith(" or any app installed in the Start menu. Waits until its window is open and in front.")
    assert "'wheel' = ctrl + mouse wheel (default" in d["zoom"]
    assert d["type_text"].endswith("For a new document use press_keys ctrl+n first.")
    assert d["replace_text"].startswith("CHANGE existing text in an editor (Notepad, VS Code, ...): replaces")
    assert "'ctrl+n', 'ctrl+s', 'enter', 'tab', 'esc', 'ctrl+a', 'down down enter', 'f5'." in d["press_keys"]
    assert toolbox.specs["click"].properties["keys"]["description"] == \
        "keys held down meanwhile: ctrl, shift, alt, win, or e.g. 'ctrl+shift'"
    assert toolbox.specs["wait_for"].properties["event"]["description"].startswith(
        "notification = a new Windows notification (chat messages")


def test_the_windows_prompt_and_errors_are_the_old_ones() -> None:
    assert prompt.build_system_prompt(["a"], profile=WINDOWS) == prompt.build_system_prompt(["a"])
    assert prompt.build_system_prompt(["a"]).startswith(prompt.SYSTEM_PROMPT + prompt.MEMORY_NOTE)
    for old, new in prompt._platform_words(WINDOWS):
        assert old == new and old in prompt.SYSTEM_PROMPT, old      # (each word is still in the prompt)
    assert replies.friendly_error("offline", "", "m", "u").endswith("```powershell\nollama serve\n```")
    assert replies.friendly_error("model_missing", "", "m", "u", shell=WINDOWS.shell) == \
        replies.friendly_error("model_missing", "", "m", "u")


def test_windows_finds_windows_by_its_own_names(desk) -> None:  # type: ignore[no-untyped-def]
    word = desk.add_window(40, "Report", "WINWORD.EXE")
    desk.add_window(41, "Report - Microsoft Word", "Microsoft Word")
    assert find_windows("word", desk.windows) == find_windows("word", desk.windows, WINDOWS.window_alias_map()) == [word]


# ================================================================ a Mac's words and shortcuts
def test_mac_tool_texts(mac_tb: ToolBox) -> None:
    d = {n: sp.description for n, sp in mac_tb.specs.items()}
    assert d["open_folder"].startswith("Open a folder in Finder.")
    assert d["open_settings"] == "Open a System Settings page."
    assert d["lock_screen"] == "Lock the PC (Control+Command+Q)."
    assert d["force_end"] == "Force-end an app like Activity Monitor."
    assert d["open_file"].startswith("Show a text file to the user in TextEdit or VS Code")
    assert "textedit" in d["open_app"] and "wordpad" in d["open_app"] and "character map" not in d["open_app"]
    assert d["open_app"].endswith(" or any app installed in the Applications folder. Waits until its window is open "
                                  "and in front.")
    assert "'wheel' = cmd + mouse wheel" in d["zoom"] and d["type_text"].endswith("press_keys cmd+n first.")
    assert "'cmd+n', 'cmd+s', 'enter', 'tab', 'esc', 'cmd+a'" in d["press_keys"]
    assert "ctrl, shift, alt (option), win (cmd)" in mac_tb.specs["drag"].properties["keys"]["description"]
    assert "a new macOS notification" in mac_tb.specs["wait_for"].properties["event"]["description"]


def test_mac_open_app_uses_the_macs_apps(mac_tb: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    desk.shortcuts["spotify"] = ("Spotify", "/Applications/Spotify.app")
    _spec, a = mac_tb.prepare("open_app", {"name": "Notepad"})
    assert a["_target"] == ["/usr/bin/open", "-a", "TextEdit"] and a["_label"] == "the app 'notepad'"
    _spec, a = mac_tb.prepare("open_app", {"name": "spotify"})
    assert a["_label"] == "'Spotify' (Applications folder)"


def test_mac_window_names(mac_tb: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(40, "Chat", "MSTeams")
    desk.add_window(41, "Report", "Microsoft Word")
    ag = mac_tb.agent
    assert ag.resolve("teams").hwnd == 40 and ag.resolve("word").hwnd == 41 and ag.resolve("notepad").hwnd == TEXTEDIT
    _spec, a = mac_tb.prepare("window_action", {"target": "teams", "action": "focus"})
    assert a["_win"].hwnd == 40


@pytest.mark.parametrize(("keys", "hard", "force"), [
    ("cmd+q", True, False), ("Command+W", True, False), ("ctrl+cmd+q", True, False), ("cmd+ctrl+q", True, False),
    ("control+command+q", True, False), ("opt+shift+cmd+q", True, False), ("alt+f4", True, False),
    ("cmd+option+esc", False, True), ("option+command+esc", False, True), ("cmd+space", False, True),
    ("cmd+s", False, False), ("ctrl+s", False, False), ("cmd+n", False, False),
])
def test_mac_closing_and_system_shortcuts(mac_tb: ToolBox, desk, keys: str, hard: bool, force: bool) -> None:  # type: ignore[no-untyped-def]
    desk.platform.input.answers("parse_combo", _mac_keys)
    _spec, a = mac_tb.prepare("press_keys", {"keys": keys, "target": "textedit"})
    assert (a.get("_confirm") == "hard", bool(a.get("_force_confirm"))) == (hard, force)


def test_mac_held_keys(mac_tb: ToolBox) -> None:
    _spec, a = mac_tb.prepare("click", {"element": 1, "keys": "cmd"})
    assert a["_keys"] == ["win"]
    _spec, a = mac_tb.prepare("scroll", {"direction": "up", "keys": "option+shift"})
    assert a["_keys"] == ["alt", "shift"]
    with pytest.raises(ToolError, match="only ctrl, shift, alt and win"):
        mac_tb.prepare("click", {"element": 1, "keys": "fn"})


def test_mac_command_lines(mac_ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(50, "zsh", "Terminal")
    desk.add_window(51, "", "Spotlight")
    desk.add_window(52, "Desktop", "Finder", cls="")
    assert mac_ag.is_command_line(50) and mac_ag.is_command_line(51) and not mac_ag.is_command_line(52)


def test_mac_paste_select_and_undo_use_command(mac_ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.foreground = TEXTEDIT
    desk.field_text = ""
    desk.focused = el(1, "edit", "Body")
    with pytest.raises(AgentError):            # (the fake field only takes ctrl+v: the text "didn't arrive")
        mac_ag.type("a longer text to paste", TEXTEDIT)
    assert ("press_combo", [0x5B, ord("V")], 0.0) in desk.calls            # cmd+v (Command is "win")
    desk.calls.clear()
    assert mac_ag._copy_all(TEXTEDIT) is None
    assert [c[1] for c in desk.calls if c[0] == "press_combo"] == [[0x5B, ord("A")], [0x5B, ord("C")]]
    assert mac_ag._keys_of(MAC.doc_end) == [0x5B, 0x28]                     # cmd+down: the end of the document


def test_mac_zoom_holds_command(mac_ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.pinch_result = "failed"
    out = mac_ag.zoom(("mid", TEXTEDIT), "in", 2, how="pinch")
    assert out.text.startswith("Zoomed in (2 steps, cmd + mouse wheel) in TextEdit: 'Notes.txt'. (macOS didn't take "
                               "a touch pinch here, so I used cmd + mouse wheel instead.)")
    assert ("hold", ["win"]) in desk.calls                                  # Command, as the ports name it


def test_mac_prompt_and_errors() -> None:
    text = prompt.build_system_prompt(["a"], profile=MAC)
    assert "lives on the user's Mac desktop." in text and "press_keys for shortcuts: cmd+n new document" in text
    assert "ctrl+" not in text and "Windows" not in text and "```zsh" in text
    assert "open_app textedit -> press_keys cmd+n" in text and "keys='cmd'/'shift' held\n  (cmd-click" in text
    assert "(cmd + wheel" in text and "or macOS asks for an administrator password, stop" in text
    assert MAC.prompt_note in text and text.index(MAC.prompt_note) < text.index(prompt.MEMORY_NOTE)
    named = prompt.build_system_prompt([], "Mochi", MAC)
    assert named.startswith("You are Mochi, a tiny") and "Mac desktop" in named
    assert replies.friendly_error("offline", "", "m", "u", shell=MAC.shell).endswith("```zsh\nollama serve\n```")


def test_mac_watches(mac, desk) -> None:  # type: ignore[no-untyped-def]
    class NoFeed:
        def last_id(self) -> int:
            raise OSError("macOS doesn't let apps read other apps' notifications")

    mac.notifications.answers("open_feed", NoFeed)
    checker = Checker(OWN_PID, platform=mac)
    assert [w.hwnd for w in checker._windows("notepad")] == [TEXTEDIT]
    with pytest.raises(WatchError, match="I can't read macOS notifications on this PC"):
        checker.baseline(Watch(1, "notification", "a new notification", app="teams"))


# ================================================================ the system's "not allowed yet"
def test_read_tells_what_to_allow(mac_ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.platform.accessibility.answers("snapshot", lambda *a, **k: (_ for _ in ()).throw(NO_AX))
    with pytest.raises(PermissionNeeded) as exc:
        mac_ag.read(TEXTEDIT)
    assert exc.value is NO_AX


def test_other_snapshot_errors_still_read_as_before(agent: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(NOTEPAD, "Notes.txt - Notepad", "notepad.exe")
    desk.platform.accessibility.answers("snapshot", lambda *a, **k: (_ for _ in ()).throw(OSError("COM error")))
    with pytest.raises(AgentError, match="doesn't let me read its contents"):
        agent.read(NOTEPAD)


def test_look_carries_on_without_the_element_list(mac_ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.platform.accessibility.answers("snapshot", lambda *a, **k: (_ for _ in ()).throw(NO_AX))
    out = mac_ag.look(TEXTEDIT)
    assert out.images and "No element list for this window" in out.text


def test_waiting_for_window_text_tells_what_to_allow(mac, mac_ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.platform.accessibility.answers("snapshot", lambda *a, **k: (_ for _ in ()).throw(NO_AX))
    with pytest.raises(PermissionNeeded):
        mac_ag.watch(TEXTEDIT, None, until="text", text="Done", seconds=1)
    checker = Checker(OWN_PID, platform=mac)
    w = Watch(1, "window_text", "'Done' to appear", app="textedit", text="Done")
    with pytest.raises(PermissionNeeded):
        checker.baseline(w)
    assert checker._check_window_text(w) == ""                  # the background check stays quiet
    assert watch_mod._window_text(mac.accessibility, TEXTEDIT) is None


class QuietCapture:
    """A capture like the Mac's: no Screen Recording yet; ``grab*`` would ask for it, inside ``quiet()`` they don't."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._local = threading.local()
        self.asked = 0

    @contextmanager
    def quiet(self) -> Iterator[None]:
        self._local.quiet = True
        try:
            yield
        finally:
            self._local.quiet = False

    def _deny(self, *_a: Any) -> None:
        if not getattr(self._local, "quiet", False):
            self.asked += 1                       # (the real one would show macOS' prompt here)
        raise NO_PICTURES

    grab = grab_without = grab_window = _deny

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_open_app_never_asks_for_screen_pictures(cfg, hooks, mac, desk, clock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cap = QuietCapture(mac.capture)
    tb = ToolBox(cfg, hooks.hooks(), platform=dataclasses.replace(mac, capture=cap))

    def started(cmd: list[str], **_k: Any) -> None:
        assert cmd == ["/usr/bin/open", "-a", "TextEdit"]
        desk.add_window(60, "Untitled", "TextEdit")

    monkeypatch.setattr(tools_mod, "subprocess", SimpleNamespace(Popen=started))
    _spec, a = tb.prepare("open_app", {"name": "textedit"})
    t0 = clock.t
    out = tb._open_app(a)
    assert "Its window is ready and in front" in out and cap.asked == 0
    assert clock.t - t0 < 4.0                                   # a short wait instead of comparing pictures
    assert tb.agent._fingerprint(60) is None and cap.asked == 0


def test_a_new_window_that_cant_be_raised_still_opens(mac_ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    before = {w.hwnd for w in mac_ag.windows()}
    desk.add_window(60, "Untitled", "TextEdit")
    desk.platform.windows.answers("bring_to_front", lambda *a, **k: (_ for _ in ()).throw(NO_AX))
    assert mac_ag.wait_for_window(before, "textedit").startswith(" Its window is ready and in front")
    assert mac_ag.last_hwnd == 60
