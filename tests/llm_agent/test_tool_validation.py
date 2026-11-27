"""ToolBox.prepare: argument validation and normalisation for the non-screen tools."""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

from pixelcat.adapters.windows import control
from pixelcat.llm.tools import ToolBox, ToolError


def prep(tb: ToolBox, tool: str, **args):  # type: ignore[no-untyped-def]
    return tb.prepare(tool, args)[1]


def refused(tb: ToolBox, tool: str, **args) -> str:
    with pytest.raises(ToolError) as exc:
        tb.prepare(tool, args)
    return str(exc.value)


# ------------------------------------------------------------------ timers / reminders / cat
@pytest.mark.parametrize(("raw", "minutes"), [(5, 5.0), ("2.5", 2.5), (1440, 1440.0), ("0.01", 0.01)])
def test_set_timer_minutes(toolbox: ToolBox, raw, minutes: float) -> None:  # type: ignore[no-untyped-def]
    assert prep(toolbox, "set_timer", minutes=raw)["minutes"] == minutes


@pytest.mark.parametrize(("raw", "msg"), [
    ("soon", "minutes must be a number"), ([1], "minutes must be a number"),
    (0, "timers must be between 0 and 1440 minutes"), (-3, "timers must be between 0 and 1440 minutes"),
    (1441, "timers must be between 0 and 1440 minutes"),
])
def test_set_timer_refuses(toolbox: ToolBox, raw, msg: str) -> None:  # type: ignore[no-untyped-def]
    assert refused(toolbox, "set_timer", minutes=raw) == msg


def test_set_timer_label(toolbox: ToolBox) -> None:
    assert prep(toolbox, "set_timer", minutes=1)["label"] == "Timer"
    assert prep(toolbox, "set_timer", minutes=1, label="L" * 100)["label"] == "L" * 80


@pytest.mark.parametrize(("raw", "norm"), [("7:05", "07:05"), (" 17.30 ", "17:30"), ("23:59", "23:59"), ("0:00", "00:00")])
def test_set_reminder_time(toolbox: ToolBox, raw: str, norm: str) -> None:
    a = prep(toolbox, "set_reminder", time=raw, text="T" * 300)
    assert a["time"] == norm and a["text"] == "T" * 200


@pytest.mark.parametrize("raw", ["24:00", "12:60", "noon", "1730", "5pm", "12:5"])
def test_set_reminder_refuses(toolbox: ToolBox, raw: str) -> None:
    assert refused(toolbox, "set_reminder", time=raw, text="x") == "time must be HH:MM (24h)"


@pytest.mark.parametrize(("raw", "act"), [("Come Here", "come_here"), (" SIT ", "sit"), ("zoomies", "zoomies")])
def test_cat_action(toolbox: ToolBox, raw: str, act: str) -> None:
    assert prep(toolbox, "cat_action", action=raw)["action"] == act


def test_cat_action_unknown(toolbox: ToolBox) -> None:
    assert refused(toolbox, "cat_action", action="Fly Away") == "unknown cat action 'fly_away'"


# ------------------------------------------------------------------ web
@pytest.mark.parametrize(("raw", "url"), [
    ("example.com", "https://example.com"),
    ("  www.x.org  ", "https://www.x.org"),
    ("HTTP://Example.com/x", "HTTP://Example.com/x"),
    ("http://a.b/c?d=1", "http://a.b/c?d=1"),
    ("ftp://x", "https://ftp://x"),
])
def test_open_url_normalises(toolbox: ToolBox, raw: str, url: str) -> None:
    assert prep(toolbox, "open_url", url=raw)["url"] == url


@pytest.mark.parametrize("raw", ["http://", "https://a b", "https:///path"])
def test_open_url_refuses(toolbox: ToolBox, raw: str) -> None:
    assert refused(toolbox, "open_url", url=raw) == "only http(s) URLs are allowed"


