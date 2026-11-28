"""PixelCatApp: construction, start, the loops (tick, windows, monitors, fullscreen, z-order),
saving and quitting - all with the desktop stubbed (see conftest.py)."""
from __future__ import annotations

import pytest

from pixelcat import naming, progress
from pixelcat.domain import FALLBACK_MONITOR, Monitor, UserState, WinInfo
from pixelcat.engine.states import S
from pixelcat.persistence import SavedState
from pixelcat.ui import theme

pytestmark = pytest.mark.qt


class FakeQApp:
    def __init__(self) -> None:
        self.quits = 0

    def quit(self) -> None:
        self.quits += 1


def _win(hwnd: int, rect: tuple[int, int, int, int], z: int = 0) -> WinInfo:
    return WinInfo(hwnd, rect, z, f"Window {hwnd}", "TestClass", False, False)


# ---------------------------------------------------------------- construction
def test_construction_wires_the_parts_without_starting_anything(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    assert [m.device for m in pet.world_state.monitors] == ["TEST1"]
    assert pet.menu.title == "PIXEL CAT" and pet.model_menu.title == "MODEL FOR THIS CHAT"
    assert pet.chat_ui.chat is None and pet.game_state.mini is None and pet.session.worker is None and pet.chat_state.history == []
    assert pet.brain.game is pet.game and pet.game.on_end == pet.minigames.game_ended
    assert not any(t.isActive() for t in (pet.tick_timer, pet.win_timer, pet.mon_timer, pet.fs_timer,
                                          pet.save_timer, pet.top_timer, pet.tray_timer, pet.quit_timer))
    assert pet.tick_timer.interval() == 1000 // pet.t.fps_active
    assert pet.platform.system.called("message_box") == [] and pet.platform.windows.calls == [] and app_env.ensures == []
    assert not pet.overlay.isVisible()


@pytest.mark.parametrize("logical", [False, True])
def test_the_platform_says_how_to_place_windows_and_where_ollama_is(make_app, fake_platform,  # type: ignore[no-untyped-def]
                                                                   logical: bool) -> None:
    from pixelcat.ui import geometry

    fake_platform.display.returns("qt_logical", logical)
    pet = make_app()
    assert geometry._QT_LOGICAL is logical
    assert pet.model_status.find_ollama == fake_platform.apps.find_ollama
    assert pet.model_status.start_ollama == fake_platform.apps.start_ollama


def test_locked_cosmetics_in_the_save_fall_back(make_app) -> None:  # type: ignore[no-untyped-def]
    locked_skin = next(k for k in progress.SKINS if not progress.is_unlocked(0, "skin", k))
    locked_hat = next(k for k in progress.HATS if not progress.is_unlocked(0, "hat", k))
    pet = make_app(SavedState(autostart_asked=True, skin=locked_skin, hat=locked_hat))
    assert (pet.saved.skin, pet.saved.hat) == ("charcoal", "")
    assert pet.sprites.skin.key == "charcoal" and pet.overlay.hat == ""


def test_the_saved_look_and_name_are_applied(make_app) -> None:  # type: ignore[no-untyped-def]
    pet = make_app(SavedState(autostart_asked=True, ui_style="midnight", ui_accent="teal", cat_name="Mochi"))
    assert theme.current().key == "midnight" and theme.choice("accent") == "teal"
    assert naming.custom() == "Mochi" and pet.menu.title == "MOCHI"
    assert "Find Mochi" in [a.text() for a in pet.tray.menu.actions()]


def test_offline_time_changes_the_needs(make_app) -> None:  # type: ignore[no-untyped-def]
    import time

    pet = make_app(SavedState(autostart_asked=True, hunger=0.1, last_seen=time.time() - 6 * 3600))
    assert pet.needs.hunger > 0.1


@pytest.mark.parametrize(("dpi", "override", "scale"), [(96, 0, 3), (120, 0, 4), (144, 0, 4), (192, 0, 6),
                                                       (96, 2.4, 2), (192, 5, 5)])
def test_scale_for(pet, dpi: int, override: float, scale: int) -> None:  # type: ignore[no-untyped-def]
    pet.cfg.pixel_scale_override = override
    assert pet.world.scale_for(Monitor(1, (0, 0, 10, 10), (0, 0, 10, 10), dpi, True, "x")) == scale


def test_ui_unit_follows_the_size_setting(pet) -> None:  # type: ignore[no-untyped-def]
    mon = pet.world_state.monitors[0]
    assert pet.world.ui_unit(mon) == 2
    theme.configure(size="huge")
    assert pet.world.ui_unit(mon) == 3


# ---------------------------------------------------------------- start
def test_start_shows_the_cat_and_starts_the_timers(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet.start()
    hwnd = pet.overlay.hwnd()
    assert pet.overlay.isVisible() and hwnd and pet.session.agent.overlay_hwnd == hwnd
    assert ((hwnd,), {}) in pet.platform.windows.called("make_tool_window")
    assert all(t.isActive() for t in (pet.tick_timer, pet.win_timer, pet.mon_timer, pet.fs_timer, pet.save_timer,
                                       pet.top_timer, pet.tray_timer, pet.quit_timer))
    assert pet.brain.state == S.SITTING
    assert pet.brain.x == 1920 - 160 and pet.brain.smap is not None
    assert app_env.ensures == []                            # (warm_model=False: the AI isn't loaded)


def test_starts_and_lives_on_the_null_platform(make_app, qtbot) -> None:  # type: ignore[no-untyped-def]
    # Off Windows and macOS (or on a Mac without pyobjc) bootstrap picks the null adapter: the cat must
    # still start, find a screen and keep running, with no desktop to look at.
    from pixelcat.adapters import null

    pet = make_app(platform=null.build())
    pet.start()
    assert pet.world_state.monitors == [FALLBACK_MONITOR] and pet.overlay.isVisible()
    for _ in range(30):
        for step in (pet.world.tick, pet.world.refresh_windows, pet.world.refresh_monitors, pet.world.check_fullscreen,
                     pet.world.keep_on_top, pet.world.sync_tray, pet._check_quit_request, pet.save):
            step()
    qtbot.wait(300)                                         # ... and its timers run too
    assert pet.tick_timer.isActive() and pet.overlay.isVisible()
    l, t, r, b = FALLBACK_MONITOR.rect
    assert l <= pet.brain.x <= r and t <= pet.brain.y <= b


def test_start_quiet_and_at_the_saved_place(make_app) -> None:  # type: ignore[no-untyped-def]
    pet = make_app(SavedState(autostart_asked=True, quiet=True, pos_x=500, pos_y=1039))
    pet.start()
    assert pet.brain.state == S.LOAFING and pet.brain.x == 500


def test_the_note_about_an_unreadable_save_goes_with_the_app(make_app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # (ProgressIO is no QObject: without the app as the timer's context the note would still pop up after the app
    # was deleted)
    from PySide6.QtCore import QTimer

    from pixelcat import persistence

    later: list[tuple] = []
    monkeypatch.setattr(QTimer, "singleShot", lambda *a: later.append(a))
    monkeypatch.setattr(persistence, "last_load", "unreadable")
    pet = make_app()
    pet.start()
    assert (3000, pet, pet.progress_io.tell_unreadable_save) in later


def test_the_note_about_a_locked_save_goes_with_the_app(make_app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QTimer

    from pixelcat import persistence

    later: list[tuple] = []
    monkeypatch.setattr(QTimer, "singleShot", lambda *a: later.append(a))
    monkeypatch.setattr(persistence, "last_load", "locked")
    pet = make_app()
    pet.start()
    assert (3000, pet, pet.progress_io.tell_locked_save) in later
    assert all(a[-1] != pet.progress_io.tell_unreadable_save for a in later)


def test_a_save_the_secret_store_refuses_is_told_once_and_not_tried_again(make_app, monkeypatch,  # type: ignore[no-untyped-def]
                                                                          tmp_path) -> None:
    from PySide6.QtCore import QTimer

    import pixelcat.app.controller as app_mod
    from pixelcat import paths, persistence
    from pixelcat.domain import SecretStoreUnavailable

    monkeypatch.setattr(persistence, "_locked", set())
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "PixelCat")
    monkeypatch.setattr(app_mod, "save_state", persistence.save_state)
    later: list[tuple] = []
    pet = make_app()
    monkeypatch.setattr(QTimer, "singleShot", lambda *a: later.append(a))
    asked: list[bytes] = []

    def refuse(data: bytes) -> bytes:
        asked.append(data)
        raise SecretStoreUnavailable("the user denied the keychain")
    pet.platform.secrets.answers("protect", refuse)
    told: list[bool] = []
    monkeypatch.setattr(pet.progress_io, "tell_locked_save", lambda saving=False: told.append(saving))
    for _ in range(3):
        pet.save()
    notes = [a for a in later if a[:2] == (0, pet)]
    assert len(asked) == 1 and len(notes) == 1
    notes[0][-1]()
    assert told == [True]


def test_a_saved_place_off_every_screen_is_ignored(make_app) -> None:  # type: ignore[no-untyped-def]
    pet = make_app(SavedState(autostart_asked=True, pos_x=-9000, pos_y=200))
    pet.start()
    assert pet.brain.x == 1920 - 160


def test_selftest_finishes_by_itself_without_asking(make_app, qtbot, app_env) -> None:  # type: ignore[no-untyped-def]
    pet = make_app(SavedState(autostart_asked=False), selftest=0.01)
    finished: list[bool] = []
    pet._selftest_finish = lambda: finished.append(True)
    pet.start(warm_model=True)
    assert not pet.quit_timer.isActive()                    # (a selftest is not the installed instance)
    qtbot.waitUntil(lambda: finished == [True], timeout=2000)
    assert app_env.ensures == [] and not hasattr(pet, "_dialog")


def test_selftest_checks_each_part_of_the_build_once(pet, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    import anthropic
    from copilot._cli_version import CLI_VERSION

    from pixelcat.adapters.llm import copilot_client

    monkeypatch.setattr(copilot_client, "new_client", lambda token, path: None)
    monkeypatch.setattr(copilot_client, "runtime_path", lambda: None)
    external_ok = f"selftest: external models ok (anthropic {anthropic.__version__})"
    with caplog.at_level(logging.INFO, logger="pixelcat.app"):
        pet._selftest_providers()
        monkeypatch.setattr(copilot_client, "new_client", lambda token, path: 1 / 0)
        pet._selftest_providers()
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        ("pixelcat.app", external_ok),
        ("pixelcat.app", f"selftest: copilot sdk ok (runtime {CLI_VERSION}, downloaded on first use)"),
        ("pixelcat.app", external_ok), ("pixelcat.app", "selftest: copilot sdk broken")]
    assert caplog.records[-1].exc_info is not None


# ---------------------------------------------------------------- the loops
@pytest.fixture
def started(pet):  # type: ignore[no-untyped-def]
    pet.start()
    for tm in pet.findChildren(type(pet.tick_timer)):
        tm.stop()
    errors: list[str] = []
    pet.rl.exception = lambda key, msg, *a: errors.append(f"{key}: {msg}")
    pet.errors = errors
    return pet


def test_ticks_run_the_cat(started, app_env) -> None:  # type: ignore[no-untyped-def]
    pet = started
    clock = pet.brain.clock
    for _ in range(40):
        pet.world._last_tick -= 0.05
        pet.world.tick()
    assert pet.errors == []
    assert pet.brain.clock > clock
    assert pet.tick_timer.interval() == 1000 // pet.brain.fps_hint()


def test_ticks_with_the_ball_game(started) -> None:  # type: ignore[no-untyped-def]
    pet = started
    pet.saved.tutorials_seen.append("ball")
    pet.minigames.start_minigame("ball")
    assert pet.game.active and pet.minigames.running_game() == "ball"
    for _ in range(30):
        pet.world._last_tick -= 1 / 60
        pet.world.tick()
    assert pet.errors == [] and pet.ball_win.isVisible()
    assert pet.tick_timer.interval() == 1000 // pet.t.fps_game
    pet._on_menu("stopgame")
    assert not pet.game.active and pet.saved.plays == 1


def test_a_tick_while_hidden_only_slows_down(started) -> None:  # type: ignore[no-untyped-def]
    pet = started
    pet.world_state.hidden_fullscreen = True
    clock = pet.brain.clock
    pet.world.tick()
    assert pet.brain.clock == clock and pet.tick_timer.interval() == 1000 // pet.t.fps_hidden


def test_window_refresh_builds_the_world_from_the_windows(started, app_env) -> None:  # type: ignore[no-untyped-def]
    pet = started
    app_env.desktop.windows = [_win(101, (100, 300, 900, 800))]
    pet.world.refresh_windows()
    assert pet.errors == []
    assert [w.hwnd for w in pet.world._real_wins] == [101] and 101 in pet.brain.win_rects
    assert any(s.hwnd == 101 for s in pet.brain.smap.surfaces)


def test_a_new_window_next_to_the_cat_startles_it(started, app_env) -> None:  # type: ignore[no-untyped-def]
    pet = started
    pet.world.refresh_windows()
    b = pet.brain
    startled: list[bool] = []
    b.startle = lambda: startled.append(True)
    x, y = int(b.x), int(b.y)
    app_env.desktop.windows = [_win(202, (x + 20, y - 400, x + 500, y - 120))]
    pet.world.refresh_windows()
    app_env.desktop.windows.append(_win(303, (0, 0, 50, 50), 1))
    pet.world.refresh_windows()
    assert startled == [True]


def test_hidden_during_fullscreen_apps_and_back(started, app_env) -> None:  # type: ignore[no-untyped-def]
    pet = started
    pet.chat_ui.ensure_chat().show_near(pet.overlay.cat_rect(), pet.world_state.monitors[0].work, 2, activate=False)
    app_env.desktop.user_state = UserState.FULLSCREEN_APP
    pet.world.check_fullscreen()
    assert pet.world_state.hidden_fullscreen and not pet.overlay.isVisible() and not pet.chat_ui.chat.isVisible()
    app_env.desktop.user_state = UserState.NORMAL
    pet.world.check_fullscreen()
    assert not pet.world_state.hidden_fullscreen and pet.overlay.isVisible() and pet.chat_ui.chat.isVisible()
    assert pet.errors == []


@pytest.mark.parametrize(("presentations", "hides"), [(True, True), (False, False)])
def test_presentation_mode(started, app_env, presentations: bool, hides: bool) -> None:  # type: ignore[no-untyped-def]
    started.cfg.hide_during_presentations = presentations
    app_env.desktop.user_state = UserState.PRESENTATION
    started.world.check_fullscreen()
    assert started.world_state.hidden_fullscreen is hides


def test_a_fullscreen_foreground_window_of_another_app_hides_the_cat(started, app_env) -> None:  # type: ignore[no-untyped-def]
    app_env.desktop.foreground, app_env.desktop.fullscreen = 77, True
    started.world.check_fullscreen()
    assert started.world_state.hidden_fullscreen
    started.platform.windows.returns("pid", started.own_pid)                    # our own window: not hidden
    started.world.check_fullscreen()
    assert not started.world_state.hidden_fullscreen


def test_menus_and_popups_are_closed_while_hidden(started, app_env) -> None:  # type: ignore[no-untyped-def]
    pet = started
    pet.menus.open_menu()
    assert pet.menu.isVisible()
    pet.world._set_hidden(True)
    assert not pet.menu.isVisible()
    pet.menus.open_menu()
    assert not pet.menu.isVisible()                    # (nothing opens while hidden)
    pet.world._set_hidden(False)


def test_a_new_monitor_layout_is_picked_up(started, app_env) -> None:  # type: ignore[no-untyped-def]
    pet = started
    new = Monitor(2, (-2560, 0, 0, 1440), (-2560, 0, 0, 1400), 144, True, "TEST2")
    app_env.desktop.monitors = [new]
    pet.world.refresh_monitors()
    assert pet.world_state.monitors == [new] and pet.errors == []
    assert -2560 <= pet.brain.x < 0                    # the cat was put on the remaining screen
    n = len(pet.brain.win_rects)
    pet.world.refresh_monitors()
    assert pet.world_state.monitors == [new] and len(pet.brain.win_rects) == n


def test_keep_on_top(started) -> None:  # type: ignore[no-untyped-def]
    started.world.keep_on_top()
    assert ((started.overlay.hwnd(),), {}) in started.platform.windows.called("keep_topmost")


def test_the_window_under_the_cat_is_followed(started, app_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pet = started
    app_env.desktop.windows = [_win(404, (200, 600, 1400, 1000))]
    pet.world.refresh_windows()
    rides: list[tuple] = []
    monkeypatch.setattr(pet.brain, "ride_window", lambda hwnd, rect: rides.append((hwnd, rect)))
    surf = next(s for s in pet.brain.smap.surfaces if s.hwnd == 404)
    pet.brain.mode, pet.brain.surf = "ground", surf
    pet.world._ride_window()                            # (the fake platform says: no such window)
    assert rides == [(404, None)] and pet.world.ledge_t == 0.0


def test_tray_status(started) -> None:  # type: ignore[no-untyped-def]
    started.world.sync_tray()
    assert started.tray.toolTip().startswith("Pixel Cat - ")
    started.brain.set_paused(True)
    started.world.sync_tray()
    assert [a.text() for a in started.tray.menu.actions()][0] == "Resume"


def test_the_tick_survives_errors(started) -> None:  # type: ignore[no-untyped-def]
    def boom(dt, ctx):  # type: ignore[no-untyped-def]
        raise ValueError("bad frame")

    started.brain.update = boom
    started.world.tick()
    assert started.errors == ["tick: update loop error"]


# ---------------------------------------------------------------- saving and quitting
def test_save_writes_the_cats_state(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet.needs.energy, pet.quiet = 0.33, True
    pet.brain.mode, pet.brain.x, pet.brain.y = "ground", 640.4, 1039.0
    pet.save()
    st = app_env.saved_states[-1]
    assert st is pet.saved
    assert (st.energy, st.quiet, st.pos_x, st.pos_y, st.watches) == (0.33, True, 640, 1039, [])


def test_quit_saves_stops_and_hides_everything(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet.start()
    pet.qapp = FakeQApp()
    pet.chat_ui.ensure_chat().show()
    pet.quit()
    assert pet._quitting and pet.qapp.quits == 1 and app_env.saved_states
    assert not any(t.isActive() for t in (pet.tick_timer, pet.win_timer, pet.mon_timer, pet.fs_timer,
                                          pet.save_timer))
    assert not pet.overlay.isVisible() and not pet.chat_ui.chat.isVisible() and not pet.ball_win.isVisible()
    assert pet.session.agent.cancel.is_set() and pet.watches._stop.is_set() and pet.model_status._stop.is_set()


def test_quit_still_quits_when_saving_fails(pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pet.qapp = FakeQApp()

    def broken() -> None:
        raise OSError("disk full")

    pet.save = broken
    with pytest.raises(OSError):
        pet.quit()
    assert pet.qapp.quits == 1


def test_quit_from_the_menu_and_the_quit_request(pet) -> None:  # type: ignore[no-untyped-def]
    pet.qapp = FakeQApp()
    pet._on_menu("quit")
    assert pet.qapp.quits == 1
    pet.platform.system.returns("quit_requested", True)
    pet._check_quit_request()
    assert pet.qapp.quits == 2


def test_autostart_question(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet.saved.autostart_asked = False
    pet._ask_autostart()
    assert pet._dialog.isVisible()
    pet._dialog.yes.click()
    assert pet.saved.autostart_asked and app_env.autostart_set == [True] and app_env.saved_states


def test_looking_inside_windows_for_ledges(make_app, app_env) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.settings import Config

    cfg = Config()
    cfg.explore_windows = True
    pet = make_app(cfg=cfg)
    searches: list[tuple] = []
    pet.ledges.start = lambda wins, area, own, min_len, scale: searches.append(  # type: ignore[method-assign]
        ([w.hwnd for w in wins], area, min_len, scale)) or True          # (no screen capture thread)
    pet.start()
    searches.clear()
    app_env.desktop.windows = [_win(505, (100, 200, 1000, 900)), _win(606, (3000, 0, 3500, 500), 1)]
    pet.brain.mode, pet.brain.ride_t = "ground", 0.0
    pet.brain.set_state(S.SITTING, travel=False)
    pet.world.ledge_t = -100.0
    pet.world.refresh_windows()
    assert searches == [([505, 606], (0, 0, 1920, 1040), 16 * 3, 3)]
    pet.world.refresh_windows()
    assert len(searches) == 1                           # (not again right away)
    pet._on_menu("explore")
    assert not pet.cfg.explore_windows and pet.ledges.wins == {}
