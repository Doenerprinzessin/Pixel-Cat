"""Pictures for the chat's model (Qt only, no platform calls): shrink a screenshot, mark its elements, encode it."""
from __future__ import annotations

import base64

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen


def fit(img: QImage, max_px: int) -> tuple[QImage, float]:
    """Downscale so the long side is at most ``max_px``; returns (image, scale)."""
    long_side = max(img.width(), img.height())
    if long_side <= max_px:
        return img, 1.0
    s = max_px / long_side
    return img.scaled(int(img.width() * s), int(img.height() * s), Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation), s


def draw_marks(img: QImage, marks: list[tuple[int, int, int, int, int]]) -> None:
    """Draw numbered boxes (id, x1, y1, x2, y2 in image pixels) so the model can name elements."""
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    font = QFont("Segoe UI")
    font.setPixelSize(11)
    font.setBold(True)
    p.setFont(font)
    fm = p.fontMetrics()
    for mid, x1, y1, x2, y2 in marks:
        p.setPen(QPen(QColor(230, 40, 140), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRect(x1, y1, max(1, x2 - x1), max(1, y2 - y1)))
        label = str(mid)
        tw, th = fm.horizontalAdvance(label) + 4, fm.height()
        bx, by = max(0, x1), max(0, y1 - th + 2) if y1 - th + 2 >= 0 else y1
        p.fillRect(QRect(bx, by, tw, th), QColor(230, 40, 140))
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRect(bx, by, tw, th), Qt.AlignmentFlag.AlignCenter, label)
    p.end()


def to_base64_png(img: QImage) -> str:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return base64.b64encode(bytes(data.data())).decode("ascii")
