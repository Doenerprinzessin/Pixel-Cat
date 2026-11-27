"""Stubs for the UI and ``PixelCatApp`` tests.

Every desktop call the widgets and the app controller reach (Win32 window styles, the cursor,
monitors, the window list, the registry, DPAPI, Ollama, the Copilot runtime, the browser) is
replaced here, so the tests do the same on macOS and on a real Windows desktop.  Later PRs that
move these functions behind ports only need to update the targets in this file.
"""
from __future__ import annotations

import webbrowser
from dataclasses import dataclass, field
from typing import Any

import pytest

from pixelcat.domain import Monitor

MONITOR = Monitor(handle=1, rect=(0, 0, 1920, 1080), work=(0, 0, 1920, 1040), dpi=96, primary=True,
                  device="TEST1")


@dataclass
class Desktop:
    """What the fake desktop reports, and what was asked of it."""

    cursor: tuple[int, int] = (5000, 5000)          # far away from everything
    buttons_down: bool = False
    idle: float = 0.0
    monitors: list[Monitor] = field(default_factory=lambda: [MONITOR])
    windows: list[Any] = field(default_factory=list)
    foreground: int = 0
    fullscreen: bool = False
    notification_state: int = 5                     # QUNS_ACCEPTS_NOTIFICATIONS
    autostart: bool = False
    calls: list[tuple] = field(default_factory=list)

    def log(self, name: str):  # type: ignore[no-untyped-def]
        def fn(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, *args, *kwargs.values()))
        return fn

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


def _fake_protect(data: bytes) -> bytes:
    return b"FAKE" + data


def _fake_unprotect(blob: bytes) -> bytes:
    from pixelcat import savecrypt

    if not blob.startswith(b"FAKE"):
        raise savecrypt.Unreadable("not ours")
    return blob[4:]


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch, fake_dll) -> Desktop:  # type: ignore[no-untyped-def]
    """The Win32 calls the overlay, the ball, the chat, the menu and the minigame windows make."""
    from pixelcat.adapters.windows import escwatch, inputs, win32

    d = Desktop()
    monkeypatch.setattr(win32, "user32", fake_dll("user32"))
    for name in ("set_click_through", "make_tool_noactivate", "move_window", "keep_topmost", "set_exstyle",
                 "message_box"):
        monkeypatch.setattr(win32, name, d.log(name))
    monkeypatch.setattr(win32, "cursor_pos", lambda: d.cursor)
    monkeypatch.setattr(win32, "release_went_elsewhere", lambda hwnd: False)
    monkeypatch.setattr(win32, "left_button_down", lambda: d.buttons_down)
    monkeypatch.setattr(win32, "has_mouse_capture", lambda hwnd: False)
    monkeypatch.setattr(win32, "mouse_buttons_down", lambda: d.buttons_down)
    monkeypatch.setattr(win32, "last_input_tick", lambda: 0)
    monkeypatch.setattr(win32, "idle_seconds", lambda: d.idle)
    monkeypatch.setattr(win32, "window_dpi", lambda hwnd: 96)
    monkeypatch.setattr(win32, "notification_state", lambda: d.notification_state)
    monkeypatch.setattr(win32, "window_pid", lambda hwnd: 4242)
    monkeypatch.setattr(win32, "is_cloaked", lambda hwnd: False)
    monkeypatch.setattr(win32, "frame_bounds", lambda hwnd: None)
    monkeypatch.setattr(win32, "get_exstyle", lambda hwnd: 0)
    monkeypatch.setattr(inputs, "passthrough_active", lambda: False)
    hook_dll = fake_dll("user32")
    hook_dll.SetWindowsHookExW.result = 0xBEEF                       # (a fake hook handle)
    monkeypatch.setattr(escwatch, "_u32", hook_dll)                  # never a real keyboard hook
    monkeypatch.setattr(escwatch, "_k32", fake_dll("kernel32"))
    return d


