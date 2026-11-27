"""ToolBox.prepare for the screen tools (eyes and hands): targets, spots, keys and guard-rail flags."""
from __future__ import annotations

import pytest

from llm_agent.agent_fakes import OWN_PID, el
from pixelcat.desktop import uia
from pixelcat.llm.agent import Shot
from pixelcat.llm.tools import ToolBox, ToolError

NOTEPAD, TERMINAL, CHROME = 21, 22, 23
NP_LABEL = "notepad: 'Notes.txt - Notepad'"     # describe_hwnd (spots, elements)
NP_WIN = "notepad: Notes.txt - Notepad"           # AppWindow.label (targets)


@pytest.fixture
def tb(toolbox: ToolBox, desk) -> ToolBox:  # type: ignore[no-untyped-def]
    desk.add_window(NOTEPAD, "Notes.txt - Notepad", "notepad.exe")
    desk.add_window(TERMINAL, "Command Prompt", "cmd.exe", rect=(1000, 100, 1600, 600))
    desk.add_window(CHROME, "News - Google Chrome", "chrome.exe", minimized=True)
    ag = toolbox.agent
    ag.shot = Shot(NOTEPAD, (100, 100), 0.5, (400, 300))
    ag.snap = uia.Snapshot(NOTEPAD, (100, 100, 900, 700), [
        el(1, "edit", "Title", (150, 150, 350, 170)), el(2, "button", "Save", (700, 600, 760, 630)),
        el(3, "checkbox", "", (120, 200, 130, 210))])
    return toolbox


def prep(tb: ToolBox, tool: str, **args):  # type: ignore[no-untyped-def]
    spec, a = tb.prepare(tool, args)
    return a, tb.describe(spec, a)


def refused(tb: ToolBox, tool: str, **args) -> str:
    with pytest.raises(ToolError) as exc:
        tb.prepare(tool, args)
    return str(exc.value)


# ------------------------------------------------------------------ eyes
def test_look_without_target_is_the_whole_screen(tb: ToolBox) -> None:
    a, d = prep(tb, "look_at_screen", target="  ")
    assert (a["_hwnd"], a["_label"], d) == (0, "your screen", "look at your screen")


def test_look_and_read_resolve_the_target(tb: ToolBox) -> None:
    a, d = prep(tb, "look_at_screen", target="notepad")
    assert (a["_hwnd"], d) == (NOTEPAD, f"look at {NP_WIN}")
    a, d = prep(tb, "read_window", target="Command")
    assert (a["_hwnd"], d) == (TERMINAL, "read cmd: Command Prompt")


def test_empty_target_means_the_window_used_last(tb: ToolBox) -> None:
    assert prep(tb, "read_window")[0]["_hwnd"] == NOTEPAD          # first non-minimized window
    tb.agent.last_hwnd = TERMINAL
    assert prep(tb, "read_window")[0]["_hwnd"] == TERMINAL
    tb.agent.last_hwnd = CHROME                                       # minimized but used last: still it
    assert prep(tb, "read_window")[0]["_hwnd"] == CHROME


def test_unknown_target_lists_the_open_windows(tb: ToolBox) -> None:
    assert refused(tb, "read_window", target="excel") == (
        "no open window matches 'excel' (open windows: notepad: Notes.txt - Notepad, cmd: Command Prompt, "
        "chrome: News - Google Chrome (minimized))")


