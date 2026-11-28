"""The cat's world and loop without the whole app: a real brain, fake ports and fake windows."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pixelcat.app.state import GameState, WorldState
from pixelcat.app.world import World
from pixelcat.domain import Monitor, UserState, WinInfo
from pixelcat.engine.brain import Brain
from pixelcat.engine.needs import Needs
from pixelcat.persistence import SavedState
from pixelcat.settings import Config

SCREEN = Monitor(1, (0, 0, 1920, 1080), (0, 0, 1920, 1040), 96, True, "TEST1")


class FakeWidget:
    def __init__(self, wid: int, visible: bool = True) -> None:
        self.wid, self.visible = wid, visible

    def isVisible(self) -> bool:  # noqa: N802 - the Qt name
        return self.visible

    def winId(self) -> int:  # noqa: N802
        return self.wid

    def show(self) -> None:
        self.visible = True

    def reshow(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def close(self) -> None:
        self.visible = False


class FakeTimer:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def interval(self) -> int:
        return self.ms

    def setInterval(self, ms: int) -> None:  # noqa: N802
        self.ms = ms


def make(platform: Any) -> tuple[World, SimpleNamespace]:
    cfg = Config()
    cfg.explore_windows = False                                  # (no screen pictures to look for ledges in)
    brain = Brain(cfg.tuning, Needs(0.9, 0.3, 0.7))
    brain.place(900, 1039)
    overlay = FakeWidget(1)
    overlay.cat_rect = lambda: (880, 1000, 920, 1040)            # type: ignore[attr-defined]
    host = SimpleNamespace(hidden=False, props=[], prop_infos=lambda: [])
    host.set_hidden = lambda hide: setattr(host, "hidden", hide)
    f = SimpleNamespace(overlay=overlay, chat=FakeWidget(2), menu=FakeWidget(3), pop=FakeWidget(4), host=host,
                        timer=FakeTimer(33), brain=brain)
    world = World(WorldState([SCREEN]), GameState(), cfg, SavedState(), brain, SimpleNamespace(), overlay,
                  FakeWidget(5, visible=False), SimpleNamespace(active=False, fx=[]), host, SimpleNamespace(),
                  f.menu, FakeWidget(6, visible=False), SimpleNamespace(open=[f.pop]), SimpleNamespace(),
                  SimpleNamespace(), SimpleNamespace(), SimpleNamespace(mini_active=lambda: False),
                  platform.display, platform.windows, SimpleNamespace(exception=lambda *a: None), 4242,
                  chat=lambda: f.chat, quiet=lambda: False, tick_timer=lambda: f.timer)
    return world, f


def test_a_fullscreen_app_hides_the_cat_and_its_windows_until_it_is_gone(fake_platform) -> None:  # type: ignore[no-untyped-def]
    world, f = make(fake_platform)
    fake_platform.display.returns("user_state", UserState.FULLSCREEN_APP)
    world.check_fullscreen()
    assert world.state.hidden_fullscreen and world.state.chat_was_visible and f.host.hidden
    assert not any(w.visible for w in (f.overlay, f.chat, f.menu, f.pop))
    world.refresh_windows()                                       # (nothing is looked at while hidden)
    assert fake_platform.windows.called("top_level") == []
    fake_platform.display.returns("user_state", UserState.NORMAL)
    world.check_fullscreen()
    assert not world.state.hidden_fullscreen and not f.host.hidden
    assert f.overlay.visible and f.chat.visible and f.pop.visible and not f.menu.visible
    assert len(fake_platform.windows.called("top_level")) == 1   # back: the windows are looked at again


def test_a_tick_while_hidden_leaves_the_cat_and_slows_down(fake_platform) -> None:  # type: ignore[no-untyped-def]
    world, f = make(fake_platform)
    world.state.hidden_fullscreen = True
    clock = f.brain.clock
    world.tick()
    assert f.brain.clock == clock and f.timer.interval() == 1000 // world.t.fps_hidden


def test_a_new_monitor_layout_replaces_the_list_and_moves_the_cat_there(fake_platform) -> None:  # type: ignore[no-untyped-def]
    world, f = make(fake_platform)
    new = Monitor(2, (-2560, 0, 0, 1440), (-2560, 0, 0, 1400), 144, True, "TEST2")
    fake_platform.display.answers("monitors", lambda: [new])
    old = world.state.monitors
    world.refresh_monitors()
    assert world.state.monitors == [new] and old == [SCREEN]
    assert new.contains(f.brain.x, f.brain.y) and world.cat_monitor() == new
    now = world.state.monitors
    world.refresh_monitors()                                       # (the same layout: kept as it is)
    assert world.state.monitors is now


def test_the_windows_become_the_cats_surfaces_and_games_skip_our_own(fake_platform) -> None:  # type: ignore[no-untyped-def]
    world, f = make(fake_platform)
    other = WinInfo(101, (100, 300, 900, 800), 0, "Editor", "TestClass", False, False)
    ours = WinInfo(2, (1000, 400, 1400, 900), 1, "Chat", "TestClass", False, False)
    fake_platform.windows.returns("top_level", [other, ours])
    world.refresh_windows()
    assert fake_platform.windows.called("top_level") == [((4242, {2, 3, 4}), {})]   # the chat, menu and popup
    assert {101, 2} <= set(f.brain.win_rects)
    assert any(s.hwnd == 101 for s in f.brain.smap.surfaces) and any(s.hwnd == 2 for s in f.brain.smap.surfaces)
    assert [w.hwnd for w in world.game_windows()] == [101]