@pytest.fixture(autouse=True)
def ui_globals(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """The look (theme), the cat's name and the model names are module globals: each test starts
    from the defaults and leaves nothing behind (e.g. listeners of deleted windows)."""
    from pixelcat import naming
    from pixelcat.llm import external
    from pixelcat.ui import theme

    monkeypatch.setattr(theme, "_listeners", [])
    monkeypatch.setattr(theme, "_choice", {"style": "catchat", "accent": "default", "size": "normal",
                                           "font": "default"})
    monkeypatch.setattr(theme, "_style", theme.STYLES["catchat"])
    for colour in ("INK", "PAPER", "PAPER_DARK", "ACCENT", "ACCENT_DARK", "TEXT", "MUTED", "SHADOW"):
        monkeypatch.setattr(theme, colour, getattr(theme, colour))
    monkeypatch.setattr(naming, "_name", "")
    monkeypatch.setattr(external, "NAMES", {})
    theme._sync_colours()
    yield
    theme._listeners.clear()


def _flush_deletes() -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def close_new_windows(qapp):  # type: ignore[no-untyped-def]
    """Every window a test opened (popups, effects, the tray's menu...) is deleted before the
    desktop stubs are undone - so no timer of a leftover window runs later against real Win32
    (and queued single-shot calls to a deleted app object are dropped)."""
    import shiboken6

    before = {shiboken6.getCppPointer(w)[0] for w in qapp.topLevelWidgets()}
    yield
    for w in qapp.topLevelWidgets():
        if shiboken6.getCppPointer(w)[0] not in before:
            try:
                w.hide()
                w.deleteLater()
            except RuntimeError:
                pass
    _flush_deletes()


@pytest.fixture
def sprites(qapp):  # type: ignore[no-untyped-def]
    from pixelcat.sprites.library import SpriteLibrary

    return SpriteLibrary("charcoal")


@pytest.fixture
def track(qapp):  # type: ignore[no-untyped-def]
    """``track(widget)``: closed after the test (also widgets that delete themselves on close)."""
    widgets: list[Any] = []

    def add(w: Any) -> Any:
        widgets.append(w)
        return w

    yield add
    for w in widgets:
        try:
            w.hide()
            w.deleteLater()
        except RuntimeError:                        # (already deleted)
            pass
    _flush_deletes()


@pytest.fixture
def tuning():  # type: ignore[no-untyped-def]
    from pixelcat.settings import Tuning

    return Tuning()


# ---------------------------------------------------------------- PixelCatApp
@dataclass
class AppEnv:
    desktop: Desktop
    saved_states: list[Any] = field(default_factory=list)
    user_settings: list[tuple[str, Any]] = field(default_factory=list)
    ensures: list[bool] = field(default_factory=list)
    opened_urls: list[str] = field(default_factory=list)
    autostart_set: list[bool] = field(default_factory=list)
    clipboard: Any = field(default_factory=lambda: Clipboard())


class Clipboard:
    def __init__(self) -> None:
        self._text = ""

    def setText(self, text: str) -> None:  # noqa: N802 - Qt's name
        self._text = text

    def text(self) -> str:
        return self._text


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch, desktop: Desktop, tmp_path) -> AppEnv:  # type: ignore[no-untyped-def]
    """Everything ``PixelCatApp`` touches outside the process, replaced."""
    from PySide6.QtGui import QDesktopServices, QGuiApplication

    import pixelcat.app as app_mod
    from pixelcat import persistence, savecrypt, settings
    from pixelcat.adapters.windows import control, hardware, monitors, system
    from pixelcat.llm import model_status
    from pixelcat.sprites.library import SpriteLibrary
    from pixelcat.ui import tray

    env = AppEnv(desktop)
    monkeypatch.setattr(app_mod, "enumerate_monitors", lambda: list(desktop.monitors))
    monkeypatch.setattr(monitors, "enumerate_monitors", lambda: list(desktop.monitors))
    monkeypatch.setattr(app_mod, "enumerate_windows", lambda own_pid, include=frozenset(): list(desktop.windows))
    monkeypatch.setattr(app_mod, "foreground_window", lambda: desktop.foreground)
    monkeypatch.setattr(app_mod, "is_fullscreen_window", lambda hwnd, rect: desktop.fullscreen)
    monkeypatch.setattr(app_mod, "save_state", lambda st, *a, **k: env.saved_states.append(st))
    monkeypatch.setattr(persistence, "last_load", "fresh")            # (another test may have loaded a save)
    monkeypatch.setattr(app_mod.PixelCatApp, "_documents", staticmethod(lambda: tmp_path / "Documents"))
    monkeypatch.setattr(system, "autostart_enabled", lambda: desktop.autostart)
    monkeypatch.setattr(system, "set_autostart", lambda on: (env.autostart_set.append(on), True)[1])
    monkeypatch.setattr(system, "create_quit_event", lambda: 0)
    monkeypatch.setattr(system, "quit_requested", lambda handle: False)
    monkeypatch.setattr(system, "session_locked", lambda: False)
    monkeypatch.setattr(control, "prefetch_store_apps", lambda: None)
    monkeypatch.setattr(SpriteLibrary, "prewarm", lambda self, poses, scale: 0)     # (a 1 s warm-up cache)
    monkeypatch.setattr(hardware, "detect", lambda fresh=False: hardware.Hardware(
        ram_gb=32.0, cores=8, cpu="Test CPU", avx2=True, gpus=[], disk_free_gb=500.0))
    monkeypatch.setattr(model_status.ModelStatus, "ensure", lambda self, force=False: env.ensures.append(force))
    monkeypatch.setattr(savecrypt, "protect", _fake_protect)
    monkeypatch.setattr(savecrypt, "unprotect", _fake_unprotect)
    monkeypatch.setattr(settings, "save_user_setting", lambda k, v: env.user_settings.append((k, v)))
    monkeypatch.setattr(tray.Tray, "show", lambda self: None)
    monkeypatch.setattr(tray.Tray, "showMessage", lambda self, *a, **k: None)
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: env.opened_urls.append(url.toString())))
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: env.opened_urls.append(url))
    monkeypatch.setattr(QGuiApplication, "clipboard", staticmethod(lambda: env.clipboard))   # not the real one
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "ollama-models"))     # no model is downloaded
    from pixelcat.llm import anthropic_client, copilot_client, openai_client

    def no_request(*a: Any, **k: Any) -> None:
        raise AssertionError("a test reached a real model request - use the `workers` fixture or a fake")

    monkeypatch.setattr(app_mod, "ChatWorker", no_request)
    for mod, name in ((anthropic_client, "AnthropicWorker"), (openai_client, "OpenAIWorker"),
                      (copilot_client, "CopilotWorker")):
        monkeypatch.setattr(mod, name, no_request)
    return env