@pytest.mark.parametrize(("start", "norm"), [(None, 0), ("12", 12), (-5, 0), ("abc", 0), ([3], 0)])
def test_read_web_page_start(toolbox: ToolBox, start, norm: int) -> None:  # type: ignore[no-untyped-def]
    a = prep(toolbox, "read_web_page", url="example.org", start=start)
    assert a == {"url": "https://example.org", "start": norm}


def test_search_web(toolbox: ToolBox) -> None:
    assert prep(toolbox, "search_web", query="  pixel art cats ")["query"] == "pixel art cats"
    assert prep(toolbox, "search_web", query="q" * 400)["query"] == "q" * 300
    assert refused(toolbox, "search_web", query="   ") == "empty search"


# ------------------------------------------------------------------ settings, media, clipboard, volume
@pytest.mark.parametrize(("raw", "page"), [("WiFi", "wifi"), (" night light ", "night light")])
def test_open_settings(toolbox: ToolBox, raw: str, page: str) -> None:
    assert prep(toolbox, "open_settings", page=raw)["page"] == page


def test_open_settings_unknown(toolbox: ToolBox) -> None:
    assert refused(toolbox, "open_settings", page="Registry") == "unknown settings page 'registry'"


@pytest.mark.parametrize(("raw", "act"), [
    ("play", "play_pause"), ("Pause", "play_pause"), ("play/pause", "play_pause"), ("play pause", "play_pause"),
    ("prev", "previous"), ("skip", "next"), ("NEXT", "next"), ("stop", "stop"),
])
def test_media_control(toolbox: ToolBox, raw: str, act: str) -> None:
    assert prep(toolbox, "media_control", action=raw)["action"] == act


def test_media_control_unknown(toolbox: ToolBox) -> None:
    assert refused(toolbox, "media_control", action="Louder") == "unknown media action 'louder'"


def test_clipboard_text_is_capped(toolbox: ToolBox) -> None:
    assert prep(toolbox, "copy_to_clipboard", text=12345)["text"] == "12345"
    assert len(prep(toolbox, "copy_to_clipboard", text="x" * 30_000)["text"]) == 20_000


@pytest.mark.parametrize(("raw", "level"), [(55, 55), ("55.7", 55), (150, 100), (-3, 0), ("0", 0)])
def test_set_volume(toolbox: ToolBox, raw, level: int) -> None:  # type: ignore[no-untyped-def]
    assert prep(toolbox, "set_volume", level=raw)["level"] == level


def test_set_volume_refuses(toolbox: ToolBox) -> None:
    assert refused(toolbox, "set_volume", level="loud") == "level must be a number 0-100"


@pytest.mark.parametrize(("raw", "muted"), [(True, True), (False, False), ("yes", True), ("ON", True), ("1", True),
                                            ("off", False), ("no", False)])
def test_mute(toolbox: ToolBox, raw, muted: bool) -> None:  # type: ignore[no-untyped-def]
    assert prep(toolbox, "mute", muted=raw)["muted"] is muted


def test_wait_seconds(toolbox: ToolBox) -> None:
    assert prep(toolbox, "wait", seconds=0.1)["seconds"] == 0.5
    assert prep(toolbox, "wait", seconds="3")["seconds"] == 3.0
    assert prep(toolbox, "wait", seconds=14)["seconds"] == 10.0
    assert refused(toolbox, "wait", seconds="soon") == "seconds must be a number"
    assert "use wait_for" in refused(toolbox, "wait", seconds=600)


# ------------------------------------------------------------------ apps and windows
def test_open_app_whitelisted(toolbox: ToolBox) -> None:
    a = prep(toolbox, "open_app", name=" Notepad ")
    assert a == {"name": "notepad", "_target": ["notepad.exe"], "_label": "the app 'notepad'"}
    assert toolbox.describe(toolbox.specs["open_app"], a) == "open the app 'notepad'"


