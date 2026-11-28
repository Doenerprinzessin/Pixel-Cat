"""pixelcat.llm.agent: the desktop agent's eyes and hands, with every desktop call faked."""
from __future__ import annotations

import threading

import pytest

from llm_agent.agent_fakes import OWN_PID, SCREEN, el
from pixelcat.domain import Monitor, Snapshot
from pixelcat.llm import agent as agent_mod
from pixelcat.llm import watch as watch_mod
from pixelcat.llm.agent import AgentError, DesktopAgent, Shot, ToolOutput

NOTEPAD, CHROME = 21, 22
NP = "notepad: 'Notes.txt - Notepad'"
NP_FRONT = f" Now in front: {NP}."


@pytest.fixture
def ag(agent: DesktopAgent, desk) -> DesktopAgent:  # type: ignore[no-untyped-def]
    desk.add_window(NOTEPAD, "Notes.txt - Notepad", "notepad.exe")
    desk.add_window(CHROME, "News - Google Chrome", "chrome.exe", rect=(1000, 100, 1800, 900))
    snap = Snapshot(NOTEPAD, (100, 100, 900, 700), [
        el(1, "edit", "Title", (150, 150, 350, 170), value="Weekend"),
        el(2, "button", "Save", (700, 600, 760, 630)),
        el(3, "checkbox", "Important", (120, 200, 130, 210), checked=False)])
    desk.snapshots[NOTEPAD] = snap
    agent.snap = snap
    agent.shot = Shot(NOTEPAD, (100, 100), 0.5, (400, 300))
    return agent


