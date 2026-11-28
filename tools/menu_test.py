"""Right-click menu test (developer tool), rendered offscreen - the menu is never shown.

Checks the layout (Pause, Chat and Quit on top; Minigames, Cosmetics and Settings as
sections that fold open, one at a time), that a click on a game or action closes the menu
and does it, that a click on a setting toggles it and keeps the menu open with the new
state, the Cat Pass locks (games, skins, hats), the first-time game explanation, and
keyboard use.  No game is started on screen (that's games_ui_test.py).
Also Settings -> Name... (the cat's name in the chat, the menu, the tray and the AI's prompt)
and the chat window's edges and fullscreen (the chat is never shown either).
Doesn't touch real settings ("Start with Windows" is only checked, not toggled).
Saves build/preview/menu_closed.png, menu_open.png, menu_cosmetics.png.
Usage:  python tools/menu_test.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelcat.adapters.windows import win32  # noqa: E402
from pixelcat.logsetup import setup_logging  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def name_and_chat_checks(pet, m, row) -> None:  # type: ignore[no-untyped-def]  # noqa: C901
    """Settings -> Name... and the chat's size / fullscreen (nothing is shown on screen)."""
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QMouseEvent

    from pixelcat import naming, persistence
    from pixelcat.llm.prompt import build_system_prompt
    from pixelcat.sprites import pixelfont
    from pixelcat.ui import theme
    from pixelcat.ui.chat import ChatWindow

    chat = pet.chat_ui.ensure_chat()
    dialogs: list = []
    pet.provider_settings.show_amount_dialog = dialogs.append  # type: ignore[method-assign]   (not shown)
    m.close = lambda: None                                     # type: ignore[method-assign]
    m.expanded = {"settings"}
    m.items = pet.menus.menu_items()
    m._layout()
    m._activate(row("set_name"))
    dlg = dialogs[-1] if dialogs else None
    check("Settings -> Name... asks for the name", dlg is not None and dlg.field.text() == "")
    for bad in ("<b>Mochi</b>", "--- ..."):
        dlg.field.setText(bad)
        dlg._save()
        check(f"a name like {bad!r} is refused with a reason (the old name stays)",
              naming.custom() == "" and dlg.status.text() != "" and pet.saved.cat_name == "", dlg.status.text())
    check("at most 20 characters (the field stops there, too)", naming.check("A" * 21)[1] != ""
          and dlg.field.maxLength() == 20 and naming.check("Müsli Ü")[0] == "Müsli Ü")
    dlg.field.setText("  Mochi  ")
    dlg._save()
    m.items = pet.menus.menu_items()
    item = next(c for it in m.items for c in it.children if c.key == "set_name")
    tray = {a.text() for a in pet.tray.menu.actions()}
    check("'Mochi': saved, and the menu title, the Name item and the tray say so",
          pet.saved.cat_name == "Mochi" and m.title == "MOCHI" and item.note == "MOCHI"
          and {"Find Mochi", "Quit Mochi"} <= tray, f"{m.title} {item.note} {sorted(tray)}")
    chat._flush()
    shown = chat.view.toPlainText()
    check("the chat: 'MOCHI CHAT' on top, 'Mochi' above its messages, 'Talk to Mochi…'",
          chat._title_text() == "MOCHI CHAT" and "🐱 Mochi" in shown and "🐱 cat" not in shown
          and chat.input.placeholderText().startswith("Talk to Mochi…"), f"{chat._title_text()!r} {shown[:40]!r}")
    check("the AI is told its name (and is 'Pixel' without one)",
          build_system_prompt([], naming.ai()).startswith("You are Mochi, a tiny")
          and build_system_prompt([]).startswith("You are Pixel, a tiny"))
    check("a renamed cat gets a fresh Copilot session with the next message", pet.session.copilot_stale is True)
    pet.session.copilot_stale = False
    check("the name stays on this PC: not part of an exported progress file",
          "cat_name" not in persistence.progress_of(pet.saved))
    st = persistence._from_dict({"cat_name": 5, "chat_size": "big", "chat_full": "yes"})
    check("a damaged save gives no name, the usual chat size and no fullscreen",
          st.cat_name == "" and st.chat_size == [] and st.chat_full is False)
    check("the pixel font draws accented letters as the plain letter (a cat called Müsli)",
          pixelfont.glyph("ü") == pixelfont.glyph("U") and pixelfont.glyph("é") == pixelfont.glyph("E"))
    u = chat.u
    for auto in (False, True):
        chat.auto_mode = auto
        pet.menus.set_cat_name("Sir Fluffington III")
        title = chat._title_text()
        room = chat._auto_rect().x() - 17 * u - ((theme.text_size("AUTO", u)[0] + 2 * u) if auto else 0)
        check(f"a long name fits the title bar ({'auto mode' if auto else 'normal'}): {title!r}",
              theme.text_size(title, u)[0] <= room and title.startswith("SIR"), f"{theme.text_size(title, u)[0]} <= {room}")
    chat.auto_mode = False
    pet.menus.set_cat_name("")
    tray = {a.text() for a in pet.tray.menu.actions()}
    chat._flush()
    check("an empty name: back to Pixel Cat everywhere", m.title == "PIXEL CAT" and chat._title_text() == "MEOW CHAT"
          and {"Find my cat", "Quit Pixel Cat"} <= tray and "🐱 cat" in chat.view.toPlainText()
          and pet.saved.cat_name == "")

    # ---- the chat's edges and fullscreen (the window is never shown; its geometry still works)
    chat.apply_scale(2)
    area = chat._screen_area(QPoint(200, 200))
    chat.setGeometry(area.left() + 100, area.top() + 100, *chat._want_size())
    check("the chat starts at its usual size (175 x 225 art pixels)", (chat.width(), chat.height()) == (350, 450),
          f"{chat.width()}x{chat.height()}")

    def drag(side: str, local: QPoint, dx: int, dy: int) -> None:
        g = chat._grips[side]
        start = g.mapToGlobal(local)
        btn, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
        mods = Qt.KeyboardModifier.NoModifier
        g.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(local), QPointF(start), btn, btn, mods))
        for i in (1, 2):
            to = QPointF(start.x() + dx * i / 2, start.y() + dy * i / 2)
            g.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(local), to, none, btn, mods))
        g.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(local), QPointF(start.x() + dx,
                                        start.y() + dy), btn, none, mods))

    x0 = chat.x()
    drag("r", QPoint(2, 200), 100, 0)
    check("dragging the right edge makes it wider (saved in art pixels)", chat.width() == 450 and chat.x() == x0
          and pet.saved.chat_size == [225.0, 225.0], f"{chat.width()} {pet.saved.chat_size}")
    drag("l", QPoint(2, chat.height() - 4), -40, 30)
    check("near a corner both ways: the bottom-left corner", (chat.x(), chat.width(), chat.height()) ==
          (x0 - 40, 490, 480), f"{chat.x() - x0} {chat.width()}x{chat.height()}")
    g = chat._grips["r"]
    g.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(2, 3), g.mapToGlobal(QPointF(2, 3)),
                                 Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    check("the edges show resize arrows (diagonal at a corner)", g.cursor().shape() == Qt.CursorShape.SizeBDiagCursor
          and chat._grips["b"].cursor().shape() == Qt.CursorShape.SizeVerCursor and chat.cursor().shape() ==
          Qt.CursorShape.ArrowCursor)
    drag("b", QPoint(200, 2), 0, -2000)
    drag("r", QPoint(2, 200), -2000, 0)
    check("never smaller than 150 x 160 art pixels", (chat.width(), chat.height()) ==
          (max(300, chat.minimumWidth()), max(320, chat.minimumHeight())), f"{chat.width()}x{chat.height()}")
    drag("r", QPoint(2, 200), 60, 0)
    size = chat.size_u
    chat.apply_scale(3)
    check("its size follows the scale (another DPI, Settings -> Size)", (chat.width(), chat.height()) ==
          chat._want_size() and abs(chat.width() - min(round(size[0] * 3), chat._screen_area().width())) <= 1,
          f"{chat.width()}x{chat.height()} for {size}")
    chat.apply_scale(2)
    before = chat.geometry()
    chat.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(chat._full_rect().center()),
                                     QPointF(chat.mapToGlobal(chat._full_rect().center())), Qt.MouseButton.LeftButton,
                                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    full_area = chat._screen_area()
    check("the fullscreen button fills the screen's work area (the taskbar stays visible; saved)",
          chat.is_full and chat.geometry() == full_area and pet.saved.chat_full is True
          and not any(gr.isVisibleTo(chat) for gr in chat._grips.values()), f"{chat.geometry()} vs {full_area}")
    chat.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(60, 8), QPointF(chat.mapToGlobal(
        QPoint(60, 8))), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    check("fullscreen can't be dragged around", chat._drag is None)
    chat.mouseDoubleClickEvent(QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(60, 8), QPointF(chat.mapToGlobal(
        QPoint(60, 8))), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    check("a double click on the title goes back: same size and place as before", not chat.is_full
          and chat.geometry() == before and pet.saved.chat_full is False and all(gr.isVisibleTo(chat) for gr in
                                                                                chat._grips.values()),
          f"{chat.geometry()} vs {before}")
    chat.mouseDoubleClickEvent(QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(60, 8), QPointF(chat.mapToGlobal(
        QPoint(60, 8))), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    check("...and a double click on the title goes fullscreen", chat.is_full)
    chat.set_fullscreen(False)
    other = ChatWindow(pet.sprites, display=pet.platform.display)
    other.set_layout([200, 250], False)
    odd = ChatWindow(pet.sprites, display=pet.platform.display)
    odd.set_layout("nonsense", False)
    small = ChatWindow(pet.sprites, display=pet.platform.display)
    small.set_layout([10, 10], True)
    check("a saved size opens like that; nonsense gives the usual size; fullscreen is remembered",
          (other.width(), other.height()) == (400, 500) and (odd.width(), odd.height()) == (350, 450)
          and small.size_u == ChatWindow.MIN_U and small.is_full)
    for w in (other, odd, small):
        w.deleteLater()


def history_and_effort_checks(pet) -> None:  # type: ignore[no-untyped-def]  # noqa: C901 - linear checks
    """The chat history (its own folder in build\\, never the real one) and the chat's effort."""
    import shutil

    from PySide6.QtCore import QPointF, QUrl
    from PySide6.QtGui import QMouseEvent

    from pixelcat import chats as chats_mod
    from pixelcat.chats import ChatStore

    folder = ROOT / "build" / "chathisttest"
    shutil.rmtree(folder, ignore_errors=True)
    pet.chat_ui.chats = ChatStore(folder, secrets=pet.platform.secrets)
    chat = pet.chat_ui.ensure_chat()
    chat.hide_history()
    pet.session.new_chat()
    pet.chat_state.history[:] = [
        {"role": "user", "content": "Plan my trip to Rome please"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "look_at_screen", "arguments": {}}}],
         "raw": [{"type": "thinking", "signature": "abc"}]},
        {"role": "tool", "content": "Screenshot of the screen.", "tool_name": "look_at_screen", "images": ["iVBORw0"]},
        {"role": "assistant", "content": "Here is your Rome plan: the Colosseum first."}]
    chat.add("user", "Plan my trip to Rome please")
    chat.add("info", "👀 Looked at the screen")
    chat.add("cat", "Here is your Rome plan: the Colosseum first.")
    pet.chat_state.chat_effort = "high"
    pet.chat_ui.store_chat()
    idx = pet.chat_ui.chats.index()
    files = list(folder.glob("*.chat"))
    raw = files[0].read_bytes() if files else b""
    check("a chat is saved: titled with the first question, the effort with it",
          len(idx) == 1 and idx[0].title == "Plan my trip to Rome please" and idx[0].count == 1 and len(files) == 1,
          str(idx[:1]))
    check("...encrypted (the file doesn't show what was said)", raw and b"Rome" not in raw and "Rome".encode(
        "utf-16-le") not in raw)
    cid = idx[0].id
    data = pet.chat_ui.chats.load(cid) or {}
    hist = data.get("history") or []
    check("...without screenshots or a model's replay blocks; the greeting isn't kept",
          len(hist) == 4 and "images" not in hist[2] and "not kept" in hist[2]["content"] and "raw" not in hist[1]
          and data.get("chat_effort") == "high" and [m[0] for m in data.get("msgs", [])] == ["user", "info", "cat"])
    pet.chat_ui.store_chat()
    check("an unchanged chat isn't written again", files[0].read_bytes() == raw)
    pet.session.new_chat()
    check("a new chat: the old one stays saved, the effort goes back to the default",
          pet.chat_state.history == [] and pet.chat_ui.chat_id is None and pet.chat_state.chat_effort is None and len(pet.chat_ui.chats.index()) == 1)
    chat.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(chat._hist_rect().center()),
                                     QPointF(chat.mapToGlobal(chat._hist_rect().center())), Qt.MouseButton.LeftButton,
                                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    shown = chat.hist_view.toPlainText()
    check("the clock button shows the saved chats (title, when, how many messages)",
          chat.history_shown and "Plan my trip to Rome please" in shown and "1 message" in shown
          and chat._title_text() == "CHAT HISTORY" and not chat.view.isVisibleTo(chat), shown[:120])
    chat.hist_search.setText("colosseum")
    found = "Plan my trip" in chat.hist_view.toPlainText()
    chat.hist_search.setText("zebra")
    check("search finds a chat by what was said in it", found and "No chat matches" in chat.hist_view.toPlainText())
    chat.hist_search.setText("")
    chat._history_link(QUrl(f"open:{cid}"))
    check("clicking a chat reopens it: the messages, what the AI remembers, its effort",
          pet.chat_ui.chat_id == cid and len(pet.chat_state.history) == 4 and pet.chat_state.chat_effort == "high" and not chat.history_shown
          and [m.role for m in chat.msgs][:3] == ["user", "info", "cat"] and "remember it" in chat.msgs[-1].text,
          str([(m.role, m.text[:30]) for m in chat.msgs]))
    before = files[0].read_bytes()
    pet.chat_ui.store_chat()
    check("only opening it doesn't change it (it keeps its place in the list)", files[0].read_bytes() == before)
    pet.chat_state.history.append({"role": "user", "content": "And Florence?"})
    chat.add("user", "And Florence?")
    pet.chat_ui.store_chat()
    idx = pet.chat_ui.chats.index()
    check("talking on: the same chat is updated, not a new one", len(idx) == 1 and idx[0].id == cid
          and idx[0].count == 2)
    pet.session.new_chat()
    pet.chat_state.history[:] = [{"role": "user", "content": "What's 2+2?"}, {"role": "assistant", "content": "4"}]
    chat.add("user", "What's 2+2?")
    chat.add("cat", "4")
    pet.chat_ui.store_chat()
    cid2 = pet.chat_ui.chat_id
    idx = pet.chat_ui.chats.index()
    check("a second chat: the newest first", [i.id for i in idx] == [cid2, cid])
    pet.commands.local_command("/history")
    chat._history_link(QUrl(f"del:{cid}"))
    asked = "delete?" in chat.hist_view.toPlainText() and len(pet.chat_ui.chats.index()) == 2
    chat._history_link(QUrl(f"del:{cid}:yes"))
    check("/history; delete asks once more, then the chat is gone (the open one stays)",
          chat.history_shown and asked and [i.id for i in pet.chat_ui.chats.index()] == [cid2] and pet.chat_ui.chat_id == cid2)
    chat._history_link(QUrl(f"del:{cid2}"))
    chat._history_link(QUrl(f"del:{cid2}:yes"))
    check("deleting the open chat starts a fresh one", pet.chat_ui.chats.index() == [] and pet.chat_state.history == []
          and pet.chat_ui.chat_id is None and chat.history_shown and "No saved chats yet" in chat.hist_view.toPlainText())
    for q in ("One?", "Two?"):
        pet.session.new_chat()
        pet.chat_state.history[:] = [{"role": "user", "content": q}]
        pet.chat_ui.store_chat()
    chat.show_history(pet.chat_ui.history_items(), pet.chat_ui.chat_id)
    chat._history_link(QUrl("delall:ask"))
    asked = "Delete all 2 saved chats?" in chat.hist_view.toPlainText()
    chat._history_link(QUrl("delall:yes"))
    check("delete all: asks first, then every chat (and the open one) is gone",
          asked and pet.chat_ui.chats.index() == [] and not list(folder.glob("*.chat")) and pet.chat_state.history == [])
    chat._history_link(QUrl("back:"))
    keep = chats_mod.KEEP
    chats_mod.KEEP = 3
    try:
        ids = []
        for n in range(5):
            cid_n = f"20200101-00000{n}-ab{n:02x}"
            ids.append(cid_n)
            pet.chat_ui.chats.save({"id": cid_n, "created": 0, "msgs": [["user", f"chat {n}"]],
                            "history": [{"role": "user", "content": f"chat {n}"}], "model": ""})
            time.sleep(0.01)
        left = [i.id for i in pet.chat_ui.chats.index()]
    finally:
        chats_mod.KEEP = keep
    check("only the newest are kept (the oldest chats are deleted)", left == ids[:1:-1]
          and len(list(folder.glob("*.chat"))) == 3, str(left))
    (folder / "index.dat").write_bytes(b"broken")
    check("a damaged list is rebuilt from the chats", sorted(i.id for i in pet.chat_ui.chats.index()) == sorted(left))
    pet.chat_ui.chats.save({"id": "20200102-000000-abcd", "created": 0, "msgs": [["user", "hi"]],
                    "history": [{"role": "user", "content": "hi"}], "model": "anthropic:claude-gone",
                    "chat_model": "anthropic:claude-gone"})
    real_auth = pet.accounts.auth
    pet.accounts.auth = lambda p: ""                     # type: ignore[method-assign]   (nothing connected)
    pet.session.open_saved_chat("20200102-000000-abcd")
    pet.accounts.auth = real_auth                        # type: ignore[method-assign]
    check("a chat whose model isn't there any more opens with the default model (and says so)",
          pet.chat_state.chat_model is None and "isn't available" in chat.msgs[-1].text, chat.msgs[-1].text)
    pet.chat_ui.chats.delete_all()
    pet.session.new_chat()

    # ---- effort, from the chat's model button
    def effort_menu():  # type: ignore[no-untyped-def]
        return pet.model_choice.model_menu_items()[-1]

    real_model = pet.cfg.model
    pet.cfg.model = "qwen3.5:35b-a3b"                # (a local default, whatever this PC uses)
    m = effort_menu()
    check("the model button's menu has Effort: Default + Low/Medium/High for a local model (what each does)",
          m.key == "chateffort" and [c.key for c in m.children] == [
              "chateffort:default", "chateffort:low", "chateffort:medium", "chateffort:high"]
          and m.children[1].note == "NO THINKING" and m.children[0].checked, str([c.label for c in m.children]))
    pet.model_choice.on_model_menu("chateffort:high")
    check("High: this chat always thinks; the button shows it", pet.chat_state.chat_effort == "high" and pet.session._think_now() is True
          and chat.model_btn.text().endswith("· HIGH") and chat._model_temp and effort_menu().children[3].checked,
          chat.model_btn.text())
    pet.model_choice.on_model_menu("chateffort:low")
    low = pet.session._think_now()
    pet.model_choice.on_model_menu("chateffort:medium")
    pet.chat_state.history[:] = [{"role": "user", "content": "hi"}]
    planning = pet.session._think_now()
    pet.chat_state.history.append({"role": "user", "content": "[tool result for wait] ok", "_tool": True})
    routine = pet.session._think_now()
    check("Low never thinks; Medium thinks when planning, not for routine steps",
          low is False and planning is True and routine is False)
    pet.model_choice.on_model_menu("chateffort:default")
    check("Default goes back", pet.chat_state.chat_effort is None and not chat._model_temp)
    pet.accounts.ext_p("anthropic")["models"] = [{"id": "claude-t", "name": "Claude T", "effort": ["low", "medium", "high"]},
                                         {"id": "claude-n", "name": "Claude N", "effort": []}]
    pet.accounts.ext_p("copilot")["models"] = [{"id": "gpt-c", "name": "GPT C", "efforts": ["low", "medium", "high", "xhigh"]}]
    looks = {}
    for tag in ("anthropic:claude-t", "anthropic:claude-n", "copilot:gpt-c", "openai:gpt-o"):
        pet.chat_state.chat_model = tag
        looks[tag] = [c.key.split(":")[1] for c in effort_menu().children]
    check("online models: only the levels the model has", looks == {
        "anthropic:claude-t": ["default", "low", "medium", "high"], "anthropic:claude-n": ["none"],
        "copilot:gpt-c": ["default", "low", "medium", "high", "xhigh"],
        "openai:gpt-o": ["default", "low", "medium", "high", "xhigh", "max"]}, str(looks))
    pet.chat_state.chat_model = "anthropic:claude-t"
    pet.saved.ext_settings["thinking"] = False
    pet.saved.ext_settings["effort"] = "low"
    base = pet.model_choice.ext_think_effort()
    pet.commands.local_command("/effort max")
    refused = pet.chat_state.chat_effort is None and "low, medium, high" in chat.msgs[-1].text
    pet.commands.local_command("/effort high")
    check("an online model gets this chat's effort (thinking on); /effort sets it, only to levels it has",
          base == (False, "low") and refused and pet.model_choice.ext_think_effort() == (True, "high"), chat.msgs[-1].text)
    pet.saved.ext_settings.pop("thinking")
    pet.saved.ext_settings.pop("effort")
    pet.chat_state.chat_model = None
    pet.session.new_chat()
    pet.cfg.model = real_model
    check("a new chat starts with the default effort", pet.chat_state.chat_effort is None)
    shutil.rmtree(folder, ignore_errors=True)