def test_no_window_at_all(tb: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    desk.alive.clear()
    assert refused(tb, "read_window") == "no app window is open"


# ------------------------------------------------------------------ click
def test_click_element(tb: ToolBox) -> None:
    a, d = prep(tb, "click", element="[2]")
    assert (a["_eid"], a["_hwnd"], a["_x"], a["_y"]) == (2, NOTEPAD, None, None)
    assert (a["_button"], a["_double"], a["_count"], a["_hold"], a["_keys"]) == ("left", False, 1, 0.0, [])
    assert d == f"click [2] button 'Save' in {NP_LABEL}"
    assert prep(tb, "click", element=3)[1] == f"click [3] checkbox in {NP_LABEL}"


def test_click_spot(tb: ToolBox) -> None:
    a, d = prep(tb, "click", x=20, y="30.5")
    assert (a["_eid"], a["_hwnd"], a["_x"], a["_y"]) == (None, NOTEPAD, 20.0, 30.5)
    assert d == f"click at 20,30 in {NP_LABEL}"


def test_click_spot_on_a_whole_screen_picture(tb: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    tb.agent.shot = Shot(0, (0, 0), 1.0, (1920, 1080))
    a, d = prep(tb, "click", x=1200, y=300)
    assert a["_hwnd"] == 0 and d == "click at 1200,300 on the screen"
    assert a["_force_confirm"] is True         # the terminal is at that spot
    assert "_force_confirm" not in prep(tb, "click", x=300, y=300)[0]
    desk.pids[TERMINAL] = OWN_PID              # the cat's own windows don't count
    assert "_force_confirm" not in prep(tb, "click", x=1200, y=300)[0]


@pytest.mark.parametrize(("args", "msg"), [
    ({}, "click needs element=ID, or x and y from the last screenshot"),
    ({"x": 5}, "click needs element=ID, or x and y from the last screenshot"),
    ({"x": "left", "y": 3}, "click needs element=ID, or x and y from the last screenshot"),
    ({"element": 0}, "click needs element=ID, or x and y from the last screenshot"),
    ({"element": "Save"}, "element must be a number from the last look"),
    ({"element": 9}, "there is no element [9] in my last look - look again"),
    ({"x": 401, "y": 3}, "x/y must be inside the last screenshot (400x300)"),
    ({"x": 1, "y": 1, "button": "fourth"}, "button must be left, right, middle, back or forward"),
    ({"x": 1, "y": 1, "hold": "long"}, "hold must be a number"),
    ({"x": 1, "y": 1, "keys": "ctrl+q"}, "keys: only ctrl, shift, alt and win can be held with the mouse "
                                          "(to hold other keys use press_keys with hold)"),
])
def test_click_refuses(tb: ToolBox, args: dict, msg: str) -> None:
    assert refused(tb, "click", **args) == msg


def test_click_needs_a_look_first(tb: ToolBox) -> None:
    tb.agent.shot = tb.agent.snap = None
    assert refused(tb, "click", x=1, y=2) == "look_at_screen first, so I know where to click"
    assert refused(tb, "click", element=1) == "I need to look at the window first (look_at_screen or read_window)"


def test_click_element_of_a_closed_window(tb: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    desk.alive.discard(NOTEPAD)
    assert refused(tb, "click", element=1) == "that window is gone (it was closed)"


@pytest.mark.parametrize(("args", "verb"), [
    ({"clicks": 2}, "double-click"), ({"double": "yes"}, "double-click"), ({"clicks": 3}, "triple-click"),
    ({"clicks": 7}, "triple-click"), ({"button": "secondary"}, "right-click"), ({"button": "Right", "clicks": 2},
                                                                                 "right-double-click"),
    ({"button": "wheel"}, "middle-click"), ({"button": "x1"}, "click the mouse's back button on"),
    ({"button": "mouse5"}, "click the mouse's forward button on"), ({"hold": 1.5}, "press and hold (1.5 s)"),
    ({"hold": 0.2}, "click"), ({"hold": 99}, "press and hold (10 s)"),
    ({"button": "right", "hold": 2}, "right-click (held 2 s)"),
    ({"keys": "ctrl"}, "click holding ctrl"), ({"keys": "Shift + CTRL"}, "click holding shift+ctrl"),
    ({"keys": "cmd,alt,cmd"}, "click holding win+alt"), ({"keys": "none"}, "click"),
])
def test_click_verbs(tb: ToolBox, args: dict, verb: str) -> None:
    a, d = prep(tb, "click", x=10, y=10, **args)
    assert a["_verb"] == verb and d == f"{verb} at 10,10 in {NP_LABEL}"


def test_click_held_keys_and_counts(tb: ToolBox) -> None:
    a, _ = prep(tb, "click", x=1, y=1, keys="ctrl+shift", clicks=2, hold="0.5")
    assert (a["_keys"], a["_count"], a["_double"], a["_hold"]) == ([0x11, 0x10], 2, True, 0.5)
    assert prep(tb, "click", x=1, y=1, clicks=3)[0]["_double"] is False


def test_click_into_a_command_line_always_asks(tb: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    tb.agent.shot = Shot(TERMINAL, (1000, 100), 1.0, (600, 500))
    assert prep(tb, "click", x=5, y=5)[0]["_force_confirm"] is True
    assert "_force_confirm" not in prep(tb, "click", element=2)[0]


def test_run_box_counts_as_a_command_line(tb: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(30, "Run", "explorer.exe", cls="#32770")
    assert tb.agent.is_command_line(30) is True
    desk.classes[30] = "CabinetWClass"
    assert tb.agent.is_command_line(30) is False


# ------------------------------------------------------------------ typing and replacing
def test_type_text(tb: ToolBox) -> None:
    a, d = prep(tb, "type_text", text="Hello\nWorld", target="notepad")
    assert (a["_eid"], a["_hwnd"], d) == (None, NOTEPAD, f"type 11 characters into {NP_WIN}")
    a, d = prep(tb, "type_text", text=12345, element=1)
    assert (a["text"], a["_eid"], a["_hwnd"], d) == ("12345", 1, NOTEPAD, f"type 5 characters into {NP_LABEL}")
    assert refused(tb, "type_text", text="x" * 20_001) == "that's too much text to type at once (max 20,000 characters)"
    assert prep(tb, "type_text", text="dir", target="command")[0]["_force_confirm"] is True


def test_replace_text(tb: ToolBox) -> None:
    a, d = prep(tb, "replace_text", old="sort(;", new="sort();", replace_all="yes")
    assert (a["old"], a["new"], a["replace_all"], a["_hwnd"]) == ("sort(;", "sort();", True, NOTEPAD)
    assert d == f"replace 'sort(;' with 'sort();' in {NP_WIN}"
    assert prep(tb, "replace_text", old="delete me", new="")[0]["new"] == ""
    assert refused(tb, "replace_text", old="  ", new="x") == ("'old' must be some existing text (to insert new text "
                                                              "use type_text)")
    assert refused(tb, "replace_text", old="a", new="a") == "'old' and 'new' are the same - nothing would change"
    assert refused(tb, "replace_text", old="a" * 20_001, new="b") == "that's too much text for one replacement"
    assert refused(tb, "replace_text", old="a", new="b" * 50_001) == "that's too much text for one replacement"
    assert refused(tb, "replace_text", old="a", new=None) == "missing argument 'new'"
    assert prep(tb, "replace_text", old="a", new="b", target="cmd")[0]["_force_confirm"] is True


# ------------------------------------------------------------------ keys
def test_press_keys_parsing(tb: ToolBox) -> None:
    a, d = prep(tb, "press_keys", keys=" Ctrl + S , tab;enter ")
    assert [c[0] for c in a["_combos"]] == ["ctrl+s", "tab", "enter"]
    assert a["_combos"][0][1] == [0x11, ord("S")]
    assert d == f"press ctrl+s tab enter in {NP_WIN}"
    assert "_confirm" not in a and "_force_confirm" not in a


@pytest.mark.parametrize("keys", ["alt+f4", "Control+W", "ctrl+q", "win+l", "Windows+L", "shift+delete", "tab alt+f4"])
def test_closing_shortcuts_are_a_hard_confirm(tb: ToolBox, keys: str) -> None:
    a, _ = prep(tb, "press_keys", keys=keys)
    assert a["_confirm"] == "hard" and "_force_confirm" not in a


@pytest.mark.parametrize("keys", ["win+r", "win+x", "ctrl+shift+esc", "ctrl+alt+del", "control+alt+delete"])
def test_system_shortcuts_always_ask(tb: ToolBox, keys: str) -> None:
    assert prep(tb, "press_keys", keys=keys)[0]["_force_confirm"] is True


def test_keys_into_a_command_line_always_ask(tb: ToolBox) -> None:
    assert prep(tb, "press_keys", keys="enter", target="cmd")[0]["_force_confirm"] is True


def test_press_keys_hold(tb: ToolBox) -> None:
    a, d = prep(tb, "press_keys", keys="w", hold=3)
    assert a["_hold"] == 3.0 and d == f"press w (held 3 s) in {NP_WIN}"
    a, _ = prep(tb, "press_keys", keys=" ".join(["a"] * 20), hold=10)
    assert a["_hold"] == 3.0                                       # 60 s in total at most
    a, d = prep(tb, "press_keys", keys="w", hold=0.05)
    assert a["_held"] == "" and d == f"press w in {NP_WIN}"


@pytest.mark.parametrize(("keys", "msg"), [
    (" ".join(["a"] * 21), "give 1-20 keys, e.g. 'ctrl+s' or 'tab tab enter'"),
    (" , ; ", "give 1-20 keys, e.g. 'ctrl+s' or 'tab tab enter'"),
    ("ctrl+banana", "unknown key 'banana'"),
])
def test_press_keys_refuses(tb: ToolBox, keys: str, msg: str) -> None:
    assert refused(tb, "press_keys", keys=keys) == msg


# ------------------------------------------------------------------ scroll, zoom, hover, drag
def test_scroll(tb: ToolBox) -> None:
    a, d = prep(tb, "scroll", direction=" Down ")
    assert (a["direction"], a["amount"], a["_eid"], a["_xy"], a["_hwnd"]) == ("down", 5, None, None, NOTEPAD)
    assert d == f"scroll down in {NP_WIN}"
    assert prep(tb, "scroll", direction="up", amount="100")[0]["amount"] == 50
    assert prep(tb, "scroll", direction="up", amount=0)[0]["amount"] == 5
    assert prep(tb, "scroll", direction="up", amount="lots")[0]["amount"] == 5
    a, d = prep(tb, "scroll", direction="up", element=2, keys="shift")
    assert (a["_eid"], a["_hwnd"], a["_keys"], d) == (2, NOTEPAD, [0x10], f"scroll up in {NP_LABEL}")
    a, d = prep(tb, "scroll", direction="left", x=40, y=50)
    assert (a["_xy"], a["_hwnd"], d) == ((40.0, 50.0), NOTEPAD, f"scroll left in 40,50 in {NP_LABEL}")
    assert refused(tb, "scroll", direction="sideways") == "direction must be up, down, left or right"


@pytest.mark.parametrize(("raw", "d"), [("in", "in"), ("Zoom Out", "out"), ("+", "in"), ("-", "out"),
                                        ("bigger", "in"), ("away", "out")])
def test_zoom_direction(tb: ToolBox, raw: str, d: str) -> None:
    assert prep(tb, "zoom", direction=raw)[0]["direction"] == d


def test_zoom(tb: ToolBox) -> None:
    a, d = prep(tb, "zoom", direction="in")
    assert (a["_steps"], a["_how"], a["_ref"], a["_hwnd"]) == (3, "wheel", ("mid", NOTEPAD), NOTEPAD)
    assert d == f"zoom in in {NP_WIN}"
    a, d = prep(tb, "zoom", direction="out", steps=50, how="two fingers", x=10, y=20)
    assert (a["_steps"], a["_how"], a["_ref"]) == (10, "pinch", ("xy", 10.0, 20.0))
    assert d == f"zoom out at 10,20 in {NP_LABEL}"
    assert prep(tb, "zoom", direction="in", steps=0, how="Touch")[0]["_steps"] == 1
    assert prep(tb, "zoom", direction="in", element=1)[0]["_ref"] == ("el", 1)
    assert refused(tb, "zoom", direction="sideways") == "direction must be in or out"
    assert prep(tb, "zoom", direction="in", target="cmd")[0]["_force_confirm"] is True


def test_move_mouse(tb: ToolBox) -> None:
    a, d = prep(tb, "move_mouse", element=2)
    assert (a["_ref"], a["_hwnd"], a["_seconds"]) == (("el", 2), NOTEPAD, 1.0)
    assert d == f"move the mouse onto [2] button 'Save' in {NP_LABEL}"
    assert prep(tb, "move_mouse", x=1, y=1, seconds=60)[0]["_seconds"] == 5.0
    assert refused(tb, "move_mouse") == "move the mouse needs element=ID, or x and y from the last screenshot"


def test_drag_between_elements_and_spots(tb: ToolBox) -> None:
    a, d = prep(tb, "drag", from_element=1, to_x=300, to_y=200, keys="ctrl", button="right")
    assert (a["_src"], a["_dst"], a["_path"], a["_button"], a["_keys"]) == (("el", 1), ("xy", 300.0, 200.0), [],
                                                                            "right", [0x11])
    assert a["_seconds"] == 0.6 and a["_hwnd"] == NOTEPAD
    assert d == (f"drag from [1] edit 'Title' in {NP_LABEL} to 300,200 in {NP_LABEL} with the right button "
                 "holding ctrl")


@pytest.mark.parametrize("path", [[[10, 10], [20, 20], [30, 25]], [{"x": 10, "y": 10}, {"x": 20, "y": 20}, (30, 25)],
                                  "10,10 20,20 30,25"])
def test_drag_path_formats(tb: ToolBox, path) -> None:  # type: ignore[no-untyped-def]
    a, d = prep(tb, "drag", from_x=1, from_y=1, path=path)
    assert a["_path"] == [(10.0, 10.0), (20.0, 20.0), (30.0, 25.0)] and a["_dst"] is None
    assert a["_seconds"] == pytest.approx(0.66)
    assert d == f"drag from 1,1 in {NP_LABEL} to 30,25 along 3 points"


@pytest.mark.parametrize(("args", "msg"), [
    ({"from_x": 1, "from_y": 1}, "drag needs where to: to_element, or to_x and to_y (or a path of points)"),
    ({"to_x": 1, "to_y": 1}, "drag needs from_element=ID, or from_x and from_y from the last screenshot"),
    ({"from_x": 1, "from_y": 1, "to_x": 999, "to_y": 1}, "to_x/to_y must be inside the last screenshot (400x300)"),
    ({"from_x": 1, "from_y": 1, "path": [[1, 2, 3], ["a"]]}, "path must be a list of points like [[120, 80], [160, 95]]"),
    ({"from_x": 1, "from_y": 1, "path": [[1, 2], 5]}, "path must be a list of points like [[120, 80], [160, 95]]"),
    ({"from_x": 1, "from_y": 1, "path": [{"x": 1}]}, "path must be a list of points like [[120, 80], [160, 95]]"),
    ({"from_x": 1, "from_y": 1, "path": 7}, "path must be a list of points like [[120, 80], [160, 95]]"),
    ({"from_x": 1, "from_y": 1, "path": [[1, 1]] * 1001}, "path: at most 1000 points"),
    ({"from_x": 1, "from_y": 1, "path": [[1, 1], [500, 1]]}, "path point 500,1 is outside the last screenshot (400x300)"),
    ({"from_x": 1, "from_y": 1, "to_x": 2, "to_y": 2, "button": "back"}, "button must be left, right or middle"),
])
def test_drag_refuses(tb: ToolBox, args: dict, msg: str) -> None:
    assert refused(tb, "drag", **args) == msg


def test_drag_seconds(tb: ToolBox) -> None:
    assert prep(tb, "drag", from_x=1, from_y=1, path=[[2, 2]] * 400)[0]["_seconds"] == 8.0
    assert prep(tb, "drag", from_x=1, from_y=1, to_x=2, to_y=2, seconds=0)[0]["_seconds"] == 0.1


def test_drag_into_a_command_line_always_asks(tb: ToolBox) -> None:
    tb.agent.shot = Shot(TERMINAL, (1000, 100), 1.0, (600, 500))
    assert prep(tb, "drag", from_x=1, from_y=1, to_x=5, to_y=5)[0]["_force_confirm"] is True


def test_path_needs_a_screenshot(tb: ToolBox) -> None:
    tb.agent.shot = None
    with pytest.raises(ToolError, match="look_at_screen first, so I know where the path goes"):
        tb._path([[1, 2]])
    assert tb._path(None) == [] and tb._path("") == []


# ------------------------------------------------------------------ watch_screen
def test_watch_screen_defaults_to_the_latest_screenshot(tb: ToolBox) -> None:
    a, d = prep(tb, "watch_screen")
    assert (a["_until"], a["_hwnd"], a["_screen"], a["_area"], a["_seconds"], a["_sens"]) == (
        "change", NOTEPAD, None, None, 120.0, "normal")
    assert d == f"watch {NP_LABEL} until something changes (up to 120 s)"


@pytest.mark.parametrize(("until", "norm"), [("appears", "text"), ("Disappear", "gone"), ("stable", "still"),
                                             ("changed", "change"), ("idle", "still")])
def test_watch_screen_until_synonyms(tb: ToolBox, until: str, norm: str) -> None:
    assert prep(tb, "watch_screen", until=until, text="Next")[0]["_until"] == norm


def test_watch_screen_conditions(tb: ToolBox) -> None:
    a, d = prep(tb, "watch_screen", until="text", text=" Your turn ", seconds=9999, sensitivity="HIGH")
    assert (a["_text"], a["_seconds"], a["_sens"]) == ("Your turn", 600.0, "high")
    assert d == f"watch {NP_LABEL} until 'Your turn' appears (up to 600 s)"
    assert prep(tb, "watch_screen", until="gone", text="Loading")[1].endswith("until 'Loading' is gone (up to 120 s)")
    assert prep(tb, "watch_screen", until="still", seconds=0)[1].endswith("until it stops changing (up to 1 s)")
    assert prep(tb, "watch_screen", sensitivity="extreme")[0]["_sens"] == "normal"


def test_watch_screen_whole_screen(tb: ToolBox) -> None:
    tb.agent.shot = Shot(0, (0, 0), 1.0, (1920, 1080))
    a, d = prep(tb, "watch_screen")
    assert (a["_hwnd"], a["_screen"], d) == (0, (0, 0, 1920, 1080), "watch the screen until something changes (up to "
                                                                    "120 s)")
    a, _ = prep(tb, "watch_screen", target="the screen")
    assert (a["_hwnd"], a["_screen"]) == (0, (0, 0, 1920, 1080))


def test_watch_screen_named_window_and_no_screenshot(tb: ToolBox, desk) -> None:  # type: ignore[no-untyped-def]
    assert prep(tb, "watch_screen", target="cmd")[0]["_hwnd"] == TERMINAL
    tb.agent.shot = None
    a, _ = prep(tb, "watch_screen")
    assert (a["_hwnd"], a["_screen"]) == (0, (0, 0, 1920, 1080))
    desk.alive.discard(NOTEPAD)                     # the latest screenshot's window closed
    tb.agent.shot = Shot(NOTEPAD, (100, 100), 0.5, (400, 300))
    assert prep(tb, "watch_screen")[0]["_hwnd"] == 0


def test_watch_screen_area(tb: ToolBox) -> None:
    a, d = prep(tb, "watch_screen", area=[10, 20, 100, 50])
    assert a["_area"] == (120, 140, 320, 240)
    assert d.endswith("until something changes in a part of it (up to 120 s)")
    assert prep(tb, "watch_screen", area={"x": 10, "y": 20, "width": 100, "height": 50})[0]["_area"] == (120, 140, 320,
                                                                                                          240)
    assert prep(tb, "watch_screen", area="10, 20, 100, 50")[0]["_area"] == (120, 140, 320, 240)


@pytest.mark.parametrize(("args", "msg"), [
    ({"until": "forever"}, "until must be change, text, gone or still"),
    ({"until": "text"}, "until='text' needs the text to watch for"),
    ({"until": "gone", "text": " "}, "until='gone' needs the text to watch for"),
    ({"area": [1, 2, 3]}, "area must be [x, y, width, height] in your latest screenshot"),
    ({"area": [1, 2, 0, 3]}, "area must be [x, y, width, height] in your latest screenshot"),
    ({"area": {"x": 1}}, "area must be [x, y, width, height] in your latest screenshot"),
    ({"area": [1, 2, 3, 4], "target": "cmd"}, "area is measured in your latest screenshot of that same window - "
                                              "look_at_screen at it first"),
    ({"seconds": "long"}, "seconds must be a number"),
])
def test_watch_screen_refuses(tb: ToolBox, args: dict, msg: str) -> None:
    assert refused(tb, "watch_screen", **args) == msg


def test_watch_screen_text_needs_a_window(tb: ToolBox) -> None:
    tb.agent.shot = Shot(0, (0, 0), 1.0, (1920, 1080))
    assert refused(tb, "watch_screen", until="text", text="x") == "until='text' needs a window to read (target)"