def fails(fn, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(AgentError) as exc:
        fn(*args, **kwargs)
    return str(exc.value)


# ================================================================ pure helpers
@pytest.mark.parametrize(("doc", "old", "spans"), [
    ("a sort(; b", "sort(;", [(2, 8)]),
    ("x  =\n  1", "x = 1", [(0, 8)]),                         # different spaces / line breaks
    ("f(b,a) + 1", "f(b, a)", [(0, 6)]),                      # a space too many
    ("f(b, a) + 1", "f(b,a)", [(0, 7)]),                      # a space missing
])
def test_find_span(doc: str, old: str, spans: list) -> None:
    assert DesktopAgent.find_span(doc, old) == spans


def test_find_span_ambiguous_and_missing() -> None:
    assert DesktopAgent.find_span("ab ab", "ab", replace_all=True) == [(0, 2), (3, 5)]
    assert fails(DesktopAgent.find_span, "ab ab", "ab") == ("'ab' appears 2 times - include more of the surrounding "
                                                            "text so it is unique (or set replace_all)")
    msg = fails(DesktopAgent.find_span, "hello world", "bye")
    assert msg == "I can't find 'bye' in the document. Its current text is:\nhello world\nUse an exact piece of that " \
                  "text as 'old'"
    long = fails(DesktopAgent.find_span, "x" * 2000, "yy")
    assert "\n" + "x" * 1500 + "\n...\n" in long and "x" * 1501 not in long
    assert fails(DesktopAgent.find_span, "f(a,b)", "f(a, c)").startswith("I can't find")   # short: no space-free match
    assert fails(DesktopAgent.find_span, "ab", "a b").startswith("I can't find")


def test_diff_and_changed() -> None:
    a = bytes([0, 0, 0] * 10)
    assert agent_mod._diff(a, a) == 0.0
    assert agent_mod._diff(a, bytes([0, 0, 29] + [0, 0, 0] * 9)) == 0.1
    assert agent_mod._diff(a, bytes([0, 0, 28] + [0, 0, 0] * 9)) == 0.0
    assert agent_mod._diff(b"", b"") == 1.0 and agent_mod._diff(a, a[:-3]) == 1.0
    assert DesktopAgent._changed(None, a) is None and DesktopAgent._changed(a, a[:-1]) is None
    assert DesktopAgent._changed(bytes(100), bytes([31] * 40 + [0] * 60)) is False
    assert DesktopAgent._changed(bytes(100), bytes([31] * 41 + [0] * 59)) is True


def test_clip() -> None:
    assert agent_mod._clip((-10, 5, 3000, 900), SCREEN) == (0, 5, 1920, 900)


def test_control_monitor(agent: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    mons = [Monitor(1, (0, 0, 1920, 1080), (0, 0, 1920, 1040), 96, True, "A"),
            Monitor(2, (1920, 0, 3840, 1080), (1920, 0, 3840, 1040), 144, False, "B")]
    desk.platform.display.returns("monitors", mons)
    assert agent.control_monitor(2000, 10) == (1920, 0, 3840, 1080)
    assert agent.control_monitor(5, 5) == (0, 0, 1920, 1080)


@pytest.mark.parametrize(("after", "before", "result"), [
    ("abc hello", "abc ", "exact"), ("x\r\nhello\r\n", None, "exact"), ("old", "old", "unchanged"),
    ("...line two\nline three", "", "tail"), ("other", "", "differs"), (None, "", "unknown"),
])
def test_verify(agent: DesktopAgent, desk, after, before, result: str) -> None:  # type: ignore[no-untyped-def]
    desk.field_text = after
    text = "hello" if result != "tail" else "line one\nline two\nline three"
    assert agent._verify(text, before) == result


# ================================================================ windows
def test_describe_hwnd(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    assert ag.describe_hwnd(NOTEPAD) == NP
    desk.add_window(30, "T" * 80, "Tool")
    assert ag.describe_hwnd(30) == "Tool: '" + "T" * 67 + "...'"
    assert ag.describe_hwnd(999) == "app: '(untitled)'"


def test_resolve(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(23, "Other - Google Chrome", "chrome.exe", minimized=True)
    assert ag.resolve("chrome").hwnd == CHROME
    ag.last_hwnd = 23
    assert ag.resolve("chrome").hwnd == 23 and ag.resolve(None).hwnd == 23
    ag.last_hwnd = 0
    assert ag.resolve("  ").hwnd == NOTEPAD
    with pytest.raises(AgentError, match=r"^no open window matches 'excel' \(open windows: notepad: Notes.txt"):
        ag.resolve("excel")


def test_is_command_line(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    for i, exe in enumerate(["cmd.exe", "WindowsTerminal.exe", "pwsh.exe", "regedit.exe", "mmc.exe"]):
        desk.add_window(40 + i, "x", exe)
        assert ag.is_command_line(40 + i), exe
    assert not ag.is_command_line(NOTEPAD)
    desk.add_window(50, "Save as", "cmd.exe")
    desk.popups[NOTEPAD] = 50                         # a dialog of the window counts
    assert ag.is_command_line(NOTEPAD)


def test_element(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    e, hwnd = ag.element(2)
    assert (e.name, hwnd) == ("Save", NOTEPAD)
    assert fails(ag.element, 9) == "there is no element [9] in my last look - look again"
    desk.alive.discard(NOTEPAD)
    assert fails(ag.element, 2) == "that window is gone (it was closed)"
    ag.snap = None
    assert fails(ag.element, 2) == "I need to look at the window first (look_at_screen or read_window)"


def test_to_screen(ag: DesktopAgent) -> None:
    assert ag.to_screen(40, 50) == (180, 200)
    assert ag.to_screen(400, 300) == (900, 700)
    assert fails(ag.to_screen, 401, 3) == "x/y must be inside the last screenshot (400x300)"
    ag.shot = Shot(0, (1900, 0), 1.0, (100, 100))
    assert fails(ag.to_screen, 50, 5) == "that spot is off-screen"
    ag.shot = None
    assert fails(ag.to_screen, 1, 1) == "give an element id, or x and y from a screenshot (look_at_screen first)"


def test_focus(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    assert ag._focus(NOTEPAD) == NOTEPAD and ag.last_hwnd == NOTEPAD and desk.foreground == NOTEPAD
    desk.add_window(31, "Save as", "notepad.exe", pid=desk.pids[NOTEPAD])
    desk.popups[NOTEPAD] = 31
    assert ag._focus(NOTEPAD) == 31
    desk.bring_ok, desk.foreground = False, CHROME
    assert fails(ag._focus, NOTEPAD) == ("I couldn't bring notepad: 'Save as' to the front - please click on it once, "
                                         "then ask again")
    desk.foreground = 31
    assert ag._focus(NOTEPAD) == 31                   # it was in front already
    desk.alive.discard(NOTEPAD)
    assert fails(ag._focus, NOTEPAD) == "that window is gone (it was closed)"


def test_admin_windows_are_left_alone(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.elevated.add(desk.pids[NOTEPAD])
    assert fails(ag.click, 2, None, None, None) == (f"{NP} runs as administrator - Windows doesn't let me type or "
                                                    "click in it (and I won't ask for admin rights for that)")
    assert not desk.called("click")


def test_after(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    assert ag.after() == ""
    desk.foreground = NOTEPAD
    assert ag.after() == NP_FRONT
    desk.focused = el(5, "edit", "Search the web please", (0, 0, 1, 1))
    assert ag.after() == NP_FRONT + " Keyboard focus: edit 'Search the web please'."
    desk.pids[NOTEPAD] = OWN_PID
    assert ag.after() == ""
    desk.uac = True
    assert ag.after().startswith(" Windows is now showing its admin-rights prompt (UAC). Only the user can answer it")


# ================================================================ eyes
def test_look_at_a_window(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    ag.snap = ag.shot = None
    out = ag.look(NOTEPAD)
    assert isinstance(out, ToolOutput) and len(out.images) == 1 and out.display is None
    assert out.text == (f"Screenshot of {NP}, 800x600 px. Pink numbered boxes mark the clickable elements (use click "
                        "with element=ID):\n[1] edit 'Title' value='Weekend' at 150,60\n[2] button 'Save' at 630,515\n"
                        "[3] checkbox 'Important' (unchecked) at 25,105")
    assert ag.shot == Shot(NOTEPAD, (100, 100), 1.0, (800, 600))
    assert ag.snap is desk.snapshots[NOTEPAD] and ag.last_hwnd == NOTEPAD
    assert desk.called("draw_marks")[0][1][1] == (2, 600, 500, 660, 530)
    assert desk.foreground == 0 and not desk.called("bring_to_front")         # looking is read-only


def test_look_without_marks_or_elements(ag: DesktopAgent, desk, cfg) -> None:  # type: ignore[no-untyped-def]
    cfg.vision_marks = False
    del desk.snapshots[NOTEPAD]
    out = ag.look(NOTEPAD)
    assert out.text.endswith("(No element list for this window - use x/y pixel positions in this image to click.)")
    assert not desk.called("draw_marks") and ag.snap is None


def test_look_at_the_whole_screen(ag: DesktopAgent, desk, cfg) -> None:  # type: ignore[no-untyped-def]
    out = ag.look(None)
    assert out.text.startswith("Screenshot of the whole screen, 1600x900 px. Pink numbered boxes")
    assert out.text.endswith("\nOpen windows: notepad: Notes.txt - Notepad | chrome: News - Google Chrome")
    assert ag.shot == Shot(0, (0, 0), 1600 / 1920, (1600, 900))
    assert ("grab", SCREEN) in desk.calls


@pytest.mark.parametrize(("setup", "msg"), [
    (lambda d: d.alive.discard(NOTEPAD), "that window is gone (it was closed)"),
    (lambda d: d.hung.add(NOTEPAD), f"{NP} is not responding right now"),
    (lambda d: d.rects.__setitem__(NOTEPAD, (3000, 0, 3500, 500)), "that window is not visible on any screen"),
])
def test_look_refuses(ag: DesktopAgent, desk, setup, msg: str) -> None:  # type: ignore[no-untyped-def]
    setup(desk)
    assert fails(ag.look, NOTEPAD) == msg


def test_look_at_a_minimized_window(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(25, "Mini", "mini.exe", minimized=True)
    assert fails(ag.look, 25) == "mini: 'Mini' is minimized - restore it first (window_action restore), then look again"


def test_read(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.docs[NOTEPAD] = "Dear Anna,\nsee you."
    snap = desk.snapshots[NOTEPAD]
    snap.focused = snap.elements[0]
    out = ag.read(NOTEPAD)
    assert out == (f"Window {NP}. Keyboard focus is on [1].\nElements (use click with element=ID):\n"
                   "[1] edit 'Title' value='Weekend'\n[2] button 'Save'\n[3] checkbox 'Important' (unchecked)\n\n"
                   'Text content:\n"""\nDear Anna,\nsee you.\n"""')
    assert ag.last_hwnd == NOTEPAD and desk.foreground == 0


def test_read_falls_back_to_the_biggest_text_box(ag: DesktopAgent, desk, cfg) -> None:  # type: ignore[no-untyped-def]
    cfg.read_max_chars = 10
    desk.snapshots[NOTEPAD].elements.append(el(4, "document", "", value="0123456789ABC"))
    desk.snapshots[NOTEPAD].elements.append(el(5, "edit", "pw", value="secret secret secret", password=True))
    out = ag.read(NOTEPAD)
    assert out.endswith('Text content (beginning):\n"""\n0123456789\n"""')
    assert "secret" not in out


def test_read_refuses(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    del desk.snapshots[NOTEPAD]
    assert fails(ag.read, NOTEPAD) == (f"{NP} doesn't let me read its contents (or is too slow) - use look_at_screen "
                                       "instead")
    desk.hung.add(NOTEPAD)
    assert fails(ag.read, NOTEPAD) == f"{NP} is not responding right now"
    desk.alive.discard(NOTEPAD)
    assert fails(ag.read, NOTEPAD) == "that window is gone (it was closed)"


def test_read_a_minimized_window(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(25, "Mini", "mini.exe", minimized=True)
    desk.snapshots[25] = Snapshot(25, (0, 0, 1, 1))
    assert ag.read(25) == "Window mini: 'Mini' (minimized).\nElements (use click with element=ID):\n(none found)"


def test_uia_snapshot_errors_and_busy_windows(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("COM error")
    desk.platform.accessibility.answers("snapshot", boom)
    assert ag._uia_snapshot(NOTEPAD) is None
    release = threading.Event()
    desk.platform.accessibility.answers("snapshot", lambda *a, **k: release.wait(5) and None)
    assert ag._uia_snapshot(NOTEPAD, timeout=0.05) is None            # too slow
    assert ag._uia_snapshot(NOTEPAD, timeout=5) is None               # still busy: not asked again
    release.set()


# ================================================================ hands
def test_click_element(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    def click(x, y, button="left", count=1, hold=0.0, cancel=None):  # type: ignore[no-untyped-def]
        desk.calls.append(("click", x, y, button, count, hold))
        desk.cursor = (x, y)
    desk.platform.input.answers("click", click)
    out = ag.click(2, None, None, None)
    assert out == f"Clicked [2] button 'Save' in {NP}.{NP_FRONT}"
    assert desk.called("click") == [(730, 615, "left", 1, 0.0)]
    assert desk.called("move_cursor") == [(5, 5)]                    # the pointer goes back
    assert ag.last_hwnd == NOTEPAD


def test_click_spot_and_verbs(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    assert ag.click(None, 40, 50, None).startswith(f"Clicked the spot 40,50 in {NP}.")
    assert desk.called("click")[0][:3] == (180, 200, "left")
    assert ag.click(None, 40, 50, None, button="right", count=2).startswith("Right-clicked twice: the spot 40,50")
    assert ag.click(None, 40, 50, None, count=3).startswith("Triple-clicked the spot")
    assert ag.click(None, 40, 50, None, button="middle", count=4).startswith("Middle-clicked 4 times: the spot")
    assert ag.click(None, 40, 50, None, double=True).startswith("Double-clicked the spot")
    assert ag.click(None, 40, 50, None, hold=1.0).startswith("Pressed and held (1 s) the spot")
    assert ag.click(None, 40, 50, None, button="back").startswith("Clicked the mouse's back button on the spot")
    assert ag.click(None, 40, 50, None, keys=["ctrl", "shift"]).startswith("Clicked (holding ctrl+shift) the spot")
    assert ("hold", ["ctrl", "shift"]) in desk.calls


def test_click_reports_the_checkbox_state(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    after = Snapshot(NOTEPAD, (100, 100, 900, 700), [el(7, "checkbox", "Important", (120, 200, 130, 210),
                                                            checked=True)])
    desk.snapshots[NOTEPAD] = after
    assert ag.click(3, None, None, None) == f"Clicked [3] checkbox 'Important' in {NP}. It is now checked.{NP_FRONT}"


def test_quiet_click_waits_for_the_text_field(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.focused = el(1, "edit", "Title")
    assert ag.click(1, None, None, None, quiet=True) == ""


@pytest.mark.parametrize(("at", "msg"), [
    ("chrome", "chrome: 'News - Google Chrome' covers that spot, so I didn't click"),
    ("own", "my own chat window covers that spot - move the chat aside and ask again"),
])
def test_click_never_lands_on_another_window(ag: DesktopAgent, desk, at: str, msg: str) -> None:  # type: ignore[no-untyped-def]
    if at == "own":
        desk.add_window(99, "Pixel chat", "pixelcat.exe", pid=OWN_PID)
        desk.at = 99
    else:
        desk.at = CHROME
    assert fails(ag.click, 2, None, None, None) == msg
    assert not desk.called("click")


def test_click_on_a_whole_screen_picture_checks_the_app_there(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    ag.shot = Shot(0, (0, 0), 1.0, (1920, 1080))
    assert ag.click(None, 1200, 300, None).startswith("Clicked the spot 1200,300 in chrome: 'News - Google Chrome'.")
    desk.elevated.add(desk.pids[CHROME])
    assert "runs as administrator" in fails(ag.click, None, 1200, 300, None)


def test_click_needs_something_to_click(ag: DesktopAgent) -> None:
    ag.shot = None
    assert fails(ag.click, None, 1, 2, None) == "give an element id, or x and y from a screenshot (look_at_screen first)"


def test_type_short_text_with_keystrokes(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.field_text = "Hello "
    out = ag.type("world", NOTEPAD)
    assert out == (f"Typed 5 characters into {NP}. Checked: the text arrived exactly.\nThe last lines of the field are "
                   f"now (one per line):\n  | Hello world\n{NP_FRONT}")
    assert desk.called("type_text") == [("world",)] and not desk.called("set_clipboard_private_text")


def test_type_long_text_is_pasted_and_the_clipboard_restored(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.field_text = ""
    out = ag.type("line one\nline two\n", NOTEPAD)
    assert out.startswith(f"Pasted 18 characters into {NP}. Checked: the text arrived exactly.")
    assert desk.called("set_clipboard_private_text") == [("line one\nline two\n",)]
    assert desk.called("write_clipboard") == [([(13, b"user clip")],)]
    assert not desk.called("type_text")


def test_type_with_an_empty_clipboard_clears_it_afterwards(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.field_text, desk.clipboard = "", []
    ag.type("a much longer text", NOTEPAD)
    assert desk.called("clear_clipboard") == [()] and not desk.called("write_clipboard")


def test_type_when_the_clipboard_is_busy(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.field_text, desk.clipboard = "", None
    assert ag.type("a much longer text", NOTEPAD).startswith("Typed 18 characters")
    assert fails(ag.type, "two\nlines", NOTEPAD).startswith("the clipboard is busy right now, and typing several lines")


def test_type_refuses_password_fields(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.focused = el(1, "edit", "Password", password=True)
    assert fails(ag.type, "hunter2", NOTEPAD) == "the keyboard focus is on a password field - I never type passwords"
    assert not desk.called("type_text")


def test_type_stops(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.typed_limit = 2
    assert ag.type("hello", NOTEPAD) == f"Stopped typing after 2 of 5 characters because {NP} was no longer in front."
    ag.cancel.set()
    assert ag.type("hello", NOTEPAD).endswith("because you pressed Esc / Stop.")


def test_paste_stops_when_the_window_lost_the_front(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.platform.windows.answers("is_foreground", lambda h: False)
    assert fails(ag.type, "a much longer text", NOTEPAD) == f"{NP} is no longer in front, so I stopped"
    assert desk.called("write_clipboard") == [([(13, b"user clip")],)]


def test_paste_when_the_clipboard_cannot_be_set(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.platform.clipboard.answers("set_private_text", lambda t: False)
    assert fails(ag.type, "a much longer text", NOTEPAD) == "the clipboard is busy - try again"
    assert desk.called("write_clipboard") == [([(13, b"user clip")], ("private", False))]


def test_typed_text_that_differs_is_an_error(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    reads = iter(["before"])
    desk.field_text = lambda: next(reads, "something else")
    assert fails(ag.type, "hello", NOTEPAD) == (f"the text in {NP} does not match what I sent (keyboard focus: "
                                                "unknown). Look at the window and fix it, or tell the user")


def test_text_that_goes_nowhere_is_an_error(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.focused = el(2, "button", "Save")
    assert fails(ag.type, "hello", NOTEPAD).startswith(f"nothing changed in {NP} - the keyboard focus is not in a "
                                                       "text field (focus: button 'Save')")


def test_unreadable_apps_get_a_screenshot_to_check(ag: DesktopAgent, desk, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    prints = iter([bytes(100), bytes([255] * 100)])
    monkeypatch.setattr(ag, "_fingerprint", lambda hwnd: next(prints))
    out = ag.type("hello", NOTEPAD)
    assert isinstance(out, ToolOutput) and len(out.images) == 1
    assert out.text.startswith(f"Typed 5 characters into {NP}, but this app does not let me read text back.")
    assert out.display == f"Typed 5 characters into {NP} (checking with a screenshot)"
    assert ag.shot is not None and ag.shot.hwnd == NOTEPAD


def test_type_into_an_element_clicks_it_first(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.focused = el(1, "edit", "Title")
    desk.field_text = ""
    assert ag.type("Hi", CHROME, eid=1).startswith(f"Typed 2 characters into {NP}.")
    assert desk.called("click")[0][:2] == (250, 160)


def test_keys(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    combos = [("ctrl+s", [0x11, 0x53]), ("enter", [0x0D])]
    assert ag.keys(combos, NOTEPAD) == f"Pressed ctrl+s, enter in {NP}.{NP_FRONT}"
    assert desk.called("press_combo") == [([0x11, 0x53], 0.0), ([0x0D], 0.0)]
    assert ag.keys(combos, NOTEPAD, hold=1.0).startswith("Pressed ctrl+s, enter (held 1 s each) in")
    assert ag.keys(combos[:1], NOTEPAD, hold=1.0).startswith("Pressed ctrl+s (held 1 s) in")


def test_keys_stop(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    combos = [("ctrl+s", [0x11, 0x53]), ("enter", [0x0D])]

    def press(keys, hold=0.0, cancel=None):  # type: ignore[no-untyped-def]
        desk.calls.append(("press_combo", keys))
        desk.foreground = CHROME
    desk.platform.input.answers("press_combo", press)
    assert ag.keys(combos, NOTEPAD) == ("Pressed ctrl+s; stopped before enter because another app came to the "
                                        "front.")
    ag.cancel.set()
    assert ag.keys(combos, NOTEPAD) == "Pressed nothing; stopped before ctrl+s because you pressed Esc / Stop."


def test_scroll(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    assert ag.scroll("down", 3, NOTEPAD) == f"Scrolled down 3x in {NP}.{NP_FRONT}"
    assert desk.called("scroll") == [(500, 400, -3, ("horizontal", False))]
    out = ag.scroll("left", 99, NOTEPAD, xy=(40, 50), keys=["shift"])
    assert out.startswith(f"Scrolled left 50x over the spot 40,50 (holding shift) in {NP}.")
    assert desk.called("scroll")[-1] == (180, 200, 50, ("horizontal", True))
    assert ag.scroll("up", 1, NOTEPAD, eid=2).startswith("Scrolled up 1x over [2] button 'Save' in")


def test_drag(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    out = ag.drag(("el", 1), ("xy", 300.0, 200.0), [], keys=["ctrl"])
    assert out.text == f"Dragged from [1] edit 'Title' to the spot 300,200 holding ctrl in {NP}.{NP_FRONT}"
    assert out.display == f"🖱️ Dragged from [1] edit 'Title' to the spot 300,200 holding ctrl in {NP}."
    assert len(out.images) == 1
    assert desk.called("drag") == [([(250, 160), (700, 500)], "left", 0.6, ag.cancel)]
    out = ag.drag(("xy", 1.0, 1.0), None, [(10.0, 10.0), (20.0, 30.0)], button="right")
    assert out.text.startswith(f"Dragged from the spot 1,1 to the spot 20,30 along 2 points with the right button in")


@pytest.mark.parametrize(("why", "text"), [
    ("cancel", "Stopped the drag because you pressed Esc / Stop - I let go of the button where the pointer was."),
    ("user", "Stopped the drag because you moved the mouse - I let go of the button and left it to you."),
])
def test_drag_stopped(ag: DesktopAgent, desk, why: str, text: str) -> None:  # type: ignore[no-untyped-def]
    desk.drag_result = why
    assert ag.drag(("el", 1), ("el", 2), []).text.startswith(text)
    assert not desk.called("move_cursor")


def test_drag_refuses(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    assert fails(ag.drag, ("el", 1), None, []) == "say where to drag to (to_element or to_x/to_y, or a path)"
    desk.add_window(99, "Pixel chat", "pixelcat.exe", pid=OWN_PID, rect=(600, 450, 800, 600))
    desk.windows.insert(0, desk.windows.pop())
    assert fails(ag.drag, ("el", 1), ("xy", 300.0, 200.0), []) == ("my own chat window covers the spot to drop on - "
                                                                   "move the chat aside and ask again")


def test_zoom(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    out = ag.zoom(("mid", NOTEPAD), "in", 3)
    assert out.text == f"Zoomed in (3 steps, ctrl + mouse wheel) in {NP}.{NP_FRONT}"
    assert ("hold", ["ctrl"]) in desk.calls and desk.called("scroll") == [(500, 400, 3)]
    assert ag.shot == Shot(NOTEPAD, (100, 100), 1.0, (800, 600))      # the picture it returned is the latest now
    out = ag.zoom(("xy", 40.0, 50.0), "out", 1, how="pinch")
    assert out.text.startswith(f"Zoomed out (1 step, two-finger pinch) at the spot 40,50 in {NP}.")
    assert desk.called("pinch")[0][:2] == (140, 150)
    desk.pinch_result = "failed"
    assert ag.zoom(("mid", NOTEPAD), "in", 2, how="pinch").text.startswith(
        f"Zoomed in (2 steps, ctrl + mouse wheel) in {NP}. (Windows didn't take a touch pinch here, so I used ctrl + "
        "mouse wheel instead.)")
    desk.pinch_result = "cancel"
    assert ag.zoom(("mid", NOTEPAD), "in", 2, how="pinch").text.startswith("Stopped zooming because you pressed Esc")


@pytest.mark.parametrize(("why", "text"), [
    ("", f"The mouse pointer is on [2] button 'Save' in {NP} and stays there."),
    ("user", "Stopped because you moved the mouse."),
    ("cancel", "Stopped because you pressed Esc / Stop."),
])
def test_hover(ag: DesktopAgent, desk, why: str, text: str) -> None:  # type: ignore[no-untyped-def]
    desk.glide_result = why
    out = ag.hover(("el", 2), seconds=1.0)
    assert out.text == text and out.display == f"🖱️ {text}" and len(out.images) == 1
    assert desk.called("glide")[0][:2] == (730, 615)
    grabbed = [c[1] for c in desk.calls if c[0] == "grab"]
    assert grabbed[-1] == (0, 0, 1050, 850)          # the window plus the room around it for tooltips


# ================================================================ replace_text
@pytest.fixture
def editor(ag: DesktopAgent, desk):  # type: ignore[no-untyped-def]
    desk.focused = el(9, "document", "Text editor")
    original = "const a = 1;\r\narray.sort(;\r\n"
    desk.copy_all_text = lambda: desk.private_text if desk.private_text is not None else original
    return desk


def test_replace(ag: DesktopAgent, editor) -> None:  # type: ignore[no-untyped-def]
    out = ag.replace(NOTEPAD, "sort(;", "sort();")
    assert out == (f"Replaced 'sort(;' with 'sort();' in {NP} (1 place). Checked: the whole text is exactly as "
                   f"intended. (ctrl+z would undo it.){NP_FRONT} Keyboard focus: document 'Text editor'.")
    assert editor.private_text == "const a = 1;\r\narray.sort();\r\n"          # Windows line ends are kept
    keys = [c[0] for c in editor.called("press_combo")]
    assert keys == [[0x11, ord("A")], [0x11, ord("C")], [0x11, ord("V")], [0x11, ord("A")], [0x11, ord("C")],
                    [0x11, 0x23]]                                          # ...and nothing is left selected
    assert editor.called("write_clipboard") == [([(13, b"user clip")],)]


def test_replace_everything_with_nothing(ag: DesktopAgent, editor) -> None:  # type: ignore[no-untyped-def]
    copies = iter(["only line"])
    editor.copy_all_text = lambda: next(copies, "")
    assert ag.replace(NOTEPAD, "only line", "").startswith("Replaced 'only line' with '' in")
    assert [0x2E] in [c[0] for c in editor.called("press_combo")]         # delete


@pytest.mark.parametrize(("setup", "msg"), [
    (lambda d: setattr(d, "focused", el(9, "edit", "pw", password=True)),
     "the keyboard focus is on a password field - I never touch passwords"),
    (lambda d: setattr(d, "focused", el(9, "button", "Save")),
     f"the keyboard focus in {NP} is not in a text editor (it's on button 'Save') - click into the text first"),
    (lambda d: setattr(d, "focused", None),
     f"the keyboard focus in {NP} is not in a text editor (it's on nothing) - click into the text first"),
    (lambda d: setattr(d, "clipboard", None), "the clipboard is busy right now - try again in a moment"),
    (lambda d: setattr(d, "copy_all_text", None),
     f"I couldn't copy the text of {NP} - is the keyboard focus in the editor?"),
])
def test_replace_refuses(ag: DesktopAgent, editor, setup, msg: str) -> None:  # type: ignore[no-untyped-def]
    setup(editor)
    assert fails(ag.replace, NOTEPAD, "sort(;", "sort();") == msg


def test_replace_refuses_formatted_text(ag: DesktopAgent, editor) -> None:  # type: ignore[no-untyped-def]
    editor.add_window(30, "Letter - Word", "winword.exe")
    editor.clip_formats = {"rtf"}
    assert fails(ag.replace, 30, "sort(;", "x").startswith("winword: 'Letter - Word' holds formatted text")
    assert editor.called("write_clipboard")                               # the clipboard is put back anyway
    editor.clip_formats = {"rtf", "html"}
    assert ag.replace(NOTEPAD, "sort(;", "sort();").startswith("Replaced")  # plain-text editors may copy HTML too


def test_replace_reports_a_mismatch(ag: DesktopAgent, editor) -> None:  # type: ignore[no-untyped-def]
    editor.copy_all_text = lambda: "const a = 1;\r\narray.sort(;\r\n" if editor.private_text is None else "reformatted"
    assert fails(ag.replace, NOTEPAD, "sort(;", "sort();").startswith(
        f"I replaced the text in {NP}, but reading it back it doesn't match exactly")


def test_copy_all_of_files_is_not_text(ag: DesktopAgent, editor) -> None:  # type: ignore[no-untyped-def]
    editor.clip_formats = {"files"}
    assert ag._copy_all(NOTEPAD) is None


# ================================================================ waiting
def test_wait(ag: DesktopAgent, clock) -> None:  # type: ignore[no-untyped-def]
    t0 = clock.t
    assert ag.wait(2.0) == "Waited 2.0 s."
    assert clock.t - t0 >= 2.0
    ag.cancel.set()
    t0 = clock.t
    ag.wait(5.0)
    assert clock.t == t0


def test_wait_for_window_finds_the_new_window(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    before = {w.hwnd for w in ag.windows()}
    desk.add_window(60, "Untitled - Paint", "mspaint.exe")
    desk.add_window(61, "Other", "other.exe")
    out = ag.wait_for_window(before, "paint")
    assert out == " Its window is ready and in front (2 s to load): mspaint: 'Untitled - Paint'."
    assert ag.last_hwnd == 60 and desk.called("bring_to_front") == [(60,), (60,)]


def test_wait_for_window_falls_back(ag: DesktopAgent, desk, clock) -> None:  # type: ignore[no-untyped-def]
    before = {w.hwnd for w in ag.windows()}
    t0 = clock.t
    assert ag.wait_for_window(before, "x", timeout=3.0) == (" (I don't see a new window yet - it may still be starting, "
                                                            "or it reused an open window.)")
    assert 3.0 <= clock.t - t0 < 3.5
    desk.add_window(62, "Dialog", "x.exe", minimized=True)
    desk.foreground = 62
    assert ag.wait_for_window(before, "x", timeout=1.0) == " In front now: x: 'Dialog'."
    assert ag.last_hwnd == 62


# ================================================================ watch_screen
@pytest.fixture
def texts(monkeypatch):  # type: ignore[no-untyped-def]
    seq: list = []
    monkeypatch.setattr(watch_mod, "_window_text",
                        lambda a11y, hwnd, timeout=8.0: seq.pop(0) if len(seq) > 1 else seq[0])
    return seq


def test_watch_for_text(ag: DesktopAgent, texts) -> None:  # type: ignore[no-untyped-def]
    texts[:] = ["Loading...", "Loading...", "Your  TURN now"]
    out = ag.watch(NOTEPAD, None, "text", "your turn", seconds=10)
    assert out.text.startswith(f"'your turn' is there (after 1.2 s). Screenshot of {NP}")
    assert out.display == f"👀 'your turn' is there (after 1.2 s) - {NP}"


def test_watch_for_text_already_gone_and_timeouts(ag: DesktopAgent, texts) -> None:  # type: ignore[no-untyped-def]
    texts[:] = ["Done"]
    assert ag.watch(NOTEPAD, None, "gone", "Exporting", seconds=10).text.startswith("'Exporting' is gone (now).")
    out = ag.watch(NOTEPAD, None, "text", "never", seconds=2)
    assert out.text == (f"'never' didn't appear on {NP} within 2 s. If it is still going (a game, a quiz, a lobby), "
                        "call watch_screen again to keep waiting.")
    assert out.display == f"👀 'never' didn't appear on {NP} (2 s)" and not out.images
    texts[:] = [None]
    assert fails(ag.watch, NOTEPAD, None, "text", "x") == f"I can't read the text of {NP} - use until='change' instead"


def test_watch_text_refuses(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    assert fails(ag._watch_text, 0, "x", "x", False, 0, 1) == ("until='text' or 'gone' needs a window to read - give "
                                                               "target")
    assert fails(ag._watch_text, NOTEPAD, "x", "  ", False, 0, 1) == "say which text to watch for"
    desk.add_window(25, "Mini", "mini.exe", minimized=True)
    assert fails(ag.watch, 25, None) == ("mini: 'Mini' is minimized - restore it first (window_action restore), then "
                                         "watch it")
    desk.alive.discard(NOTEPAD)
    assert fails(ag.watch, NOTEPAD, None) == "that window is gone (it was closed)"


def test_watch_pixels_nothing_changes(ag: DesktopAgent) -> None:
    out = ag.watch(NOTEPAD, None, "change", seconds=2)
    assert out.text.startswith(f"Nothing changed on {NP} within 2 s.") and not out.images


def test_watch_pixels_sees_a_change(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    colours = iter([(255, 255, 255)] * 3)
    desk.color = lambda: next(colours, (0, 0, 0))
    out = ag.watch(NOTEPAD, None, "change", seconds=30)
    assert out.text.startswith("The screen changed after 0.8 s. Screenshot of")
    assert len(out.images) == 1


def test_watch_pixels_compares_with_the_latest_screenshot(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    desk.color = lambda: (0, 0, 0)
    ag.look(NOTEPAD)
    desk.color = lambda: (255, 255, 255)
    out = ag.watch(NOTEPAD, None, "change", seconds=30)
    assert out.text.startswith("It had already changed since your latest screenshot. Screenshot of")


def test_watch_pixels_until_still(ag: DesktopAgent) -> None:
    out = ag.watch(0, SCREEN, "still", seconds=30)
    assert out.text.startswith("The screen stopped changing after 1.2 s. Screenshot of the whole screen")


def test_watch_pixels_dialog_opened(ag: DesktopAgent, desk) -> None:  # type: ignore[no-untyped-def]
    calls = iter([NOTEPAD, NOTEPAD])
    desk.platform.windows.answers("active_popup", lambda h: next(calls, 77))
    desk.add_window(77, "Save as", "notepad.exe", pid=desk.pids[NOTEPAD])
    out = ag.watch(NOTEPAD, None, "change", seconds=30)
    assert out.text.startswith("A dialog window opened or closed after 0.5 s. Screenshot of notepad: 'Save as'")


def test_watch_area_outside_the_window(ag: DesktopAgent) -> None:
    assert fails(ag.watch, NOTEPAD, None, "change", area=(2000, 2000, 2100, 2100)) == "that area is not on the window"


def test_watch_cancelled(ag: DesktopAgent) -> None:
    ag.cancel.set()
    out = ag.watch(NOTEPAD, None, "change", seconds=5)
    assert (out.text, out.display) == ("Stopped watching because you pressed Esc / Stop.", f"👀 Stopped watching {NP}")
