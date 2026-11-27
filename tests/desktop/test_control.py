"""pixelcat.desktop.control: chat desktop actions - windows, apps, processes (mocked DLLs)."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop.fakes import FakeDLL, enum_windows, fill_text, out, raising
from pixelcat.desktop import control, win32
from pixelcat.desktop.control import AppWindow, KillPlan, Proc


@pytest.fixture
def ctl(monkeypatch, w32, clock, tmp_path):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))
    monkeypatch.setattr(control, "time", clock)
    monkeypatch.setattr(control, "_shortcut_cache", None)
    monkeypatch.setattr(control, "_store_apps", {})
    monkeypatch.setattr(control, "_prefetching", False)
    shell = FakeDLL("shell32")
    monkeypatch.setattr(control, "_shell32", shell, raising=False)
    last_error = {"code": 0}
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error["code"])
    return SimpleNamespace(u=w32.user32, k=w32.kernel32, w32=w32, shell=shell, last_error=last_error, clock=clock)


# ---------------------------------------------------------------- AppWindow / find_windows
def aw(title: str, exe: str = "app.exe", hwnd: int = 1, pid: int = 0, **kw) -> AppWindow:  # type: ignore[no-untyped-def]
    return AppWindow(hwnd, title, exe, kw.get("minimized", False), kw.get("maximized", False), kw.get("cls", ""), pid)


@pytest.mark.parametrize("win, label", [
    (aw("Inbox", "OUTLOOK.EXE"), "OUTLOOK: Inbox"),
    (aw("Song", "Spotify", minimized=True), "Spotify: Song (minimized)"),
    (aw("Doc", "winword.exe", maximized=True), "winword: Doc (maximized)"),
    (aw("x" * 61, "a.exe"), "a: " + "x" * 57 + "..."),
    (aw("x" * 60, "a.exe"), "a: " + "x" * 60),
])
def test_app_window_label(win, label):
    assert win.label() == label


WINDOWS = [
    aw("Inbox - Outlook", "OUTLOOK.EXE", 1),
    aw("Budget.xlsx - Excel", "EXCEL.EXE", 2),
    aw("Report.docx - Word", "WINWORD.EXE", 3),
    aw("Chat | Microsoft Teams", "ms-teams.exe", 4),
    aw("Old Teams", "Teams.exe", 5),
    aw("GitHub - Google Chrome", "chrome.exe", 6),
    aw("Untitled - Notepad", "notepad.exe", 7),
    aw("Notepad++ settings", "notepad++.exe", 8),
    aw("Pixel Cat project - Visual Studio Code", "Code.exe", 9),
]


@pytest.mark.parametrize("query, hwnds", [
    ("word", [3]), ("Excel", [2]), ("excel.exe", [2]), ("teams", [4, 5]), ("chrome", [6]),
    ("google chrome", [6]), ("vs code", [9]), ("notepad", [7]),
    ("pad", [7, 8]),                    # part of an exe name
    ("budget", [2]),                    # window title
    ("Inboxx - Outlook", [1]),          # fuzzy title
    ("", []), ("   ", []), ("zzz", []),
])
def test_find_windows(query, hwnds):
    assert [w.hwnd for w in control.find_windows(query, WINDOWS)] == hwnds


# ---------------------------------------------------------------- list_app_windows / process_exe
def desktop(ctl, wins: dict[int, dict], exes: dict[int, str] | None = None) -> None:  # type: ignore[no-untyped-def]
    """wins: hwnd -> props (title, cls, pid, ex, owner, visible, iconic, zoomed, cloaked, ghost_of)."""
    exes = exes or {}
    u = ctl.u
    enum_windows(u, list(wins))
    g = lambda h, key, default=0: wins.get(int(h), {}).get(key, default)  # noqa: E731
    u.IsWindowVisible.result = lambda h: g(h, "visible", True)
    u.IsIconic.result = lambda h: g(h, "iconic")
    u.IsZoomed.result = lambda h: g(h, "zoomed")
    u.GetWindow.result = lambda h, kind: g(h, "owner") if kind == win32.GW_OWNER else 0
    u.GetWindowLongPtrW.result = lambda h, i: g(h, "ex") if i == win32.GWL_EXSTYLE else 0
    u.GetClassNameW.result = lambda h, buf, n: fill_text(buf, g(h, "cls", "AppWnd"))
    u.GetWindowTextLengthW.result = lambda h: len(g(h, "title", ""))
    u.GetWindowTextW.result = lambda h, buf, n: fill_text(buf, g(h, "title", ""))
    u.HungWindowFromGhostWindow.result = lambda h: g(h, "ghost_of")

    def pid(h, p):  # type: ignore[no-untyped-def]
        out(p).value = g(h, "pid")
        return 1

    u.GetWindowThreadProcessId.result = pid

    def cloaked(h, which, p, size):  # type: ignore[no-untyped-def]
        out(p).value = int(g(h, "cloaked"))
        return 0

    ctl.w32.dwmapi.DwmGetWindowAttribute.result = cloaked
    ctl.k.OpenProcess.result = lambda access, inherit, pid: (0x1000 + pid) if pid in exes else 0

    def image_name(h, flags, buf, size):  # type: ignore[no-untyped-def]
        buf.value = exes[h - 0x1000]
        return 1

    ctl.k.QueryFullProcessImageNameW.result = image_name


def test_list_app_windows_alt_tab_like(ctl):
    desktop(ctl, {
        1: {"title": "Mail", "pid": 10, "zoomed": 1},
        2: {"title": "Hidden", "pid": 10, "visible": False},
        3: {"title": "Mini", "pid": 11, "iconic": 1},
        4: {"title": "Tool", "pid": 10, "ex": win32.WS_EX_TOOLWINDOW},
        5: {"title": "Tool app", "pid": 10, "ex": win32.WS_EX_TOOLWINDOW | win32.WS_EX_APPWINDOW},
        6: {"title": "Owned dialog", "pid": 10, "owner": 1},
        7: {"title": "Owned app", "pid": 10, "owner": 1, "ex": win32.WS_EX_APPWINDOW},
        8: {"title": "Me", "pid": 99},
        9: {"title": "Taskbar", "pid": 12, "cls": "Shell_TrayWnd"},
        10: {"title": "", "pid": 10},
        11: {"title": "Cloaked", "pid": 10, "cloaked": 1},
        12: {"title": "Unknown exe", "pid": 13},
    }, exes={10: "C:/Program Files/Mail/mail.exe", 11: "C:/x/mini.exe"})
    got = control.list_app_windows(own_pid=99)
    assert [w.hwnd for w in got] == [1, 3, 5, 7, 12]
    assert got[0] == AppWindow(1, "Mail", "mail.exe", False, True, "AppWnd", 10)
    assert got[1].minimized and got[-1].exe == "unknown"


def test_list_app_windows_ghost_reports_the_hung_app(ctl):
    desktop(ctl, {
        1: {"title": "Word (Not Responding)", "pid": 5, "cls": "Ghost", "ghost_of": 2},
        2: {"title": "Word", "pid": 20, "visible": False},
        3: {"title": "Cat (Not Responding)", "pid": 5, "cls": "Ghost", "ghost_of": 4},
        4: {"title": "Cat", "pid": 99, "visible": False},
        5: {"title": "Ghost without owner", "pid": 5, "cls": "Ghost"},
    }, exes={20: "C:/Office/WINWORD.EXE", 5: "C:/Windows/dwm.exe"})
    got = control.list_app_windows(own_pid=99)
    assert [(w.hwnd, w.exe, w.pid) for w in got] == [(1, "WINWORD.EXE", 20), (5, "dwm.exe", 5)]


def test_list_app_windows_skips_a_window_that_fails(ctl):
    desktop(ctl, {1: {"title": "A", "pid": 1}, 2: {"title": "B", "pid": 1}}, exes={1: "a.exe"})
    ctl.u.IsWindowVisible.result = lambda h: raising(OSError("gone"))() if h == 1 else True
    assert [w.hwnd for w in control.list_app_windows(own_pid=0)] == [2]


def test_process_exe(ctl):
    desktop(ctl, {}, exes={7: "C:/Apps/Thing.exe"})
    assert control.process_exe(7) == "Thing.exe"
    assert ctl.k.CloseHandle.calls == [(0x1007,)]
    assert control.process_exe(8) == ""
    ctl.k.QueryFullProcessImageNameW.result = 0
    assert control.process_exe(7) == ""
    assert len(ctl.k.CloseHandle.calls) == 2


# ---------------------------------------------------------------- window actions
def test_close_windows_posts_wm_close_and_counts(ctl):
    ctl.u.PostMessageW.result = lambda h, msg, w, l: h != 2
    assert control.close_windows([aw("a", hwnd=1), aw("b", hwnd=2), aw("c", hwnd=3)]) == 2
    assert {c[1] for c in ctl.u.PostMessageW.calls} == {control.WM_CLOSE}


@pytest.mark.parametrize("action, iconic, shows, focus", [
    ("minimize", 0, [(5, control.SW_MINIMIZE)], False),
    ("maximize", 0, [(5, control.SW_MAXIMIZE)], False),
    ("restore", 0, [(5, control.SW_RESTORE)], False),
    ("focus", 0, [], True),
    ("focus", 1, [(5, control.SW_RESTORE)], True),
])
def test_window_action(ctl, action, iconic, shows, focus):
    ctl.u.IsIconic.result = iconic
    control.window_action(aw("x", "app.exe", 5), action)
    assert ctl.u.ShowWindowAsync.calls == shows
    assert bool(ctl.u.SetForegroundWindow.calls) is focus


def test_window_action_refuses_hung_and_unknown(ctl):
    with pytest.raises(ValueError):
        control.window_action(aw("x"), "close")
    ctl.u.IsHungAppWindow.result = 1
    with pytest.raises(RuntimeError, match="app is not responding"):
        control.window_action(aw("x"), "minimize")
    assert ctl.u.ShowWindowAsync.calls == []


# ---------------------------------------------------------------- start menu / store apps
@pytest.fixture
def start_menu(ctl):  # type: ignore[no-untyped-def]
    user = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    common = Path(os.environ["ProgramData"]) / "Microsoft/Windows/Start Menu/Programs"
    files = [user / "Firefox.lnk", user / "Tools/Calculator Plus.lnk", user / "Uninstall Firefox.lnk",
             user / "Readme.lnk", user / "notes.txt", user / "Site.url", user / "Click.appref-ms",
             common / "Firefox.lnk", common / "Paint.NET.lnk", common / "Microsoft Edge.lnk",
             common / "Microsoft Edge Dev.lnk", common / "Rechner.lnk", common / "Visual Studio Code.lnk"]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("")
    return SimpleNamespace(user=user, common=common)


def test_start_menu_shortcuts_filters_and_prefers_per_user(start_menu):
    found = control.start_menu_shortcuts()
    assert set(found) == {"Firefox", "Calculator Plus", "Site", "Click", "Paint.NET", "Microsoft Edge",
                          "Microsoft Edge Dev", "Rechner", "Visual Studio Code"}
    assert found["Firefox"] == start_menu.user / "Firefox.lnk"


def test_start_menu_shortcuts_are_cached_for_two_minutes(start_menu, ctl):
    first = control.start_menu_shortcuts()
    (start_menu.user / "New App.lnk").write_text("")
    assert control.start_menu_shortcuts() is first
    ctl.clock.now += 121
    assert "New App" in control.start_menu_shortcuts()


@pytest.mark.parametrize("name, expected", [
    ("firefox", "Firefox"),
    ("microsoft edge", "Microsoft Edge"),         # exact beats the longer prefix match
    ("microsoft", "Microsoft Edge"),              # shortest prefix match
    ("code", "Visual Studio Code"),               # whole word
    ("calculater plus", "Calculator Plus"),       # typo
    ("calculator", "Calculator Plus"),            # a match beats the localized alias
    ("paint.net", "Paint.NET"),
    ("", None), ("zzzz", None),
])
def test_find_shortcut(start_menu, name, expected):
    hit = control.find_shortcut(name)
    assert (hit[0] if hit else None) == expected


def test_find_shortcut_store_apps_after_shortcuts(start_menu, monkeypatch):
    monkeypatch.setattr(control, "_store_apps", {"Firefox": "Mozilla.Firefox!App",
                                                 "Einstellungen": "windows.immersivecontrolpanel!App"})
    assert control.find_shortcut("firefox")[1] == start_menu.user / "Firefox.lnk"
    assert control.find_shortcut("settings") == ("Einstellungen", "shell:AppsFolder\\windows.immersivecontrolpanel!App")


def test_find_shortcut_starts_loading_store_apps_in_the_background_once(ctl, monkeypatch):
    monkeypatch.setattr(control, "_store_apps", None)
    started = []
    monkeypatch.setattr(control.threading, "Thread",
                        lambda target, name, daemon: SimpleNamespace(start=lambda: started.append((target, name))))
    assert control.find_shortcut("nothing here") is None
    control.find_shortcut("still nothing")
    assert started == [(control.store_apps, "store-apps")]


@pytest.mark.parametrize("stdout, expected", [
    (json.dumps([{"Name": "Photos", "AppID": "Microsoft.Photos!App"}, {"Name": "Uninstall X", "AppID": "u"},
                 {"Name": "", "AppID": "e"}, {"Name": "NoId"}]), {"Photos": "Microsoft.Photos!App"}),
    (json.dumps({"Name": "Only", "AppID": "One!App"}), {"Only": "One!App"}),
    ("", {}),
    ("not json", {}),
])
def test_store_apps_parses_get_start_apps(ctl, monkeypatch, stdout, expected):
    monkeypatch.setattr(control, "_store_apps", None)
    calls = []

    def run(args, **kw):  # type: ignore[no-untyped-def]
        calls.append((args, kw))
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(control.subprocess, "run", run)
    assert control.store_apps() == expected
    assert control.store_apps() is control._store_apps          # cached: asked once
    (args, kw), = calls
    assert args[0] == "powershell" and "Get-StartApps" in args[-1]
    assert kw["creationflags"] == 0x08000000 and kw["timeout"] == 30


def test_store_apps_survives_a_missing_powershell(ctl, monkeypatch):
    monkeypatch.setattr(control, "_store_apps", None)
    monkeypatch.setattr(control.subprocess, "run", raising(subprocess.TimeoutExpired("powershell", 30)))
    assert control.store_apps() == {}


def test_launch_uses_the_shell_association(monkeypatch):
    opened = []
    monkeypatch.setattr(control.os, "startfile", opened.append, raising=False)
    control.launch(Path("C:/x/app.lnk"))
    control.launch("ms-settings:display")
    assert opened == [str(Path("C:/x/app.lnk")), "ms-settings:display"]


# ---------------------------------------------------------------- misc actions
@pytest.mark.parametrize("action, vk", sorted(control.MEDIA_KEYS.items()))
def test_media_key_presses_and_releases(ctl, action, vk):
    control.media_key(action)
    assert ctl.u.keybd_event.calls == [(vk, 0, 0, 0), (vk, 0, control.KEYEVENTF_KEYUP, 0)]


def test_lock_screen(ctl):
    ctl.u.LockWorkStation.result = 1
    assert control.lock_screen() is True


def filetimes(ctl, samples):  # type: ignore[no-untyped-def]
    it = iter(samples)

    def get(*fts):  # type: ignore[no-untyped-def]
        for ft, v in zip(fts, next(it)):
            out(ft).dwLowDateTime, out(ft).dwHighDateTime = v & 0xFFFFFFFF, v >> 32
        return 1

    ctl.k.GetSystemTimes.result = get


@pytest.mark.parametrize("a, b, expected", [
    ((0, 0, 0), (75, 100, 0), 25.0),                     # kernel includes idle
    ((1 << 33, 1 << 34, 0), ((1 << 33) + 10, (1 << 34) + 20, 20), 75.0),
    ((5, 5, 5), (5, 5, 5), 0.0),
])
def test_cpu_percent(ctl, a, b, expected):
    filetimes(ctl, [a, b])
    assert control.cpu_percent(0.5) == pytest.approx(expected)
    assert ctl.clock.sleeps == [0.5]


def test_system_info(ctl, monkeypatch):
    def power(p):  # type: ignore[no-untyped-def]
        out(p).ACLineStatus, out(p).BatteryFlag, out(p).BatteryLifePercent = 0, 1, 57
        return 1

    def memory(p):  # type: ignore[no-untyped-def]
        assert out(p).dwLength == ctypes.sizeof(control.MEMORYSTATUSEX)
        out(p).ullTotalPhys, out(p).ullAvailPhys, out(p).dwMemoryLoad = 16 * 2**30, 4 * 2**30, 75
        return 1

    ctl.k.GetSystemPowerStatus.result = power
    ctl.k.GlobalMemoryStatusEx.result = memory
    ctl.k.GetTickCount64.result = (2 * 86400 + 3 * 3600 + 4 * 60 + 5) * 1000
    filetimes(ctl, [(0, 0, 0), (50, 100, 0)])
    monkeypatch.setenv("SystemDrive", "D:")
    seen = []
    monkeypatch.setattr(control.shutil, "disk_usage",
                        lambda p: seen.append(p) or SimpleNamespace(free=120 * 2**30, total=500 * 2**30))
    assert control.system_info() == ("Battery: 57% (on battery); RAM: 12.0 of 16.0 GB used (75%); CPU: 50%; "
                                     "Disk D: 120 GB free of 500 GB; Uptime: 2d 3h 4m")
    assert seen == ["D:\\"]


@pytest.mark.parametrize("flag, percent, ac, text", [
    (128, 255, 1, "Power: no battery (desktop)"),
    (1, 255, 1, "Power: no battery (desktop)"),
    (8, 100, 1, "Battery: 100% (plugged in)"),
])
def test_system_info_power(ctl, monkeypatch, flag, percent, ac, text):
    def power(p):  # type: ignore[no-untyped-def]
        out(p).ACLineStatus, out(p).BatteryFlag, out(p).BatteryLifePercent = ac, flag, percent
        return 1

    ctl.k.GetSystemPowerStatus.result = power
    filetimes(ctl, [(0, 0, 0), (0, 0, 0)])
    monkeypatch.setattr(control.shutil, "disk_usage", raising(OSError("no drive")))
    info = control.system_info()
    assert info.startswith(text + "; CPU: 0%; Uptime:") and "Disk" not in info and "RAM" not in info


# ---------------------------------------------------------------- processes and /force
def processes(ctl, procs: list[tuple[int, int, str]], critical: set[int] = frozenset()) -> None:  # type: ignore[no-untyped-def]
    ctl.k.CreateToolhelp32Snapshot.result = 0x77
    state = {"i": 0}

    def fill(snap, p):  # type: ignore[no-untyped-def]
        if state["i"] >= len(procs):
            return 0
        e = out(p)
        assert e.dwSize == ctypes.sizeof(control.PROCESSENTRY32W)
        e.th32ProcessID, e.th32ParentProcessID, e.szExeFile = procs[state["i"]]
        state["i"] += 1
        return 1

    def first(snap, p):  # type: ignore[no-untyped-def]
        state["i"] = 0
        return fill(snap, p)

    ctl.k.Process32FirstW.result = first
    ctl.k.Process32NextW.result = fill
    ctl.k.OpenProcess.result = lambda access, inherit, pid: 0x10000 + pid

    def is_critical(h, p):  # type: ignore[no-untyped-def]
        out(p).value = (h - 0x10000) in critical
        return 1

    ctl.k.IsProcessCritical.result = is_critical


def test_list_processes(ctl):
    processes(ctl, [(4, 0, "System"), (1200, 4, "chrome.exe")])
    assert control.list_processes() == [Proc(4, 0, "System"), Proc(1200, 4, "chrome.exe")]
    assert (0x77,) in ctl.k.CloseHandle.calls


@pytest.mark.parametrize("snap", [0, ctypes.c_void_p(-1).value])
def test_list_processes_no_snapshot(ctl, snap):
    ctl.k.CreateToolhelp32Snapshot.result = snap
    assert control.list_processes() == []


def test_alive(ctl):
    processes(ctl, [(1, 0, "a.exe"), (3, 0, "b.exe")])
    assert control.alive([1, 2, 3]) == [1, 3]


OWN = 999
TREE = [
    (4, 0, "System"), (500, 4, "explorer.exe"), (600, 4, "svchost.exe"),
    (1000, 500, "chrome.exe"), (1001, 1000, "chrome.exe"), (1002, 1000, "chrome.exe"),
    (1003, 1000, "crashpad.exe"),             # a helper without a window: goes along
    (1004, 1000, "notepad.exe"),              # an app started from chrome with its own window: stays
    (1005, 1000, "svchost.exe"),              # protected: never
    (2000, 500, "code.exe"), (2001, 2000, "code.exe"),
    (3000, 500, "python.exe"), (OWN, 3000, "python.exe"),
    (4000, 500, "Hung.exe"),
]


@pytest.fixture
def force(ctl, monkeypatch):  # type: ignore[no-untyped-def]
    processes(ctl, TREE)
    windows = [aw("Inbox - Gmail", "chrome.exe", 1, 1000), aw("notes.txt - Editor", "notepad.exe", 2, 1004),
               aw("Frozen thing (Not Responding)", "Hung.exe", 3, 4000)]
    monkeypatch.setattr(control, "list_app_windows", lambda own_pid: windows)
    return ctl


def test_plan_force_end_takes_the_apps_helpers_but_not_other_apps(force):
    assert control.plan_force_end("Chrome", OWN) == KillPlan(
        "chrome.exe, crashpad.exe (4 processes)", [1000, 1001, 1002, 1003], ["chrome.exe", "crashpad.exe"])


@pytest.mark.parametrize("target, pids", [
    ("code.exe", [2000, 2001]),
    ('"vs code"', [2000, 2001]),            # alias, quoted
    ("cod", [2000, 2001]),                  # part of the exe name (3+ letters)
    ("frozen thing", [4000]),               # window title
    ("python", [3000]),                     # never ourselves
])
def test_plan_force_end_targets(force, target, pids):
    assert control.plan_force_end(target, OWN).pids == pids


def test_plan_force_end_explorer_does_not_take_its_children(force):
    plan = control.plan_force_end("explorer", OWN)
    assert plan.pids == [500] and plan.label == "explorer.exe (1 process)"


@pytest.mark.parametrize("target, message", [
    ("", "tell me which app"), ("  '' ", "tell me which app"),
    ("svchost", "won't end svchost.exe"),
    ("system", "no running app matches"),   # exe names without ".exe" never match by name
    ("nothing-like-this", "no running app matches"),
])
def test_plan_force_end_refuses(force, target, message):
    with pytest.raises(ValueError, match=message):
        control.plan_force_end(target, OWN)


def test_plan_force_end_skips_critical_processes(force, ctl):
    processes(ctl, TREE, critical={2000, 2001})
    with pytest.raises(ValueError, match="nothing I'm allowed to end"):
        control.plan_force_end("code", OWN)


def test_plan_force_end_keeps_processes_it_cannot_inspect(force, ctl):
    ctl.k.OpenProcess.result = 0
    assert control.plan_force_end("code", OWN).pids == [2000, 2001]


def test_plan_force_end_label_for_many_exes(ctl, monkeypatch):
    processes(ctl, [(10, 1, "tool.exe"), (11, 10, "a.exe"), (12, 10, "b.exe"), (13, 10, "c.exe")])
    monkeypatch.setattr(control, "list_app_windows", lambda own_pid: [])
    plan = control.plan_force_end("tool", OWN)
    assert plan.label == "a.exe, b.exe, c.exe ... (4 processes)" and len(plan.exes) == 4


def test_force_end(ctl):
    handles = {1: 0x11, 2: 0, 3: 0, 4: 0x14, 5: 0x15}
    errors = {2: control.ERROR_ACCESS_DENIED, 3: 87, 5: control.ERROR_ACCESS_DENIED}
    current = {"pid": 0}

    def open_(access, inherit, pid):  # type: ignore[no-untyped-def]
        assert access == control.PROCESS_TERMINATE | control.SYNCHRONIZE
        current["pid"] = pid
        ctl.last_error["code"] = errors.get(pid, 0)
        return handles[pid]

    ctl.k.OpenProcess.result = open_
    ctl.k.TerminateProcess.result = lambda h, code: h != 0x15
    assert control.force_end(KillPlan("x", [1, 2, 3, 4, 5], [])) == ([1, 4], [2, 5])
    assert ctl.k.WaitForSingleObject.calls == [(0x11, 2000), (0x14, 2000)]
    assert sorted(c[0] for c in ctl.k.CloseHandle.calls) == [0x11, 0x14, 0x15]


def run_as(ctl, ok=1, error=0, process=0x99):  # type: ignore[no-untyped-def]
    seen = {}

    def execute(p):  # type: ignore[no-untyped-def]
        info = p._obj
        assert info.cbSize == ctypes.sizeof(control.SHELLEXECUTEINFOW)
        seen.update(verb=info.lpVerb, file=info.lpFile, params=info.lpParameters, show=info.nShow, mask=info.fMask)
        info.hProcess = process
        ctl.last_error["code"] = error
        return ok

    ctl.shell.ShellExecuteExW.result = execute
    return seen


def test_force_end_elevated_runs_taskkill_through_uac(ctl):
    seen = run_as(ctl)
    assert control.force_end_elevated([12, 34]) == (True, "ok")
    assert seen == {"verb": "runas", "file": "taskkill.exe", "params": "/F /T /PID 12 /PID 34", "show": 0,
                    "mask": control.SEE_MASK_NOCLOSEPROCESS}
    assert ctl.k.WaitForSingleObject.calls == [(0x99, 15000)] and ctl.k.CloseHandle.calls == [(0x99,)]


def test_run_as_admin_does_not_wait(ctl):
    seen = run_as(ctl, process=None)
    assert control.run_as_admin(Path("C:/Tools/x.exe")) == (True, "ok")
    assert seen["file"] == str(Path("C:/Tools/x.exe")) and seen["params"] is None and seen["show"] == 1
    assert ctl.k.WaitForSingleObject.calls == [] and ctl.k.CloseHandle.calls == []


@pytest.mark.parametrize("error, message", [(1223, "the admin prompt was cancelled"), (2, "Windows refused (error 2)")])
def test_run_as_admin_refused(ctl, error, message):
    run_as(ctl, ok=0, error=error)
    assert control.run_as_admin("x.exe") == (False, message)