def test_open_app_start_menu(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    desk.shortcuts["spotify"] = ("Spotify", Path("C:/Start/Spotify.lnk"))
    a = prep(toolbox, "open_app", name="Spotify")
    assert a["_target"] == Path("C:/Start/Spotify.lnk") and a["_label"] == "'Spotify' (Start menu)"
    assert "_admin" not in a


def test_open_app_unknown(toolbox: ToolBox) -> None:
    assert refused(toolbox, "open_app", name="Hacker Tool") == "I can't find an app called 'Hacker Tool'"


def test_open_app_start_menu_disabled(cfg, hooks, desk) -> None:  # type: ignore[no-untyped-def]
    cfg.allow_start_menu_apps = False
    desk.shortcuts["spotify"] = ("Spotify", "C:/Start/Spotify.lnk")
    with pytest.raises(ToolError, match="can't find an app called 'spotify'"):
        ToolBox(cfg, hooks.hooks()).prepare("open_app", {"name": "spotify"})


def test_open_app_that_needs_admin_always_asks(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    desk.admin_targets.add(str(["notepad.exe"]))
    a = prep(toolbox, "open_app", name="notepad")
    assert a["_admin"] is True and a["_confirm"] == "hard" and a["_force_confirm"] is True
    assert toolbox.describe(toolbox.specs["open_app"], a).endswith("it needs ADMIN RIGHTS (Windows will ask you too)")


@pytest.fixture
def two_chromes(desk):  # type: ignore[no-untyped-def]
    desk.add_window(11, "Inbox - Outlook", "olk.exe")
    desk.add_window(12, "News - Google Chrome", "chrome.exe", minimized=True)
    desk.add_window(13, "Mail - Google Chrome", "chrome.exe")
    return desk


@pytest.mark.parametrize(("action", "hwnd"), [("focus", 13), ("MINIMIZE", 13), ("restore", 12), ("maximize", 13)])
def test_window_action_picks_the_right_window(toolbox: ToolBox, two_chromes, action: str, hwnd: int) -> None:  # type: ignore[no-untyped-def]
    a = prep(toolbox, "window_action", target=" chrome ", action=action)
    assert a["_win"].hwnd == hwnd and a["action"] == action.lower()


def test_window_action_refuses(toolbox: ToolBox, two_chromes) -> None:  # type: ignore[no-untyped-def]
    assert refused(toolbox, "window_action", target="excel", action="focus") == "no open window matches 'excel'"
    assert (refused(toolbox, "window_action", target="chrome", action="close")
            == "action must be focus, minimize, maximize or restore")


def test_close_app_takes_at_most_ten_windows(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    for i in range(12):
        desk.add_window(100 + i, f"Doc {i} - Word", "winword.exe")
    a = prep(toolbox, "close_app", target="word")
    assert [w.hwnd for w in a["_wins"]] == list(range(100, 110))
    assert toolbox.describe(toolbox.specs["close_app"], a).startswith("close winword: Doc 0 - Word; winword: Doc 1")


def test_force_end_internal(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ToolError, match="no running app matches 'chrome'"):
        toolbox.prepare("force_end", {"target": "chrome"}, internal=True)
    desk.plan = control.KillPlan("chrome.exe (3 processes)", [1, 2, 3], ["chrome.exe"])
    spec, a = toolbox.prepare("force_end", {"target": "chrome"}, internal=True)
    assert a["_plan"] is desk.plan
    assert toolbox.describe(spec, a).startswith("FORCE-END chrome.exe (3 processes) right now")


def test_run_as_admin_internal(toolbox: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    a = toolbox.prepare("run_as_admin", {"name": "Notepad"}, internal=True)[1]
    assert a["_target"] == "notepad.exe" and a["_label"] == "'notepad'"
    # a whitelisted URI is not a program: it goes to the Start menu
    desk.shortcuts["settings"] = ("Settings", "shell:AppsFolder\\windows.immersivecontrolpanel")
    with pytest.raises(ToolError, match="^'Settings' is a Store app - those can't run as administrator$"):
        toolbox.prepare("run_as_admin", {"name": "settings"}, internal=True)
    desk.shortcuts["regedit"] = ("Registry Editor", Path("C:/Start/regedit.lnk"))
    a = toolbox.prepare("run_as_admin", {"name": "regedit"}, internal=True)[1]
    assert a["_target"] == str(Path("C:/Start/regedit.lnk")) and a["_label"] == "'Registry Editor'"
    with pytest.raises(ToolError, match="can't find an app called 'nope'"):
        toolbox.prepare("run_as_admin", {"name": "nope"}, internal=True)


# ------------------------------------------------------------------ folders and notes
def test_folder_aliases_resolve_to_known_folders(toolbox: ToolBox, folders) -> None:  # type: ignore[no-untyped-def]
    for alias in ("desktop", "Documents", "DOWNLOADS", "pictures", "music", "videos", "docs", "Bilder"):
        assert prep(toolbox, "list_files", folder=alias)["_path"].parent == folders["desktop"].parent.resolve()
    folders["notes"].mkdir(parents=True)
    assert prep(toolbox, "open_folder", folder="notes")["_path"] == folders["notes"].resolve()


def test_folder_paths_inside_and_outside(toolbox: ToolBox, folders, tmp_path) -> None:  # type: ignore[no-untyped-def]
    sub = folders["documents"] / "proj"
    sub.mkdir()
    assert prep(toolbox, "list_files", folder=str(sub))["_path"] == sub.resolve()
    assert prep(toolbox, "list_files", folder="proj")["_path"] == sub.resolve()   # relative = in Documents
    assert prep(toolbox, "list_files", folder=f'"{sub}"')["_path"] == sub.resolve()
    msg = refused(toolbox, "open_folder", folder=str(tmp_path))
    assert msg.startswith(f"{tmp_path.resolve()} is outside the folders I'm allowed to touch (desktop, documents")
    assert refused(toolbox, "list_files", folder=str(sub / ".." / ".." / "..")).endswith(
        "pictures, music, videos, pixelcat_notes)")
    # an alias with a sub path is NOT resolved by the folder tools (unlike the file tools)
    assert refused(toolbox, "list_files", folder="documents/missing") == \
        f"folder {(folders['documents'] / 'documents' / 'missing').resolve()} does not exist"


def test_allowed_roots_come_from_the_config(cfg, hooks, folders) -> None:  # type: ignore[no-untyped-def]
    cfg.allowed_roots = ["downloads", "no_such_alias"]
    tb = ToolBox(cfg, hooks.hooks())
    assert prep(tb, "list_files", folder="downloads")["_path"] == folders["downloads"].resolve()
    assert "outside the folders I'm allowed to touch (downloads, no_such_alias)" in refused(tb, "list_files",
                                                                                            folder="desktop")


def test_create_note_defaults(toolbox: ToolBox, folders) -> None:  # type: ignore[no-untyped-def]
    a = prep(toolbox, "create_note", content="hello")
    assert re.fullmatch(r"note_\d{8}_\d{6}\.txt", a["filename"])
    assert a["_target"] == folders["notes"].resolve() / a["filename"]
    assert not folders["notes"].exists()        # nothing is written while preparing


@pytest.mark.parametrize(("raw", "safe"), [
    # Windows reads "a:" as a drive and drops it (pathlib), so the name differs there.
    ("../evil.exe", "evil.exe.txt"), ("a:b?.md", "b_.md" if sys.platform == "win32" else "a_b_.md"),
    ("x?y:z.md", "x_y_z.md"), ("  .hidden  ", "hidden.txt"), ("...", "note.txt"),
    ("notes.MD", "notes.MD"), ("dir\\sub\\file.txt", "file.txt"), ("ü name.txt", "ü name.txt"),
    ("x" * 200, "x" * 120),
])
def test_create_note_filenames(toolbox: ToolBox, raw: str, safe: str) -> None:
    assert prep(toolbox, "create_note", filename=raw, content="c", folder="desktop")["_target"].name == safe


def test_create_note_never_overwrites(toolbox: ToolBox, folders) -> None:  # type: ignore[no-untyped-def]
    (folders["desktop"] / "plan.txt").write_text("x")
    (folders["desktop"] / "plan (2).txt").write_text("x")
    a = prep(toolbox, "create_note", filename="plan.txt", content="c" * 200_000, folder="desktop")
    assert a["_target"].name == "plan (3).txt" and len(a["content"]) == 100_000
    assert toolbox.describe(toolbox.specs["create_note"], a) == f"create the file {a['_target']} (100000 characters)"


def test_create_note_outside_folders(toolbox: ToolBox, tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert "is outside the folders" in refused(toolbox, "create_note", filename="a.txt", content="c",
                                               folder=str(tmp_path))


# ------------------------------------------------------------------ file tools
@pytest.fixture
def js(folders) -> Path:  # type: ignore[no-untyped-def]
    p = folders["desktop"] / "sort.js"
    p.write_text("const a = [3, 1];\narray.sort(;\n", encoding="utf-8")
    return p


@pytest.mark.parametrize("raw", ["desktop/sort.js", "Desktop\\sort.js", "desktop:/sort.js", " 'desktop/sort.js' "])
def test_file_paths_with_folder_alias(toolbox: ToolBox, js: Path, raw: str) -> None:
    assert prep(toolbox, "read_file", path=raw)["_path"] == js.resolve()


def test_file_paths_full_relative_and_env(toolbox: ToolBox, js: Path, folders, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert prep(toolbox, "read_file", path=str(js))["_path"] == js.resolve()
    (folders["documents"] / "a.txt").write_text("x")
    assert prep(toolbox, "read_file", path="a.txt")["_path"] == (folders["documents"] / "a.txt").resolve()
    monkeypatch.setenv("PCTEST_DIR", str(folders["desktop"]))
    assert prep(toolbox, "read_file", path=str(Path("$PCTEST_DIR") / "sort.js"))["_path"] == js.resolve()


def test_file_path_errors(toolbox: ToolBox, js: Path, folders, tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert refused(toolbox, "read_file", path=" '' ") == "no file path given"
    outside = tmp_path / "x.txt"
    outside.write_text("x")
    assert refused(toolbox, "read_file", path=str(outside)).startswith(f"{outside.resolve()} is outside the folders")
    assert refused(toolbox, "read_file", path="desktop") == \
        f"{(folders['documents'] / 'desktop').resolve()} does not exist. ({folders['documents'].resolve()} has: )"
    (folders["desktop"] / "sub").mkdir()
    assert refused(toolbox, "write_file", path="desktop/sub", content="x") == \
        f"{(folders['desktop'] / 'sub').resolve()} is a folder, not a file"


def test_missing_file_suggests_similar_names(toolbox: ToolBox, js: Path, folders) -> None:  # type: ignore[no-untyped-def]
    msg = refused(toolbox, "read_file", path="desktop/sorting.js")
    assert msg == f"{(folders['desktop'] / 'sorting.js').resolve()} does not exist. Files there with a similar name: sort.js"
    msg = refused(toolbox, "read_file", path="desktop/zzz.txt")
    assert msg.endswith(f"({folders['desktop'].resolve()} has: sort.js)")


@pytest.mark.parametrize("name", ["run.bat", "x.EXE", "a.ps1", "desktop.ini", "Thumbs.db"])
def test_write_file_refuses_programs_and_system_files(toolbox: ToolBox, name: str) -> None:
    msg = refused(toolbox, "write_file", path=f"desktop/{name}", content="x")
    assert "programs and system files are off limits" in msg or "is a system file" in msg


def test_write_file_new_and_existing(toolbox: ToolBox, js: Path, folders) -> None:  # type: ignore[no-untyped-def]
    spec = toolbox.specs["write_file"]
    a = prep(toolbox, "write_file", path="desktop/new.txt", content=123)
    assert a["_exists"] is False and a["content"] == "123" and "_confirm" not in a
    assert toolbox.describe(spec, a) == f"create the file {(folders['desktop'] / 'new.txt').resolve()} (3 characters)"
    msg = refused(toolbox, "write_file", path="desktop/sort.js", content="x")
    assert msg.endswith("already exists - use edit_file to change it, or overwrite=true to replace all of it")
    a = prep(toolbox, "write_file", path="desktop/sort.js", content="x", overwrite="true")
    assert a["_exists"] is True and a["_confirm"] == "hard"
    assert toolbox.describe(spec, a).startswith("OVERWRITE ")


def test_write_file_size_limit(toolbox: ToolBox, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import files

    monkeypatch.setattr(files, "MAX_WRITE_CHARS", 10)
    assert refused(toolbox, "write_file", path="desktop/a.txt", content="x" * 11) == "that's too much text for one file"


def test_edit_file_prepare(toolbox: ToolBox, js: Path) -> None:
    a = prep(toolbox, "edit_file", path="desktop/sort.js", old="sort(;\r\n", new="sort();\r\n", replace_all="1")
    assert a["_path"] == js.resolve() and a["old"] == "sort(;\n" and a["new"] == "sort();\n" and a["replace_all"] is True
    assert prep(toolbox, "edit_file", path="desktop/sort.js", old="sort(;", new="x")["replace_all"] is False


def test_edit_file_refuses(toolbox: ToolBox, js: Path) -> None:
    assert refused(toolbox, "edit_file", path="desktop/sort.js", old="   ", new="x").startswith("'old' must be existing")
    assert refused(toolbox, "edit_file", path="desktop/sort.js", old="sort(;", new="sort(;") == \
        "'old' and 'new' are the same - nothing would change"
    msg = refused(toolbox, "edit_file", path="desktop/sort.js", old="banana", new="x")
    assert msg.startswith("I can't find 'banana' in the document. Its current text is:\nconst a = [3, 1];")
    assert refused(toolbox, "edit_file", path="desktop/sorted.js", old="a", new="b").endswith(
        "Files there with a similar name: sort.js")
    bat = js.with_name("x.bat")
    bat.write_text("echo")
    assert "off limits" in refused(toolbox, "edit_file", path="desktop/x.bat", old="echo", new="x")


@pytest.mark.parametrize(("app", "suffix", "vscode", "chosen"), [
    ("notepad", ".js", True, "notepad"), ("code", ".txt", False, "vscode"), ("VS Code", ".txt", False, "vscode"),
    ("", ".js", True, "vscode"), ("", ".js", False, "notepad"), ("", ".txt", True, "notepad"),
    ("emacs", ".py", True, "vscode"),
])
def test_open_file_picks_an_editor(toolbox: ToolBox, folders, monkeypatch, app: str, suffix: str, vscode: bool,  # type: ignore[no-untyped-def]
                                   chosen: str) -> None:
    monkeypatch.setattr(ToolBox, "_vscode_exe", staticmethod(lambda: "C:/VS/Code.exe" if vscode else None))
    (folders["desktop"] / f"f{suffix}").write_text("x")
    assert prep(toolbox, "open_file", path=f"desktop/f{suffix}", app=app)["app"] == chosen


def test_vscode_exe_lookup(desk, monkeypatch, isolated_dirs) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.windows import elevation

    assert ToolBox._vscode_exe() is None
    exe = isolated_dirs / "Code.exe"
    exe.write_text("")
    desk.shortcuts["visual studio code"] = ("Visual Studio Code", str(isolated_dirs / "VS Code.lnk"))
    monkeypatch.setattr(elevation, "lnk_target", lambda p: (str(exe), False))
    assert ToolBox._vscode_exe() == str(exe)


# ------------------------------------------------------------------ wait_for
class FakeWatches:
    def __init__(self) -> None:
        self.added: list = []


@pytest.fixture
def wtb(cfg, hooks) -> ToolBox:  # type: ignore[no-untyped-def]
    return ToolBox(cfg, hooks.hooks(watches=FakeWatches()))


def test_wait_for_needs_the_watch_manager(toolbox: ToolBox) -> None:
    assert refused(toolbox, "wait_for", event="time", minutes=5) == "watching isn't available right now"


@pytest.mark.parametrize(("event", "kind"), [
    ("message", "notification"), ("Email", "notification"), ("Toast", "notification"),
    ("window open", "window_opened"), ("app-closed", "window_closed"), ("screen text", "window_text"),
    ("download", "file"), ("reminder", "time"), ("TIME", "time"),
])
def test_wait_for_event_synonyms(wtb: ToolBox, folders, event: str, kind: str) -> None:  # type: ignore[no-untyped-def]
    args = {"notification": {}, "window_opened": {"app": "x"}, "window_closed": {"app": "x"},
            "window_text": {"app": "x", "text": "y"}, "file": {"path": "downloads"}, "time": {"minutes": 5}}[kind]
    w = prep(wtb, "wait_for", event=event, **args)["_watch"]
    assert w.kind == kind


def test_wait_for_unknown_event(wtb: ToolBox) -> None:
    assert refused(wtb, "wait_for", event="earthquake") == ("event must be one of: notification, window_text, "
                                                            "window_opened, window_closed, file, time")


def test_wait_for_common_fields(wtb: ToolBox, hooks) -> None:  # type: ignore[no-untyped-def]
    w = prep(wtb, "wait_for", event="notification", app=" 'teams' ", text='"Anna"', then="  ")["_watch"]
    assert (w.id, w.app, w.text, w.gone, w.then) == (0, "teams", "Anna", False, "tell the user what happened")
    assert w.label == "a new Microsoft Teams notification mentioning 'Anna'"
    assert w.request == hooks.request
    assert 479 * 60 < w.until - w.started <= 480 * 60
    w = prep(wtb, "wait_for", event="notification", then="x" * 700, minutes=10_000_000)["_watch"]
    assert w.label == "a new notification from any app" and len(w.then) == 600
    assert w.until - w.started == pytest.approx(7 * 24 * 3600, abs=5)
    assert refused(wtb, "wait_for", event="notification", minutes="soon") == "minutes must be a number"


def test_wait_for_unfriendly_app_name_is_kept(wtb: ToolBox) -> None:
    assert prep(wtb, "wait_for", event="notification", app="Signal")["_watch"].label == "a new Signal notification"


def test_wait_for_without_task_request(cfg, hooks) -> None:  # type: ignore[no-untyped-def]
    tb = ToolBox(cfg, hooks.hooks(watches=FakeWatches(), task_request=False))
    assert prep(tb, "wait_for", event="time", minutes=1)["_watch"].request == ""


@pytest.mark.parametrize(("at", "hhmm"), [("17:30", "17:30"), ("5pm", "17:00"), ("5 pm", "17:00"), ("12am", "00:00"),
                                          ("12pm", "12:00"), ("9.15", "09:15"), ("18 uhr", "18:00"), ("7h", "07:00")])
def test_wait_for_clock_time(wtb: ToolBox, at: str, hhmm: str) -> None:
    w = prep(wtb, "wait_for", event="time", at=at)["_watch"]
    when = datetime.fromtimestamp(w.at)
    assert when.strftime("%H:%M") == hhmm and w.at == w.until
    assert 0 < w.at - datetime.now().timestamp() <= 24 * 3600 + 5
    tomorrow = when.date() != datetime.now().date()
    assert w.label == f"the time {hhmm}" + (" tomorrow" if tomorrow else "")


@pytest.mark.parametrize("at", ["25:00", "12:75", "noon", "13pm:00"])
def test_wait_for_bad_clock_time(wtb: ToolBox, at: str) -> None:
    assert refused(wtb, "wait_for", event="time", at=at) == "at must be a time like 17:30"


def test_wait_for_minutes(wtb: ToolBox) -> None:
    w = prep(wtb, "wait_for", event="time", minutes="90")["_watch"]
    assert w.at - w.started == pytest.approx(90 * 60, abs=2)
    assert w.label.startswith("the time ")
    assert refused(wtb, "wait_for", event="time") == "event time needs at (HH:MM) or minutes"
    assert refused(wtb, "wait_for", event="time", minutes=0) == "minutes must be between 0 and 10080"
    assert refused(wtb, "wait_for", event="time", minutes=10081) == "minutes must be between 0 and 10080"


def test_wait_for_far_time_label(wtb: ToolBox) -> None:
    w = prep(wtb, "wait_for", event="time", minutes=3 * 24 * 60)["_watch"]
    assert w.label == f"the time {datetime.fromtimestamp(w.at):%H:%M} on {datetime.fromtimestamp(w.at):%a %d %b}"


def test_wait_for_window_events(wtb: ToolBox) -> None:
    w = prep(wtb, "wait_for", event="window_text", app="Exporter", text="Done", gone="true")["_watch"]
    assert w.gone is True and w.label == "'Done' to disappear from Exporter"
    assert prep(wtb, "wait_for", event="window_text", app="X", text="Y")["_watch"].label == "'Y' to appear in X"
    assert prep(wtb, "wait_for", event="window_opened", text="Save as")["_watch"].label == "a Save as window to open"
    assert prep(wtb, "wait_for", event="window_closed", app="setup")["_watch"].label == "setup to close"
    assert refused(wtb, "wait_for", event="window_text", app="X") == \
        "window_text needs app (which window) and text (what to wait for)"
    assert refused(wtb, "wait_for", event="window_opened") == "window_opened needs app (or text from the window title)"
    assert refused(wtb, "wait_for", event="window_closed", text="x") == "window_closed needs app: the window to wait for"


def test_wait_for_files(wtb: ToolBox, folders, tmp_path) -> None:  # type: ignore[no-untyped-def]
    dl = folders["downloads"].resolve()
    w = prep(wtb, "wait_for", event="file", path="downloads", text="report")["_watch"]
    assert w.path == str(dl) and w.label == f"a new file in {dl} named like 'report'"
    w = prep(wtb, "wait_for", event="file", path="downloads/")["_watch"]
    assert w.label == f"a new file in {dl}"
    w = prep(wtb, "wait_for", event="file", path="downloads/result.txt")["_watch"]
    assert w.path == str(dl / "result.txt") and w.label == "result.txt to appear"
    (dl / "result.txt").write_text("x")
    assert prep(wtb, "wait_for", event="file", path=str(dl / "result.txt"))["_watch"].label == "result.txt to change"
    assert prep(wtb, "wait_for", event="file", path="later.pdf")["_watch"].path == \
        str(folders["documents"].resolve() / "later.pdf")
    assert refused(wtb, "wait_for", event="file") == "event file needs path (a file, or a folder for any new file)"
    assert refused(wtb, "wait_for", event="file", path=str(tmp_path)).endswith(
        "pictures, music, videos, pixelcat_notes)")
    assert "outside the folders I'm allowed to look at" in refused(wtb, "wait_for", event="file", path=str(tmp_path))


def test_wait_for_describe(wtb: ToolBox) -> None:
    spec, a = wtb.prepare("wait_for", {"event": "window_closed", "app": "setup", "minutes": 30})
    when = datetime.fromtimestamp(a["_watch"].until)
    day = "" if when.date() == datetime.now().date() else when.strftime("%a ")
    assert wtb.describe(spec, a) == f"watch for setup to close (until {day}{when:%H:%M})"
    assert a["_watch"].until - a["_watch"].started == pytest.approx(1800, abs=2)
