"""pixelcat.adapters.windows.platform: each port method forwards to the module function it wraps."""
from __future__ import annotations

import inspect
import threading
from types import SimpleNamespace

import pytest

from adapters.windows.fakes import by_hwnd, enum_windows, out, raising
from pixelcat import domain, installer, paths, persistence, savecrypt
from pixelcat.adapters.windows import (capture, control, elevation, escwatch, hardware, inputs, ledges, monitors,
                                       notifications, platform, system, uia, volume, win32, windows)
from pixelcat.domain import AppWindow, KillPlan, UserState

EV = threading.Event()
R = (10, 20, 110, 220)
W = AppWindow(7, "Notes", "notepad.exe", False, False)
PLAN = KillPlan("x.exe", [1], ["x.exe"])


def _ok() -> bool:
    return True


def _cb() -> None:
    pass


# (port, method, args, module, function); the function must get exactly ``args``
DELEGATES = [
    ("display", "set_dpi_awareness", (), win32, "set_dpi_awareness"),
    ("display", "monitors", (), monitors, "enumerate_monitors"),
    ("display", "virtual_screen", (), win32, "virtual_screen"),
    ("display", "window_dpi", (7,), win32, "window_dpi"),
    ("display", "session_locked", (), system, "session_locked"),
    ("windows", "top_level", (42, frozenset({7})), windows, "enumerate_windows"),
    ("windows", "app_windows", (42,), control, "list_app_windows"),
    ("windows", "foreground", (), windows, "foreground_window"),
    ("windows", "is_fullscreen", (7, R), windows, "is_fullscreen_window"),
    ("windows", "exists", (7,), win32, "is_window"),
    ("windows", "is_visible", (7,), win32, "is_visible"),
    ("windows", "is_minimized", (7,), win32, "is_minimized"),
    ("windows", "is_hung", (7,), win32, "is_hung"),
    ("windows", "is_cloaked", (7,), win32, "is_cloaked"),
    ("windows", "frame_bounds", (7,), win32, "frame_bounds"),
    ("windows", "rect", (7,), win32, "window_rect"),
    ("windows", "pid", (7,), win32, "window_pid"),
    ("windows", "title", (7,), win32, "window_text"),
    ("windows", "class_name", (7,), win32, "class_name"),
    ("windows", "window_at", (3, 4), inputs, "window_at"),
    ("windows", "active_popup", (7,), inputs, "active_popup"),
    ("windows", "same_app", (7, 8), inputs, "same_app"),
    ("windows", "is_foreground", (7,), inputs, "is_foreground"),
    ("windows", "bring_to_front", (7, 0.8), inputs, "bring_to_front"),
    ("windows", "window_action", (W, "minimize"), control, "window_action"),
    ("windows", "close_windows", ([W],), control, "close_windows"),
    ("windows", "make_tool_window", (7,), win32, "make_tool_noactivate"),
    ("windows", "set_click_through", (7, True), win32, "set_click_through"),
    ("windows", "move", (7, 1, 2, 30, 40, False), win32, "move_window"),
    ("windows", "keep_topmost", (7,), win32, "keep_topmost"),
    ("windows", "place_below", (7, 8), win32, "place_below"),
    ("windows", "own_click_through", (42, 7), win32, "own_click_through"),
    ("windows", "end_own_click_through", ([7, 8],), win32, "end_own_click_through"),
    ("input", "cursor_pos", (), win32, "cursor_pos"),
    ("input", "release_went_elsewhere", (7,), win32, "release_went_elsewhere"),
    ("input", "mouse_buttons_down", (), win32, "mouse_buttons_down"),
    ("input", "last_input_tick", (), win32, "last_input_tick"),
    ("input", "idle_seconds", (), win32, "idle_seconds"),
    ("input", "passthrough_active", (), inputs, "passthrough_active"),
    ("input", "move_cursor", (3, 4), inputs, "move_cursor"),
    ("input", "click", (3, 4, "right", True, 3, 0.5, EV), inputs, "click"),
    ("input", "scroll", (3, 4, -2, True), inputs, "scroll"),
    ("input", "drag", ([(0, 0), (5, 5)], "middle", 1.0, EV), inputs, "drag"),
    ("input", "glide", (3, 4, 0.5, EV), inputs, "glide"),
    ("input", "pinch", (3, 4, 10.0, 90.0, 0.4, EV), inputs, "pinch"),
    ("input", "parse_combo", ("ctrl+s",), inputs, "parse_combo"),
    ("input", "press_combo", ([0x11, 0x53], 0.2, EV), inputs, "press_combo"),
    ("input", "type_text", ("hi", _ok, EV, 2, 0.01), inputs, "type_text"),
    ("input", "escape_watcher", (_cb,), escwatch, "EscWatcher"),
    ("clipboard", "backup", (), inputs, "backup_clipboard"),
    ("clipboard", "write", ([(13, b"x")], False), inputs, "write_clipboard"),
    ("clipboard", "set_private_text", ("x",), inputs, "set_clipboard_private_text"),
    ("clipboard", "clear", (), inputs, "clear_clipboard"),
    ("clipboard", "sequence", (), inputs, "clipboard_sequence"),
    ("clipboard", "text", (), inputs, "get_clipboard_text"),
    ("accessibility", "focused_element", (), uia, "focused_element"),
    ("accessibility", "focused_text", (100,), uia, "focused_text"),
    ("accessibility", "document_text", (7, 100), uia, "document_text"),
    ("capture", "grab", (R,), capture, "grab"),
    ("capture", "grab_without", (R, 42), capture, "grab_without"),
    ("capture", "grab_window", (7, R), capture, "grab_window"),
    ("capture", "excluding", (42,), capture, "excluding"),
    ("capture", "snapshot", (R,), ledges, "grab"),
    ("system", "acquire_single_instance", (), system, "acquire_single_instance"),
    ("system", "create_quit_event", (), system, "create_quit_event"),
    ("system", "quit_requested", (5,), system, "quit_requested"),
    ("system", "message_box", ("hi", "Title"), win32, "message_box"),
    ("system", "system_info", (), control, "system_info"),
    ("system", "lock_screen", (), control, "lock_screen"),
    ("system", "media_key", ("next",), control, "media_key"),
    ("system", "get_volume", (), volume, "get_volume"),
    ("system", "set_volume", (30,), volume, "set_volume"),
    ("system", "set_mute", (True,), volume, "set_mute"),
    ("system", "hardware", (True,), hardware, "detect"),
    ("apps", "prefetch", (), control, "prefetch_store_apps"),
    ("apps", "find_shortcut", ("paint",), control, "find_shortcut"),
    ("apps", "open", ("C:\\x.lnk",), control, "launch"),
    ("apps", "open_elevated", ("C:\\x.exe", ["/a", "b c"]), control, "launch_elevated"),
    ("apps", "needs_admin", (["x.exe"],), elevation, "needs_admin"),
    ("apps", "lnk_target", ("C:\\x.lnk",), elevation, "lnk_target"),
    ("apps", "run_as_admin", ("C:\\x.exe",), control, "run_as_admin"),
    ("processes", "process_exe", (5,), control, "process_exe"),
    ("processes", "plan_force_end", ("chrome", 42), control, "plan_force_end"),
    ("processes", "force_end", (PLAN,), control, "force_end"),
    ("processes", "force_end_elevated", ([1, 2],), control, "force_end_elevated"),
    ("processes", "alive", ([1, 2],), control, "alive"),
    ("processes", "is_elevated", (5,), inputs, "is_elevated"),
    ("processes", "self_elevated", (), inputs, "self_elevated"),
    ("processes", "uac_prompt_open", (), elevation, "uac_prompt_open"),
    ("notifications", "open_feed", (), notifications, "Feed"),
    ("notifications", "remember_own", ("Title", "text"), notifications, "remember_own"),
    ("secrets", "protect", (b"data",), savecrypt, "protect"),
    ("secrets", "unprotect", (b"blob",), savecrypt, "unprotect"),
    ("folders", "known_folder", ("documents",), paths, "shell_folder"),
    ("autostart", "enabled", (), system, "autostart_enabled"),
    ("autostart", "set", (True,), system, "set_autostart"),
    ("installer", "is_setup_name", ("PixelCat-Setup.exe",), installer, "is_setup_name"),
    ("installer", "run_quiet", (SimpleNamespace(uninstall=True),), installer, "run_quiet"),
    ("installer", "run_window", ("install",), installer, "run_window"),
]