def main() -> int:
    setup_logging(False)
    win32.set_dpi_awareness()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    from pixelcat import settings
    from pixelcat.app import PixelCatApp
    from pixelcat.bootstrap import build_platform, build_providers
    from pixelcat.persistence import SavedState
    from pixelcat.settings import load_config

    written: list[tuple[str, object]] = []
    settings.save_user_setting = lambda k, v: written.append((k, v))   # never touch the real config
    pet = PixelCatApp(app, load_config(), SavedState(autostart_asked=True), selftest=1, platform=build_platform(),
                      providers=build_providers())
    pet.save = lambda: None
    m = pet.menu
    out = ROOT / "build" / "preview"
    out.mkdir(parents=True, exist_ok=True)

    def render(name: str) -> None:
        m.u = 3
        w, h = m._layout()
        m.resize(w, h)
        m.grab().save(str(out / name))

    def row(key: str) -> int:
        return next(i for i, (it, _d, _r) in enumerate(m._rows) if it.key == key)

    m.items = pet.menus.menu_items()
    m.expanded.clear()
    top = [it.key for it in m.items]
    check("on top: Pause, Chat, Minigames, Cosmetics, Settings, Quit",
          top == ["pause", "chat", "games", "cosmetics", "settings", "quit"], str(top))
    games = next(it for it in m.items if it.key == "games").children
    sets = next(it for it in m.items if it.key == "settings").children
    check("Minigames: all 8 games (and Feed)", [c.key for c in games] == [
        "game:ball", "game:laser", "game:treats", "game:knock", "game:mice", "game:fishing", "game:hide",
        "game:boxes", "feed"] and games[0].label == "Play ball", str([c.label for c in games]))
    check("at the start only Play ball, Laser pointer and Treat toss are unlocked; the rest shows its stage",
          [c.locked for c in games[:8]] == [False, False, False, True, True, True, True, True]
          and games[3].note.startswith("STAGE "), str([(c.label, c.note) for c in games]))
    cos = next(it for it in m.items if it.key == "cosmetics").children
    check("Cosmetics: Cat Pass (stage + progress bar), Skins, Hats", [c.key for c in cos] == ["pass", "skins", "hats"]
          and cos[0].bar is not None and cos[0].note == "STAGE 1", str([(c.label, c.note) for c in cos]))
    hat_rows = [h for g in cos[2].children[1:] for h in g.children]
    check("11 skins (only Charcoal unlocked at first); No hat + 115 hats in 10 groups (all locked at first)",
          len(cos[1].children) == 11 and [c.key for c in cos[1].children if not c.locked] == ["skin:charcoal"]
          and cos[2].children[0].key == "hat:" and len(cos[2].children) == 11 and len(hat_rows) == 115
          and not any(not h.locked for h in hat_rows) and cos[2].children[1].note == "0/10",
          f"{len(cos[2].children) - 1} groups, {len(hat_rows)} hats, {cos[2].children[1].note}")
    check("Settings: Name, Style, Accent color, Size, Font, AI model, Your progress, then the on/off settings",
          [c.key for c in sets] == ["set_name", "set_style", "set_accent", "set_size", "set_font", "set_model",
                                    "progress", "quiet", "autostart", "automode", "preload_ai", "explore"],
          str([c.label for c in sets]))
    styles = sets[1].children
    check("10 styles to choose from, the current one marked", len(styles) == 10
          and sum(1 for c in styles if c.checked) == 1 and styles[0].label == "Cat Chat", str([c.label for c in styles]))
    local = [c for c in sets[5].children if c.key != "ext"]
    check("AI models from tiny to huge, with sizes and which are downloaded - then External AI models",
          [c.label for c in local][0] == "Tiny 0.8B" and [c.label for c in local][-1] == "Huge 122B"
          and any("READY" in c.note for c in local) and sets[5].children[-1].label == "External AI models",
          str([c.note for c in local]))
    render("menu_closed.png")
    check("the sections start folded: 6 rows", len(m._rows) == 6)
    m._activate(row("games"))
    check("clicking a section folds it open", len(m._rows) == 6 + len(games)
          and [d for _it, d, _r in m._rows].count(1) == len(games))
    m._activate(row("settings"))
    check("one section open at a time: opening Settings folds Minigames", len(m._rows) == 6 + len(sets)
          and "games" not in m.expanded and "settings" in m.expanded)
    m._activate(row("set_style"))
    check("sections fold open inside sections (Settings -> Style)", any(it.key == "style:midnight" for it, _d, _r
                                                                         in m._rows))
    render("menu_open.png")
    # a setting: toggles, menu stays open, new state shown
    closed: list[bool] = []
    real_close = m.close
    m.close = lambda: (closed.append(True), real_close())[1]  # type: ignore[method-assign]
    m.isVisible = lambda: True  # type: ignore[method-assign]   (as if it were open on screen)
    was = pet.saved.auto_mode
    m._activate(row("automode"))
    now = next(c for it in m.items for c in it.children if c.key == "automode")
    check("clicking a setting toggles it and keeps the menu open, showing the new state",
          pet.saved.auto_mode != was and not closed and now.checked == pet.saved.auto_mode)
    m._activate(row("automode"))
    pre = pet.cfg.preload_on_start
    m._activate(row("preload_ai"))
    check("'Load AI at start' is a setting too (saved in your own config.json)",
          pet.cfg.preload_on_start != pre and written[-1] == ("preload_on_start", not pre))
    m._activate(row("preload_ai"))
    check("the section stays open after a toggle", "settings" in m.expanded)
    # a style: applies at once, menu stays open
    from pixelcat.ui import theme

    looks: list[tuple] = []
    real_set = pet.menus.set_look
    pet.menus.set_look = lambda k, v: (looks.append((k, v)), theme.configure(**{k: v}))  # type: ignore[method-assign]
    m._activate(row("style:midnight"))
    check("clicking a style applies it at once and the menu stays open (style saved)",
          looks == [("style", "midnight")] and theme.current().key == "midnight" and not closed)
    m._activate(row("style:catchat"))
    pet.menus.set_look = real_set  # type: ignore[method-assign]
    # a model that isn't downloaded: asks first
    asked: list[str] = []
    pet.chat_ui.open_chat = pet.chat_ui.ensure_chat  # type: ignore[method-assign]   (not shown on screen)
    pet.chat_ui.ensure_chat()
    pet.chat_ui.chat.ask_confirm = lambda text, cb, allow_always=False: asked.append(text)  # type: ignore[method-assign]
    before = pet.cfg.model
    m._activate(row("set_model"))
    m._activate(row("model:qwen3.5:122b-a10b"))
    check("picking a model that isn't downloaded asks before downloading it (81 GB)",
          asked and "81 GB" in asked[0] and pet.cfg.model == before, asked[0] if asked else "")
    # locked things do nothing
    from pixelcat import progress

    m.expanded = {"games"}
    m.items = pet.menus.menu_items()
    m._layout()
    began: list[str] = []
    real_begin = pet.minigames._begin_game
    pet.minigames._begin_game = lambda k: began.append(k)  # type: ignore[method-assign]
    m._activate(row("game:hide"))
    check("clicking a locked game does nothing (menu stays open)", not began and not closed)
    # the first time: an explanation first (a fake popup - nothing on screen)
    from PySide6.QtCore import QObject, Signal

    class FakePop(QObject):
        answered = Signal(bool)

        def close_later(self, _s: float) -> None: ...

    pops: list[tuple] = []

    def fake_popup(title, text, ok, cancel=None, picture=None):  # type: ignore[no-untyped-def]
        fp = FakePop()
        pops.append((title, text, ok, cancel, picture, fp))
        return fp

    pet.popups.popup = fake_popup  # type: ignore[method-assign]
    pet.selftest = 0
    m._activate(row("game:laser"))
    check("the first time a game is played, a little window explains it first", bool(pops) and not began
          and pops[0][0] == "Laser pointer" and "laser" in pops[0][1] and pops[0][2] == "LET'S PLAY!"
          and pops[0][4] is not None, str(pops[0][:4]) if pops else "")
    pops[0][5].answered.emit(True)
    check("'Let's play!' starts the game and it is not explained again", began == ["laser"]
          and "laser" in pet.saved.tutorials_seen)
    pet.minigames.start_minigame("laser")
    check("the second time it starts right away", began == ["laser", "laser"] and len(pops) == 1)
    pet.minigames.start_minigame("treats")
    pops[-1][5].answered.emit(False)
    check("'Not now' doesn't start it (and it is explained again next time)", began[-1] == "laser"
          and "treats" not in pet.saved.tutorials_seen)
    pet.minigames._begin_game = real_begin  # type: ignore[method-assign]
    # XP: stages unlock things, with a result window
    pet.saved.xp = progress.xp_to_reach(4) - 10
    pops.clear()
    pet.minigames._award("laser", "Laser pointer", 40, "12 pounces!", "")
    check("XP from a game fills the Cat Pass; a new stage unlocks something (shown in the result window)",
          progress.state(pet.saved.xp).stage == 4 and pops and "+40 XP" in pops[-1][1] and "Ginger" in pops[-1][1]
          and pops[-1][2] == "PUT IT ON", pops[-1][1] if pops else "")
    pops[-1][5].answered.emit(True)
    check("'Put it on' wears the new skin at once", pet.saved.skin == "ginger" and pet.sprites.skin.key == "ginger")
    pet.selftest = 1
    # cosmetics: pick a skin and a hat, menu stays open
    from pixelcat.sprites import hats as hat_art

    pet.saved.xp = progress.xp_to_reach(12)
    first = next(k for kind, k in progress.REWARDS if kind == "hat")
    m.expanded = {"cosmetics", "hats", f"hatgroup:{hat_art.HATS[first].group}"}
    m.items = pet.menus.menu_items()
    m._layout()
    closed.clear()
    m._activate(row(f"hat:{first}"))
    check(f"Cosmetics -> Hats -> a group: a hat ({first}) goes on at once and the menu stays open",
          pet.saved.hat == first and pet.overlay.hat == first and not closed)
    group_row = next(it for it, _d, _r in m._rows if it.key == f"hatgroup:{hat_art.HATS[first].group}")
    check("the group shows how many of its hats you have (and that you wear one of them)",
          group_row.note.endswith("WEARING") and group_row.note.split("/")[0] != "0", group_row.note)
    m._activate(row("hat:"))
    check("'No hat' takes it off", pet.saved.hat == "" and pet.overlay.hat == "")
    locked = next(it.key for it, _d, _r in m._rows if it.key.startswith("hat:") and it.locked)
    m._activate(row(locked))
    check(f"a locked hat ({locked[4:]}) can't be worn", pet.saved.hat == "")
    m.expanded = {"cosmetics", "skins"}
    m.items = pet.menus.menu_items()
    m._layout()
    m._activate(row("skin:snow"))
    check("Cosmetics -> Skins: the cat changes skin at once", pet.sprites.skin.key == "snow" and not closed)
    m._activate(row("skin:galaxy"))
    check("a locked skin can't be picked", pet.sprites.skin.key == "snow")
    m.expanded = {"cosmetics", "pass"}
    m.items = pet.menus.menu_items()
    m._layout()
    stages = [it for it, _d, _r in m._rows if it.key.startswith("pass:")]
    check("the Cat Pass lists the stages around you: what each unlocks, how much XP is still needed",
          len(stages) == 8 and any(not it.locked for it in stages) and any(it.locked and it.note.endswith("XP")
                                                                           for it in stages),
          str([(it.label, it.note) for it in stages]))
    render("menu_cosmetics.png")
    m.expanded = {"cosmetics", "hats", "hatgroup:magic"}
    m.items = pet.menus.menu_items()
    render("menu_hats.png")
    # a menu taller than the screen scrolls
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    m.u = 3
    m._work = (0, 0, 1200, 700)
    m.expanded = {"cosmetics", "hats", "hatgroup:magic"}
    m.items = pet.menus.menu_items()
    m._reposition()
    tall = m._content_h
    check("a menu taller than the screen fits on it and scrolls", m.height() <= 700 and m._scroll_max > 0
          and tall > 700, f"content {tall}px, window {m.height()}px")
    y = m._view_top() + 5
    before = m._row_at(QPoint(40, y))
    m.wheelEvent(QWheelEvent(QPointF(40, y), QPointF(40, y), QPoint(0, 0), QPoint(0, -240), Qt.MouseButton.NoButton,
                             Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
    after = m._row_at(QPoint(40, y))
    check("the mouse wheel scrolls the rows (the header stays)", m._scroll > 0 and after > before,
          f"scroll {m._scroll}, row {before} -> {after}")
    m._hover = 0
    for _ in range(len(m._rows) - 1):
        m.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    r = m._rows[m._hover][2]
    check("arrow keys scroll to the highlighted row", m._view_top() <= r.top() - m._scroll
          and r.bottom() - m._scroll <= m._view_bottom(), f"row {m._hover}, scroll {m._scroll}")
    m._scroll = 0
    m._work = (0, 0, 1920, 1080)
    pet.minigames.set_skin("charcoal")
    pet.saved.xp = 0
    # a game: menu closes, the game starts
    pet.world.refresh_windows()                       # the cat needs its world (monitors, windows) to play
    pet.brain.place(pet.brain.smap.floors[0].x1 + 400, pet.brain.smap.floors[0].y - 1)
    m.expanded = {"games"}
    m.items = pet.menus.menu_items()
    m._layout()
    closed.clear()
    m._activate(row("game:ball"))
    check("clicking Play ball closes the menu and starts the game", bool(closed) and pet.game.active)
    m.items = pet.menus.menu_items()
    m._layout()
    label = next(c.label for it in m.items for c in it.children if c.key == "stopgame")
    check("during a game it says 'Stop playing ball'", label == "Stop playing ball", label)
    pet.game.stop(poof=False)
    # keyboard: arrows move, Enter folds a section
    m.expanded.clear()
    m.items = pet.menus.menu_items()
    m._layout()
    m._hover = -1
    for _ in range(3):
        m.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    m.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    check("keyboard: arrow keys and Enter open Minigames", "games" in m.expanded)
    m._hover = -1
    m.expanded = {"cosmetics", "pass"}
    m.items = pet.menus.menu_items()
    m._layout()
    seen = set()
    for _ in range(len(m._rows) + 2):
        m.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
        seen.add(m._rows[m._hover][0].key)
    check("keyboard skips the Cat Pass info rows", not any(k.startswith("pass:") or k == "pass_total" for k in seen),
          str(sorted(seen)))
    # dragging the menu by its title moves it; it stays there when sections fold open
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    m.expanded = set()
    m.items = pet.menus.menu_items()
    m._work = (0, 0, 1920, 1080)
    m._avoid = (900, 500, 996, 596)
    m._user_pos = None
    m._reposition()
    start = m.pos()
    grab_at = QPointF(30, 8)

    def mev(kind, local):  # type: ignore[no-untyped-def]
        glob = QPointF(m.pos().x() + local.x(), m.pos().y() + local.y())
        btn = Qt.MouseButton.LeftButton
        return QMouseEvent(kind, local, glob, btn, btn if kind != QEvent.Type.MouseButtonRelease else
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)

    fired: list[str] = []
    m.triggered.connect(fired.append)
    m.mousePressEvent(mev(QEvent.Type.MouseButtonPress, grab_at))
    for i in range(1, 11):
        m.mouseMoveEvent(mev(QEvent.Type.MouseMove, QPointF(grab_at.x() - 40 + (m.pos().x() - start.x()) * 0 - 0,
                                                            grab_at.y() + 0)) if False else
                         QMouseEvent(QEvent.Type.MouseMove, grab_at,
                                     QPointF(start.x() + grab_at.x() - 40 * i, start.y() + grab_at.y() - 25 * i),
                                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                     Qt.KeyboardModifier.NoModifier))
    m.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, grab_at))
    moved = m.pos()
    check("the menu can be dragged by its title (and letting go isn't a click)",
          (moved.x(), moved.y()) == (start.x() - 400, start.y() - 250) and not fired,
          f"{(start.x(), start.y())} -> {(moved.x(), moved.y())}")
    m._activate(row("games"))
    check("...and stays where you put it when a section folds open", (m.pos().x(), m.pos().y()) ==
          (moved.x(), moved.y()), f"{(m.pos().x(), m.pos().y())}")
    from pixelcat.adapters.windows import system

    check("'Start with Windows' shows the real setting (not changed by this test)",
          next(c for c in sets if c.key == "autostart").checked is system.autostart_enabled())
    name_and_chat_checks(pet, m, row)
    history_and_effort_checks(pet)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    rc = main()
    os._exit(rc)
