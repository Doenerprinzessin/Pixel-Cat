"""Small pixel-styled yes/no dialog (used for the first-run autostart consent)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme
from .geometry import Rect, phys_to_qt, place_near


class PixelDialog(QWidget):
    answered = Signal(bool)

    def __init__(self, title: str, text: str, yes: str = "YES", no: str = "NO") -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.u = 2
        self._done = False
        lbl = QLabel(f"<b>{title}</b><br>{text}")
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        self.yes = QPushButton(yes)
        self.no = QPushButton(no)
        self.no.setObjectName("noBtn")
        self.yes.clicked.connect(lambda: self._finish(True))
        self.no.clicked.connect(lambda: self._finish(False))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.yes)
        row.addWidget(self.no)
        self.lay = QVBoxLayout(self)
        self.lay.addWidget(lbl)
        self.lay.addLayout(row)
        self.label = lbl

    def show_near(self, avoid: Rect, work: Rect, u: int) -> None:
        self.u = max(1, u)
        px = theme.font_px_for_dpi(48 * self.u, 10)
        self.setStyleSheet(theme.chat_stylesheet(max(1, u // 2 + u % 2), px)
                           + f"QLabel {{ color:{theme.current().text}; font-family:'Segoe UI'; font-size:{px}px; }}")
        self.lay.setContentsMargins(6 * u, 6 * u, 7 * u, 7 * u)
        self.setFixedWidth(150 * u)
        self.adjustSize()
        x, y = place_near(avoid, (self.width(), self.height()), work, gap=6 * u)
        self.move(phys_to_qt(x, y))
        self.show()

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        theme.draw_pixel_panel(p, self.rect(), self.u)
        p.end()

    def _finish(self, ok: bool) -> None:
        if not self._done:
            self._done = True
            self.answered.emit(ok)
        self.close()


class KeyDialog(QWidget):
    """Paste an API key: hidden while typed, checked with the provider before it is kept.
    ``submitted(key)`` - the owner checks it and calls ``checked()`` with the result."""

    submitted = Signal(str)
    removed = Signal()
    link = Signal()                      # "GET A KEY": open the provider's key page

    def __init__(self, title: str, text: str, placeholder: str, has_key: bool, noun: str = "key") -> None:
        from PySide6.QtWidgets import QLineEdit

        super().__init__(None)
        self.noun = noun
        self.empty_msg, self.busy_msg = f"Paste the {noun} first.", f"Checking the {noun}…"
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.u = 2
        lbl = QLabel(f"<b>{title}</b><br>{text}")
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText(placeholder)
        self.field.returnPressed.connect(self._save)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.get = QPushButton(f"GET A {noun.upper()}")
        self.save = QPushButton("SAVE")
        self.cancel = QPushButton("CANCEL")
        self.cancel.setObjectName("noBtn")
        self.remove = QPushButton(f"REMOVE {noun.upper()}")
        self.remove.setObjectName("noBtn")
        self.remove.setVisible(has_key)
        self.get.clicked.connect(self.link.emit)
        self.save.clicked.connect(self._save)
        self.cancel.clicked.connect(self.close)
        self.remove.clicked.connect(lambda: (self.removed.emit(), self.close()))
        row = QHBoxLayout()
        row.addWidget(self.get)
        row.addStretch(1)
        row.addWidget(self.save)
        row.addWidget(self.cancel)
        self.lay = QVBoxLayout(self)
        self.lay.addWidget(lbl)
        self.lay.addWidget(self.field)
        self.lay.addWidget(self.status)
        self.lay.addLayout(row)
        if has_key:
            self.lay.addWidget(self.remove)

    def _save(self) -> None:
        key = self.field.text().strip()
        if not key:
            self.set_status(self.empty_msg, True)
            return
        self.field.setEnabled(False)
        self.save.setEnabled(False)
        self.set_status(self.busy_msg)
        self.submitted.emit(key)

    def checked(self, ok: bool, message: str) -> None:
        if ok:
            self.close()
            return
        self.field.setEnabled(True)
        self.save.setEnabled(True)
        self.field.selectAll()
        self.field.setFocus()
        self.set_status(message, True)

    def set_status(self, text: str, error: bool = False) -> None:
        st = theme.current()
        color = getattr(st, "error", None) or "#e05555"
        self.status.setStyleSheet(f"color:{color};" if error else f"color:{st.muted};")
        self.status.setText(text)

    def show_near(self, avoid: Rect, work: Rect, u: int) -> None:
        self.u = max(1, u)
        px = theme.font_px_for_dpi(48 * self.u, 10)
        self.setStyleSheet(theme.chat_stylesheet(max(1, u // 2 + u % 2), px)
                           + f"QLabel {{ color:{theme.current().text}; font-family:'Segoe UI'; font-size:{px}px; }}")
        self.lay.setContentsMargins(6 * u, 6 * u, 7 * u, 7 * u)
        self.setFixedWidth(170 * u)
        self.adjustSize()
        x, y = place_near(avoid, (self.width(), self.height()), work, gap=6 * u)
        self.move(phys_to_qt(x, y))
        self.show()
        self.activateWindow()
        self.raise_()
        self.field.setFocus()

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        theme.draw_pixel_panel(p, self.rect(), self.u)
        p.end()


class AmountDialog(KeyDialog):
    """Type an amount (a custom limit), shown as typed; the owner checks it like a key."""

    def __init__(self, title: str, text: str, placeholder: str, current: str = "") -> None:
        from PySide6.QtWidgets import QLineEdit

        super().__init__(title, text, placeholder, False, noun="amount")
        self.empty_msg, self.busy_msg = "Type an amount first.", "Checking…"
        self.field.setEchoMode(QLineEdit.EchoMode.Normal)
        self.field.setText(current)
        self.field.selectAll()
        self.get.hide()
        self.save.setText("SET")


class NameDialog(AmountDialog):
    """Type the cat's name; empty gives back the default (the owner checks it like a key)."""

    def __init__(self, title: str, text: str, current: str, max_len: int) -> None:
        super().__init__(title, text, "e.g. Mochi", current)
        self.field.setMaxLength(max_len)

    def _save(self) -> None:
        self.submitted.emit(self.field.text().strip())