@pytest.fixture
def plat():  # type: ignore[no-untyped-def]
    return platform.build()


def _recorder(monkeypatch: pytest.MonkeyPatch, module: object, name: str) -> SimpleNamespace:
    rec = SimpleNamespace(calls=[], result=object())

    def fn(*args, **kwargs):  # type: ignore[no-untyped-def]
        rec.calls.append((args, kwargs))
        return rec.result

    monkeypatch.setattr(module, name, fn)
    return rec


@pytest.mark.parametrize("port, method, args, module, function", DELEGATES,
                         ids=[f"{d[0]}.{d[1]}" for d in DELEGATES])
def test_forwards_to_the_module_function(plat, monkeypatch, port, method, args, module, function):  # type: ignore[no-untyped-def]
    rec = _recorder(monkeypatch, module, function)
    adapter = getattr(plat, port)
    got = getattr(adapter, method)(*args)
    assert rec.calls == [(args, {})]
    if inspect.signature(getattr(type(adapter), method)).return_annotation != "None":
        assert got is rec.result


def test_the_table_covers_every_port_method(plat):  # type: ignore[no-untyped-def]
    translated = {("display", "user_state"), ("input", "set_passthrough_until"), ("input", "holding"),
                  ("clipboard", "has"), ("accessibility", "snapshot"), ("apps", "open_settings"),
                  ("save_marks", "is_marked"), ("save_marks", "mark"), ("save_marks", "forget")}
    public = {(port, name) for port in vars(plat) if port != "name"
              for name in dir(getattr(plat, port)) if not name.startswith("_") and name != "instance"}
    assert public == {(d[0], d[1]) for d in DELEGATES} | translated


