"""ToolBox.run: what each tool does (desktop, browser, launching and files mocked; files in temp folders)."""
from __future__ import annotations

import codecs
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from llm_agent.agent_fakes import OWN_PID, FakeFeed
from pixelcat.domain import KillPlan
from pixelcat.llm import tools as tools_mod
from pixelcat.llm import webpage
from pixelcat.llm.agent import ToolOutput
from pixelcat.llm.tools import ToolBox, ToolError


def run(tb: ToolBox, tool: str, internal: bool = False, **args):  # type: ignore[no-untyped-def]
    spec, a = tb.prepare(tool, args, internal=internal)
    return tb.run(spec, a)


@pytest.fixture
def no_wait(monkeypatch, toolbox: ToolBox) -> list:  # type: ignore[no-untyped-def]
    """The agent's 'wait for the new window' after launching something."""
    seen: list = []

    def fake(before, name, timeout=15.0):  # type: ignore[no-untyped-def]
        seen.append((before, name, timeout))
        return " (window ready)"
    monkeypatch.setattr(toolbox.agent, "wait_for_window", fake)
    return seen


# ------------------------------------------------------------------ read-only and in-app
def test_get_time(toolbox: ToolBox) -> None:
    out = run(toolbox, "get_time")
    assert re.fullmatch(r"It is \d\d:\d\d on \w+, \d\d \w+ \d{4}\.", out)


def test_system_info(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "system_info") == "RAM: 8 GB | CPU: 3%"


def test_list_windows(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "list_windows") == "No app windows are open."
    for i in range(30):
        desk.add_window(100 + i, f"Doc {i}", "winword.exe", minimized=i == 1)
    out = run(toolbox, "list_windows")
    assert out.startswith("Open windows: winword: Doc 0 | winword: Doc 1 (minimized) | winword: Doc 2")
    assert out.endswith("winword: Doc 24") and out.count(" | ") == 24


def test_volume_tools(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "get_volume") == "Volume is 40%."
    desk.volume = (0, True)
    assert run(toolbox, "get_volume") == "Volume is 0% (muted)."
    assert run(toolbox, "set_volume", level="70") == "Volume set to 70%."
    assert run(toolbox, "mute", muted="true") == "Sound muted."
    assert run(toolbox, "mute", muted=False) == "Sound unmuted."
    assert desk.called("set_volume") == [(70,)] and desk.called("set_mute") == [(True,), (False,)]


def test_timer_and_reminder_use_the_app_timer(toolbox: ToolBox, hooks) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "set_timer", minutes=2.5, label="Tea") == "Timer set: Tea."
    assert hooks.calls == [("set_timer", (150.0, "Tea"))]
    later = datetime.now() + timedelta(minutes=10)
    out = run(toolbox, "set_reminder", time=f"{later:%H:%M}", text="Call mum")
    secs, label = hooks.calls[-1][1]
    assert label == "Call mum" and 9 * 60 - 1 <= secs <= 10 * 60
    assert out == f"Reminder set for {later:%a %H:%M}: Call mum"


def test_reminder_in_the_past_is_for_tomorrow(toolbox: ToolBox, hooks) -> None:  # type: ignore[no-untyped-def]
    earlier = datetime.now() - timedelta(minutes=10)
    run(toolbox, "set_reminder", time=f"{earlier:%H:%M}", text="x")
    secs = hooks.calls[-1][1][0]
    assert 23 * 3600 < secs <= 24 * 3600


def test_hook_tools(toolbox: ToolBox, hooks) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "cat_action", action="Zoomies") == "The cat does zoomies."
    assert run(toolbox, "take_screenshot") == "Saved a screenshot."
    assert run(toolbox, "copy_to_clipboard", text="meow") == "Copied."
    assert hooks.calls == [("cat_action", "zoomies"), ("screenshot", None), ("set_clipboard", "meow")]