def _close_all(pet: Any) -> None:
    for th in [*pet._live_tools, pet._tool_thread, pet.worker, *pet._live_workers]:
        if th is not None and hasattr(th, "isRunning") and th.isRunning():
            th.wait(5000)                           # (a QThread destroyed while running aborts Python)
    for tm in pet.findChildren(type(pet.tick_timer)):
        tm.stop()
    for w in [pet.chat, pet.menu, pet.model_menu, pet.overlay, pet.ball_win, pet.hud, *pet._popups,
              getattr(pet, "_key_dialog", None), getattr(pet, "_dialog", None)]:
        try:
            if w is not None:
                w.hide()
                w.deleteLater()
        except RuntimeError:
            pass
    pet.tray.hide()
    pet.tray.deleteLater()
    pet.watches.stop()
    pet.model_status.stop()


@pytest.fixture
def make_app(qapp, app_env: AppEnv, fake_platform):  # type: ignore[no-untyped-def]
    """``make_app(saved=..., cfg=..., selftest=...)`` -> a headless ``PixelCatApp`` (not started:
    no timer runs unless a test starts it or calls the loop methods itself).  Looking inside
    windows (screen captures) is off unless a test passes its own ``cfg``."""
    from pixelcat.app import PixelCatApp
    from pixelcat.persistence import SavedState
    from pixelcat.settings import Config

    made: list[Any] = []

    def make(saved: SavedState | None = None, cfg: Config | None = None, selftest: float = 0.0) -> PixelCatApp:
        if cfg is None:
            cfg = Config()
            cfg.explore_windows = False
        pet = PixelCatApp(qapp, cfg, saved or SavedState(autostart_asked=True), selftest=selftest,
                          platform=fake_platform)
        made.append(pet)
        return pet

    yield make
    for pet in made:
        _close_all(pet)
        pet.deleteLater()
    _flush_deletes()


@pytest.fixture
def pet(make_app):  # type: ignore[no-untyped-def]
    return make_app()


@pytest.fixture
def sync_background():  # type: ignore[no-untyped-def]
    """``sync_background(pet)``: what the app runs in a background thread runs right away."""
    def install(pet: Any) -> None:
        def run(fn: Any, cb: Any) -> None:
            try:
                res = fn()
            except Exception as exc:  # noqa: BLE001 - handed to cb like the real one
                res = exc
            cb(res)
        pet._in_background = run
    return install


@pytest.fixture
def workers(monkeypatch: pytest.MonkeyPatch, app_env: AppEnv) -> list[Any]:
    """Local-model requests go to fake workers the test drives (tokens, done, failed)."""
    from PySide6.QtCore import QObject, Signal

    import pixelcat.app as app_mod

    made: list[Any] = []

    class FakeWorker(QObject):
        token = Signal(str)
        thinking = Signal(int)
        done = Signal(str, list)
        failed = Signal(str, str)
        finished = Signal()

        def __init__(self, cfg: Any, messages: list[dict[str, Any]], tools: Any, think: Any,
                     model: str | None = None) -> None:
            super().__init__()
            self.messages, self.tools, self.think, self.model = messages, tools, think, model
            self.started = self.cancelled = False
            made.append(self)

        def start(self) -> None:
            self.started = True

        def cancel(self) -> None:
            self.cancelled = True

        def wait(self, ms: int = 0) -> bool:
            return True

    monkeypatch.setattr(app_mod, "ChatWorker", FakeWorker)
    return made
