"""ui.chat: the chat window - messages and streaming, cards, history, size and fullscreen."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QUrl
from PySide6.QtGui import QKeyEvent, QMouseEvent

from pixelcat import naming
from pixelcat.ui import chat as chat_mod
from pixelcat.ui import theme
from pixelcat.ui.chat import GREETING, ChatWindow, md_to_html

CAT = (800, 500, 896, 596)
WORK = (0, 0, 1920, 1040)


# ---------------------------------------------------------------- markdown
@pytest.mark.parametrize(("text", "has", "lacks"), [
    ("**bold** text", ["<b>bold</b>"], ["**"]),
    ("use `pip`", ['<code style="', ">pip</code>"], ["`"]),
    ("- one\n- two", ["&nbsp;• one<br>&nbsp;• two"], []),
    ("<script>x</script>", ["&lt;script&gt;"], ["<script>"]),
    ("[docs](https://example.com/a)", ['<a href="https://example.com/a"', ">docs</a>"], []),
    ("[bad](javascript:alert(1))", ["[bad](javascript:alert(1))"], ["<a "]),
    ("```py\nprint(1 < 2)\n```", ["<pre", "print(1 &lt; 2)", ">py</div>"], ["```"]),
    ("```\nno lang\n```", ["<pre", "no lang"], ["</div>"]),
    ("before ```py\nstill streaming", ["before", "<pre", "still streaming"], ["```"]),
])
def test_md_to_html(text: str, has: list[str], lacks: list[str]) -> None:
    out = md_to_html(text)
    for h in has:
        assert h in out
    for x in lacks:
        assert x not in out


def test_md_to_html_uses_the_current_style() -> None:
    theme.configure(style="midnight")
    assert theme.STYLES["midnight"].code_bg in md_to_html("```\nx\n```")


# ---------------------------------------------------------------- the window
@pytest.fixture
def chat(qapp, desktop, fake_platform, sprites, track):  # type: ignore[no-untyped-def]
    return track(ChatWindow(sprites, display=fake_platform.display))


def _shown(chat: ChatWindow) -> str:
    chat._flush()
    return chat.view.toPlainText()


@pytest.mark.qt
def test_starts_with_the_greeting_at_its_usual_size(chat: ChatWindow) -> None:
    assert [(m.role, m.text) for m in chat.msgs] == [("cat", GREETING)]
    assert "Mrrrow" in _shown(chat) and "🐱 cat" in _shown(chat)
    assert (chat.width(), chat.height()) == (350, 450)
    assert chat.input.placeholderText() == "Talk to the cat… (Ctrl+N: new chat)"
    assert chat._title_text() == "MEOW CHAT"
    assert chat.send_btn.text() == "SEND"


@pytest.mark.qt
def test_sending_adds_the_message_and_asks_for_an_answer(chat: ChatWindow) -> None:
    sent: list[str] = []
    chat.send_requested.connect(sent.append)
    chat.input.setText("  hello cat  ")
    chat.input.returnPressed.emit()
    assert sent == ["hello cat"] and chat.input.text() == ""
    assert chat.msgs[-1].role == "user" and "hello cat" in _shown(chat)
    chat.input.setText("   ")
    chat.send_btn.click()
    assert sent == ["hello cat"]


@pytest.mark.qt
def test_streaming_an_answer(chat: ChatWindow) -> None:
    chat.begin_cat()
    assert chat.send_btn.text() == "STOP" and not chat.input.isEnabled()
    assert "thinking" in _shown(chat)
    for tok in ("Hel", "lo ", "**there**"):
        chat.append_cat(tok)
    assert chat.msgs[-1].text == "Hello **there**"
    assert "Hello there" in _shown(chat)
    chat.set_cat_text("Replaced")
    assert chat.msgs[-1].text == "Replaced"
    chat.end_cat()
    assert chat.send_btn.text() == "SEND" and chat.input.isEnabled()
    assert _shown(chat).count("Replaced") == 1


@pytest.mark.qt
def test_an_empty_answer_is_taken_back(chat: ChatWindow) -> None:
    chat.begin_cat()
    chat.end_cat()
    assert [m.role for m in chat.msgs] == ["cat"]
    chat.append_cat("x")
    chat.drop_last_cat()
    chat.drop_last_cat()
    assert chat.msgs == []


@pytest.mark.qt
def test_while_busy_send_and_escape_mean_stop(chat: ChatWindow) -> None:
    stops: list[bool] = []
    chat.stop_requested.connect(lambda: stops.append(True))
    chat.set_busy(True)
    chat.input.setText("more")
    chat.send_btn.click()
    chat.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert stops == [True, True]
    assert chat.send_btn.toolTip().startswith("Stop")


@pytest.mark.qt
@pytest.mark.parametrize("role", ["user", "cat", "info", "error", "event"])
def test_every_kind_of_message_is_shown(chat: ChatWindow, role: str) -> None:
    chat.add(role, f"a {role} message")
    assert f"a {role} message" in _shown(chat)


@pytest.mark.qt
def test_only_changed_messages_are_redrawn(chat: ChatWindow) -> None:
    chat.add("user", "one")
    _shown(chat)
    starts = list(chat._starts)
    chat.append_cat("two")
    _shown(chat)
    assert chat._starts[:2] == starts
    chat._flush()                                       # nothing new: nothing happens
    assert not chat._dirty


@pytest.mark.qt
def test_confirm_card(chat: ChatWindow) -> None:
    answers: list[str] = []
    chat.ask_confirm("close <Notepad>", answers.append, allow_always=True)
    assert chat.confirm.isVisibleTo(chat) and chat.always_btn.isVisibleTo(chat)
    assert "close &lt;Notepad&gt;" in chat.confirm_label.text()
    sent: list[str] = []
    chat.send_requested.connect(sent.append)
    chat.input.setText("ignored while asking")
    chat._send()
    assert sent == []
    chat.always_btn.click()
    chat.yes_btn.click()
    assert answers == ["always"] and not chat.confirm.isVisibleTo(chat)
    chat.ask_confirm("x", answers.append)
    assert not chat.always_btn.isVisibleTo(chat)
    chat.no_btn.click()
    assert answers == ["always", "no"]


@pytest.mark.qt
def test_choice_card(chat: ChatWindow) -> None:
    got: list[str] = []
    chat.ask_choice("<b>Token limit reached.</b>", [("switch", "SWITCH MODEL"), ("raise", "RAISE"), ("stop", "STOP")],
                    got.append)
    assert [b.text() for b in chat._choice_btns] == ["SWITCH MODEL", "RAISE", "STOP"]
    assert chat._choice_btns[-1].objectName() == "noBtn"
    chat.ask_choice("again", [("a", "A"), ("b", "B")], got.append)
    assert [b.text() for b in chat._choice_btns] == ["A", "B"]
    chat._choice_btns[1].click()
    assert got == ["b"] and not chat.choice.isVisibleTo(chat)
    chat.ask_choice("x", [("a", "A")], got.append)
    chat.drop_choice()
    chat._chose("a")
    assert got == ["b"]


@pytest.mark.qt
def test_watch_bar_lists_up_to_five_with_stop_links(chat: ChatWindow) -> None:
    cancelled: list[int] = []
    chat.watch_cancel_requested.connect(cancelled.append)
    chat.set_watches([(i, f"thing <{i}>") for i in range(1, 8)])
    text = chat.watch_bar.text()
    assert chat.watch_bar.isVisibleTo(chat) and text.count('href="stop:') == 5
    assert "thing &lt;1&gt;" in text and "…and 2 more" in text
    chat._watch_link("stop:3")
    chat._watch_link("stop:x")
    chat._watch_link("http://x")
    assert cancelled == [3]
    chat.set_watches([])
    assert not chat.watch_bar.isVisibleTo(chat)


@pytest.mark.qt
def test_model_banner_and_chip(chat: ChatWindow) -> None:
    chat.set_model_banner("⏳ Model is starting up", ("Manage usage", "https://x.test/u?a=1&b=2"))
    assert chat.model_bar.isVisibleTo(chat)
    assert 'href="https://x.test/u?a=1&amp;b=2"' in chat.model_bar.text()
    chat.set_model_banner(None)
    assert not chat.model_bar.isVisibleTo(chat)
    chat.set_model_chip("9B · HIGH", True, "This chat uses Medium 9B")
    assert chat.model_btn.text() == "9B · HIGH" and chat.model_btn.toolTip() == "This chat uses Medium 9B"
    assert theme.current().accent in chat.model_btn.styleSheet()
    chat.set_model_chip("35B", False, "")
    assert chat.model_btn.styleSheet() == ""


@pytest.mark.qt
def test_signals_of_the_buttons(chat: ChatWindow) -> None:
    got: list[str] = []
    chat.model_menu_requested.connect(lambda: got.append("model"))
    chat.model_btn.click()
    assert got == ["model"]


@pytest.mark.qt
def test_reset_and_load(chat: ChatWindow) -> None:
    chat.add("user", "hi")
    chat.ask_confirm("x", lambda a: None)
    chat.ask_choice("y", [("a", "A")], lambda k: None)
    chat.set_busy(True)
    chat.load([("user", "earlier question"), ("cat", "earlier answer")])
    assert [(m.role, m.text) for m in chat.msgs] == [("user", "earlier question"), ("cat", "earlier answer")]
    assert chat._confirm_cb is None and chat._choice_cb is None and chat.input.isEnabled()
    assert "earlier answer" in _shown(chat)
    chat.load([])
    assert [m.text for m in chat.msgs] == [GREETING]
    chat.add("user", "x")
    chat.reset()
    assert [m.text for m in chat.msgs] == [GREETING]


@pytest.mark.qt
def test_a_name_shows_in_the_title_the_input_and_above_its_messages(chat: ChatWindow) -> None:
    naming.set_name("Mochi")
    chat.rename()
    assert chat._title_text() == "MOCHI CHAT"
    assert chat.input.placeholderText().startswith("Talk to Mochi…")
    assert "🐱 Mochi" in _shown(chat)


@pytest.mark.qt
@pytest.mark.parametrize("auto", [False, True])
def test_a_long_name_is_shortened_to_fit_the_title_bar(chat: ChatWindow, auto: bool) -> None:
    naming.set_name("Sir Fluffington III")
    chat.set_auto_mode(auto)
    title = chat._title_text()
    room = chat._auto_rect().x() - 17 * chat.u - ((theme.text_size("AUTO", chat.u)[0] + 2 * chat.u) if auto else 0)
    assert theme.text_size(title, chat.u)[0] <= room and title.startswith("SIR")


# ---------------------------------------------------------------- the saved chats
@dataclass
class Info:
    id: str
    title: str
    updated: float
    count: int
    model: str = ""
    find: str = ""


@pytest.fixture
def items() -> list[Info]:
    now = time.time()
    return [Info("a1", "Packing list <for> Paris", now, 4, "Smart 35B", "paris"),
            Info("b2", "Music", now - 86400, 1, "", "spotify"),
            Info("c3", "Old", now - 400 * 86400, 12)]


@pytest.mark.qt
def test_history_replaces_the_messages_and_lists_the_chats(chat: ChatWindow, items) -> None:  # type: ignore[no-untyped-def]
    chat.show_history(items, "a1")
    assert chat.history_shown and chat._title_text() == "CHAT HISTORY"
    assert not chat.view.isVisibleTo(chat) and chat.hist_panel.isVisibleTo(chat)
    text = chat.hist_view.toPlainText()
    assert "Packing list <for> Paris" in text and "3 saved chats" in text and "open now" in text
    assert "4 messages" in text and "1 message" in text and "Smart 35B" in text
    chat.hide_history()
    assert not chat.history_shown and chat.view.isVisibleTo(chat)


@pytest.mark.qt
def test_history_search(chat: ChatWindow, items) -> None:  # type: ignore[no-untyped-def]
    chat.show_history(items, None)
    chat.hist_search.setText("  SPOTIFY ")
    text = chat.hist_view.toPlainText()
    assert "Music" in text and "Paris" not in text
    chat.hist_search.setText("nothing like it")
    assert "No chat matches" in chat.hist_view.toPlainText()
    chat.set_history(items[:1], None)
    assert chat.hist_search.text() == "nothing like it"


@pytest.mark.qt
def test_history_empty(chat: ChatWindow) -> None:
    chat.show_history([], None)
    assert "No saved chats yet" in chat.hist_view.toPlainText()


@pytest.mark.qt
def test_history_links(chat: ChatWindow, items) -> None:  # type: ignore[no-untyped-def]
    got: list[tuple] = []
    chat.history_open.connect(lambda cid: got.append(("open", cid)))
    chat.history_delete.connect(lambda cid: got.append(("delete", cid)))
    chat.history_delete_all.connect(lambda: got.append(("delete_all",)))
    chat.show_history(items, None)
    chat._history_link(QUrl("open:b2"))
    chat._history_link(QUrl("del:c3"))
    assert chat._hist_confirm == "c3" and "delete?" in chat.hist_view.toHtml()
    chat._history_link(QUrl("del:c3:no"))
    assert chat._hist_confirm == ""
    chat._history_link(QUrl("del:c3:yes"))
    chat._history_link(QUrl("delall:ask"))
    assert "Delete all 3 saved chats?" in chat.hist_view.toPlainText()
    chat._history_link(QUrl("delall:no"))
    chat._history_link(QUrl("delall:yes"))
    assert got == [("open", "b2"), ("delete", "c3"), ("delete_all",)]
    chat._history_link(QUrl("back:"))
    assert not chat.history_shown


@pytest.mark.qt
def test_escape_leaves_the_history_first_then_hides(chat: ChatWindow, items) -> None:  # type: ignore[no-untyped-def]
    chat.show_near(CAT, WORK, 2)
    chat.show_history(items, None)
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    chat.keyPressEvent(esc)
    assert not chat.history_shown and chat.isVisible()
    chat.keyPressEvent(esc)
    assert not chat.isVisible()


def test_when_labels() -> None:
    now = time.time()
    assert ChatWindow._when(now).startswith("Today ")
    assert ChatWindow._when(now - 86400).startswith("Yesterday ")
    assert len(ChatWindow._when(now - 3 * 365 * 86400).split()) == 3


# ---------------------------------------------------------------- size, place and fullscreen
@pytest.mark.qt
@pytest.mark.parametrize(("size", "full", "want_u", "want_px"), [
    ([200, 250], False, (200.0, 250.0), (400, 500)),
    ("nonsense", False, (175.0, 225.0), (350, 450)),
    ([float("nan"), 3], False, (175.0, 225.0), (350, 450)),
    ([20000, 300], False, (175.0, 225.0), (350, 450)),
    ([10, 10], True, (150.0, 160.0), None),
])
def test_set_layout(chat: ChatWindow, size, full, want_u, want_px) -> None:  # type: ignore[no-untyped-def]
    chat.set_layout(size, full)
    assert chat.size_u == want_u and chat.is_full is full
    if want_px:
        assert (chat.width(), chat.height()) == want_px


@pytest.mark.qt
def test_show_near_places_it_next_to_the_cat(chat: ChatWindow) -> None:
    chat.show_near(CAT, WORK, 2, activate=False)
    g = chat.geometry()
    assert chat.isVisible()
    assert not g.intersects(QRect(CAT[0], CAT[1], CAT[2] - CAT[0], CAT[3] - CAT[1]))
    chat.hide()
    chat.show_near(CAT, WORK, 3)
    assert chat.u == 3 and chat.width() == min(525, chat._screen_area().width())


@pytest.mark.qt
def test_fullscreen_and_back(chat: ChatWindow) -> None:
    changes: list[bool] = []
    chat.layout_changed.connect(lambda: changes.append(chat.is_full))
    chat.show_near(CAT, WORK, 2)
    chat.move(chat._screen_area().topLeft() + QPoint(10, 10))
    before = chat.geometry()
    chat.toggle_fullscreen()
    assert chat.is_full and chat.geometry() == chat._screen_area()
    assert not any(g.isVisibleTo(chat) for g in chat._grips.values())
    chat.set_fullscreen(True)
    chat.toggle_fullscreen()
    assert not chat.is_full and chat.geometry() == before
    assert all(g.isVisibleTo(chat) for g in chat._grips.values())
    assert changes == [True, False]


def _press(w, kind: QEvent.Type, pos: QPoint, glob: QPoint | None = None) -> QMouseEvent:  # type: ignore[no-untyped-def]
    btn = Qt.MouseButton.LeftButton
    return QMouseEvent(kind, QPointF(pos), QPointF(glob if glob is not None else w.mapToGlobal(pos)), btn,
                       Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else btn,
                       Qt.KeyboardModifier.NoModifier)


@pytest.mark.qt
def test_title_bar_buttons(chat: ChatWindow) -> None:
    got: list[str] = []
    chat.new_chat_requested.connect(lambda: got.append("new"))
    chat.history_toggled.connect(lambda: got.append("history"))
    chat.auto_toggled.connect(lambda: got.append("auto"))
    chat.show_near(CAT, WORK, 2)
    for rect in (chat._new_rect(), chat._hist_rect(), chat._auto_rect()):
        chat.mousePressEvent(_press(chat, QEvent.Type.MouseButtonPress, rect.center()))
    assert got == ["new", "history", "auto"]
    chat.mousePressEvent(_press(chat, QEvent.Type.MouseButtonPress, chat._full_rect().center()))
    assert chat.is_full
    chat.mouseDoubleClickEvent(_press(chat, QEvent.Type.MouseButtonDblClick, QPoint(60, 8)))
    assert not chat.is_full
    chat.mousePressEvent(_press(chat, QEvent.Type.MouseButtonPress, chat._close_rect().center()))
    assert not chat.isVisible()


@pytest.mark.qt
def test_drag_by_the_title_bar(chat: ChatWindow) -> None:
    chat.show_near(CAT, WORK, 2)
    start = chat.pos()
    g = chat.mapToGlobal(QPoint(60, 8))
    chat.mousePressEvent(_press(chat, QEvent.Type.MouseButtonPress, QPoint(60, 8), g))
    chat.mouseMoveEvent(_press(chat, QEvent.Type.MouseMove, QPoint(60, 8), g + QPoint(30, 20)))
    chat.mouseReleaseEvent(_press(chat, QEvent.Type.MouseButtonRelease, QPoint(60, 8)))
    assert chat.pos() - start == QPoint(30, 20) and chat._drag is None


@pytest.mark.qt
def test_resizing_at_an_edge_saves_the_size_in_art_pixels(chat: ChatWindow) -> None:
    changes: list[bool] = []
    chat.layout_changed.connect(lambda: changes.append(True))
    chat.show_near(CAT, WORK, 2)
    chat.move(200, 100)
    x0 = chat.x()
    g = chat._grips["r"]
    start = g.mapToGlobal(QPoint(2, 200))
    g.mousePressEvent(_press(g, QEvent.Type.MouseButtonPress, QPoint(2, 200), start))
    g.mouseMoveEvent(_press(g, QEvent.Type.MouseMove, QPoint(2, 200), start + QPoint(100, 0)))
    g.mouseReleaseEvent(_press(g, QEvent.Type.MouseButtonRelease, QPoint(2, 200)))
    assert chat.width() == 450 and chat.x() == x0 and chat.size_u == (225.0, 225.0) and changes == [True]
    g.mousePressEvent(_press(g, QEvent.Type.MouseButtonPress, QPoint(2, 200), start))
    g.mouseMoveEvent(_press(g, QEvent.Type.MouseMove, QPoint(2, 200), start - QPoint(5000, 0)))
    assert chat.width() == max(300, chat.minimumWidth())          # never smaller than 150 art px


@pytest.mark.parametrize(("side", "pos", "edges"), [
    ("r", QPoint(2, 3), "tr"), ("r", QPoint(2, 200), "r"), ("l", QPoint(2, 445), "bl"), ("b", QPoint(3, 2), "bl"),
    ("b", QPoint(345, 2), "br"), ("t", QPoint(100, 1), "t")])
@pytest.mark.qt
def test_edges_near_corners(chat: ChatWindow, side: str, pos: QPoint, edges: str) -> None:
    window_pos = chat._grips[side].mapTo(chat, pos)
    assert chat._edges_at(window_pos, side) == edges
    assert chat_mod._CURSORS[edges] is not None


@pytest.mark.qt
def test_moving_to_another_dpi_rescales(chat: ChatWindow, fake_platform) -> None:  # type: ignore[no-untyped-def]
    chat.show_near(CAT, WORK, 2)
    fake_platform.display.answers("window_dpi", lambda win: 192 if win == int(chat.winId()) else 0)
    chat._check_dpi()
    assert chat.dpi == 192 and chat.u == 4 and (chat.width(), chat.height()) == chat._want_size()


@pytest.mark.qt
def test_restyle_and_scale(chat: ChatWindow) -> None:
    chat.set_watches([(1, "x")])
    theme.configure(style="retro", size="large")
    chat.restyle()
    assert theme.STYLES["retro"].field in chat.styleSheet()
    assert chat.u == theme.unit_for_dpi(chat.dpi)
    chat.apply_scale(3, 144)
    assert (chat.u, chat.dpi, chat.font_px) == (3, 144, theme.font_px_for_dpi(144, 10))


@pytest.mark.qt
def test_paints_the_title_bar_buttons(chat: ChatWindow) -> None:
    chat.set_auto_mode(True)
    img = chat.grab().toImage()
    assert img.pixelColor(chat.width() // 2, 8).name() == theme.ACCENT.name()


@pytest.mark.qt
def test_close_hides_instead_of_closing(chat: ChatWindow) -> None:
    chat.show_near(CAT, WORK, 2)
    chat.close()
    assert not chat.isVisible()
    chat.show()
    assert chat.isVisible()


@pytest.mark.qt
def test_links_in_messages_open_only_web_pages(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtGui import QDesktopServices

    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda u: opened.append(u.toString())))
    for url in ("https://example.com/", "http://a.test/", "file:///C:/Windows/System32/calc.exe", "javascript:x"):
        ChatWindow._open_link(QUrl(url))
    assert opened == ["https://example.com/", "http://a.test/"]