def test_build_names_the_platform_and_fills_every_port(plat):  # type: ignore[no-untyped-def]
    assert plat.name == "windows"
    assert isinstance(plat.windows, platform.WindowsWindows) and isinstance(plat.installer, platform.WindowsInstaller)


def test_reaches_a_module_function_replaced_after_build(plat, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(win32, "cursor_pos", lambda: (1, 2))
    assert plat.input.cursor_pos() == (1, 2)
    monkeypatch.setattr(win32, "cursor_pos", lambda: (3, 4))
    assert plat.input.cursor_pos() == (3, 4)


# ---------------------------------------------------------------- translations
@pytest.mark.parametrize("state, expected", [
    (3, UserState.FULLSCREEN_APP),          # QUNS_RUNNING_D3D_FULL_SCREEN
    (4, UserState.PRESENTATION),            # QUNS_PRESENTATION_MODE
    (0, UserState.NORMAL),                  # (the query failed)
    (1, UserState.NORMAL),                  # QUNS_NOT_PRESENT (locked, screen saver)
    (2, UserState.NORMAL),                  # QUNS_BUSY
    (5, UserState.NORMAL),                  # QUNS_ACCEPTS_NOTIFICATIONS
    (6, UserState.NORMAL),                  # QUNS_QUIET_TIME
    (7, UserState.NORMAL),                  # QUNS_APP
])
def test_user_state(plat, monkeypatch, state, expected):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(win32, "notification_state", lambda: state)
    assert plat.display.user_state() is expected


@pytest.mark.parametrize("kinds, limit, uia_kinds", [("interactive", 150, uia.INTERACTIVE), ("readable", 400, uia.READABLE)])
def test_snapshot_maps_the_element_kinds(plat, monkeypatch, kinds, limit, uia_kinds):  # type: ignore[no-untyped-def]
    rec = _recorder(monkeypatch, uia, "snapshot")
    assert plat.accessibility.snapshot(7, kinds, limit) is rec.result
    assert rec.calls == [((7, uia_kinds, limit), {})]
    assert rec.calls[0][0][1] is uia_kinds


def test_snapshot_defaults_and_errors(plat, monkeypatch):  # type: ignore[no-untyped-def]
    rec = _recorder(monkeypatch, uia, "snapshot")
    plat.accessibility.snapshot(7)
    assert rec.calls == [((7, uia.INTERACTIVE, 150), {})]
    monkeypatch.setattr(uia, "snapshot", raising(OSError("UI Automation is gone")))
    with pytest.raises(OSError, match="UI Automation is gone"):
        plat.accessibility.snapshot(7, "readable")
    with pytest.raises(KeyError):
        plat.accessibility.snapshot(7, "everything")  # type: ignore[arg-type]


@pytest.mark.parametrize("kind, fmt", [("files", 15), ("rtf", "Rich Text Format"), ("html", "HTML Format")])
def test_clipboard_kinds(plat, monkeypatch, kind, fmt):  # type: ignore[no-untyped-def]
    rec = _recorder(monkeypatch, inputs, "clipboard_has_format")
    assert plat.clipboard.has(kind) is rec.result
    assert rec.calls == [((fmt,), {})]


def test_holding_maps_modifier_names_to_virtual_keys(plat, monkeypatch):  # type: ignore[no-untyped-def]
    rec = _recorder(monkeypatch, inputs, "holding")
    assert plat.input.holding(["ctrl", "shift", "alt", "win"]) is rec.result
    assert plat.input.holding([]) is rec.result
    assert rec.calls == [(([0x11, 0x10, 0x12, 0x5B],), {}), (([],), {})]
    assert set(platform._MODIFIER_VK) == set(domain.MODIFIERS.values())


def test_set_passthrough_until(plat, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inputs, "passthrough_until", 0.0)
    plat.input.set_passthrough_until(1234.5)
    assert inputs.passthrough_until == 1234.5


@pytest.mark.parametrize("page", domain.SETTINGS_PAGES)
def test_open_settings_launches_the_page_uri(plat, monkeypatch, page):  # type: ignore[no-untyped-def]
    rec = _recorder(monkeypatch, control, "launch")
    plat.apps.open_settings(page)
    assert rec.calls == [((control.SETTINGS_PAGES[page],), {})]


def test_open_settings_uri(plat, monkeypatch):  # type: ignore[no-untyped-def]
    rec = _recorder(monkeypatch, control, "launch")
    plat.apps.open_settings("display")
    assert rec.calls == [(("ms-settings:display",), {})]


def test_instance(plat, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(system, "INSTANCE", "-test2")
    assert plat.system.instance == "-test2"


# ---------------------------------------------------------------- the save marks (winreg, errors propagate)
def test_save_marks(plat, fake_winreg, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(persistence, "REG_PATH", r"Software\PixelCatTest\Marks")
    with pytest.raises(OSError):
        plat.save_marks.is_marked("abc")
    plat.save_marks.mark("abc")
    assert fake_winreg.keys[r"hkcu\software\pixelcattest\marks"] == {"abc": 1}
    assert fake_winreg.writes == [("set", r"hkcu\software\pixelcattest\marks", "abc", fake_winreg.REG_DWORD, 1)]
    assert plat.save_marks.is_marked("abc") is True
    plat.save_marks.forget("abc")
    with pytest.raises(OSError):
        plat.save_marks.is_marked("abc")
    with pytest.raises(OSError):
        plat.save_marks.forget("abc")


# ---------------------------------------------------------------- the Win32 bits that came from the core
def test_place_below(plat, w32):  # type: ignore[no-untyped-def]
    plat.windows.place_below(7, 8)
    assert w32.user32.SetWindowPos.calls == [(7, 8, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)]


def test_virtual_screen(plat, w32):  # type: ignore[no-untyped-def]
    w32.user32.GetSystemMetrics.result = {76: -1920, 77: -100, 78: 3840, 79: 1180}.get
    assert plat.display.virtual_screen() == (-1920, -100, 1920, 1080)


def test_rect_is_the_frame_or_the_window_rect(plat, w32, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(win32, "frame_bounds", lambda h: (1, 2, 3, 4))
    assert plat.windows.rect(7) == (1, 2, 3, 4)
    monkeypatch.setattr(win32, "frame_bounds", lambda h: None)

    def get_rect(h, ref):  # type: ignore[no-untyped-def]
        rc = out(ref)
        rc.left, rc.top, rc.right, rc.bottom = 10, 20, 30, 40
        return 1

    w32.user32.GetWindowRect.result = get_rect
    assert plat.windows.rect(7) == (10, 20, 30, 40)


@pytest.fixture
def own_windows(w32, monkeypatch):  # type: ignore[no-untyped-def]
    """Our windows 1-4 (pid 42; 4 is hidden), another app's 5; 1 is the cat."""
    T, L = win32.WS_EX_TRANSPARENT, win32.WS_EX_LAYERED
    ex = {1: L, 2: L, 3: L | T, 4: L, 5: L, 6: 0}
    enum_windows(w32.user32, [1, 2, 3, 4, 5, 6])
    w32.user32.IsWindowVisible.result = by_hwnd({1: 1, 2: 1, 3: 1, 5: 1, 6: 1})
    monkeypatch.setattr(win32, "window_pid", lambda h: 99 if h == 5 else 42)
    monkeypatch.setattr(win32, "get_exstyle", lambda h: ex[h])
    monkeypatch.setattr(win32, "set_exstyle", lambda h, v: ex.__setitem__(h, v))
    return ex


def test_own_click_through(plat, own_windows):  # type: ignore[no-untyped-def]
    T, L = win32.WS_EX_TRANSPARENT, win32.WS_EX_LAYERED
    # 1 is kept, 3 already lets clicks through, 4 is hidden, 5 isn't ours, 6 isn't layered
    assert plat.windows.own_click_through(42, 1) == [2]
    assert own_windows == {1: L, 2: L | T, 3: L | T, 4: L, 5: L, 6: 0}


def test_end_own_click_through_skips_closed_windows(plat, own_windows, w32):  # type: ignore[no-untyped-def]
    T, L = win32.WS_EX_TRANSPARENT, win32.WS_EX_LAYERED
    own_windows.update({2: L | T, 3: L | T})
    w32.user32.IsWindow.result = by_hwnd({2: 1})
    plat.windows.end_own_click_through([2, 3])
    assert own_windows[2] == L and own_windows[3] == L | T


@pytest.mark.parametrize("method, dll_function", [("exists", "IsWindow"), ("is_visible", "IsWindowVisible"),
                                                  ("is_minimized", "IsIconic"), ("is_hung", "IsHungAppWindow")])
def test_window_flags(plat, w32, method, dll_function):  # type: ignore[no-untyped-def]
    getattr(w32.user32, dll_function).result = by_hwnd({7: 1})
    assert getattr(plat.windows, method)(7) is True
    assert getattr(plat.windows, method)(8) is False