# ------------------------------------------------------------------ browser, folders, settings, media, lock
def test_open_url_and_search(toolbox: ToolBox, cfg, desk) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "open_url", url="example.com/a") == "Opened https://example.com/a in the browser."
    assert run(toolbox, "search_web", query="pixel cats & dogs") == "Searched the web for 'pixel cats & dogs'."
    assert desk.called("browser") == [("https://example.com/a", ("new", 2)),
                                      ("https://www.bing.com/search?q=pixel+cats+%26+dogs", ("new", 2))]
    cfg.search_url = "https://duck.example/?q={q}&x=1"
    run(toolbox, "search_web", query="ä b")
    assert desk.called("browser")[-1][0] == "https://duck.example/?q=%C3%A4+b&x=1"


def test_open_folder_uses_the_shell(toolbox: ToolBox, folders, desk) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "open_folder", folder="downloads") == f"Opened folder {folders['downloads'].resolve()}."
    assert desk.called("open") == [(str(folders["downloads"].resolve()),)]


def test_list_files(toolbox: ToolBox, folders) -> None:  # type: ignore[no-untyped-def]
    d = folders["documents"]
    assert run(toolbox, "list_files", folder="documents") == f"{d.resolve()}: (empty)"
    (d / "b.txt").write_text("x")
    (d / "A.md").write_text("x")
    (d / "zdir").mkdir()
    assert run(toolbox, "list_files", folder="documents") == f"{d.resolve()}: zdir/, A.md, b.txt"
    for i in range(45):
        (d / f"f{i:02d}.txt").write_text("x")
    out = run(toolbox, "list_files", folder="documents")
    assert out.endswith(" (+8 more)") and out.count(", ") == 39


def test_settings_media_lock(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "open_settings", page="Bluetooth") == "Opened the bluetooth settings."
    assert desk.called("open_settings") == [("bluetooth",)]
    assert run(toolbox, "media_control", action="pause") == "Pressed media play/pause."
    assert desk.called("media_key") == [("play_pause",)]
    assert run(toolbox, "lock_screen") == "Locked the PC."
    desk.platform.system.answers("lock_screen", lambda: False)
    assert run(toolbox, "lock_screen") == "Could not lock the PC."


