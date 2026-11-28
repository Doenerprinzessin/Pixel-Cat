"""Stubs for the UI and ``PixelCatApp`` tests.

The widgets and the app controller reach the desktop (window styles, the cursor, monitors, the window
list, the PC, DPAPI...) only through the ports of ``fake_platform``; what those ports answer comes from
:class:`Desktop` here.  Ollama, the Copilot runtime and the browser are replaced too, so the tests do the
same on macOS and on a real Windows desktop.
"""
from __future__ import annotations

import webbrowser
from dataclasses import dataclass, field
from typing import Any

import pytest

from pixelcat.domain import Hardware, Monitor, UserState

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
    user_state: UserState = UserState.NORMAL
    autostart: bool = False


def _fake_protect(data: bytes) -> bytes:
    return b"FAKE" + data


def _fake_unprotect(blob: bytes) -> bytes:
    from pixelcat import savecrypt

    if not blob.startswith(b"FAKE"):
        raise savecrypt.Unreadable("not ours")
    return blob[4:]


@pytest.fixture
def desktop(fake_platform) -> Desktop:  # type: ignore[no-untyped-def]
    """What the ports of ``fake_platform`` report (the pointer, the monitors, the window list...).  The
    overlay, the ball, the chat, the minigame windows and the app controller use those ports; their
    calls are recorded there.  Everything else the ports answer is the null adapter's "nothing there"
    (no window exists, none is cloaked, no frame bounds, Esc is never pressed...)."""
    d = Desktop()
    inp, disp, wins = fake_platform.input, fake_platform.display, fake_platform.windows
    inp.answers("cursor_pos", lambda: d.cursor)
    inp.answers("mouse_buttons_down", lambda: d.buttons_down)
    inp.answers("idle_seconds", lambda: d.idle)
    disp.returns("window_dpi", 96)
    disp.answers("monitors", lambda: list(d.monitors))
    disp.answers("user_state", lambda: d.user_state)
    wins.answers("top_level", lambda own_pid, include=frozenset(): list(d.windows))
    wins.answers("foreground", lambda: d.foreground)
    wins.answers("is_fullscreen", lambda win, monitor_rect: d.fullscreen)
    wins.returns("pid", 4242)
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
def app_env(monkeypatch: pytest.MonkeyPatch, desktop: Desktop, tmp_path, fake_platform) -> AppEnv:  # type: ignore[no-untyped-def]
    """Everything ``PixelCatApp`` touches outside the process, replaced."""
    from PySide6.QtGui import QDesktopServices, QGuiApplication

    import pixelcat.app as app_mod
    from pixelcat import persistence, settings
    from pixelcat.llm import model_status
    from pixelcat.sprites.library import SpriteLibrary
    from pixelcat.ui import tray

    env = AppEnv(desktop)
    monkeypatch.setattr(app_mod, "save_state", lambda st, *a, **k: env.saved_states.append(st))
    monkeypatch.setattr(persistence, "last_load", "fresh")            # (another test may have loaded a save)
    monkeypatch.setattr(app_mod.PixelCatApp, "_documents", staticmethod(lambda: tmp_path / "Documents"))
    fake_platform.autostart.answers("enabled", lambda: desktop.autostart)
    fake_platform.autostart.answers("set", lambda on: (env.autostart_set.append(on), True)[1])
    monkeypatch.setattr(SpriteLibrary, "prewarm", lambda self, poses, scale: 0)     # (a 1 s warm-up cache)
    fake_platform.system.answers("hardware", lambda fresh=False: Hardware(
        ram_gb=32.0, cores=8, cpu="Test CPU", avx2=True, gpus=[], disk_free_gb=500.0))
    monkeypatch.setattr(model_status.ModelStatus, "ensure", lambda self, force=False: env.ensures.append(force))
    fake_platform.secrets.answers("protect", _fake_protect)
    fake_platform.secrets.answers("unprotect", _fake_unprotect)
    monkeypatch.setattr(settings, "save_user_setting", lambda k, v: env.user_settings.append((k, v)))
    monkeypatch.setattr(tray.Tray, "show", lambda self: None)
    monkeypatch.setattr(tray.Tray, "showMessage", lambda self, *a, **k: None)
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: env.opened_urls.append(url.toString())))
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: env.opened_urls.append(url))
    monkeypatch.setattr(QGuiApplication, "clipboard", staticmethod(lambda: env.clipboard))   # not the real one
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "ollama-models"))     # no model is downloaded
    from pixelcat.llm import anthropic_client, copilot_client, openai_client, qt_chat

    def no_request(*a: Any, **k: Any) -> None:
        raise AssertionError("a test reached a real model request - use the `workers` fixture or a fake")

    monkeypatch.setattr(app_mod, "OllamaChat", no_request)
    for mod, name in ((anthropic_client, "AnthropicChat"), (openai_client, "OpenAIChat"),
                      (copilot_client, "CopilotJob")):
        monkeypatch.setattr(mod, name, no_request)
    # (no chat job runs in a thread here: start() runs the (fake) job at once; the test reports through its events)
    monkeypatch.setattr(qt_chat.QtChatWorker, "start", lambda self, *a: self.run())
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
    """``make_app(saved=..., cfg=..., selftest=..., platform=...)`` -> a headless ``PixelCatApp`` (not started:
    no timer runs unless a test starts it or calls the loop methods itself).  Looking inside
    windows (screen captures) is off unless a test passes its own ``cfg``."""
    from pixelcat.app import PixelCatApp
    from pixelcat.persistence import SavedState
    from pixelcat.settings import Config

    made: list[Any] = []

    def make(saved: SavedState | None = None, cfg: Config | None = None, selftest: float = 0.0,
             platform: Any = None) -> PixelCatApp:
        if cfg is None:
            cfg = Config()
            cfg.explore_windows = False
        pet = PixelCatApp(qapp, cfg, saved or SavedState(autostart_asked=True), selftest=selftest,
                          platform=fake_platform if platform is None else platform)
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
    """Local-model requests go to fake chat jobs the test drives through their events
    (``workers[-1].events.done("hi", [])``)."""
    from fakes.chat import FakeJob

    import pixelcat.app as app_mod

    made: list[Any] = []

    def make(req: Any, events: Any) -> FakeJob:
        job = FakeJob(req, events)
        made.append(job)
        return job

    monkeypatch.setattr(app_mod, "OllamaChat", make)
    return made