def test_window_action_and_close_app(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    w = desk.add_window(11, "Inbox", "olk.exe")
    desk.add_window(12, "Calendar", "olk.exe")
    assert run(toolbox, "window_action", target="olk", action="minimize") == "Minimized olk: Inbox."
    assert desk.called("window_action") == [(w, "minimize")]
    assert run(toolbox, "window_action", target="inbox", action="focus") == "Switched to olk: Inbox."
    assert run(toolbox, "close_app", target="olk") == "Asked 2 window(s) to close: olk: Inbox; olk: Calendar"


def test_create_note_never_overwrites(toolbox: ToolBox, folders) -> None:  # type: ignore[no-untyped-def]
    spec, a = toolbox.prepare("create_note", {"filename": "plan", "content": "1. eat\n2. nap"})
    target = folders["notes"].resolve() / "plan.txt"
    assert toolbox.run(spec, a) == f"Created {target}."
    assert target.read_text(encoding="utf-8") == "1. eat\n2. nap"
    with pytest.raises(FileExistsError):
        toolbox.run(spec, a)                      # prepared before the file existed: 'x' mode refuses
    assert run(toolbox, "create_note", filename="plan.txt", content="new") == \
        f"Created {target.with_name('plan (2).txt')}."


# ------------------------------------------------------------------ web pages
def test_read_web_page(toolbox: ToolBox, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    got: list = []

    def fake_read(url: str, start: int = 0) -> str:
        got.append((url, start))
        return "Web page: Cats\nAddress: https://cats.example\n\nmeow"
    monkeypatch.setattr(webpage, "read", fake_read)
    out = run(toolbox, "read_web_page", url="cats.example", start=5)
    assert isinstance(out, ToolOutput) and out.text.endswith("meow") and out.display == "🌐 Read Cats"
    assert got == [("https://cats.example", 5)]
    monkeypatch.setattr(webpage, "read", lambda url, start=0: "Web page:\nAddress: x")
    assert run(toolbox, "read_web_page", url="x.example").display == "🌐 Read https://x.example"


def test_read_web_page_errors(toolbox: ToolBox, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(url: str, start: int = 0) -> str:
        raise webpage.WebError("that address is on this PC or the local network")
    monkeypatch.setattr(webpage, "read", boom)
    with pytest.raises(ToolError, match="^that address is on this PC or the local network$"):
        run(toolbox, "read_web_page", url="http://192.168.1.1")


# ------------------------------------------------------------------ launching apps
def test_open_app_runs_the_command_without_a_shell(toolbox: ToolBox, desk, no_wait) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(5, "Old", "x.exe")
    assert run(toolbox, "open_app", name="notepad") == "Opened notepad. (window ready)"
    assert desk.called("popen") == [(["notepad.exe"], ("close_fds", True), ("shell", False))]
    assert no_wait == [({5}, "notepad", 8.0)]
    assert not desk.called("open") and not desk.called("open_elevated")


def test_open_app_uri_and_start_menu_are_opened_by_the_shell(toolbox: ToolBox, desk, no_wait) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "open_app", name="settings") == "Opened settings. (window ready)"
    desk.shortcuts["spotify"] = ("Spotify", Path("C:/Start/Spotify.lnk"))
    assert run(toolbox, "open_app", name="spotify") == "Opened 'Spotify' (Start menu). (window ready)"
    assert desk.called("open") == [("ms-settings:",), (str(Path("C:/Start/Spotify.lnk")),)]
    assert not desk.called("popen")


def test_open_app_with_admin_rights(cfg, hooks, desk, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cfg.apps["tool"] = ["C:\\Tools\\tool.exe", "--fast", "a b"]
    tb = ToolBox(cfg, hooks.hooks(), platform=hooks.platform)
    waits: list = []
    monkeypatch.setattr(tb.agent, "wait_for_window", lambda b, n, timeout=15.0: waits.append(timeout) or "")
    desk.admin_targets.add(str(cfg.apps["tool"]))
    out = run(tb, "open_app", name="tool")
    assert out == "Opened tool. Windows asks for admin rights now (UAC) - only the user can answer that."
    assert desk.called("open_elevated") == [("C:\\Tools\\tool.exe", ["--fast", "a b"])]
    assert waits == [20.0]


def test_open_app_refused_for_elevation_is_not_retried_silently(toolbox: ToolBox, desk, no_wait, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def popen(*a, **k):  # type: ignore[no-untyped-def]
        exc = OSError("The requested operation requires elevation")
        exc.winerror = 740  # type: ignore[attr-defined]
        raise exc
    monkeypatch.setattr(tools_mod.subprocess, "Popen", popen)
    out = run(toolbox, "open_app", name="paint")
    assert out.startswith("paint needs administrator rights, so I did NOT start it.")
    a = toolbox.prepare("open_app", {"name": "paint"})[1]
    assert a["_admin"] is True and a["_confirm"] == "hard" and a["_force_confirm"] is True

    def other(*a, **k):  # type: ignore[no-untyped-def]
        raise OSError(2, "not found")
    monkeypatch.setattr(tools_mod.subprocess, "Popen", other)
    with pytest.raises(OSError):
        run(toolbox, "open_app", name="calculator")


def test_force_end(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    desk.plan = KillPlan("chrome.exe (2 processes)", [1, 2], ["chrome.exe"])
    spec, a = toolbox.prepare("force_end", {"target": "chrome"}, internal=True)
    assert toolbox.run(spec, a) == "Force-ended chrome.exe: 2 processes stopped."
    assert a["_denied"] == []
    desk.platform.processes.answers("force_end", lambda plan: ([1], [2, 3]))
    desk.platform.processes.answers("alive", lambda pids: [1])
    out = toolbox.run(spec, a)
    assert out == ("Force-ended chrome.exe: 1 process stopped. 2 process(es) can only be ended with admin rights. "
                   "(1 still shutting down.)")
    assert a["_denied"] == [2, 3]


@pytest.mark.parametrize(("ok", "alive", "msg"), [
    (True, [], "With admin rights: the remaining 2 process(es) are gone too."),
    (True, [7], "1 process(es) are still running even with admin rights."),
    (False, [], "2 process(es) need admin rights, but you said no."),
])
def test_force_end_admin(toolbox: ToolBox, desk, ok: bool, alive: list, msg: str) -> None:  # type: ignore[no-untyped-def]
    desk.platform.processes.answers("force_end_elevated", lambda pids: (ok, "you said no"))
    desk.platform.processes.answers("alive", lambda pids: alive)
    assert toolbox.force_end_admin([7, 8]) == msg


def test_run_as_admin(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    assert run(toolbox, "run_as_admin", internal=True, name="notepad") == "Started 'notepad' as administrator."
    assert desk.called("run_as_admin") == [("notepad.exe",)]
    desk.platform.apps.answers("run_as_admin", lambda t: (False, "the user said no"))
    assert run(toolbox, "run_as_admin", internal=True, name="notepad") == \
        "Couldn't start 'notepad' as admin: the user said no."


# ------------------------------------------------------------------ files
@pytest.fixture
def js(folders) -> Path:  # type: ignore[no-untyped-def]
    p = folders["desktop"] / "sort.js"
    p.write_bytes(b"const a = [3, 1];\r\narray.sort(;\r\narray.sort(;\r\n")
    return p


def backups(isolated_dirs: Path) -> list[Path]:
    folder = isolated_dirs / "AppData" / "Roaming" / "PixelCat" / "backups"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_read_file(toolbox: ToolBox, js: Path) -> None:
    out = run(toolbox, "read_file", path="desktop/sort.js")
    assert isinstance(out, ToolOutput) and out.display == f"📖 Read {js.resolve()}"
    assert out.text == (f'File {js.resolve()} - 4 lines, 44 characters:\n"""\nconst a = [3, 1];\narray.sort(;\n'
                        'array.sort(;\n\n"""')


def test_read_file_errors_become_tool_errors(toolbox: ToolBox, js: Path) -> None:
    spec, a = toolbox.prepare("read_file", {"path": "desktop/sort.js"})
    js.unlink()
    with pytest.raises(ToolError, match="does not exist"):
        toolbox.run(spec, a)


def test_edit_file_keeps_a_backup_and_line_endings(toolbox: ToolBox, js: Path, isolated_dirs) -> None:  # type: ignore[no-untyped-def]
    out = run(toolbox, "edit_file", path="desktop/sort.js", old="sort(;", new="sort();", replace_all=True)
    assert js.read_bytes() == b"const a = [3, 1];\r\narray.sort();\r\narray.sort();\r\n"
    [bk] = backups(isolated_dirs)
    assert bk.name.endswith("_sort.js") and bk.read_bytes() == b"const a = [3, 1];\r\narray.sort(;\r\narray.sort(;\r\n"
    assert out.startswith(f"Edited {js.resolve()}: replaced 'sort(;' with 'sort();' (2 places). Checked: the file now "
                          f"reads exactly as intended. Backup of the old version: {bk.name}.")


def test_edit_file_tolerates_spacing(toolbox: ToolBox, js: Path) -> None:
    js.write_text("array.sort((a,b)=>a-b);\nx = 1\n", encoding="utf-8")
    out = run(toolbox, "edit_file", path="desktop/sort.js", old="array.sort((a, b) => a - b);",
              new="array.sort((a, b) => b - a);")
    assert "(1 place)" in out
    assert js.read_text(encoding="utf-8") == "array.sort((a, b) => b - a);\nx = 1\n"


def test_edit_file_refuses_ambiguous_text_at_run_time(toolbox: ToolBox, js: Path) -> None:
    spec, a = toolbox.prepare("edit_file", {"path": "desktop/sort.js", "old": "const", "new": "let"})
    js.write_text("const\nconst\n", encoding="utf-8")
    with pytest.raises(ToolError, match="^'const' appears 2 times - include more"):
        toolbox.run(spec, a)


def test_edit_file_reports_a_mismatch(toolbox: ToolBox, js: Path, monkeypatch, isolated_dirs) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import files

    spec, a = toolbox.prepare("edit_file", {"path": "desktop/sort.js", "old": "const", "new": "let"})
    monkeypatch.setattr(files, "write_text", lambda tf, text: None)
    with pytest.raises(ToolError) as exc:
        toolbox.run(spec, a)
    [bk] = backups(isolated_dirs)
    assert str(exc.value) == f"I wrote sort.js, but reading it back it doesn't match - the backup is {bk}"


@pytest.mark.parametrize(("name", "data"), [("list.txt", b"- milk\r\n- eggs"), ("list.md", b"- milk\r\n- eggs"),
                                            ("data.csv", b"- milk\r\n- eggs"), ("app.py", b"- milk\n- eggs"),
                                            ("sub/dir/new.json", b"- milk\n- eggs")])
def test_write_file_creates_with_platform_line_endings(toolbox: ToolBox, folders, name: str, data: bytes) -> None:  # type: ignore[no-untyped-def]
    out = run(toolbox, "write_file", path=f"documents/{name}", content="- milk\n- eggs")
    p = (folders["documents"] / name).resolve()
    assert p.read_bytes() == data
    assert out == f"Created {p} (13 characters). Checked: the file reads exactly as intended."


def test_write_file_overwrite_keeps_encoding_and_backup(toolbox: ToolBox, folders, isolated_dirs) -> None:  # type: ignore[no-untyped-def]
    p = folders["desktop"] / "old.txt"
    p.write_bytes(codecs.BOM_UTF8 + "Grüezi\r\n".encode())
    out = run(toolbox, "write_file", path="desktop/old.txt", content="Neu\nZeile", overwrite=True)
    assert p.read_bytes() == codecs.BOM_UTF8 + "Neu\r\nZeile".encode()
    [bk] = backups(isolated_dirs)
    assert out.endswith(f"Backup of the old version: {bk.name}.") and out.startswith("Replaced the content of")


def test_write_file_unencodable_text(toolbox: ToolBox, folders) -> None:  # type: ignore[no-untyped-def]
    p = folders["desktop"] / "ansi.txt"
    p.write_bytes("Grüße\n".encode("cp1252") + b"\xff\xfe")
    with pytest.raises(ToolError, match="codec can't encode"):
        run(toolbox, "write_file", path="desktop/ansi.txt", content="😺", overwrite=True)


@pytest.mark.parametrize(("app", "argv0"), [("notepad", "notepad.exe"), ("vscode", "C:/VS/Code.exe")])
def test_open_file_shows_it_in_an_editor(toolbox: ToolBox, js: Path, desk, no_wait, monkeypatch,  # type: ignore[no-untyped-def]
                                         app: str, argv0: str) -> None:
    desk.editors["vscode"] = ["C:/VS/Code.exe"]
    out = run(toolbox, "open_file", path="desktop/sort.js", app=app)
    assert out == f"Opened sort.js in {app}. (window ready)"
    assert desk.called("popen") == [([argv0, str(js.resolve())], ("close_fds", True))]
    assert no_wait[0][1] == ("code" if app == "vscode" else "notepad")
    assert not desk.called("open") and not desk.called("open_elevated")


def test_open_file_without_vscode(toolbox: ToolBox, js: Path, desk) -> None:  # type: ignore[no-untyped-def]
    desk.editors["vscode"] = ["C:/VS/Code.exe"]
    spec, a = toolbox.prepare("open_file", {"path": "desktop/sort.js", "app": "vscode"})
    del desk.editors["vscode"]
    with pytest.raises(ToolError, match="^VS Code isn't installed - try notepad$"):
        toolbox.run(spec, a)


def test_open_file_without_any_editor(toolbox: ToolBox, js: Path, desk) -> None:  # type: ignore[no-untyped-def]
    spec, a = toolbox.prepare("open_file", {"path": "desktop/sort.js", "app": "notepad"})
    desk.editors.clear()
    with pytest.raises(ToolError, match="^there's no text editor I can open here$"):
        toolbox.run(spec, a)
    assert not desk.called("popen")


# ------------------------------------------------------------------ screen tools hand over to the agent
def test_screen_tools_call_the_desktop_agent(toolbox: ToolBox, desk, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm.agent import Shot

    desk.add_window(21, "Notes - Notepad", "notepad.exe")
    ag = toolbox.agent
    ag.shot = Shot(21, (100, 100), 1.0, (800, 600))
    got: list = []
    for meth in ("click", "drag", "hover", "zoom", "watch", "type", "replace", "keys", "scroll", "read", "look",
                 "wait"):
        monkeypatch.setattr(ag, meth, lambda *a, _m=meth, **k: got.append((_m, a, k)) or ToolOutput(_m))
    run(toolbox, "click", x=5, y=6, button="right", keys="ctrl")
    run(toolbox, "drag", from_x=1, from_y=2, to_x=3, to_y=4)
    run(toolbox, "move_mouse", x=1, y=1, seconds=2)
    run(toolbox, "zoom", direction="in", steps=2)
    run(toolbox, "watch_screen", until="still", seconds=5)
    run(toolbox, "type_text", text="hi")
    run(toolbox, "replace_text", old="a", new="b")
    run(toolbox, "press_keys", keys="ctrl+s", hold=0.5)
    run(toolbox, "scroll", direction="down", amount=3)
    run(toolbox, "read_window")
    look = run(toolbox, "look_at_screen")
    run(toolbox, "wait", seconds=2)
    assert got == [
        ("click", (None, 5.0, 6.0, 21, "right", False), {"count": 1, "hold": 0.0, "keys": ["ctrl"]}),
        ("drag", (("xy", 1.0, 2.0), ("xy", 3.0, 4.0), [], "left", [], 0.6), {}),
        ("hover", (("xy", 1.0, 1.0), 2.0), {}),
        ("zoom", (("mid", 21), "in", 2, "wheel"), {}),
        ("watch", (21, None, "still", "", None, 5.0, "normal"), {}),
        ("type", ("hi", 21, None), {}),
        ("replace", (21, "a", "b", False), {}),
        ("keys", ([("ctrl+s", [0x11, ord("S")])], 21, 0.5), {}),
        ("scroll", ("down", 3, 21, None, None, []), {}),
        ("read", (21,), {}),
        ("look", (None,), {}),
        ("wait", (2.0,), {}),
    ]
    assert look.display == "👀 Looked at your screen"


def test_read_window_output(toolbox: ToolBox, desk, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(21, "Notes - Notepad", "notepad.exe")
    monkeypatch.setattr(toolbox.agent, "read", lambda hwnd: "Window text")
    out = run(toolbox, "read_window")
    assert (out.text, out.display) == ("Window text", "📖 Read notepad: Notes - Notepad")


# ------------------------------------------------------------------ wait_for / stop_waiting with the real manager
@pytest.fixture
def watches(monkeypatch, desk):  # type: ignore[no-untyped-def]
    from pixelcat.llm.watch import WatchManager

    mgr = WatchManager(OWN_PID, platform=desk.platform)
    monkeypatch.setattr(mgr, "_ensure_thread", lambda: None)
    mgr.checker.feed = FakeFeed()
    return mgr


@pytest.fixture
def wtb(cfg, hooks, watches) -> ToolBox:  # type: ignore[no-untyped-def]
    return ToolBox(cfg, hooks.hooks(watches=watches), platform=hooks.platform)


def test_wait_for_starts_a_watch(wtb: ToolBox, watches) -> None:  # type: ignore[no-untyped-def]
    out = run(wtb, "wait_for", event="time", minutes=30, then="open outlook")
    w = watches.active()[0]
    assert w.id == 1 and w.then == "open outlook" and w.request == "the user's request"
    assert out.text.startswith(f"Watching now (wait_for #1): {w.short()}. Saved your note for then: 'open outlook'.")
    assert "End your turn now with ONE short sentence" in out.text
    assert out.display == f"👀 Watching for {w.short()}"


def test_wait_for_twice_keeps_one_watch(wtb: ToolBox, watches) -> None:  # type: ignore[no-untyped-def]
    run(wtb, "wait_for", event="notification", app="teams", text="Anna")
    out = run(wtb, "wait_for", event="message", app="Teams", text="anna")
    assert len(watches.active()) == 1
    assert out.text.startswith("Already watching for this (wait_for #1: ") and out.display.startswith("👀 Already")


def test_wait_for_something_that_is_already_the_case(wtb: ToolBox, watches, desk) -> None:  # type: ignore[no-untyped-def]
    out = run(wtb, "wait_for", event="window_text", app="exporter", text="Exporting", gone=True)
    assert out.text == ("No need to wait - it is already the case: No window matches 'exporter' right now, so "
                        "'Exporting' isn't shown anywhere. Carry on with the task now ('tell the user what happened').")
    assert not watches.active()


def test_wait_for_baseline_errors_and_cancel(wtb: ToolBox, watches) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ToolError, match="^no open window matches 'setup' - it may be closed already$"):
        run(wtb, "wait_for", event="window_closed", app="setup")
    wtb.agent.cancel.set()
    with pytest.raises(ToolError, match="^stopped by the user$"):
        run(wtb, "wait_for", event="time", minutes=1)
    assert not watches.active()


def test_wait_for_limit(wtb: ToolBox, watches) -> None:  # type: ignore[no-untyped-def]
    for i in range(10):
        run(wtb, "wait_for", event="time", minutes=10 + i * 5)
    with pytest.raises(ToolError, match=r"I'm already watching 10 things - stop one first \(stop_waiting\)"):
        run(wtb, "wait_for", event="time", minutes=500)


def test_wait_for_note_about_minimized_windows(wtb: ToolBox, watches, desk, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import watch

    desk.add_window(7, "Exporter", "exporter.exe", minimized=True)
    monkeypatch.setattr(watch, "_window_text", lambda a11y, hwnd, timeout=8.0: None)
    out = run(wtb, "wait_for", event="window_text", app="exporter", text="Done")
    assert out.text.endswith("Note: the window is minimized - I can only read it while it isn't minimized (behind "
                             "other windows is fine).")


def test_stop_waiting(wtb: ToolBox, watches) -> None:  # type: ignore[no-untyped-def]
    assert run(wtb, "stop_waiting") == "I'm not watching anything right now."
    run(wtb, "wait_for", event="time", minutes=30)
    run(wtb, "wait_for", event="window_opened", app="zoom")
    w1, w2 = watches.active()
    out = run(wtb, "stop_waiting")
    assert out == f"I'm watching several things - call stop_waiting again with the id: #1 {w1.short()}; #2 {w2.short()}"
    assert run(wtb, "stop_waiting", id="#7") == f"There is no watch #7. Active: #1 {w1.short()}; #2 {w2.short()}"
    assert run(wtb, "stop_waiting", id="2") == f"Stopped watching for {w2.short()}."
    assert run(wtb, "stop_waiting") == f"Stopped watching for {w1.short()}."
    run(wtb, "wait_for", event="time", minutes=30)
    run(wtb, "wait_for", event="time", minutes=90)
    assert run(wtb, "stop_waiting", all="yes").startswith("Stopped all watches: #3 ")
    assert not watches.active()
    assert run(wtb, "stop_waiting", id="x") == "I'm not watching anything right now."


def test_stop_waiting_without_manager(toolbox: ToolBox) -> None:
    assert run(toolbox, "stop_waiting", id=1) == "I'm not watching anything right now."

